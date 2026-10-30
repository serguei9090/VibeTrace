"""CLI commands for auditing architectural patterns, security bounds, and execution splits."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vibetrace.mcp.formatters import format_split_audit_md
from vibetrace.security.formatters import format_security_audit_md
from vibetrace.security.service import SecurityService
from vibetrace.services.graph_service import get_context_for_path, safe_str
from vibetrace.services.split_audit_service import (
    audit_splits,
    record_split_approval,
)


def cmd_audit_splits(args: argparse.Namespace) -> int:
    """Audits cross-module bifurcations against .vibetrace/splits.toml baseline."""
    auto_sync = not getattr(args, "no_sync", False)
    root_override = getattr(args, "root", None)
    path_hint = getattr(args, "path", ".")

    db, root = get_context_for_path(path_hint, auto_sync=auto_sync, root_override=root_override)
    project_root = Path(root)

    symbol_filter = getattr(args, "symbol", None)
    report = audit_splits(db, project_root, symbol=symbol_filter)

    is_json = getattr(args, "json", False)
    is_ci = getattr(args, "ci", False)
    is_strict = getattr(args, "strict", False)

    if is_json:
        print(report.model_dump_json(indent=2))
        if is_ci and (report.new_unapproved_count > 0 or report.modified_count > 0):
            return 1
        if is_ci and is_strict and any(s.status == "RISK" for s in report.splits):
            return 1
        return 0

    print(safe_str(format_split_audit_md(report)))

    if is_ci:
        failed = False
        if report.new_unapproved_count > 0 or report.modified_count > 0:
            print(
                f"\n❌ CI Check Failed: {report.new_unapproved_count} unapproved, "
                f"{report.modified_count} modified splits.",
                file=sys.stderr,
            )
            failed = True
        if is_strict and any(s.status == "RISK" for s in report.splits):
            print(
                "\n❌ CI Strict Check Failed: Splits flagged as RISK detected in baseline.",
                file=sys.stderr,
            )
            failed = True

        if failed:
            return 1
        print("\n✅ All architectural splits verified against baseline.")
        return 0

    # Interactive TTY triage if interactive session
    if sys.stdin.isatty():
        actionable = [s for s in report.splits if s.status in ("NEW_UNAPPROVED", "MODIFIED_CODE")]
        if not actionable:
            print("\n✨ All architectural splits are reviewed and match baseline.")
            return 0

        print(f"\nFound {len(actionable)} split(s) needing triage. Interactive Review:")
        for idx, s in enumerate(actionable, 1):
            print(f"\n[{idx}/{len(actionable)}] Split: {s.symbol} ({s.file_path}:L{s.start_line})")
            print(f"      Branches: {s.branches_count} targets across external modules.")
            print(f"      Status: {s.status} (Hash: {s.code_hash[:8]})")
            for b in s.branches[:5]:
                print(f"        -> {b.target_name} ({b.target_file})")
            if len(s.branches) > 5:
                print(f"        ... and {len(s.branches) - 5} more")

            try:
                choice = input("      [A]pprove / [R]isk / [S]kip / [Q]uit > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if choice == "a":
                try:
                    rat = input("      Rationale [Architectural review verified]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    rat = ""
                if not rat:
                    rat = "Architectural review verified"
                record_split_approval(
                    project_root=project_root,
                    symbol=s.symbol,
                    qualified_name=s.qualified_name,
                    file_path=s.file_path,
                    code_hash=s.code_hash,
                    branches=[b.target_name for b in s.branches],
                    status="approved",
                    rationale=rat,
                )
                print("      ✅ Recorded as APPROVED.")
            elif choice == "r":
                try:
                    rat = (
                        input("      Risk rationale: ").strip() or "Architectural bifurcation risk"
                    )
                except (EOFError, KeyboardInterrupt):
                    rat = "Architectural bifurcation risk"
                record_split_approval(
                    project_root=project_root,
                    symbol=s.symbol,
                    qualified_name=s.qualified_name,
                    file_path=s.file_path,
                    code_hash=s.code_hash,
                    branches=[b.target_name for b in s.branches],
                    status="risk",
                    rationale=rat,
                )
                print("      🛑 Recorded as RISK.")
            elif choice == "q":
                break

    return 0


def cmd_audit_security(args: argparse.Namespace) -> int:
    """Audits codebase for security vulnerabilities against .vibetrace/security.toml baseline."""
    auto_sync = not getattr(args, "no_sync", False)
    root_override = getattr(args, "root", None)
    path_hint = getattr(args, "path", ".")

    db, root = get_context_for_path(path_hint, auto_sync=auto_sync, root_override=root_override)
    project_root = Path(root)

    file_filter = getattr(args, "file", None)
    skip_deps = getattr(args, "skip_deps", False)
    force_update = getattr(args, "update", False)
    report = SecurityService.audit_project(
        db,
        project_root,
        file_filter=file_filter,
        check_dependencies=not skip_deps,
        force_update_cves=force_update,
        analyze_taint=not getattr(args, "no_taint", False),
        include_tests=getattr(args, "include_tests", False),
    )

    if getattr(args, "approve", False):
        rationale = getattr(args, "rationale", "Security review verified via CLI")
        approved_now = 0
        for f in report.findings:
            if f.status != "APPROVED":
                SecurityService.record_exception(
                    project_root=project_root,
                    finding_id=f.id,
                    rule_id=f.rule_id,
                    file_path=f.file_path,
                    code_hash=f.code_hash,
                    rationale=rationale,
                    severity=str(getattr(f.severity, "value", f.severity)),
                    status="APPROVED",
                    reviewed_by="cli-user",
                    category=str(getattr(f.category, "value", f.category)),
                    symbol_name=f.symbol_name,
                )
                approved_now += 1
        print(
            safe_str(
                f"Approved {approved_now} finding(s) in .vibetrace/security.toml ({rationale})."
            )
        )
        return 0

    is_json = getattr(args, "json", False)
    is_sarif = getattr(args, "sarif", False)
    is_ci = getattr(args, "ci", False)
    is_strict = getattr(args, "strict", False)
    fail_on = getattr(args, "fail_on", "HIGH").upper()

    if is_sarif:
        from vibetrace.security.formatters import format_security_audit_sarif

        print(format_security_audit_sarif(report))
    elif is_json:
        print(report.model_dump_json(indent=2))
    else:
        print(safe_str(format_security_audit_md(report)))

    if getattr(args, "fix", False):
        bundle = SecurityService.build_remediation_bundle(
            db=db, project_root=project_root, file_filter=file_filter
        )
        for item in bundle.get("remediations", []):  # type: ignore[attr-defined]
            if isinstance(item, dict):
                print(safe_str(f"\n--- AI REMEDIATION ({item['finding_id']}) ---"))
                print(safe_str(str(item["ai_fix_prompt"])))

    if is_ci:
        # Determine failure conditions based on severity threshold
        unapproved_critical = [
            f for f in report.findings if f.severity == "CRITICAL" and f.status != "APPROVED"
        ]
        unapproved_high = [
            f for f in report.findings if f.severity == "HIGH" and f.status != "APPROVED"
        ]
        unapproved_medium = [
            f for f in report.findings if f.severity == "MEDIUM" and f.status != "APPROVED"
        ]

        has_failure = False
        if unapproved_critical or (fail_on in ("HIGH", "MEDIUM", "LOW") and unapproved_high):
            has_failure = True
        elif is_strict or fail_on in ("MEDIUM", "LOW"):
            if unapproved_medium:
                has_failure = True

        if report.modified_count > 0:
            has_failure = True

        if has_failure:
            print(
                f"\n❌ CI Security Gate Failed: {report.unapproved_count} unapproved, "
                f"{report.modified_count} modified vulnerabilities detected.",
                file=sys.stderr,
            )
            return 1

        print("\n✅ All security vulnerabilities verified or approved in baseline.")
        return 0

    # Interactive triage prompt if TTY
    if sys.stdin.isatty():
        actionable = [f for f in report.findings if f.status in ("UNAPPROVED", "MODIFIED_CODE")]
        if not actionable:
            return 0

        print(f"\nFound {len(actionable)} security finding(s) needing triage. Interactive Review:")
        for idx, f in enumerate(actionable, 1):
            print(f"\n[{idx}/{len(actionable)}] [{f.severity}] {f.cwe_id}: {f.title}")
            print(f"      Location: {f.file_path}:L{f.start_line}-L{f.end_line}")
            print(f"      Status: {f.status} (Code Hash: {f.code_hash[:8]})")
            print(f"      Issue: {f.message}")

            try:
                choice = input("      [A]pprove exception / [S]kip / [Q]uit > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if choice == "a":
                try:
                    rat = input("      Rationale [Security review exception verified]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    rat = ""
                if not rat:
                    rat = "Security review exception verified"

                SecurityService.record_exception(
                    project_root=project_root,
                    finding_id=f.id,
                    rule_id=f.rule_id,
                    file_path=f.file_path,
                    code_hash=f.code_hash,
                    rationale=rat,
                    severity=f.severity.value,
                    status="APPROVED",
                )
                print("      ✅ Recorded as APPROVED exception in .vibetrace/security.toml.")
            elif choice == "q":
                break

    return 0
