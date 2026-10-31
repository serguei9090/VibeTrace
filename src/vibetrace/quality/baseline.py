"""Baseline Exception and Triage Manager for Code Quality (.vibetrace/quality.toml)."""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vibetrace.quality.models import (
    QualityAuditReport,
    QualityExceptionRecord,
    QualityFinding,
)

QUALITY_BASELINE_FILE = ".vibetrace/quality.toml"


def _escape_toml_str(val: str) -> str:
    """Escapes string values safely for TOML format."""
    return val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _upsert_exception_entry(
    exceptions_list: list[dict[str, Any]],
    record: QualityExceptionRecord,
    now_str: str,
) -> None:
    """Updates an existing matching entry in-place or appends a new entry."""
    for entry in exceptions_list:
        id_match = entry.get("id") == record.id
        pos_match = (
            entry.get("rule_id") == record.rule_id
            and entry.get("file_path") == record.file_path
            and entry.get("symbol_name") == (record.symbol_name or "")
        )
        if id_match or pos_match:
            entry.update(record.model_dump(by_alias=False, exclude_none=True))
            entry["reviewed_at"] = now_str
            return

    new_dict = record.model_dump(by_alias=False, exclude_none=True)
    new_dict["reviewed_at"] = now_str
    exceptions_list.append(new_dict)


def _serialize_quality_toml(exceptions_list: list[dict[str, Any]], now_str: str) -> str:
    """Serializes the quality baseline exceptions into clean TOML string format."""
    lines = [
        "# .vibetrace/quality.toml",
        "# Code quality exceptions and approved complexity baseline.",
        f"# Last updated: {now_str}",
        "",
        "[quality]",
        'version = "1.0"',
        "default_complexity_threshold = 15",
        "",
    ]

    for exc in exceptions_list:
        lines.append("[[exceptions]]")
        lines.append(f'id = "{_escape_toml_str(str(exc.get("id", "")))}"')
        lines.append(f'rule_id = "{_escape_toml_str(str(exc.get("rule_id", "")))}"')
        lines.append(f'file_path = "{_escape_toml_str(str(exc.get("file_path", "")))}"')
        lines.append(f'symbol_name = "{_escape_toml_str(str(exc.get("symbol_name", "")))}"')
        lines.append(f'code_hash = "{_escape_toml_str(str(exc.get("code_hash", "")))}"')
        lines.append(f'severity = "{_escape_toml_str(str(exc.get("severity", "HIGH")))}"')
        lines.append(f'category = "{_escape_toml_str(str(exc.get("category", "complexity")))}"')
        lines.append(f'status = "{_escape_toml_str(str(exc.get("status", "APPROVED")))}"')
        lines.append(f'tag = "{_escape_toml_str(str(exc.get("tag", "intentional")))}"')
        lines.append(f'rationale = "{_escape_toml_str(str(exc.get("rationale", "")))}"')
        lines.append(f'reviewed_by = "{_escape_toml_str(str(exc.get("reviewed_by", "cli-user")))}"')
        lines.append(f'reviewed_at = "{_escape_toml_str(str(exc.get("reviewed_at", now_str)))}"')
        if "metric_value" in exc and exc["metric_value"] is not None:
            lines.append(f"metric_value = {exc['metric_value']}")
        if "metric_threshold" in exc and exc["metric_threshold"] is not None:
            lines.append(f"metric_threshold = {exc['metric_threshold']}")
        lines.append("")

    return "\n".join(lines)


class QualityBaselineManager:
    """Loads, saves, and evaluates quality findings against .vibetrace/quality.toml."""

    @classmethod
    def get_baseline_path(cls, project_root: Path) -> Path:
        return project_root / QUALITY_BASELINE_FILE

    @classmethod
    def load_baseline(cls, project_root: Path) -> dict[str, dict[str, Any]]:
        """Loads accepted quality exceptions keyed by finding ID and rule_id:file_path:symbol."""
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
                    sym = exc.get("symbol_name", "")
                    exceptions[f"{r_id}:{f_path}:{sym}"] = exc
            return exceptions
        except Exception:
            return {}

    @classmethod
    def _reconcile_finding_status(
        cls, f: QualityFinding, baseline: dict[str, dict[str, Any]]
    ) -> str:
        """Determines the status of a finding against the baseline exceptions."""
        if f.status == "APPROVED" and f.tag == "inline-comment":
            return "APPROVED"

        key = f.id
        alt_key = f"{f.rule_id}:{f.file_path}:{f.symbol_name or ''}"
        match = baseline.get(key) or baseline.get(alt_key)
        if not match:
            f.status = "UNAPPROVED"
            return "UNAPPROVED"

        f.rationale = match.get("rationale")
        f.tag = match.get("tag")
        f.reviewed_by = match.get("reviewed_by")
        f.reviewed_at = match.get("reviewed_at")

        recorded_hash = match.get("code_hash", "")
        if recorded_hash and f.code_hash and recorded_hash != f.code_hash:
            f.status = "MODIFIED_CODE"
            return "MODIFIED_CODE"

        f.status = match.get("status", "APPROVED")
        return f.status

    @classmethod
    def apply_baseline_to_findings(
        cls, findings: list[QualityFinding], project_root: Path
    ) -> QualityAuditReport:
        """Evaluates findings against baseline exceptions and detects MODIFIED code hashes."""
        baseline = cls.load_baseline(project_root)
        report = QualityAuditReport(total_findings=len(findings))

        for f in findings:
            status = cls._reconcile_finding_status(f, baseline)
            if status == "APPROVED":
                report.approved_count += 1
            elif status == "MODIFIED_CODE":
                report.modified_count += 1
            else:
                report.unapproved_count += 1

            if f.severity.value == "HIGH":
                report.high_count += 1
            elif f.severity.value == "MEDIUM":
                report.medium_count += 1
            elif f.severity.value == "LOW":
                report.low_count += 1

            report.findings.append(f)

        return report

    @classmethod
    def record_exception(
        cls,
        project_root: Path,
        record: QualityExceptionRecord | None = None,
        **kwargs: Any,
    ) -> None:
        """Records or updates an approved quality exception in .vibetrace/quality.toml."""
        path = cls.get_baseline_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)

        if record is None:
            record = QualityExceptionRecord(**kwargs)

        existing_text = path.read_text(encoding="utf-8") if path.is_file() else ""
        existing_doc: dict[str, Any] = {}
        if existing_text:
            try:
                existing_doc = tomllib.loads(existing_text)
            except Exception:
                existing_doc = {}

        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        exceptions_list = existing_doc.get("exceptions", [])

        _upsert_exception_entry(exceptions_list, record, now_str)
        content = _serialize_quality_toml(exceptions_list, now_str)
        path.write_text(content, encoding="utf-8")
