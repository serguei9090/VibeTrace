"""Shared utilities for the multi-language CLI end-to-end benchmark suite."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    summary: str = ""
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


def _cmd_key(args: list[str]) -> str:
    if len(args) >= 2 and args[0] in ("audit", "docs"):
        if args[0] == "docs" and len(args) >= 3 and args[1] == "catalog":
            return f"docs catalog {args[2]}"
        return f"{args[0]} {args[1]}"
    return args[0] if args else ""


def _summarize_init(stdout: str) -> tuple[str, bool]:
    parts: list[str] = []
    if m_docs := re.search(r"\[(\d+) chunks indexed", stdout):
        parts.append(f"docs={m_docs.group(1)} chunks")
    if m_idx := re.search(r"Initial Index\*\*:\s*(\d+) symbols,\s*(\d+) relations", stdout):
        parts.append(f"symbols={m_idx.group(1)}, edges={m_idx.group(2)}")
    if not parts:
        parts.append("workspace initialized")
    ok = "initialized" in stdout.lower()
    return ", ".join(parts), ok


def _summarize_json_dict(cmd: str, data: dict[str, Any]) -> tuple[str, bool]:
    if cmd == "index":
        tel = data.get("telemetry", {})
        edge_res = tel.get("edge_resolution", {})
        resolved = int(edge_res.get("resolved_total", 0))
        total_e = int(edge_res.get("total_internal_call_edges", 0))
        pct = float(edge_res.get("resolution_percentage", 0.0))
        emb = int(data.get("embedded_count", 0))
        files = int(tel.get("files_processed", 0))
        doc_c = int(data.get("docs", {}).get("chunks", 0))
        return (
            f"files={files}, emb={emb}, docs={doc_c}, edges={resolved}/{total_e} ({pct:.1f}%)",
            data.get("status") == "ok",
        )
    if cmd == "summary":
        total = int(data.get("total", 0))
        n_rows = len(data.get("nodes", []))
        return f"{total} symbols ({n_rows} listed)", total > 0 and n_rows > 0
    if cmd == "trace":
        found = bool(data.get("found"))
        hops = len(data.get("hops", []))
        paths = len(data.get("paths", []))
        return f"found={found}, {hops} hops, {paths} paths", found and hops > 0
    if cmd == "call-tree":
        found = bool(data.get("found"))
        total_nodes = int(data.get("total_nodes", 0))
        return f"{total_nodes} nodes (depth={data.get('max_depth')})", found and total_nodes > 1
    if cmd == "call-stack":
        found = bool(data.get("found"))
        lines_cnt = len(data.get("call_stack", []))
        return f"{lines_cnt} lines ({data.get('direction')})", found and lines_cnt > 1
    if cmd == "impact":
        imp = int(data.get("total_impact", 0))
        lvls = len(data.get("levels", {}))
        return f"{imp} impacted ({lvls} levels)", imp > 0
    if cmd == "slice":
        found = bool(data.get("found"))
        src = data.get("source_code") or ""
        callees = len(data.get("callees", []))
        return f"{len(src.splitlines())} lines, {callees} callees", found and bool(src)
    if cmd == "references":
        found = bool(data.get("found"))
        refs = int(data.get("total_references", 0))
        targets = int(data.get("target_count", 0))
        return f"{refs} refs ({targets} targets)", found and refs > 0
    if cmd == "dependencies":
        c = data.get("counts", {})
        imp = int(c.get("total_imports", 0))
        deps = int(c.get("dependents", 0))
        return f"{imp} imports, {deps} dependents", imp > 0 or deps > 0
    if cmd == "dead-code":
        total = int(data.get("total", 0))
        return f"{total} unreferenced candidates", isinstance(data.get("symbols"), list)
    if cmd == "diagnose":
        found = bool(data.get("found"))
        c = len(data.get("callers", []))
        e = len(data.get("callee_contracts", []))
        t = len(data.get("exercising_tests", []))
        return f"{c} callers, {e} callees, {t} tests", found
    if cmd == "audit splits":
        splits = int(data.get("total_splits", 0))
        oversized = len(data.get("oversized_files", []))
        lim = f"{data.get('max_lines', 0)}+{data.get('margin', 0)}"
        return f"{splits} splits, {oversized} oversized ({lim})", (splits > 0 or oversized > 0)
    if cmd == "guard":
        found = bool(data.get("found"))
        callers = int(data.get("total_callers", 0))
        risk = data.get("risk_level", "unknown")
        return f"{callers} callers (risk={risk})", found and callers > 0
    if cmd == "bundle":
        tok = int(data.get("estimated_tokens", 0))
        docs_n = len(data.get("doc_context", []))
        tests_n = len(data.get("test_context", []))
        has_prompt = bool(data.get("formatted_prompt"))
        return f"~{tok} tok, {docs_n} docs, {tests_n} tests", tok > 0 and has_prompt
    if cmd == "docs catalog add":
        return (
            f"added {data.get('catalog')} -> {data.get('path')}",
            data.get("status") == "added",
        )
    if cmd == "docs index":
        files_c = int(data.get("total_files", 0))
        chunks_c = int(data.get("total_chunks", 0))
        return f"files={files_c}, chunks={chunks_c}", data.get("status") == "ok" and chunks_c > 0
    if cmd == "docs sync-links":
        links = int(data.get("links_created", 0))
        return f"{links} links synced", data.get("status") == "synced" and links > 0
    return f"dict({len(data)} keys)", bool(data)


def _summarize_json_list(cmd: str, data: list[Any]) -> tuple[str, bool]:
    if not data:
        return "0 items", False
    first = data[0] if isinstance(data[0], dict) else {}
    if cmd == "search":
        top = first.get("qualified_name") or first.get("name") or "?"
        srcs = ",".join(first.get("match_sources", []))
        suffix = f", src={srcs}" if srcs else ""
        return f"{len(data)} hits (top: {top}{suffix})", True
    if cmd == "docs catalog list":
        cats = ", ".join(
            f"{c.get('name')}({c.get('doc_count', 0)}/{c.get('chunk_count', 0)})"
            for c in data[:2]
            if isinstance(c, dict)
        )
        return cats or f"{len(data)} catalogs", True
    if cmd == "docs search":
        top = first.get("file_path") or "?"
        srcs = ",".join(first.get("match_sources", []))
        return f"{len(data)} hits (top: {top}, src={srcs})", True
    return f"{len(data)} items", True


def summarize_step_output(args: list[str], stdout: str) -> tuple[str, bool]:
    """Parse CLI command stdout into a short summary and non-empty check."""
    cmd = _cmd_key(args)
    if cmd == "init":
        return _summarize_init(stdout)
    try:
        data = json.loads(stdout)
    except Exception:
        first_line = stdout.strip().splitlines()[0][:60] if stdout.strip() else "empty"
        return first_line, ("--json" not in args) and bool(stdout.strip())
    if isinstance(data, dict):
        return _summarize_json_dict(cmd, data)
    if isinstance(data, list):
        return _summarize_json_list(cmd, data)
    return str(data)[:60], bool(data)


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
        summary = ""
        detail = "" if passed else (proc.stderr.strip() or proc.stdout.strip())[:300]
        if passed:
            summary, has_data = summarize_step_output(args, proc.stdout)
            if not has_data:
                passed = False
                detail = f"Command returned empty/zero result ({summary})"
        if passed and expect_substr and expect_substr not in proc.stdout:
            passed = False
            detail = f"Expected substring '{expect_substr}' not found in stdout"
        status = "PASS" if passed else "FAIL"
        summary_tag = f" -> {summary}" if summary else ""
        print(
            f"[{time.strftime('%H:%M:%S')}] [{stack}] {status:4s}  {name} "
            f"(exit={proc.returncode}, {dt:.2f}s){summary_tag}",
            flush=True,
        )
        return StepResult(stack, name, passed, proc.returncode, dt, summary, detail)
    except subprocess.TimeoutExpired:
        dt = time.perf_counter() - t0
        print(
            f"[{time.strftime('%H:%M:%S')}] [{stack}] FAIL  {name} (TIMEOUT >{timeout}s)",
            flush=True,
        )
        return StepResult(stack, name, False, -1, dt, "", f"Timed out after {timeout}s")


@dataclass(frozen=True, slots=True)
class StackSpec:
    """Configuration for a language stack E2E CLI benchmark suite."""

    stack: str
    repo_dir_name: str
    repo_url: str
    summary_file: str
    impact_file: str
    deps_file: str
    search_name: str
    search_sig: str
    semantic_query: str
    semantic_expect: str
    primary_symbol: str
    callee_symbol: str
    doc_catalog_path: str | None
    doc_search_query: str
    doc_search_expect: str


def run_stack_suite(spec: StackSpec) -> list[StepResult]:
    """Run all core VibeTrace CLI commands against a repository matching `spec`."""
    repo = REPOS_DIR / spec.repo_dir_name
    ensure_repo(spec.repo_url, repo)
    db_exists = (repo / ".vibetrace" / "vibetrace.db").exists()

    setup_steps: list[tuple[str, list[str], int]] = [
        ("init (auto)", ["init"], 600),
    ]
    if not db_exists:
        setup_steps.append(("index . --force", ["index", ".", "--force", "--json"], 600))
    setup_steps.append(("index . (incremental)", ["index", ".", "--json"], 600))
    setup_steps.append(
        ("index . --embed (Rust vectors)", ["index", ".", "--embed", "--no-lsp", "--json"], 120)
    )
    if spec.doc_catalog_path:
        setup_steps.append(
            (
                f"docs catalog add ({spec.doc_catalog_path})",
                ["docs", "catalog", "add", "core", spec.doc_catalog_path, "--json"],
                60,
            )
        )
        setup_steps.append(
            (
                "docs index (--force)",
                ["docs", "index", "core", "--force", "--json"],
                180,
            )
        )

    results: list[StepResult] = []
    for name, args, timeout in setup_steps:
        res = run_step(spec.stack, name, args, repo, timeout=timeout)
        results.append(res)
        if not res.passed:
            print(
                f"[FATAL] Setup step '{name}' failed (exit={res.exit_code}): {res.detail}",
                flush=True,
            )
            return results

    query_steps: list[tuple[str, list[str], int, str | None]] = [
        ("summary", ["summary", spec.summary_file, "--json"], 60, None),
        ("search (name)", ["search", spec.search_name, "--json"], 60, None),
        ("search (--signature)", ["search", spec.search_sig, "--signature", "--json"], 60, None),
        (
            "search (semantic vector)",
            ["search", spec.semantic_query, "--json"],
            60,
            spec.semantic_expect,
        ),
        ("trace", ["trace", spec.primary_symbol, spec.callee_symbol, "--json"], 60, None),
        ("call-tree", ["call-tree", spec.primary_symbol, "--json"], 60, None),
        ("call-stack", ["call-stack", spec.primary_symbol, "--json"], 60, None),
        ("impact", ["impact", spec.impact_file, "--json"], 60, None),
        ("slice", ["slice", spec.primary_symbol, "--json"], 60, None),
        ("references", ["references", spec.primary_symbol, "--json"], 60, None),
        ("dependencies", ["dependencies", spec.deps_file, "--json"], 60, None),
        ("dead-code (--no-lsp)", ["dead-code", "--no-lsp", "--json"], 60, None),
        (
            "dead-code (--exclude-exported)",
            ["dead-code", "--exclude-exported", "--json"],
            180,
            None,
        ),
        ("diagnose", ["diagnose", spec.primary_symbol, "--json"], 60, None),
        ("audit splits", ["audit", "splits", "--json"], 60, None),
        (
            "audit splits (--max-lines 600 --margin 100)",
            ["audit", "splits", "--max-lines", "600", "--margin", "100", "--json"],
            60,
            None,
        ),
        ("guard", ["guard", spec.primary_symbol, "--json"], 60, None),
        ("bundle", ["bundle", spec.primary_symbol, "--json"], 60, None),
        ("docs catalog list", ["docs", "catalog", "list", "--json"], 60, None),
        (
            "docs search (hybrid vector)",
            ["docs", "search", spec.doc_search_query, "--json"],
            60,
            spec.doc_search_expect,
        ),
        ("docs sync-links", ["docs", "sync-links", "--json"], 60, None),
    ]

    for name, args, timeout, expect_substr in query_steps:
        results.append(
            run_step(spec.stack, name, args, repo, timeout=timeout, expect_substr=expect_substr)
        )
    return results
