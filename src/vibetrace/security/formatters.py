"""Markdown and SARIF 2.1.0 formatting utilities for VibeTrace Security Audit findings."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
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


def _build_security_summary_matrix(findings: list[Any]) -> list[str]:
    """Builds a high-density Markdown summary matrix of security findings."""
    groups: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"severity": "", "unapproved": 0, "approved": 0, "has_taint": False, "files": set()}
    )
    for f in findings:
        sev = _sev_str(f.severity)
        cwe = f.cwe_id or f.rule_id
        g = groups[(sev, cwe)]
        g["severity"] = sev
        g["cwe_id"] = cwe
        g["title"] = f.title
        if f.status == "APPROVED":
            g["approved"] += 1
        else:
            g["unapproved"] += 1
        if f.metadata and f.metadata.get("taint_path"):
            g["has_taint"] = True
        g["files"].add(Path(f.file_path).name)

    sev_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    sorted_groups = sorted(
        groups.values(),
        key=lambda g: (
            sev_rank.get(g["severity"], 9),
            -(g["unapproved"] + g["approved"]),
        ),
    )

    lines = [
        "### [SUMMARY] Executive Security Matrix",
        "",
        "| Severity | CWE / Rule | Unapproved | Approved | Taint Path | Impacted Files |",
        "|:---|:---|:---:|:---:|:---:|:---|",
    ]
    for g in sorted_groups:
        file_samples = sorted(g["files"])[:3]
        file_str = ", ".join(f"`{f}`" for f in file_samples)
        if len(g["files"]) > 3:
            file_str += f" *(+{len(g['files']) - 3} more)*"
        taint_str = "⚠️ **Reachable**" if g["has_taint"] else "-"
        lines.append(
            f"| `{g['severity']}` | `{g['cwe_id']}` | "
            f"**{g['unapproved']}** | {g['approved']} | {taint_str} | {file_str} |"
        )
    lines.append("")
    return lines


def format_security_audit_md(report: Any, summary_only: bool = False) -> str:
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
        lines.append("✨ No security vulnerabilities detected.")
        return "\n".join(lines)

    # 1. Executive Summary Table
    lines.extend(_build_security_summary_matrix(report.findings))

    if summary_only:
        lines.extend(
            [
                "> [TIP] **Next Step for AI Agent:**",
                "> To inspect code lines and compliant solutions for an impacted file, run:",
                "> `uv run vibetrace audit security --file <impacted_file> --json`",
                "> Container image OS audit: `uv run vibetrace audit security --image <tag>`",
                "",
            ]
        )
        return "\n".join(lines).strip()

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
