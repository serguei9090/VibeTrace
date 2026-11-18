"""Entry point for the multi-language CLI end-to-end benchmark suite.

Usage:
    uv run tests/benchmarks/e2e_suite/runner.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is importable when executed directly
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.benchmarks.e2e_suite.common import REPORT_PATH, REPOS_DIR, StepResult  # noqa: E402
from tests.benchmarks.e2e_suite.stacks.python_fastapi import run_python_fastapi  # noqa: E402

ENABLE_PYTHON = True
ENABLE_TYPESCRIPT = False
ENABLE_GO = False
ENABLE_RUST = False
ENABLE_JAVA = False


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


def main() -> int:
    all_results: list[StepResult] = []
    if ENABLE_PYTHON:
        all_results.extend(run_python_fastapi())
    # Future stacks: ENABLE_TYPESCRIPT, ENABLE_GO, ENABLE_RUST, ENABLE_JAVA
    write_report(all_results)
    return 0 if all(r.passed for r in all_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
