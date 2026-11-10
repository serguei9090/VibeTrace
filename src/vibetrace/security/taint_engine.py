"""Call-graph taint reachability: escalates sinks reachable from untrusted entry points.

Marker: edge-based reachability ignores which value reaches the sink (false positives) ->
upgrade path: intra-procedural data-flow on the tree-sitter AST for argument provenance.
"""

from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path
from typing import Any

from vibetrace.constants import DEFAULT_MAX_DEPTH
from vibetrace.indexer.db import IndexerDB
from vibetrace.security.dataflow import clear_taint_file_cache, verify_interprocedural_taint_flow
from vibetrace.security.models import FindingCategory, SecurityFinding, Severity

MAX_DEPTH = DEFAULT_MAX_DEPTH
TAINT_CWES = {
    "CWE-78",
    "CWE-95",
    "CWE-502",
    "CWE-89",
    "CWE-22",
    "CWE-79",
    "CWE-918",
    "CWE-611",
    "CWE-601",
    "CWE-943",
    "CWE-1321",
    "CWE-1336",
}
SANITIZERS = frozenset(
    {
        "quote",
        "escape",
        "escape_string",
        "resolve",
        "safe_load",
        "literal_eval",
        "is_relative_to",
        "sanitize",
        "validator",
        "clean",
        "encodeURIComponent",
        "encodeURI",
        "DOMPurify",
        "bleach",
        "html.escape",
        "shlex.quote",
        "QueryEscape",
        "HTMLEscapeString",
        "Uri.EscapeDataString",
        "HtmlEncoder.Default.Encode",
    }
)
_SOURCE_DECORATOR_RE = re.compile(
    r"(\.(get|post|put|patch|delete|route|websocket)\b|click\.(command|group)|\.tool\b)"
)
_SEVERITY_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
_CONTAINER_KINDS = ("function", "method", "endpoint")


def _is_source(kind: str, decorators_json: str | None) -> bool:
    if kind == "endpoint":
        return True
    if not decorators_json:
        return False
    try:
        decorators = json.loads(decorators_json)
    except (TypeError, ValueError):
        return False
    return any(_SOURCE_DECORATOR_RE.search(str(d)) for d in decorators)


is_source = _is_source


def _bump(severity: Severity) -> Severity:
    if severity not in _SEVERITY_ORDER:
        return severity
    idx = _SEVERITY_ORDER.index(severity)
    return _SEVERITY_ORDER[min(idx + 1, len(_SEVERITY_ORDER) - 1)]


