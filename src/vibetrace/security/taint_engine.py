"""Call-graph taint reachability: escalates sinks reachable from untrusted entry points.

Marker: edge-based reachability ignores which value reaches the sink (false positives) ->
upgrade path: intra-procedural data-flow on the tree-sitter AST for argument provenance.
"""

from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.security.dataflow import verify_sink_taint_flow
from vibetrace.security.models import FindingCategory, SecurityFinding, Severity

MAX_DEPTH = 8
TAINT_CWES = {"CWE-78", "CWE-95", "CWE-502", "CWE-89", "CWE-22", "CWE-79", "CWE-918", "CWE-611"}
SANITIZERS = frozenset(
    {"quote", "escape", "escape_string", "resolve", "safe_load", "literal_eval", "is_relative_to"}
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


def _bump(severity: Severity) -> Severity:
    if severity not in _SEVERITY_ORDER:
        return severity
    idx = _SEVERITY_ORDER.index(severity)
    return _SEVERITY_ORDER[min(idx + 1, len(_SEVERITY_ORDER) - 1)]


class TaintEngine:
    """Annotates AST findings with source-to-sink call paths from the indexed call graph."""

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
                "SELECT from_node_id, to_node_id FROM edges WHERE relation_type != 'defines'"
            ).fetchall():
                callers.setdefault(r["to_node_id"], []).append(r["from_node_id"])

        for finding in candidates:
            sink_id = cls._enclosing_node_id(nodes, finding)
            if sink_id is None:
                continue
            path = cls._path_from_source(nodes, callers, sink_id, sanitizers)
            if path is None:
                continue
            sink_node = nodes[sink_id]
            if project_root is not None and sink_node["start_line"] and sink_node["end_line"]:
                is_tainted, var_chain = verify_sink_taint_flow(
                    project_root=project_root,
                    file_path=finding.file_path,
                    func_start_line=int(sink_node["start_line"]),
                    func_end_line=int(sink_node["end_line"]),
                    sink_line=finding.start_line,
                    sanitizers=sanitizers,
                )
                if not is_tainted:
                    finding.metadata["dataflow"] = "untainted_local"
                    continue
                if var_chain:
                    finding.metadata["taint_vars"] = var_chain
            finding.category = FindingCategory.TAINT_REACHABILITY
            finding.severity = _bump(finding.severity)
            finding.symbol_name = nodes[sink_id]["name"]
            finding.metadata["taint_path"] = [nodes[n]["name"] for n in path]
            finding.metadata["taint_path_node_ids"] = path
            finding.message = (
                f"{finding.message} Reachable from untrusted entry point "
                f"`{nodes[path[0]]['name']}` via {' -> '.join(nodes[n]['name'] for n in path)}."
            )
        return findings

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
    def _path_from_source(
        nodes: dict, callers: dict[str, list[str]], sink_id: str, sanitizers: frozenset[str]
    ):
        """Reverse BFS from sink; returns node ids ordered source -> sink, or None."""
        queue: deque[tuple[str, int]] = deque([(sink_id, 0)])
        parent: dict[str, str | None] = {sink_id: None}
        while queue:
            current, depth = queue.popleft()
            n = nodes[current]
            if _is_source(n["kind"], n["decorators"]):
                path = [current]
                while parent[path[-1]] is not None:
                    path.append(parent[path[-1]])  # type: ignore[arg-type]
                return path
            if depth >= MAX_DEPTH:
                continue
            for caller_id in callers.get(current, []):
                if caller_id in parent or caller_id not in nodes:
                    continue
                if nodes[caller_id]["name"] in sanitizers:
                    continue  # sanitizer on the path blocks propagation
                parent[caller_id] = current
                queue.append((caller_id, depth + 1))
        return None
