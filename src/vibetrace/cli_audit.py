"""CLI commands for auditing architectural patterns, security bounds, and execution splits."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vibetrace.mcp.formatters import format_split_audit_md
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
