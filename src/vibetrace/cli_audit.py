"""CLI commands for auditing architectural patterns, security bounds, quality,
and execution splits.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from vibetrace.mcp.formatters import format_split_audit_md
from vibetrace.quality.formatters import format_quality_audit_md
from vibetrace.quality.models import QualityExceptionRecord
from vibetrace.quality.service import QualityService
from vibetrace.security.formatters import format_security_audit_md
from vibetrace.security.service import SecurityService
from vibetrace.services.graph_service import get_context_for_path, safe_str
from vibetrace.services.split_audit_service import (
    audit_splits,
    record_split_approval,
)


def _evaluate_split_ci_gate(report: Any, is_strict: bool) -> int:
    """Evaluates CI exit code gate for architectural split baseline."""
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


def _record_split_cli_approval(s: Any, project_root: Path, status: str) -> None:
    """Prompts for rationale and records split approval in baseline."""
    default_rat = (
        "Architectural review verified"
        if status == "approved"
        else "Architectural bifurcation risk"
    )
    prompt_str = (
        f"      Rationale [{default_rat}]: " if status == "approved" else "      Risk rationale: "
    )
    try:
        rat = input(prompt_str).strip() or default_rat
    except (EOFError, KeyboardInterrupt):
        rat = default_rat

    record_split_approval(
        project_root=project_root,
        symbol=s.symbol,
        qualified_name=s.qualified_name,
        file_path=s.file_path,
        code_hash=s.code_hash,
        branches=[b.target_name for b in s.branches],
        status=status,
        rationale=rat,
    )
    badge = "APPROVED" if status == "approved" else "RISK"
    icon = "✅" if status == "approved" else "🛑"
    print(f"      {icon} Recorded as {badge}.")


def _run_split_interactive_triage(actionable: list[Any], project_root: Path) -> None:
    """Runs interactive terminal triage for split review."""
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
            _record_split_cli_approval(s, project_root, status="approved")
        elif choice == "r":
            _record_split_cli_approval(s, project_root, status="risk")
        elif choice == "q":
            break


def _handle_split_bulk_approve(
    report: Any, project_root: Path, symbol_filter: str | None, status: str, rationale: str
) -> int:
    """Approves all matching unapproved/modified splits into baseline."""
    targets = [
        s
        for s in report.splits
        if (symbol_filter and s.symbol == symbol_filter)
        or (not symbol_filter and s.status in ("NEW_UNAPPROVED", "MODIFIED_CODE"))
    ]
    for s in targets:
        record_split_approval(
            project_root=project_root,
            symbol=s.symbol,
            qualified_name=s.qualified_name,
            file_path=s.file_path,
            code_hash=s.code_hash,
            branches=[b.target_name for b in s.branches],
            status=status,
            rationale=rationale,
        )
        print(f"✅ Recorded '{status}' for {s.symbol} ({s.code_hash[:8]})")
    return 0


def cmd_audit_splits(args: argparse.Namespace) -> int:
    """Audits cross-module bifurcations against .vibetrace/splits.toml baseline."""
    auto_sync = not getattr(args, "no_sync", False)
    root_override = getattr(args, "root", None)
    path_hint = getattr(args, "path", ".")

    db, root = get_context_for_path(path_hint, auto_sync=auto_sync, root_override=root_override)
    project_root = Path(root)

    symbol_filter = getattr(args, "symbol", None)
    report = audit_splits(db, project_root, symbol=symbol_filter)

    if getattr(args, "approve", False):
        status = getattr(args, "status", "approved")
        rationale = getattr(args, "rationale", "Architectural split verified via CLI")
        return _handle_split_bulk_approve(report, project_root, symbol_filter, status, rationale)

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
        return _evaluate_split_ci_gate(report, is_strict)

    # Interactive TTY triage if interactive session
    if sys.stdin.isatty():
        actionable = [s for s in report.splits if s.status in ("NEW_UNAPPROVED", "MODIFIED_CODE")]
        if not actionable:
            print("\n✨ All architectural splits are reviewed and match baseline.")
            return 0
        _run_split_interactive_triage(actionable, project_root)

    return 0


def _evaluate_security_ci_gate(report: Any, fail_on: str, is_strict: bool) -> int:
    """Evaluates security CI failure conditions based on severity threshold."""
    unapproved_critical = [
        f for f in report.findings if f.severity == "CRITICAL" and f.status != "APPROVED"
    ]
    unapproved_high = [
        f for f in report.findings if f.severity == "HIGH" and f.status != "APPROVED"
    ]
    unapproved_medium = [
        f for f in report.findings if f.severity == "MEDIUM" and f.status != "APPROVED"
    ]

    has_crit_or_high = bool(
        unapproved_critical or (fail_on in ("HIGH", "MEDIUM", "LOW") and unapproved_high)
    )
    has_med = bool((is_strict or fail_on in ("MEDIUM", "LOW")) and unapproved_medium)
    has_failure = has_crit_or_high or has_med or (report.modified_count > 0)

    if has_failure:
        print(
            f"\n❌ CI Security Gate Failed: {report.unapproved_count} unapproved, "
            f"{report.modified_count} modified vulnerabilities detected.",
            file=sys.stderr,
        )
        return 1

    print("\n✅ All security vulnerabilities verified or approved in baseline.")
    return 0


def _run_security_interactive_triage(actionable: list[Any], project_root: Path) -> None:
    """Runs interactive terminal triage for security findings."""
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


def _handle_security_bulk_approve(report: Any, project_root: Path, rationale: str, tag: str) -> int:
    """Approves all unapproved security findings in baseline."""
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
                tag=tag,
            )
            approved_now += 1
    print(
        safe_str(f"Approved {approved_now} finding(s) in .vibetrace/security.toml ({rationale}).")
    )
    return 0


def _render_security_output(report: Any, is_sarif: bool, is_json: bool) -> None:
    """Renders security audit findings in SARIF, JSON, or Markdown format."""
    if is_sarif:
        from vibetrace.security.formatters import format_security_audit_sarif

        print(format_security_audit_sarif(report))
    elif is_json:
        print(report.model_dump_json(indent=2))
    else:
        print(safe_str(format_security_audit_md(report)))


def _handle_security_ai_fix(db: Any, project_root: Path, file_filter: str | None) -> None:
    """Builds and prints AI remediation suggestions for security findings."""
    bundle = SecurityService.build_remediation_bundle(
        db=db, project_root=project_root, file_filter=file_filter
    )
    for item in bundle.get("remediations", []):  # type: ignore[attr-defined]
        if isinstance(item, dict):
            print(safe_str(f"\n--- AI REMEDIATION ({item['finding_id']}) ---"))
            print(safe_str(str(item["ai_fix_prompt"])))


def cmd_audit_security(args: argparse.Namespace) -> int:
    """Audits codebase for security vulnerabilities against .vibetrace/security.toml baseline."""
    auto_sync = not getattr(args, "no_sync", False)
    root_override = getattr(args, "root", None)
    path_hint = getattr(args, "path", ".")

    db, root = get_context_for_path(path_hint, auto_sync=auto_sync, root_override=root_override)
    project_root = Path(root)

    force_update = getattr(args, "update", False)
    try:
        from vibetrace.quality.rule_sync import QualityRuleCatalogManager

        QualityRuleCatalogManager.sync_catalog(db=db, force_update=force_update)
    except Exception:
        pass

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
        tag = getattr(args, "tag", "risk-accepted")
        return _handle_security_bulk_approve(report, project_root, rationale, tag)

    is_sarif = getattr(args, "sarif", False)
    is_json = getattr(args, "json", False)
    _render_security_output(report, is_sarif, is_json)

    if getattr(args, "fix", False):
        _handle_security_ai_fix(db, project_root, file_filter)

    is_ci = getattr(args, "ci", False)
    if is_ci:
        is_strict = getattr(args, "strict", False)
        fail_on = getattr(args, "fail_on", "HIGH").upper()
        return _evaluate_security_ci_gate(report, fail_on, is_strict)

    # Interactive triage prompt if TTY
    if sys.stdin.isatty():
        actionable = [f for f in report.findings if f.status in ("UNAPPROVED", "MODIFIED_CODE")]
        if actionable:
            _run_security_interactive_triage(actionable, project_root)

    return 0


def _evaluate_quality_ci_gate(report: Any, fail_on: str, is_strict: bool) -> int:
    """Evaluates CI exit code gate for code quality and cognitive complexity."""
    unapproved_high = [
        f for f in report.findings if f.severity == "HIGH" and f.status != "APPROVED"
    ]
    unapproved_med = [
        f for f in report.findings if f.severity == "MEDIUM" and f.status != "APPROVED"
    ]

    has_high = bool(unapproved_high or (fail_on in ("MEDIUM", "LOW") and unapproved_med))
    has_med = bool(is_strict and unapproved_med)
    has_failure = has_high or has_med or (report.modified_count > 0)

    if has_failure:
        print(
            f"\n❌ CI Quality Gate Failed: {report.unapproved_count} unapproved, "
            f"{report.modified_count} modified quality finding(s).",
            file=sys.stderr,
        )
        return 1

    print("\n✅ All code quality and complexity checks passed or approved in baseline.")
    return 0


def _handle_single_quality_triage(f: Any, project_root: Path) -> bool:
    """Prompts and records triage decision for a single finding. Returns False to quit."""
    try:
        choice = (
            input("      [A]pprove exception / [I]gnore false-positive / [S]kip / [Q]uit > ")
            .strip()
            .lower()
        )
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if choice in ("a", "i"):
        tag = "false-positive" if choice == "i" else "intentional"
        default_rat = (
            "False positive verified via CLI"
            if choice == "i"
            else "Quality baseline exception verified"
        )
        try:
            rat = input(f"      Rationale [{default_rat}]: ").strip() or default_rat
        except (EOFError, KeyboardInterrupt):
            rat = default_rat

        rec = QualityExceptionRecord.from_finding(f, rationale=rat, tag=tag)
        QualityService.record_exception(project_root=project_root, record=rec)
        print(f"      ✅ Recorded as {tag.upper()} in .vibetrace/quality.toml.")
    elif choice == "q":
        return False

    return True


def _run_quality_interactive_triage(actionable: list[Any], project_root: Path) -> None:
    """Runs interactive terminal triage for quality findings."""
    print(f"\nFound {len(actionable)} quality finding(s) needing triage. Interactive Review:")
    for idx, f in enumerate(actionable, 1):
        print(f"\n[{idx}/{len(actionable)}] [{f.severity}] {f.rule_id} ({f.rspec_id}): {f.title}")
        print(f"      Location: {f.file_path}:L{f.start_line}-L{f.end_line}")
        print(f"      Status: {f.status} (Code Hash: {f.code_hash[:8]})")
        print(f"      Issue: {f.message}")

        if not _handle_single_quality_triage(f, project_root):
            break


def cmd_audit_quality(args: argparse.Namespace) -> int:
    """Audits codebase for cognitive complexity and clean code smells against baseline."""
    auto_sync = not getattr(args, "no_sync", False)
    root_override = getattr(args, "root", None)
    path_hint = getattr(args, "path", ".")

    db, root = get_context_for_path(path_hint, auto_sync=auto_sync, root_override=root_override)
    project_root = Path(root)

    force_update = getattr(args, "update", False)
    try:
        from vibetrace.quality.rule_sync import QualityRuleCatalogManager

        QualityRuleCatalogManager.sync_catalog(db=db, force_update=force_update)
    except Exception:
        pass

    file_filter = getattr(args, "file", None)
    threshold = getattr(args, "threshold", 15)
    include_tests = getattr(args, "include_tests", False)

    report = QualityService.audit_project(
        db=db,
        project_root=project_root,
        file_filter=file_filter,
        complexity_threshold=threshold,
        include_tests=include_tests,
    )

    if getattr(args, "approve", False):
        rationale = getattr(args, "rationale", "Quality baseline approved via CLI")
        tag = getattr(args, "tag", "intentional")
        approved_now = 0
        for f in report.findings:
            if f.status != "APPROVED":
                rec = QualityExceptionRecord.from_finding(f, rationale=rationale, tag=tag)
                QualityService.record_exception(project_root=project_root, record=rec)
                approved_now += 1
        print(
            safe_str(
                f"Approved {approved_now} finding(s) in .vibetrace/quality.toml ({rationale})."
            )
        )
        return 0

    is_json = getattr(args, "json", False)
    is_sarif = getattr(args, "sarif", False)
    is_ci = getattr(args, "ci", False)
    is_strict = getattr(args, "strict", False)
    fail_on = getattr(args, "fail_on", "HIGH").upper()

    if is_sarif:
        from vibetrace.quality.formatters import format_quality_audit_sarif

        print(format_quality_audit_sarif(report))
    elif is_json:
        print(report.model_dump_json(indent=2))
    else:
        print(safe_str(format_quality_audit_md(report)))

    if is_ci:
        return _evaluate_quality_ci_gate(report, fail_on, is_strict)

    # Interactive triage prompt if TTY
    if sys.stdin.isatty():
        actionable = [f for f in report.findings if f.status in ("UNAPPROVED", "MODIFIED_CODE")]
        if actionable:
            _run_quality_interactive_triage(actionable, project_root)

    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """Runs unified 1-call review combining Security, Quality, Split Audit, and Caller Guard."""
    from vibetrace.services.review_service import (
        format_unified_review_md,
        run_unified_review,
    )

    auto_sync = not getattr(args, "no_sync", False)
    root_override = getattr(args, "root", None)
    path_hint = getattr(args, "path", ".")

    db, root = get_context_for_path(path_hint, auto_sync=auto_sync, root_override=root_override)
    scope = "full" if getattr(args, "full", False) else "changed"
    check_deps = getattr(args, "deps", False)
    min_cov = getattr(args, "min_coverage", None)
    enforce_cov = bool(getattr(args, "strict", False) or min_cov is not None)

    report = run_unified_review(
        db=db,
        project_root=Path(root),
        scope=scope,
        check_dependencies=check_deps,
        min_coverage=min_cov,
        enforce_coverage=enforce_cov,
    )
    if getattr(args, "json", False):
        print(report.model_dump_json(indent=2))
    else:
        print(safe_str(format_unified_review_md(report)))

    if getattr(args, "ci", False) and not report.passed:
        return 1
    return 0
