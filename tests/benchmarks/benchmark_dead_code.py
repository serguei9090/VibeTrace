"""Multi-language dead-code benchmark against popular real-world repositories.

Clones repositories into `.benchmark_repos/` (git-ignored) if not already present,
runs both AST-only (`--no-lsp`) and Hybrid LSP (`init` + `index` + `dead-code`)
passes, and writes `.benchmark_repos/dead_code_benchmark_report.md`.

Usage:
    uv run tests/benchmarks/benchmark_dead_code.py
    uv run tests/benchmarks/benchmark_dead_code.py --repos python,go
    uv run tests/benchmarks/benchmark_dead_code.py --refresh --exclude-exported
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPOS: list[dict[str, str]] = [
    {"lang": "python", "name": "httpx", "url": "https://github.com/encode/httpx.git"},
    {"lang": "typescript", "name": "zod", "url": "https://github.com/colinhacks/zod.git"},
    {"lang": "go", "name": "cobra", "url": "https://github.com/spf13/cobra.git"},
    {"lang": "rust", "name": "ripgrep", "url": "https://github.com/BurntSushi/ripgrep.git"},
    {"lang": "java", "name": "gson", "url": "https://github.com/google/gson.git"},
]

ROOT = Path(__file__).resolve().parents[2]
REPOS_DIR = ROOT / ".benchmark_repos"
REPORT_PATH = REPOS_DIR / "dead_code_benchmark_report.md"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def vt_cmd(*args: str) -> list[str]:
    return [
        sys.executable,
        "-c",
        "import sys; from vibetrace.cli import main; sys.exit(main())",
        *args,
    ]


def run_cmd(cmd: list[str], cwd: Path) -> tuple[int, str, str, float]:
    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd, cwd=cwd, env=ENV, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return proc.returncode, proc.stdout, proc.stderr, time.perf_counter() - t0


def ensure_repo(url: str, dest: Path, refresh: bool) -> None:
    if refresh and dest.exists():
        shutil.rmtree(dest)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        rc, _, err, _ = run_cmd(["git", "clone", "--depth", "1", url, str(dest)], ROOT)
        if rc != 0:
            raise RuntimeError(f"git clone failed for {url}: {err.strip()}")


def parse_index_stats(text: str) -> str:
    m = re.search(r"(\d+)\s+files?,\s*(\d+)\s+nodes?,\s*(\d+)\s+edges?", text, re.IGNORECASE)
    if m:
        return f"{m.group(1)} / {m.group(2)} / {m.group(3)}"
    return "-"


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark vibetrace dead-code across languages.")
    parser.add_argument("--repos", help="Comma-separated languages or repo names to run")
    parser.add_argument("--refresh", action="store_true", help="Re-clone repositories")
    parser.add_argument(
        "--exclude-exported", action="store_true", help="Pass --exclude-exported to dead-code"
    )
    args = parser.parse_args()

    selected = REPOS
    if args.repos:
        wanted = {x.strip().lower() for x in args.repos.split(",") if x.strip()}
        selected = [r for r in REPOS if r["lang"] in wanted or r["name"] in wanted]

    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    summary_rows: list[str] = []
    sections: list[str] = []

    for entry in selected:
        lang, name, url = entry["lang"], entry["name"], entry["url"]
        dest = REPOS_DIR / name
        print(f"[{lang}] {name}: cloning/checking...", flush=True)
        ensure_repo(url, dest, args.refresh)

        # Pass 1: Pure AST (--no-lsp)
        print(f"[{lang}] {name}: [AST-only] indexing...", flush=True)
        _, _, _, t_idx_ast = run_cmd(vt_cmd("index", ".", "--force", "--no-lsp"), dest)
        print(f"[{lang}] {name}: [AST-only] running dead-code...", flush=True)
        rc_dc_ast, out_dc_ast, _, t_dc_ast = run_cmd(
            vt_cmd("dead-code", "--json", "--no-lsp"), dest
        )
        ast_count = -1
        if rc_dc_ast == 0:
            try:
                ast_count = json.loads(out_dc_ast).get("total", -1)
            except json.JSONDecodeError:
                pass

        # Pass 2: Init (install LSP) + Hybrid LSP
        print(f"[{lang}] {name}: [LSP] running init...", flush=True)
        run_cmd(vt_cmd("init", "--yes"), dest)

        print(f"[{lang}] {name}: [LSP] indexing...", flush=True)
        rc_idx, out_idx, err_idx, t_idx = run_cmd(vt_cmd("index", ".", "--force"), dest)
        stats = parse_index_stats(out_idx + "\n" + err_idx) if rc_idx == 0 else f"ERR({rc_idx})"

        print(f"[{lang}] {name}: [LSP] running dead-code...", flush=True)
        dc_args = ["dead-code", "--json"]
        if args.exclude_exported:
            dc_args.append("--exclude-exported")
        rc_dc, out_dc, err_dc, t_dc = run_cmd(vt_cmd(*dc_args), dest)

        items: list[dict[str, object]] = []
        total_count = 0
        if rc_dc == 0:
            try:
                data = json.loads(out_dc)
                items = data.get("symbols", [])
                total_count = data.get("total", len(items))
            except json.JSONDecodeError:
                total_count = -1
        else:
            total_count = -1

        summary_rows.append(
            f"| {lang} | {name} | {stats} | {ast_count} ({t_idx_ast + t_dc_ast:.1f}s) "
            f"| {total_count} ({t_idx + t_dc:.1f}s) |"
        )

        lines = [f"## {lang.title()} — `{name}` ({total_count} flagged)\n"]
        if rc_dc != 0:
            lines.append(f"Error running dead-code (exit {rc_dc}):\n```\n{err_dc[:500]}\n```\n")
        elif not items:
            lines.append("No unreferenced symbols flagged.\n")
        else:
            for it in items[:25]:
                kind = it.get("kind", "?")
                qname = it.get("qualified_name", "?")
                fpath = it.get("file_path", "?")
                line = it.get("start_line", "?")
                lines.append(f"- `[{kind}]` `{qname}` ({fpath}:{line})")
            lines.append("")
        sections.append("\n".join(lines))

    report = "\n".join(
        [
            "# Multi-Language Dead-Code Benchmark Report",
            "",
            (
                "| Language | Repo | Files / Nodes / Edges "
                "| AST-Only Dead-Code | Hybrid LSP Dead-Code |"
            ),
            "| :--- | :--- | :--- | ---: | ---: |",
            *summary_rows,
            "",
            *sections,
        ]
    )
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\nReport written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
