"""Predictive Test Selection Service: Sub-second test execution for modified symbols.

Analyzes Git modified ranges, maps lines to AST symbols, traverses the upstream
call-graph in SQLite, and resolves the minimal set of impacted tests.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path

from pydantic import BaseModel, Field

from vibetrace.indexer.db import IndexerDB

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class ImpactedTest(BaseModel):
    """Represents a specific test function impacted by modified symbols."""

    test_file: str
    test_function: str
    target_symbol: str
    target_file: str
    hops: int = 1
    runner: str = "pytest"


class PredictiveTestReport(BaseModel):
    """Report detailing modified symbols, impacted tests, and command invocation."""

    target_symbols: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    impacted_tests: list[ImpactedTest] = Field(default_factory=list)
    total_impacted: int = 0
    runner: str = "pytest"
    command: str | None = None
    uncovered_symbols: list[str] = Field(default_factory=list)


def get_git_modified_ranges(
    project_root: Path, staged: bool = False, include_unstaged: bool = True
) -> dict[str, list[tuple[int, int]]]:
    """Parses unified diff output to extract modified line ranges per file."""
    if not shutil.which("git"):
        return {}

    commands: list[list[str]] = []
    if staged:
        commands.append(["git", "-C", str(project_root), "diff", "--cached", "-U0"])
    elif include_unstaged:
        commands.append(["git", "-C", str(project_root), "diff", "-U0"])
        # Also include staged if any
        commands.append(["git", "-C", str(project_root), "diff", "--cached", "-U0"])

    file_ranges: dict[str, list[tuple[int, int]]] = {}

    for cmd in commands:
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=10,
            )
            if res.returncode != 0:
                continue

            current_file: str | None = None
            for line in res.stdout.splitlines():
                if line.startswith("+++ b/"):
                    current_file = line[6:].strip().replace("\\", "/")
                    file_ranges.setdefault(current_file, [])
                elif line.startswith("@@ ") and current_file:
                    if m := _HUNK_RE.match(line):
                        start = int(m.group(1))
                        count = int(m.group(2)) if m.group(2) is not None else 1
                        end = start + max(1, count) - 1
                        file_ranges[current_file].append((start, end))
        except (subprocess.SubprocessError, OSError):
            continue

    return file_ranges


def _detect_runner_for_file(file_path: str) -> str:
    """Infers test runner from file extension."""
    low = file_path.lower()
    if low.endswith((".ts", ".tsx", ".js", ".jsx")):
        return "bun"
    if low.endswith(".go"):
        return "go"
    if low.endswith(".rs"):
        return "cargo"
    return "pytest"


def _is_executable_test(name: str, file_path: str) -> bool:
    """Checks if a symbol is a runnable test function (not just a helper/fixture)."""
    low_name = (name or "").lower()
    norm_file = (file_path or "").replace("\\", "/").lower()

    if norm_file.endswith(".py"):
        return low_name.startswith(("test_", "test"))

    if norm_file.endswith("_test.go"):
        return name.startswith(("Test", "Benchmark", "Example"))

    if any(
        norm_file.endswith(s)
        for s in (
            ".test.ts",
            ".spec.ts",
            ".test.js",
            ".spec.js",
            ".test.tsx",
            ".spec.tsx",
        )
    ):
        return True

    if norm_file.endswith(".rs"):
        return low_name.startswith(("test_", "test"))

    return low_name.startswith("test_")


class PredictiveTestService:
    """Orchestrates AST-driven test caller discovery and runner construction."""

    @classmethod
    def resolve_impacted_tests(
        cls,
        db: IndexerDB,
        project_root: Path,
        staged: bool = False,
        target_symbols: list[str] | None = None,
        max_hops: int = 6,
    ) -> PredictiveTestReport:
        """Finds tests reaching modified symbols via reverse call-graph traversal."""
        modified_nodes: list[dict[str, str | int]] = []
        changed_files_list: list[str] = []

        with db._get_conn() as conn:
            if target_symbols:
                placeholders = ",".join("?" for _ in target_symbols)
                rows = conn.execute(
                    f"SELECT id, name, kind, file_path, start_line, end_line FROM nodes "
                    f"WHERE name IN ({placeholders}) AND kind IN ('function', 'method')",
                    tuple(target_symbols),
                ).fetchall()
                for r in rows:
                    modified_nodes.append(
                        {
                            "id": r[0],
                            "name": r[1],
                            "kind": r[2],
                            "file_path": r[3],
                            "start_line": r[4],
                            "end_line": r[5],
                        }
                    )
            else:
                ranges_map = get_git_modified_ranges(project_root, staged=staged)
                changed_files_list = sorted(ranges_map.keys())

                for rel_path, ranges in ranges_map.items():
                    rows = conn.execute(
                        "SELECT id, name, kind, file_path, start_line, end_line FROM nodes "
                        "WHERE file_path = ? AND kind IN ('function', 'method')",
                        (rel_path,),
                    ).fetchall()
                    for r in rows:
                        s_line, e_line = int(r[4]), int(r[5])
                        is_hit = any(
                            max(s_line, r_start) <= min(e_line, r_end) for r_start, r_end in ranges
                        )
                        if is_hit:
                            modified_nodes.append(
                                {
                                    "id": r[0],
                                    "name": r[1],
                                    "kind": r[2],
                                    "file_path": r[3],
                                    "start_line": s_line,
                                    "end_line": e_line,
                                }
                            )

            if not modified_nodes:
                return PredictiveTestReport(
                    target_symbols=target_symbols or [],
                    changed_files=changed_files_list,
                    impacted_tests=[],
                    total_impacted=0,
                )

            # Pre-load call edges into an in-memory reverse graph (callee -> callers)
            # Edge: source_id calls target_id. Reverse edge: target_id is called by source_id.
            edge_rows = conn.execute(
                "SELECT from_node_id, to_node_id FROM edges "
                "WHERE relation_type IN ('calls', 'call', 'invokes', 'references')"
            ).fetchall()
            reverse_graph: dict[str, list[str]] = {}
            for src, tgt in edge_rows:
                reverse_graph.setdefault(tgt, []).append(src)

            # Pre-load all nodes lookup
            all_node_rows = conn.execute(
                "SELECT id, name, kind, file_path FROM nodes WHERE kind IN ('function', 'method')"
            ).fetchall()
            node_map: dict[str, tuple[str, str, str]] = {
                r[0]: (r[1], r[2], r[3]) for r in all_node_rows
            }

        # For each modified node, traverse upstream up to max_hops
        impacted_map: dict[tuple[str, str], ImpactedTest] = {}
        uncovered: list[str] = []

        for mod_node in modified_nodes:
            node_id = str(mod_node["id"])
            node_name = str(mod_node["name"])
            node_file = str(mod_node["file_path"])

            # If the modified node is already a test, include it directly
            if _is_executable_test(node_name, node_file):
                runner = _detect_runner_for_file(node_file)
                key = (node_file, node_name)
                impacted_map[key] = ImpactedTest(
                    test_file=node_file,
                    test_function=node_name,
                    target_symbol=node_name,
                    target_file=node_file,
                    hops=0,
                    runner=runner,
                )
                continue

            visited: set[str] = {node_id}
            queue: deque[tuple[str, int]] = deque([(node_id, 0)])
            found_for_mod = False

            while queue:
                curr_id, dist = queue.popleft()
                if dist >= max_hops:
                    continue

                for caller_id in reverse_graph.get(curr_id, []):
                    if caller_id in visited:
                        continue
                    visited.add(caller_id)

                    caller_meta = node_map.get(caller_id)
                    if not caller_meta:
                        continue

                    caller_name, _, caller_file = caller_meta
                    if _is_executable_test(caller_name, caller_file):
                        found_for_mod = True
                        runner = _detect_runner_for_file(caller_file)
                        key = (caller_file, caller_name)
                        if key not in impacted_map or impacted_map[key].hops > dist + 1:
                            impacted_map[key] = ImpactedTest(
                                test_file=caller_file,
                                test_function=caller_name,
                                target_symbol=node_name,
                                target_file=node_file,
                                hops=dist + 1,
                                runner=runner,
                            )
                    else:
                        queue.append((caller_id, dist + 1))

            if not found_for_mod:
                uncovered.append(f"{node_file}:{node_name}")

        test_list = sorted(impacted_map.values(), key=lambda t: (t.test_file, t.test_function))
        runner_args = cls._build_runner_args(test_list)
        cmd_str = " ".join(runner_args) if runner_args else None
        detected_runner = test_list[0].runner if test_list else "pytest"

        return PredictiveTestReport(
            target_symbols=[str(n["name"]) for n in modified_nodes],
            changed_files=changed_files_list,
            impacted_tests=test_list,
            total_impacted=len(test_list),
            runner=detected_runner,
            command=cmd_str,
            uncovered_symbols=uncovered,
        )

    @classmethod
    def _build_runner_args(cls, tests: list[ImpactedTest]) -> list[str]:
        """Constructs an executable argument list tailored to runner syntax."""
        if not tests:
            return []

        by_file: dict[str, list[str]] = {}
        runner = tests[0].runner
        for t in tests:
            by_file.setdefault(t.test_file, []).append(t.test_function)

        if runner == "pytest":
            file_args = list(by_file.keys())
            funcs = sorted({f for funcs in by_file.values() for f in funcs})
            exec_prefix = (
                ["uv", "run", "python", "-m", "pytest"] if shutil.which("uv") else ["pytest"]
            )
            if len(funcs) <= 10:
                return exec_prefix + file_args + ["-k", " or ".join(funcs)]
            return exec_prefix + file_args

        if runner == "bun":
            return ["bun", "test", *list(by_file.keys())]

        if runner == "go":
            funcs = "|".join(sorted({f for funcs in by_file.values() for f in funcs}))
            return ["go", "test", "./...", "-run", f"^({funcs})$"]

        if runner == "cargo":
            funcs_list = sorted({f for funcs in by_file.values() for f in funcs})
            return ["cargo", "test", *funcs_list]

        return ["pytest", *list(by_file.keys())]

    @classmethod
    def execute_impacted_tests(
        cls,
        report: PredictiveTestReport,
        dry_run: bool = False,
        runner_override: str | None = None,
    ) -> int:
        """Runs the impacted test suite and streams output directly to stdout."""
        if report.total_impacted == 0:
            print("[PREDICTIVE TEST] No impacted tests found for modified symbols.")
            if report.uncovered_symbols:
                print(
                    f"[PREDICTIVE TEST] Warning: {len(report.uncovered_symbols)} "
                    "modified symbols have no test callers:"
                )
                for unc in report.uncovered_symbols[:5]:
                    print(f"  - {unc}")
            return 0

        exec_args = cls._build_runner_args(report.impacted_tests)
        if not exec_args:
            return 0

        display_cmd = report.command or " ".join(exec_args)

        if dry_run:
            print(f"[PREDICTIVE TEST] Impacted tests ({report.total_impacted}):")
            for t in report.impacted_tests:
                desc = (
                    f"  • {t.test_file}::{t.test_function} "
                    f"(hops: {t.hops}, from: {t.target_symbol})"
                )
                print(desc)
            print(f"\nCommand:\n  {display_cmd}")
            return 0

        print(f"[PREDICTIVE TEST] Running {report.total_impacted} impacted test(s)...")
        print(f"Command: {display_cmd}\n")

        try:
            res = subprocess.run(exec_args, check=False)
            return res.returncode
        except Exception as err:
            print(f"[PREDICTIVE TEST] Error executing test command: {err}", file=sys.stderr)
            return 1
