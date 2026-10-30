"""Baseline Exception and Triage Manager for Security Vulnerabilities (.vibetrace/security.toml)."""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vibetrace.security.models import SecurityAuditReport, SecurityFinding

SECURITY_BASELINE_FILE = ".vibetrace/security.toml"


def _escape_toml_str(val: str) -> str:
    """Escapes string values safely for TOML format."""
    return val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class SecurityBaselineManager:
    """Loads, saves, and evaluates findings against the Git-tracked .vibetrace/security.toml."""

    @classmethod
    def get_baseline_path(cls, project_root: Path) -> Path:
        return project_root / SECURITY_BASELINE_FILE

    @classmethod
    def load_sanitizers(cls, project_root: Path) -> list[str]:
        """Reads user-declared taint sanitizer function names from the [taint] table."""
        path = cls.get_baseline_path(project_root)
        if not path.is_file():
            return []
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return []
        return [str(s) for s in raw.get("taint", {}).get("sanitizers", [])]

    @classmethod
    def load_baseline(cls, project_root: Path) -> dict[str, dict[str, Any]]:
        """Loads accepted security exceptions keyed by finding ID and rule_id:file_path."""
        path = cls.get_baseline_path(project_root)
        if not path.is_file():
            return {}

        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
            exceptions: dict[str, dict[str, Any]] = {}
            for exc in raw.get("exceptions", []):
                if exc_id := exc.get("id"):
                    exceptions[exc_id] = exc
                if (r_id := exc.get("rule_id")) and (f_path := exc.get("file_path")):
                    exceptions[f"{r_id}:{f_path}"] = exc
            return exceptions
        except Exception:
            return {}

    @classmethod
    def apply_baseline_to_findings(
        cls, findings: list[SecurityFinding], project_root: Path
    ) -> SecurityAuditReport:
        """Evaluates findings against baseline exceptions and detects MODIFIED code hashes."""
        baseline = cls.load_baseline(project_root)
        report = SecurityAuditReport(total_findings=len(findings))

        for f in findings:
            key = f.id
            alt_key = f"{f.rule_id}:{f.file_path}"
            match = baseline.get(key) or baseline.get(alt_key)

            if match:
                base_hash = match.get("code_hash", "")
                if base_hash and base_hash != f.code_hash:
                    f.status = "MODIFIED_CODE"
                    report.modified_count += 1
                else:
                    f.status = match.get("status", "APPROVED")
                    f.rationale = match.get("rationale")
                    f.reviewed_by = match.get("reviewed_by")
                    f.reviewed_at = match.get("reviewed_at")
                    report.approved_count += 1
            else:
                f.status = "UNAPPROVED"
                report.unapproved_count += 1

            if f.severity == "CRITICAL":
                report.critical_count += 1
            elif f.severity == "HIGH":
                report.high_count += 1
            elif f.severity == "MEDIUM":
                report.medium_count += 1
            elif f.severity == "LOW":
                report.low_count += 1

            report.findings.append(f)

        return report

    @classmethod
    def record_exception(
        cls,
        project_root: Path,
        finding_id: str,
        rule_id: str,
        file_path: str,
        code_hash: str,
        rationale: str,
        severity: str = "HIGH",
        status: str = "APPROVED",
        reviewed_by: str = "developer",
    ) -> None:
        """Appends or updates a security exception in .vibetrace/security.toml."""
        path = cls.get_baseline_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)

        existing: list[dict[str, Any]] = []
        sanitizers = cls.load_sanitizers(project_root)
        if path.is_file():
            try:
                raw = tomllib.loads(path.read_text(encoding="utf-8"))
                existing = [e for e in raw.get("exceptions", []) if e.get("id") != finding_id]
            except Exception:
                existing = []

        now_iso = datetime.now(UTC).isoformat()
        new_entry = {
            "id": finding_id,
            "rule_id": rule_id,
            "file_path": file_path,
            "code_hash": code_hash,
            "severity": severity,
            "status": status,
            "rationale": rationale,
            "reviewed_by": reviewed_by,
            "reviewed_at": now_iso,
        }
        existing.append(new_entry)

        lines = [
            "# VibeTrace Security Baseline & Triage Exceptions",
            (
                "# This file is tracked in Git. Code edits to accepted "
                "vulnerabilities flip status to MODIFIED."
            ),
            "",
            "[meta]",
            'version = "1.0.0"',
            f'updated_at = "{now_iso}"',
            f"total_exceptions = {len(existing)}",
            "",
        ]
        if sanitizers:
            quoted = ", ".join(f'"{_escape_toml_str(s)}"' for s in sanitizers)
            lines += ["[taint]", f"sanitizers = [{quoted}]", ""]

        for exc in existing:
            lines.append("[[exceptions]]")
            lines.append(f'id = "{_escape_toml_str(exc.get("id", ""))}"')
            lines.append(f'rule_id = "{_escape_toml_str(exc.get("rule_id", ""))}"')
            lines.append(f'file_path = "{_escape_toml_str(exc.get("file_path", ""))}"')
            lines.append(f'code_hash = "{_escape_toml_str(exc.get("code_hash", ""))}"')
            lines.append(f'severity = "{_escape_toml_str(exc.get("severity", "HIGH"))}"')
            lines.append(f'status = "{_escape_toml_str(exc.get("status", "APPROVED"))}"')
            lines.append(f'rationale = "{_escape_toml_str(exc.get("rationale", ""))}"')
            lines.append(f'reviewed_by = "{_escape_toml_str(exc.get("reviewed_by", "developer"))}"')
            lines.append(f'reviewed_at = "{_escape_toml_str(exc.get("reviewed_at", now_iso))}"')
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
