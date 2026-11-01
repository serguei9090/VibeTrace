"""Markdown and SARIF 2.1.0 formatting utilities for VibeTrace Security Audit findings."""

from __future__ import annotations

import json
from typing import Any

from vibetrace.security.rules import RULES_CATALOG

_SARIF_LEVEL = {
    "CRITICAL": "error",
    "HIGH": "error",
    "MEDIUM": "warning",
    "LOW": "note",
    "INFO": "note",
}


def _sev_str(sev: Any) -> str:
    return str(getattr(sev, "value", sev))


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
        sev_icon = f"[{_sev_str(f.severity)}]"
        st_badge = f"[{f.status}]"
        owasp_cat = getattr(f, "owasp_category", None)
        if owasp_cat:
            owasp_short = owasp_cat.split("-")[0].strip()
            tax_badge = f"[{f.cwe_id} | OWASP {owasp_short}]"
        else:
            tax_badge = f"[{f.cwe_id}]"

        lines.extend(
            [
                f"### {idx}. {sev_icon} {tax_badge} {f.title}",
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
            tag_prefix = f"[{f.tag}] " if getattr(f, "tag", None) else ""
            lines.extend(
                [
                    (
                        f"> **Exception Rationale:** {tag_prefix}{f.rationale} "
                        f"(Reviewed by `{f.reviewed_by}`)"
                    ),
                    "",
                ]
            )

        lines.append("---")

    return "\n".join(lines).strip()


def format_security_audit_sarif(report: Any) -> str:
    """Formats the security audit report as OASIS SARIF 2.1.0 JSON for GitHub / IDE integration."""
    rules = [
        {
            "id": spec.cwe_id,
            "name": spec.title,
            "shortDescription": {"text": spec.title},
            "fullDescription": {"text": spec.explanation},
            "help": {"text": f"{spec.explanation}\n\nCompliant example:\n{spec.compliant_example}"},
            "defaultConfiguration": {"level": _SARIF_LEVEL.get(_sev_str(spec.severity), "warning")},
        }
        for spec in RULES_CATALOG.values()
    ]
    results = []
    for f in report.findings:
        sev = _sev_str(f.severity)
        result: dict[str, Any] = {
            "ruleId": f.cwe_id,
            "level": _SARIF_LEVEL.get(sev, "warning"),
            "message": {"text": f.message},
            "fingerprints": {"vibetraceFindingId": f.id, "codeHash": f.code_hash},
            "properties": {
                "severity": sev,
                "category": _sev_str(f.category),
                "status": f.status,
                "taintPath": f.metadata.get("taint_path"),
            },
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": f.file_path.replace("\\", "/")},
                        "region": {
                            "startLine": max(1, f.start_line),
                            "endLine": max(1, f.end_line),
                        },
                    }
                }
            ],
        }
        if f.status == "APPROVED":
            result["suppressions"] = [
                {"kind": "external", "justification": f.rationale or "Approved in security.toml"}
            ]
        results.append(result)

    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "VibeTrace Security",
                        "informationUri": "https://github.com/vibetrace/vibetrace",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)
