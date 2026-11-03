"""Markdown and SARIF 2.1.0 formatting utilities for VibeTrace Quality Audit findings."""

from __future__ import annotations

import json
from typing import Any

from vibetrace.quality.models import QualityAuditReport, QualityFinding
from vibetrace.quality.rules import QUALITY_RULES_CATALOG

_SARIF_LEVEL = {
    "HIGH": "error",
    "MEDIUM": "warning",
    "LOW": "note",
    "INFO": "note",
}


def _sev_str(sev: Any) -> str:
    return str(getattr(sev, "value", sev))


def _build_summary_matrix(findings: list[QualityFinding]) -> list[str]:
    """Builds a high-signal markdown matrix table summarizing findings by rule & severity."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for f in findings:
        sev = _sev_str(f.severity)
        key = (f.rule_id, sev)
        if key not in grouped:
            grouped[key] = {
                "rule_id": f.rule_id,
                "rspec_id": f.rspec_id,
                "severity": sev,
                "unapproved": 0,
                "approved": 0,
                "files": set(),
            }
        if f.status == "UNAPPROVED":
            grouped[key]["unapproved"] += 1
        else:
            grouped[key]["approved"] += 1
        fname = f.file_path.split("/")[-1]
        grouped[key]["files"].add(fname)

    sev_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    sorted_groups = sorted(
        grouped.values(),
        key=lambda g: (
            -g["unapproved"],
            sev_rank.get(g["severity"], 9),
            -(g["unapproved"] + g["approved"]),
        ),
    )

    lines = [
        "### [SUMMARY] Executive Findings Matrix",
        "",
        "| Severity | Rule | Unapproved | Approved | Impacted Files |",
        "|:---|:---|:---:|:---:|:---|",
    ]
    for g in sorted_groups:
        file_samples = sorted(g["files"])[:3]
        file_str = ", ".join(f"`{f}`" for f in file_samples)
        if len(g["files"]) > 3:
            file_str += f" *(+{len(g['files']) - 3} more)*"
        lines.append(
            f"| `{g['severity']}` | `{g['rule_id']}` ({g['rspec_id']}) | "
            f"**{g['unapproved']}** | {g['approved']} | {file_str} |"
        )
    lines.append("")
    return lines


def format_quality_audit_md(report: QualityAuditReport, summary_only: bool = False) -> str:
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

    # 1. Executive Summary Table
    lines.extend(_build_summary_matrix(report.findings))

    if summary_only:
        lines.extend(
            [
                "> [TIP] **Next Step for AI Agent:**",
                "> To inspect code lines and compliant solutions for an impacted file, run:",
                "> `uv run vibetrace audit quality --file <impacted_file> --json`",
                "",
            ]
        )
        return "\n".join(lines)

    # 2. Prioritize Actionable Findings: UNAPPROVED HIGH -> MEDIUM -> MODIFIED -> LOW -> APPROVED
    sev_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}

    def _finding_priority(f: QualityFinding) -> tuple[int, int, str, int]:
        is_unapp = f.status == "UNAPPROVED"
        is_mod = f.status == "MODIFIED_CODE"
        if is_unapp and str(getattr(f.severity, "value", f.severity)) == "HIGH":
            cat_rank = 0
        elif is_unapp and str(getattr(f.severity, "value", f.severity)) == "MEDIUM":
            cat_rank = 1
        elif is_mod:
            cat_rank = 2
        elif is_unapp:
            cat_rank = 3
        else:
            cat_rank = 4
        return (cat_rank, sev_rank.get(_sev_str(f.severity), 9), f.file_path, f.start_line)

    sorted_findings = sorted(report.findings, key=_finding_priority)

    # For large project reports (>50 findings), show all high/medium unapproved and cap tail
    verbose_limit = 40 if len(sorted_findings) > 50 else len(sorted_findings)
    displayed = 0
    omitted = 0

    for f in sorted_findings:
        is_high_priority = f.status in ("UNAPPROVED", "MODIFIED_CODE") and _sev_str(f.severity) in (
            "HIGH",
            "MEDIUM",
        )
        if not is_high_priority and displayed >= verbose_limit:
            omitted += 1
            continue

        displayed += 1
        sev_icon = f"[{_sev_str(f.severity)}]"
        st_badge = f"[{f.status}]"
        tax_badge = ""
        cwe_id = getattr(f, "cwe_id", None)
        if cwe_id:
            owasp_cat = getattr(f, "owasp_category", None)
            if owasp_cat:
                owasp_short = owasp_cat.split("-")[0].strip()
                tax_badge = f" [{cwe_id} | OWASP {owasp_short}]"
            else:
                tax_badge = f" [{cwe_id}]"

        lines.extend(
            [
                f"### {displayed}. {sev_icon} {f.rule_id} ({f.rspec_id}){tax_badge}: {f.title}",
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

    if omitted > 0:
        lines.extend(
            [
                f"> [INFO] *... and {omitted} more low-severity/approved findings omitted from "
                "verbose detail list (see Executive Summary table above). "
                "Use `--file <path>` to inspect individual file findings in detail.*",
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
