"""CLI command handler for interactive AST auto-refactoring and method extraction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vibetrace.services.refactor_service import RefactorService


def cmd_refactor(args: argparse.Namespace) -> int:
    """CLI handler for AST split-method extraction and candidate suggestions."""
    root_val = getattr(args, "root", None) or getattr(args, "path", None) or "."
    root = Path(str(root_val)).resolve()

    file_path = args.file
    func_name = args.function
    is_json = getattr(args, "json", False)

    try:
        # Mode 1: Suggestion mode
        if getattr(args, "suggest", False):
            candidates = RefactorService.suggest_split_candidates(
                project_root=root,
                file_path=file_path,
                func_name=func_name,
            )

            if is_json:
                print(json.dumps([c.to_dict() for c in candidates], indent=2))
                return 0

            if not candidates:
                print(f"[vibetrace refactor] No candidate split blocks found for '{func_name}'.")
                return 0

            print(
                f"[vibetrace refactor] Found {len(candidates)} candidate split block(s) "
                f"for '{func_name}' in {file_path}:\n"
            )
            for i, c in enumerate(candidates, 1):
                in_str = ", ".join(c.inputs) if c.inputs else "none"
                out_str = ", ".join(c.outputs) if c.outputs else "none"
                types_str = ", ".join(c.statement_types)
                print(f"  {i}. Lines {c.start_line} - {c.end_line} ({types_str})")
                print(f"     Complexity Reduction : -{c.complexity_reduction} cognitive score")
                print(f"     Inputs required      : {in_str}")
                print(f"     Outputs returned     : {out_str}")
                print()

            top = candidates[0]
            print(
                f"Recommended Command:\n  vibetrace refactor {file_path} {func_name} "
                f"--start-line {top.start_line} --end-line {top.end_line} --apply\n"
            )
            return 0

        # Mode 2: Split-method refactoring
        start_line = getattr(args, "start_line", None)
        end_line = getattr(args, "end_line", None)
        helper_name = getattr(args, "helper_name", None)
        apply_changes = getattr(args, "apply", False)

        plan = RefactorService.build_refactor_plan(
            project_root=root,
            file_path=file_path,
            func_name=func_name,
            start_line=start_line,
            end_line=end_line,
            helper_name=helper_name,
        )

        if is_json:
            print(json.dumps(plan.to_dict(), indent=2))
            return 0 if plan.can_auto_apply else 1

        if not plan.can_auto_apply:
            print(
                f"[vibetrace refactor] ❌ Refactoring rejected: {plan.rejection_reason}",
                file=sys.stderr,
            )
            return 1

        if apply_changes:
            RefactorService.apply_refactor_plan(root, plan)
            print(
                f"[vibetrace refactor] ✅ Successfully extracted "
                f"lines {plan.start_line}-{plan.end_line} "
                f"into '{plan.helper_name}' in {file_path}."
            )
            return 0

        # Dry-run: print diff preview
        print(f"[vibetrace refactor] Dry-run preview for {file_path} ({func_name}):\n")
        if plan.diff:
            print(plan.diff)
        else:
            print("No changes generated.")
        print(
            f"\nExtracted Helper Signature:\n{plan.helper_code}\n"
            f"Callsite Replacement:\n{plan.call_replacement}\n"
            "To apply these changes in-place, rerun with the '--apply' flag."
        )
        return 0

    except Exception as exc:
        if is_json:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
        else:
            print(f"[vibetrace refactor] Error: {exc}", file=sys.stderr)
        return 1
