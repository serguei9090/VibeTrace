"""Shared utilities for the multi-language CLI end-to-end benchmark suite."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REPOS_DIR = ROOT / ".benchmark_repos"
REPORT_PATH = REPOS_DIR / "e2e_cli_report.md"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


@dataclass(slots=True)
class StepResult:
    """Outcome of a single CLI command execution."""

    stack: str
    name: str
    passed: bool
    exit_code: int
    duration_s: float
    detail: str = ""


def vt_cmd(*args: str) -> list[str]:
    """Build a subprocess command invoking VibeTrace CLI from live src/."""
    return [
        sys.executable,
        "-c",
        "import sys; from vibetrace.cli import main; sys.exit(main())",
        *args,
    ]


def ensure_repo(url: str, dest: Path) -> None:
    """Shallow-clone repository into `dest` if it does not already exist."""
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"[{time.strftime('%H:%M:%S')}] Cloning {url} -> {dest}...", flush=True)
    proc = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed for {url}: {proc.stderr.strip()}")


def run_step(
    stack: str,
    name: str,
    args: list[str],
    cwd: Path,
    timeout: int = 90,
    expect_substr: str | None = None,
) -> StepResult:
    """Execute a VibeTrace CLI command with live logging and timeout protection."""
    t0 = time.perf_counter()
    print(f"[{time.strftime('%H:%M:%S')}] [{stack}] START {name}", flush=True)
    try:
        proc = subprocess.run(
            vt_cmd(*args),
            cwd=cwd,
            env=ENV,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        dt = time.perf_counter() - t0
        passed = proc.returncode == 0
        detail = "" if passed else (proc.stderr.strip() or proc.stdout.strip())[:300]
        if passed and expect_substr and expect_substr not in proc.stdout:
            passed = False
            detail = f"Expected substring '{expect_substr}' not found in stdout"
        status = "PASS" if passed else "FAIL"
        print(
            f"[{time.strftime('%H:%M:%S')}] [{stack}] {status:4s}  {name} "
            f"(exit={proc.returncode}, {dt:.2f}s)",
            flush=True,
        )
        return StepResult(stack, name, passed, proc.returncode, dt, detail)
    except subprocess.TimeoutExpired:
        dt = time.perf_counter() - t0
        print(
            f"[{time.strftime('%H:%M:%S')}] [{stack}] FAIL  {name} (TIMEOUT >{timeout}s)",
            flush=True,
        )
        return StepResult(stack, name, False, -1, dt, f"Timed out after {timeout}s")