class TaintEngine:
    """Annotates AST findings with source-to-sink call paths from the indexed call graph."""

    @classmethod
    def _process_candidate(
        cls,
        finding: SecurityFinding,
        nodes: dict[str, Any],
        callers: dict[str, list[str]],
        sanitizers: frozenset[str],
        project_root: Path | None,
    ) -> None:
        sink_id = cls._enclosing_node_id(nodes, finding)
        if sink_id is None:
            return
        candidate_paths = cls._paths_from_sources(nodes, callers, sink_id, sanitizers, max_paths=24)
        if not candidate_paths:
            return

        chosen_path: list[str] | None = None
        chosen_var_chain: list[str] = []
        last_reason = "untainted_local"

        for path in candidate_paths:
            sink_node = nodes[sink_id]
            if project_root is not None and sink_node["start_line"] and sink_node["end_line"]:
                path_nodes = [dict(nodes[nid]) for nid in path if nid in nodes]
                is_tainted, var_chain, reason = verify_interprocedural_taint_flow(
                    project_root=project_root,
                    path_nodes=path_nodes,
                    sink_file_path=finding.file_path,
                    sink_line=finding.start_line,
                    sanitizers=sanitizers,
                )
                if not is_tainted:
                    last_reason = reason
                    continue
                chosen_path = path
                chosen_var_chain = var_chain
                break
            else:
                chosen_path = path
                break

        if chosen_path is None:
            finding.metadata["dataflow"] = last_reason
            return

        finding.category = FindingCategory.TAINT_REACHABILITY
        finding.severity = _bump(finding.severity)
        finding.symbol_name = nodes[sink_id]["name"]
        finding.metadata["taint_path"] = [nodes[n]["name"] for n in chosen_path]
        finding.metadata["taint_path_node_ids"] = chosen_path
        if chosen_var_chain:
            finding.metadata["taint_vars"] = chosen_var_chain
        if project_root is not None:
            slices = cls.slice_taint_path(finding, project_root, nodes)
            if slices:
                finding.metadata["taint_slices"] = slices
        finding.message = (
            f"{finding.message} Reachable from untrusted entry point "
            f"`{nodes[chosen_path[0]]['name']}` via "
            f"{' -> '.join(nodes[n]['name'] for n in chosen_path)}."
        )

    @classmethod
    def analyze(
        cls,
        db: IndexerDB,
        findings: list[SecurityFinding],
        extra_sanitizers: frozenset[str] = frozenset(),
        project_root: Path | None = None,
    ) -> list[SecurityFinding]:
        candidates = [f for f in findings if f.cwe_id in TAINT_CWES]
        if not candidates:
            return findings
        sanitizers = SANITIZERS | extra_sanitizers

        with db._get_conn() as conn:
            nodes = {
                r["id"]: r
                for r in conn.execute(
                    "SELECT id, name, kind, file_path, start_line, end_line, decorators FROM nodes"
                ).fetchall()
            }
            callers: dict[str, list[str]] = {}
            for r in conn.execute(
                "SELECT from_node_id, to_node_id FROM edges "
                "WHERE relation_type IN ('calls', 'routes')"
            ).fetchall():
                callers.setdefault(r["to_node_id"], []).append(r["from_node_id"])

        try:
            for finding in candidates:
                cls._process_candidate(finding, nodes, callers, sanitizers, project_root)
        finally:
            clear_taint_file_cache()

        return findings

    @classmethod
    def slice_taint_path(
        cls,
        finding: SecurityFinding,
        project_root: Path,
        nodes: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Extracts code slices across all hops along the verified taint path."""
        path_ids = finding.metadata.get("taint_path_node_ids", [])
        if not path_ids or not nodes:
            return []

        slices: list[dict[str, Any]] = []
        for step, nid in enumerate(path_ids, 1):
            raw_node = nodes.get(nid)
            if not raw_node:
                continue
            node = dict(raw_node)
            if not node.get("file_path"):
                continue
            rel_file = node["file_path"]
            abs_path = (project_root / rel_file).resolve()
            if not abs_path.is_file() or not abs_path.is_relative_to(project_root.resolve()):
                continue
            s_line = node.get("start_line") or 1
            e_line = node.get("end_line") or s_line
            try:
                all_lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
                code_slice = "\n".join(all_lines[max(0, s_line - 1) : min(len(all_lines), e_line)])
            except OSError:
                code_slice = ""

            slices.append(
                {
                    "step": step,
                    "symbol": node.get("name", "anonymous"),
                    "file": rel_file,
                    "start_line": s_line,
                    "end_line": e_line,
                    "snippet": code_slice,
                }
            )
        return slices

    @staticmethod
    def _enclosing_node_id(nodes: dict, finding: SecurityFinding) -> str | None:
        fpath = finding.file_path.replace("\\", "/")
        best: tuple[int, str] | None = None
        for nid, n in nodes.items():
            if n["kind"] not in _CONTAINER_KINDS or n["file_path"] != fpath:
                continue
            if n["start_line"] is None or n["end_line"] is None:
                continue
            if n["start_line"] <= finding.start_line <= n["end_line"]:
                span = n["end_line"] - n["start_line"]
                if best is None or span < best[0]:
                    best = (span, nid)
        return best[1] if best else None

    @staticmethod
    def _paths_from_sources(
        nodes: dict,
        callers: dict[str, list[str]],
        sink_id: str,
        sanitizers: frozenset[str],
        max_paths: int = 24,
        max_states: int = 2048,
    ) -> list[list[str]]:
        """Reverse BFS from sink; returns candidate paths ordered source -> sink."""
        results: list[list[str]] = []
        queue: deque[list[str]] = deque([[sink_id]])
        best_depth: dict[str, int] = {sink_id: 0}
        states_explored = 0

        while queue and len(results) < max_paths and states_explored < max_states:
            current_path = queue.popleft()
            states_explored += 1
            tip = current_path[-1]
            n = nodes[tip]
            if _is_source(n["kind"], n["decorators"]):
                results.append(list(reversed(current_path)))
                continue

            next_depth = len(current_path)
            if next_depth > MAX_DEPTH:
                continue

            for caller_id in callers.get(tip, []):
                if caller_id in current_path or caller_id not in nodes:
                    continue
                if nodes[caller_id]["name"] in sanitizers:
                    continue
                caller_node = nodes[caller_id]
                is_src = _is_source(caller_node["kind"], caller_node["decorators"])
                if not is_src:
                    prev_best = best_depth.get(caller_id)
                    if prev_best is not None and next_depth > prev_best + 1:
                        continue
                    if prev_best is None or next_depth < prev_best:
                        best_depth[caller_id] = next_depth
                queue.append([*current_path, caller_id])

        return results

    @staticmethod
    def _path_from_source(
        nodes: dict, callers: dict[str, list[str]], sink_id: str, sanitizers: frozenset[str]
    ) -> list[str] | None:
        """Reverse BFS from sink; returns node ids ordered source -> sink, or None."""
        paths = TaintEngine._paths_from_sources(nodes, callers, sink_id, sanitizers, max_paths=1)
        return paths[0] if paths else None
