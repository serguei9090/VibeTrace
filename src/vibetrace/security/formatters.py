"""Markdown and JSON formatting utilities for VibeTrace Security Audit findings."""

from __future__ import annotations

from typing import Any


def format_security_audit_md(report: Any) -> str:
    """Format security audit report as clean Markdown with SonarQube-style guidance."""
    lines = [
        "## [SECURITY] VibeTrace Security & Vulnerability Audit Report",
        "",
        (
            f"- **Total Findings:** {report.total_findings} | "
            f"**Critical:** {report.critical_count} | "
            f"**High:** {report.high_count} | "
            f"**Medium:** {report.medium_count} | "
            f"**Low:** {report.low_count}"
        ),
        (
            f"- **Approved Exceptions:** {report.approved_count} | "
            f"**Modified:** {report.modified_count} | "
            f"**Unapproved:** {report.unapproved_count}"
        ),
        "",
    ]
    if not report.findings:
        lines.append("No security vulnerabilities detected.")
        return "\n".join(lines)

    for idx, f in enumerate(report.findings, 1):
        sev_icon = f"[{f.severity}]"
        st_badge = f"[{f.status}]"

        lines.extend(
            [
                f"### {idx}. {sev_icon} {f.cwe_id}: {f.title}",
                f"- **Location:** `{f.file_path}:L{f.start_line}-L{f.end_line}`",
                f"- **Status:** {st_badge} (ID: `{f.id}`)",
                f"- **Issue:** {f.message}",
                "",
            ]
        )

        if f.non_compliant_snippet:
            lines.extend(
                [
                    "#### Non-Compliant Pattern",
                    "```",
                    f.non_compliant_snippet,
                    "```",
                    "",
                ]
            )

        if f.compliant_snippet:
            lines.extend(
                [
                    "#### Compliant Remediation",
                    "```",
                    f.compliant_snippet,
                    "```",
                    "",
                ]
            )

        if f.rationale:
            lines.extend(
                [
                    f"> **Exception Rationale:** {f.rationale} (Reviewed by `{f.reviewed_by}`)",
                    "",
                ]
            )

        lines.append("---")

    return "\n".join(lines).strip()
