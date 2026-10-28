"""CLI commands for autonomous AI Vibecoding: Caller Guard and Vibe Bundle."""

from __future__ import annotations

import argparse
import json
import sys

from vibetrace.cli_commands import get_context_for_path, notify_ui_focus, safe_str
from vibetrace.services.caller_guard import (
    CallerGuardService,
    format_caller_guard_md,
    format_staged_guard_md,
)
from vibetrace.services.vibe_bundle import VibeBundleService


def cmd_guard(args: argparse.Namespace) -> int:
    """Pre-flight refactoring caller guard to verify blast radius before editing code."""
    auto_sync = not getattr(args, "no_sync", False)
    root_override = getattr(args, "root", None)
    file_hint = getattr(args, "file", None) or getattr(args, "path", ".")

    db, root = get_context_for_path(file_hint, auto_sync=auto_sync, root_override=root_override)

    # Staged changes verification
    if getattr(args, "staged", False):
        reports = CallerGuardService.get_staged_caller_guard(db, root)
        if getattr(args, "json", False):
            print(json.dumps([r.model_dump() for r in reports], indent=2))
        else:
            print(safe_str(format_staged_guard_md(reports)))

        if getattr(args, "strict", False) and any(r.total_callers > 0 for r in reports):
            return 1
        return 0

    symbol = getattr(args, "symbol", "")
    if not symbol:
        print("Error: 'symbol' is required unless using --staged", file=sys.stderr)
        return 1

    file_path = getattr(args, "file", None)
    report = CallerGuardService.get_caller_guard(db, root, symbol_name=symbol, file_path=file_path)

    if report.found and report.file_path:
        # Highlight target in UI if running
        nodes = db.find_nodes_by_name(report.symbol, file_path=report.file_path)
        if nodes:
            notify_ui_focus("guard", [nodes[0]["id"]], summary=f"CLI guard {symbol}")

    if getattr(args, "json", False):
        print(report.model_dump_json(indent=2))
    else:
        print(safe_str(format_caller_guard_md(report)))

    return 0 if report.found else 1


def cmd_bundle(args: argparse.Namespace) -> int:
    """One-shot task context packing docs, code slice, caller guard, and test fixtures."""
    auto_sync = not getattr(args, "no_sync", False)
    root_override = getattr(args, "root", None)
    path_hint = getattr(args, "path", ".")

    db, root = get_context_for_path(path_hint, auto_sync=auto_sync, root_override=root_override)

    intent = getattr(args, "intent", "")
    target_symbol = getattr(args, "symbol", None)
    budget = getattr(args, "budget", 1500)

    bundle = VibeBundleService.assemble_bundle(
        db=db,
        root=root,
        intent=intent,
        target_symbol=target_symbol,
        token_budget=budget,
    )

    if bundle.target_symbol and bundle.target_file:
        nodes = db.find_nodes_by_name(bundle.target_symbol, file_path=bundle.target_file)
        if nodes:
            notify_ui_focus("bundle", [nodes[0]["id"]], summary=f"CLI bundle {intent}")

    if getattr(args, "json", False):
        print(bundle.model_dump_json(indent=2))
    else:
        print(safe_str(bundle.formatted_prompt))

    return 0
