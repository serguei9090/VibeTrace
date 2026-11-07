"""OASIS SARIF v2.1.0 Ingestion Engine for Security & SAST Tools.

Ingests SARIF reports from Semgrep, SonarQube, GitHub CodeQL, Trivy, and ESLint,
translates them to standardized VibeTrace SecurityFindings, and enriches them
with IndexerDB Call-Graph reachability.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.security.models import FindingCategory, SecurityFinding, Severity
from vibetrace.security.rules import RULES_CATALOG
from vibetrace.security.taint_engine import SANITIZERS, TaintEngine, is_source

logger = logging.getLogger(__name__)

_LEVEL_MAP: dict[str, Severity] = {
    "error": Severity.HIGH,
    "warning": Severity.MEDIUM,
    "note": Severity.LOW,
    "none": Severity.LOW,
}


class SarifIngestEngine:
    """Parses standard SARIF v2.1.0 reports and enriches findings with Call Graph context."""

    @classmethod
    def parse_sarif_file(
        cls,
        sarif_path: Path,
        project_root: Path,
        db: IndexerDB | None = None,
    ) -> list[SecurityFinding]:
        """Reads and parses a SARIF file from disk."""
        if not sarif_path.is_file():
            logger.warning("SARIF file not found at %s", sarif_path)
            return []

        try:
            content = sarif_path.read_text(encoding="utf-8", errors="replace")
            data = json.loads(content)
            return cls.parse_sarif_dict(data, project_root, db=db)
        except (OSError, json.JSONDecodeError) as err:
            logger.error("Failed to parse SARIF file %s: %s", sarif_path, err)
            return []

    @classmethod
    def parse_sarif_dict(
        cls,
        data: dict[str, Any],
        project_root: Path,
        db: IndexerDB | None = None,
    ) -> list[SecurityFinding]:
        """Translates a loaded SARIF dictionary into SecurityFindings."""
        findings: list[SecurityFinding] = []
        runs = data.get("runs")
        if not isinstance(runs, list):
            return []

        for run in runs:
            if not isinstance(run, dict):
                continue
            driver = run.get("tool", {}).get("driver", {})
            tool_name = str(driver.get("name") or "sast").lower()
            rules_list = driver.get("rules") or []
            rules_meta: dict[str, dict[str, Any]] = {}
            if isinstance(rules_list, list):
                for r in rules_list:
                    if isinstance(r, dict) and "id" in r:
                        rules_meta[r["id"]] = r

            results = run.get("results") or []
            if not isinstance(results, list):
                continue

            for res in results:
                if not isinstance(res, dict):
                    continue
                finding = cls._parse_single_result(
                    res,
                    tool_name=tool_name,
                    rules_meta=rules_meta,
                    project_root=project_root,
                    db=db,
                )
                if finding:
                    findings.append(finding)

        if db and findings:
            cls._enrich_with_reachability(findings, db, project_root)

        return findings

    @classmethod
    def _parse_single_result(
        cls,
        res: dict[str, Any],
        tool_name: str,
        rules_meta: dict[str, dict[str, Any]],
        project_root: Path,
        db: IndexerDB | None,
    ) -> SecurityFinding | None:
        """Parses an individual SARIF result item."""
        rule_id = str(res.get("ruleId") or "SAST-RULE")
        meta = rules_meta.get(rule_id, {})
        message_obj = res.get("message") or {}
        desc_text = meta.get("shortDescription", {}).get("text")
        message = str(message_obj.get("text") or desc_text or rule_id)
        level_str = str(res.get("level") or "warning").lower()
        severity = _LEVEL_MAP.get(level_str, Severity.MEDIUM)

        # Extract CWE tags
        cwe_id = None
        props = meta.get("properties") or {}
        cwe_list = props.get("cwe") or []
        if isinstance(cwe_list, list) and cwe_list:
            cwe_str = str(cwe_list[0])
            if "CWE-" in cwe_str.upper():
                cwe_id = cwe_str.split(":")[0].strip()

        # Fallback to rule_id if it mentions CWE
        if not cwe_id and "cwe-" in rule_id.lower():
            for part in rule_id.replace("_", "-").split("-"):
                if part.isdigit():
                    cwe_id = f"CWE-{part}"
                    break

        # Map to VibeTrace known specs if available
        if cwe_id and cwe_id in RULES_CATALOG:
            spec = RULES_CATALOG[cwe_id]
            title = spec.title
            remediation = spec.explanation
            compliant = spec.compliant_example
        else:
            short_desc = meta.get("shortDescription", {}).get("text") or rule_id
            title = f"[{tool_name.upper()}] {short_desc}"
            remediation = f"Refer to {tool_name} documentation for rule {rule_id}."
            compliant = None

        # Extract location
        locations = res.get("locations") or []
        if not isinstance(locations, list) or not locations:
            return None

        first_loc = locations[0].get("physicalLocation") or {}
        uri_str = str(first_loc.get("artifactLocation", {}).get("uri") or "").replace("\\", "/")
        if not uri_str:
            return None

        # Clean URI path relative to project root
        file_p = Path(uri_str)
        try:
            rel_path = file_p.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError:
            # Normalize and ensure no directory traversal escapes project root
            cleaned = uri_str.lstrip("/").replace("../", "").replace("..\\", "")
            resolved = (project_root / cleaned).resolve()
            if resolved.is_relative_to(project_root.resolve()):
                rel_path = resolved.relative_to(project_root.resolve()).as_posix()
            else:
                rel_path = Path(uri_str).name

        region = first_loc.get("region") or {}
        start_line = int(region.get("startLine") or 1)
        end_line = int(region.get("endLine") or start_line)

        # Correlate symbol with IndexerDB if available
        symbol_name = None
        if db:
            nodes_in_file = db.get_nodes_in_file(rel_path)
            for n in nodes_in_file:
                s_l = int(n.get("start_line") or 0)
                e_l = int(n.get("end_line") or 0)
                if s_l <= start_line <= e_l:
                    symbol_name = n.get("name")
                    break

        raw_id = f"{rel_path}:{start_line}:{tool_name}:{rule_id}"
        hash_suffix = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:12]
        finding_id = f"SARIF-{tool_name[:4].upper()}-{hash_suffix}"
        code_hash = hashlib.sha256(f"{rel_path}:{start_line}:{message}".encode()).hexdigest()

        return SecurityFinding(
            id=finding_id,
            rule_id=f"{tool_name}:{rule_id}",
            cwe_id=cwe_id or "CWE-SAST",
            category=FindingCategory.AST_PATTERN,
            severity=severity,
            title=title,
            message=message,
            file_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            symbol_name=symbol_name,
            code_hash=code_hash,
            non_compliant_snippet=None,
            compliant_snippet=compliant,
            remediation_guidance=remediation,
        )

    @classmethod
    def _enrich_with_reachability(
        cls,
        findings: list[SecurityFinding],
        db: IndexerDB,
        project_root: Path,
    ) -> None:
        """Enriches findings with Call Graph reachability and silences uncallable dead code."""
        try:
            with db._get_conn() as conn:
                nodes = {
                    r["id"]: dict(r)
                    for r in conn.execute(
                        "SELECT id, name, kind, file_path, start_line, end_line, decorators "
                        "FROM nodes"
                    ).fetchall()
                }
                callers: dict[str, list[str]] = {}
                for r in conn.execute(
                    "SELECT from_node_id, to_node_id FROM edges WHERE relation_type != 'defines'"
                ).fetchall():
                    callers.setdefault(r["to_node_id"], []).append(r["from_node_id"])
        except Exception as err:
            logger.debug("Could not query call graph for SARIF reachability: %s", err)
            return

        nodes_by_file: dict[str, list[dict[str, Any]]] = {}
        for n in nodes.values():
            if n.get("file_path"):
                norm_p = n["file_path"].replace("\\", "/")
                nodes_by_file.setdefault(norm_p, []).append(n)

        for f in findings:
            norm_file = f.file_path.replace("\\", "/")
            file_nodes = nodes_by_file.get(norm_file, [])
            best_node: dict[str, Any] | None = None
            best_span: int | None = None

            for n in file_nodes:
                s_l = int(n.get("start_line") or 0)
                e_l = int(n.get("end_line") or 0)
                if s_l <= f.start_line <= e_l:
                    span = e_l - s_l
                    if best_span is None or span < best_span:
                        best_span = span
                        best_node = n

            if not best_node:
                continue

            node_id = best_node["id"]
            node_name = best_node.get("name") or "anonymous"
            f.symbol_name = node_name

            # 1. Entry point check (e.g. FastAPI/Flask endpoint, Click CLI, etc.)
            if is_source(best_node.get("kind", ""), best_node.get("decorators")):
                f.metadata["reachability"] = "entrypoint"
                f.metadata["is_reachable"] = True
                f.message = f"{f.message} (Entry point: `{node_name}`)"
                continue

            # 2. Reverse BFS search to verify path from any untrusted entry point
            path = TaintEngine._path_from_source(nodes, callers, node_id, SANITIZERS)
            if path:
                path_names = [nodes[nid]["name"] for nid in path if nid in nodes]
                f.metadata["reachability"] = "entrypoint_reachable"
                f.metadata["is_reachable"] = True
                f.metadata["reachability_path"] = path_names
                f.message = (
                    f"{f.message} (Active Call-Graph: reachable from `{path_names[0]}` "
                    f"via {' -> '.join(path_names)})"
                )
                continue

            # 3. General internal callers check
            incoming = callers.get(node_id, [])
            if incoming:
                f.metadata["reachability"] = "internal_reachable"
                f.metadata["is_reachable"] = True
                f.metadata["reachability_callers"] = len(incoming)
                continue

            # 4. Check if test fixture or top-level execution entry
            is_test = "test" in norm_file.lower()
            is_script = (
                Path(norm_file).name in ("main.py", "__main__.py", "cli.py") or node_name == "main"
            )
            if is_test:
                f.metadata["reachability"] = "test_fixture"
                f.metadata["is_reachable"] = True
            elif is_script:
                f.metadata["reachability"] = "main_script"
                f.metadata["is_reachable"] = True
            else:
                # 0 callers, not entry point, not test/script -> Unreachable Dead Code
                f.metadata["reachability"] = "unreachable_dead_code"
                f.metadata["is_reachable"] = False
                f.metadata["reachability_silenced"] = True
                f.tag = "unreachable-dead-code"
                f.status = "APPROVED"
                f.rationale = (
                    "Automatically silenced by VibeTrace: symbol is unreachable "
                    "dead code in call-graph (0 incoming callers)."
                )
                f.title = f"{f.title} [UNREACHABLE_DEAD_CODE]"
