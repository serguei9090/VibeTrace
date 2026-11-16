"""Shared split-report persistence and status tracking for Security and Quality audits."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VALID_STATUSES = (
    "OPEN",
    "FIXED",
    "APPROVED",
    "FALSE_POSITIVE",
    "DEFERRED",
    "DEPRECATED",
)

_STATUS_HEADER_RE = re.compile(
    r"^###\s+\[(OPEN|FIXED|APPROVED|FALSE_POSITIVE|DEFERRED|DEPRECATED)\]\s+`([^`]+)`"
)
_NOTE_RE = re.compile(r"^- \*\*Note:\*\*\s*(.+)$")


def _parse_existing_statuses(report_dir: Path) -> dict[str, tuple[str, str]]:
    """Read existing category markdown files and return {finding_id: (status, note)}."""
    saved: dict[str, tuple[str, str]] = {}
    if not report_dir.is_dir():
        return saved
    for md_file in report_dir.glob("*.md"):
        if md_file.name == "summary.md":
            continue
        current_id: str | None = None
        current_status = "OPEN"
        for line in md_file.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _STATUS_HEADER_RE.match(line)
            if m:
                current_status = m.group(1)
                current_id = m.group(2)
                if current_status != "OPEN" and current_id is not None:
                    saved[str(current_id)] = (current_status, "—")
            elif current_id is not None and current_status != "OPEN":
                nm = _NOTE_RE.match(line)
                if nm:
                    saved[current_id] = (current_status, nm.group(1).strip())
                    current_id = None
    return saved


def _extract_field(item: Any, key: str, default: str = "") -> str:
    val = item.get(key) if isinstance(item, dict) else getattr(item, key, default)
    if val is None:
        return default
    return str(getattr(val, "value", val))


class ReportStore:
    """Persists split category Markdown reports and a compact summary.md."""

    @staticmethod
    def get_report_dir(project_root: Path, domain: str) -> Path:
        return project_root / ".vibetrace" / "reports" / domain

    @classmethod
    def read_summary(cls, project_root: Path, domain: str) -> str | None:
        summary_path = cls.get_report_dir(project_root, domain) / "summary.md"
        if not summary_path.is_file():
            return None
        return summary_path.read_text(encoding="utf-8", errors="replace")

    @classmethod
    def save_split_report(
        cls,
        project_root: Path,
        domain: str,
        findings: list[Any],
        scope: str = "full",
        approve_hint: str = "vibetrace_approve_security_exception",
    ) -> str:
        report_dir = cls.get_report_dir(project_root, domain)
        report_dir.mkdir(parents=True, exist_ok=True)
        existing = _parse_existing_statuses(report_dir)
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        by_cat: dict[str, list[tuple[Any, str, str]]] = defaultdict(list)
        sev_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        status_totals: dict[str, int] = defaultdict(int)

        for f in findings:
            fid = _extract_field(f, "id", "unknown")
            cat = _extract_field(f, "category", "general").lower()
            sev = _extract_field(f, "severity", "MEDIUM").upper()
            raw_status = _extract_field(f, "status", "OPEN").upper()
            if fid in existing:
                status, note = existing[fid]
            elif raw_status in VALID_STATUSES:
                status, note = raw_status, "—"
            else:
                status, note = "OPEN", "—"
            by_cat[cat].append((f, status, note))
            sev_counts[sev][status] += 1
            status_totals[status] += 1

        for cat, items in by_cat.items():
            cat_counts = defaultdict(int)
            for _, st, _ in items:
                cat_counts[st] += 1
            counts_str = ", ".join(f"{cat_counts[s]} {s}" for s in VALID_STATUSES)
            lines = [
                f"# {domain.title()} Findings: {cat}",
                f"- **Updated:** {now}",
                f"- **Counts:** {counts_str}",
                "",
                "---",
                "",
            ]
            for f, st, note in items:
                fid = _extract_field(f, "id", "unknown")
                title = _extract_field(f, "title", _extract_field(f, "message", "Finding"))
                sev = _extract_field(f, "severity", "MEDIUM").upper()
                rule = _extract_field(f, "rule_id", "")
                cwe = _extract_field(f, "cwe_id", "")
                fpath = _extract_field(f, "file_path", "")
                line_no = _extract_field(f, "line_number", _extract_field(f, "start_line", "1"))
                remed = _extract_field(f, "remediation", _extract_field(f, "description", "—"))
                rule_str = f"`{rule}` ({cwe})" if cwe else f"`{rule}`"
                lines.extend(
                    [
                        f"### [{st}] `{fid}` — {title}",
                        f"- **Severity:** {sev}",
                        f"- **Rule:** {rule_str}",
                        f"- **Location:** `{fpath}:{line_no}`",
                        f"- **Remediation:** {remed}",
                        f"- **Note:** {note}",
                        "",
                    ]
                )
            (report_dir / f"{cat}.md").write_text("\n".join(lines), encoding="utf-8")

        status_summary = (
            ", ".join(f"{status_totals[s]} {s}" for s in VALID_STATUSES if status_totals[s] > 0)
            or "0 OPEN"
        )
        summary_lines = [
            f"# {domain.title()} Audit Summary",
            f"- **Generated:** {now}",
            f"- **Scope:** {scope}",
            f"- **Total Findings:** {len(findings)} ({status_summary})",
            "",
            "## By Severity",
            "| Severity | OPEN | FIXED | APPROVED | FALSE_POSITIVE | DEFERRED | DEPRECATED |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"):
            if sev in sev_counts:
                row = " | ".join(str(sev_counts[sev][s]) for s in VALID_STATUSES)
                summary_lines.append(f"| {sev} | {row} |")

        summary_lines.extend(
            [
                "",
                "## By Category",
                "| Category | File | OPEN | Total |",
                "| :--- | :--- | :---: | :---: |",
            ]
        )
        first_cat_file = ""
        for cat in sorted(by_cat):
            items = by_cat[cat]
            open_c = sum(1 for _, st, _ in items if st == "OPEN")
            rel_file = f".vibetrace/reports/{domain}/{cat}.md"
            if not first_cat_file and open_c > 0:
                first_cat_file = rel_file
            summary_lines.append(f"| {cat} | `{rel_file}` | {open_c} | {len(items)} |")

        if not first_cat_file and by_cat:
            first_cat_file = f".vibetrace/reports/{domain}/{sorted(by_cat)[0]}.md"

        target_hint = first_cat_file or f".vibetrace/reports/{domain}/"
        summary_lines.extend(
            [
                "",
                "## Next Steps (AI Agent)",
                f"1. Open `{target_hint}` to inspect `[OPEN]` findings.",
                "2. Address `CRITICAL` and `HIGH` `[OPEN]` findings first.",
                (
                    "3. Update each resolved item's tag to `[FIXED]`, `[APPROVED]`, "
                    f"`[FALSE_POSITIVE]`, or `[DEFERRED]` (with a short `Note:`), "
                    f"or run `{approve_hint}`."
                ),
                f"4. Re-run `vibetrace audit {domain} --status` to verify progress.",
                "",
            ]
        )
        summary_text = "\n".join(summary_lines)
        (report_dir / "summary.md").write_text(summary_text, encoding="utf-8")
        return summary_text
