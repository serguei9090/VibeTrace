"""Markdown and SARIF 2.1.0 formatting utilities for VibeTrace Quality Audit findings."""

from __future__ import annotations

import json
from typing import Any

from vibetrace.quality.models import QualityAuditReport
from vibetrace.quality.rules import QUALITY_RULES_CATALOG

_SARIF_LEVEL = {
    "HIGH": "error",
    "MEDIUM": "warning",
    "LOW": "note",
    "INFO": "note",
}


def _sev_str(sev: Any) -> str:
    return str(getattr(sev, "value", sev))


def format_quality_audit_md(report: QualityAuditReport) -> str:
    """Format quality audit report as clean Markdown with SonarQube-style guidance."""
    sym_desc = f"`{report.max_complexity_symbol}`" if report.max_complexity_symbol else "N/A"
    lines = [
        "## [QUALITY] VibeTrace Code Quality & Maintainability Audit Report",
        "",
        (
            f"- **Total Findings:** {report.total_findings} | "
            f"**High:** {report.high_count} | "
            f"**Medium:** {report.medium_count} | "
            f"**Low:** {report.low_count}"
        ),
        (
            f"- **Approved Exceptions:** {report.approved_count} | "
            f"**Modified:** {report.modified_count} | "
            f"**Unapproved:** {report.unapproved_count}"
        ),
        (
            f"- **Avg Complexity:** {report.average_complexity:.1f} | "
            f"**Max Complexity:** {report.max_complexity} ({sym_desc})"
        ),
        "",
    ]
    if not report.findings:
        lines.append("✨ No quality or complexity issues detected.")
        return "\n".join(lines)

    for idx, f in enumerate(report.findings, 1):
        sev_icon = f"[{_sev_str(f.severity)}]"
        st_badge = f"[{f.status}]"

        lines.extend(
            [
                f"### {idx}. {sev_icon} {f.rule_id} ({f.rspec_id}): {f.title}",
                f"- **Location:** `{f.file_path}:L{f.start_line}-L{f.end_line}`",
                f"- **Status:** {st_badge} (ID: `{f.id}`)",
                f"- **Issue:** {f.message}",
                "",
            ]
        )

        if f.non_compliant_snippet:
            lines.extend(
                [
                    "**Non-compliant code:**",
                    "```python",
                    f.non_compliant_snippet,
                    "```",
                    "",
                ]
            )

        if f.compliant_snippet:
            lines.extend(
                [
                    "**Compliant solution:**",
                    "```python",
                    f.compliant_snippet,
                    "```",
                    "",
                ]
            )

        rule_spec = QUALITY_RULES_CATALOG.get(f.rule_id.split(":")[-1])
        if rule_spec:
            lines.extend(
                [
                    f"> **Why this is an issue:** {rule_spec.explanation}",
                    "",
                ]
            )

    return "\n".join(lines)


def format_quality_audit_sarif(report: QualityAuditReport) -> str:
    """Format quality findings into standard OASIS SARIF 2.1.0 JSON format."""
    rules_map: dict[str, dict[str, Any]] = {}
    sarif_results: list[dict[str, Any]] = []

    for f in report.findings:
        short_rule = f.rule_id.split(":")[-1]
        spec = QUALITY_RULES_CATALOG.get(short_rule)

        if f.rule_id not in rules_map:
            rules_map[f.rule_id] = {
                "id": f.rule_id,
                "name": f.title,
                "shortDescription": {"text": f.title},
                "fullDescription": {"text": spec.explanation if spec else f.message},
                "defaultConfiguration": {
                    "level": _SARIF_LEVEL.get(_sev_str(f.severity), "warning")
                },
                "helpUri": f"https://rules.sonarsource.com/python/{f.rspec_id}/",
            }

        sarif_results.append(
            {
                "ruleId": f.rule_id,
                "level": _SARIF_LEVEL.get(_sev_str(f.severity), "warning"),
                "message": {"text": f.message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": f.file_path},
                            "region": {
                                "startLine": f.start_line,
                                "endLine": f.end_line,
                            },
                        }
                    }
                ],
                "properties": {
                    "findingId": f.id,
                    "status": f.status,
                    "codeHash": f.code_hash,
                    "metricValue": f.metric_value,
                    "metricThreshold": f.metric_threshold,
                },
            }
        )

    sarif_payload = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "VibeTrace-Quality",
                        "informationUri": "https://github.com/ascc/vibetrace",
                        "rules": list(rules_map.values()),
                    }
                },
                "results": sarif_results,
            }
        ],
    }
    return json.dumps(sarif_payload, indent=2)
