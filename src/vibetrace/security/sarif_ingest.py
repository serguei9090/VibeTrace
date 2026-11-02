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
            rel_path = file_p.relative_to(project_root).as_posix()
        except ValueError:
            rel_path = uri_str.lstrip("/")

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
