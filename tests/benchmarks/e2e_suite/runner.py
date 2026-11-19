"""Entry point for the multi-language CLI end-to-end benchmark suite.

Usage:
    uv run tests/benchmarks/e2e_suite/runner.py [--stacks primary|secondary|all|python,go]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

# Ensure repo root is importable when executed directly
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.benchmarks.e2e_suite.common import REPORT_PATH, REPOS_DIR, StepResult  # noqa: E402
from tests.benchmarks.e2e_suite.stacks.go_cobra import run_go_cobra  # noqa: E402
from tests.benchmarks.e2e_suite.stacks.java_gson import run_java_gson  # noqa: E402
from tests.benchmarks.e2e_suite.stacks.python_fastapi import run_python_fastapi  # noqa: E402
from tests.benchmarks.e2e_suite.stacks.rust_ripgrep import run_rust_ripgrep  # noqa: E402
from tests.benchmarks.e2e_suite.stacks.typescript_zod import run_typescript_zod  # noqa: E402

ENABLE_PYTHON = True
ENABLE_TYPESCRIPT = True
ENABLE_GO = True
ENABLE_RUST = True
ENABLE_JAVA = True

PRIMARY_STACKS: tuple[str, ...] = ("python", "typescript", "go", "rust", "java")
SECONDARY_STACKS: tuple[str, ...] = ("c", "cpp", "csharp", "ruby", "php")

STACK_RUNNERS: dict[str, Callable[[], list[StepResult]]] = {
    "python": run_python_fastapi,
    "typescript": run_typescript_zod,
    "go": run_go_cobra,
    "rust": run_rust_ripgrep,
    "java": run_java_gson,
}


def write_report(results: list[StepResult]) -> None:
    """Write a structured Markdown summary of all benchmark steps."""
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    lines = [
        "# Multi-Language CLI E2E Benchmark Report",
        "",
        f"**Total:** {len(results)} | **Passed:** {passed} | **Failed:** {failed}",
        "",
        "| Stack | Command | Status | Exit | Time | Result Summary | Detail |",
        "| :--- | :--- | :--- | ---: | ---: | :--- | :--- |",
    ]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        summary = r.summary.replace("\n", " ").replace("|", "\\|")[:90]
        detail = r.detail.replace("\n", " ").replace("|", "\\|")[:120]
        lines.append(
            f"| {r.stack} | `{r.name}` | {status} | "
            f"{r.exit_code} | {r.duration_s:.2f}s | {summary} | {detail} |"
        )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport written to {REPORT_PATH} ({passed}/{len(results)} passed)", flush=True)


def _resolve_stacks(spec: str) -> list[str]:
    """Resolve a --stacks selector ('primary', 'secondary', 'all', or CSV) into stack keys."""
    enabled_primary = {
        "python": ENABLE_PYTHON,
        "typescript": ENABLE_TYPESCRIPT,
        "go": ENABLE_GO,
        "rust": ENABLE_RUST,
        "java": ENABLE_JAVA,
    }
    norm = spec.strip().lower()
    if norm == "primary":
        return [s for s in PRIMARY_STACKS if enabled_primary.get(s, False) and s in STACK_RUNNERS]
    if norm == "secondary":
        return [s for s in SECONDARY_STACKS if s in STACK_RUNNERS]
    if norm == "all":
        return list(STACK_RUNNERS.keys())

    requested = [part.strip().lower() for part in spec.split(",") if part.strip()]
    if not requested:
        raise ValueError("No stacks specified in --stacks.")
    unknown = [name for name in requested if name not in STACK_RUNNERS]
    if unknown:
        available = ", ".join(STACK_RUNNERS.keys())
        raise ValueError(f"Unknown stack(s): {', '.join(unknown)}. Available stacks: {available}")
    return list(dict.fromkeys(requested))


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and run selected E2E benchmark stacks."""
    parser = argparse.ArgumentParser(description="Run VibeTrace multi-language CLI E2E benchmarks.")
    parser.add_argument(
        "--stacks",
        default="primary",
        help="Stacks to run: 'primary' (default), 'secondary', 'all', or comma-separated names",
    )
    args = parser.parse_args(argv)

    try:
        selected = _resolve_stacks(args.stacks)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if not selected:
        print(f"No stacks registered/enabled for selector '{args.stacks}'.", flush=True)
        write_report([])
        return 0

    all_results: list[StepResult] = []
    for stack_name in selected:
        all_results.extend(STACK_RUNNERS[stack_name]())
    write_report(all_results)
    return 0 if all(r.passed for r in all_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
