"""FastAPI query & incremental auto-sync benchmark for VibeTrace.

Clones `fastapi/fastapi` into `.benchmark_repos/fastapi` (git-ignored) if missing,
indexes it once if needed, and benchmarks `summary`, `call-tree`, `references`,
`dependencies`, `impact`, `diagnose`, and `call-tree` after a 1-file edit.

Usage:
    uv run tests/benchmarks/benchmark_fastapi_queries.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPOS_DIR = ROOT / ".benchmark_repos"
FASTAPI = REPOS_DIR / "fastapi"
FASTAPI_URL = "https://github.com/fastapi/fastapi.git"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def vt_cmd(*args: str) -> list[str]:
    return [
        sys.executable,
        "-c",
        "import sys; from vibetrace.cli import main; sys.exit(main())",
        *args,
    ]


def ensure_fastapi() -> None:
    if not FASTAPI.exists():
        REPOS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[{time.strftime('%H:%M:%S')}] Cloning fastapi into {FASTAPI}...", flush=True)
        subprocess.run(["git", "clone", "--depth", "1", FASTAPI_URL, str(FASTAPI)], check=True)
    db_file = FASTAPI / ".vibetrace" / "vibetrace.db"
    if not db_file.exists():
        print(f"[{time.strftime('%H:%M:%S')}] Initial indexing fastapi...", flush=True)
        subprocess.run(vt_cmd("index", str(FASTAPI), "--force"), env=ENV, check=True)


def run_step(label: str, cmd: list[str], timeout: int = 60) -> None:
    t0 = time.perf_counter()
    print(f"[{time.strftime('%H:%M:%S')}] START {label}", flush=True)
    proc = subprocess.run(
        cmd,
        env=ENV,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    dt = time.perf_counter() - t0
    print(
        f"[{time.strftime('%H:%M:%S')}] DONE  {label}: exit={proc.returncode} ({dt:.2f}s)",
        flush=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed: {proc.stderr[:300]}")


def main() -> None:
    ensure_fastapi()

    queries = [
        (
            "summary (--no-sync)",
            vt_cmd(
                "summary", "fastapi/applications.py", "--root", str(FASTAPI), "--no-sync", "--json"
            ),
        ),
        (
            "call-tree (--no-sync)",
            vt_cmd(
                "call-tree", "FastAPI.add_api_route", "--root", str(FASTAPI), "--no-sync", "--json"
            ),
        ),
        (
            "references (--no-sync)",
            vt_cmd(
                "references", "FastAPI.add_api_route", "--root", str(FASTAPI), "--no-sync", "--json"
            ),
        ),
        (
            "dependencies (--no-sync)",
            vt_cmd(
                "dependencies",
                "fastapi/applications.py",
                "--root",
                str(FASTAPI),
                "--no-sync",
                "--json",
            ),
        ),
        (
            "impact (--no-sync)",
            vt_cmd(
                "impact", "fastapi/applications.py", "--root", str(FASTAPI), "--no-sync", "--json"
            ),
        ),
        (
            "diagnose (--no-sync)",
            vt_cmd(
                "diagnose", "FastAPI.add_api_route", "--root", str(FASTAPI), "--no-sync", "--json"
            ),
        ),
    ]
    for label, cmd in queries:
        run_step(label, cmd)

    target = FASTAPI / "fastapi" / "__init__.py"
    orig = target.read_text(encoding="utf-8")
    try:
        target.write_text(orig + "\n# benchmark incremental edit\n", encoding="utf-8")
        run_step(
            "call-tree (after 1-file edit, auto_sync)",
            vt_cmd("call-tree", "FastAPI.add_api_route", "--root", str(FASTAPI), "--json"),
        )
    finally:
        target.write_text(orig, encoding="utf-8")


if __name__ == "__main__":
    main()
