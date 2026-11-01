"""Unit tests and speed benchmarks for VibeTrace Agentic Deep Taint Engine.

Verifies:
- Function Taint Contracts (AST parameter-to-return and sink analysis).
- Upward return-value flow propagation (helper function return taint).
- Sanitizer helper neutralization (eliminating false positives).
- Multi-hop Source-to-Sink call graph slicing.
- Sub-2-second execution benchmark.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.security.contracts import TaintContractManager
from vibetrace.security.dataflow import verify_sink_taint_flow
from vibetrace.security.models import FindingCategory, SecurityFinding, Severity
from vibetrace.security.taint_engine import TaintEngine


def test_function_taint_contracts_analyzer() -> None:
    code = """
def format_input(raw_user, prefix):
    x = raw_user.strip()
    return prefix + x

def validate_age(raw_age):
    return int(raw_age)

def execute_raw(sql_query):
    db.execute(sql_query)
"""
    contracts = TaintContractManager.analyze_source_code(code, "test.py")

    # 1. format_input: param 0 (raw_user) and param 1 (prefix) flow to return
    c1 = contracts["format_input"]
    assert c1.param_taints_return[0] is True
    assert c1.param_taints_return[1] is True
    assert c1.is_sanitizer is False

    # 2. validate_age: uses int() cast -> sanitizer
    c2 = contracts["validate_age"]
    assert c2.is_sanitizer is True

    # 3. execute_raw: calls internal execute -> sink
    c3 = contracts["execute_raw"]
    assert len(c3.internal_sinks) == 1
    assert c3.internal_sinks[0]["call"] == "execute"
    assert c3.internal_sinks[0]["cwe"] == "CWE-89"


def test_upward_return_value_taint_propagation() -> None:
    code = """
def normalize_id(val):
    return val.strip().lower()

def handle_user(request):
    user_id = request.args.get("id")
    clean_id = normalize_id(user_id)
    db.execute(f"SELECT * FROM users WHERE id = '{clean_id}'")
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        py_file = root / "app.py"
        py_file.write_text(code, encoding="utf-8")

        is_tainted, chain = verify_sink_taint_flow(
            project_root=root,
            file_path="app.py",
            func_start_line=5,
            func_end_line=9,
            sink_line=8,
            sanitizers=frozenset({"quote", "escape"}),
        )

        assert is_tainted is True
        assert "clean_id" in chain or "user_id" in chain


def test_sanitizer_neutralizes_return_taint() -> None:
    code = """
def sanitize_id(val):
    return int(val)

def handle_user(request):
    user_id = request.args.get("id")
    safe_id = sanitize_id(user_id)
    db.execute(f"SELECT * FROM users WHERE id = {safe_id}")
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        py_file = root / "app.py"
        py_file.write_text(code, encoding="utf-8")

        is_tainted, _chain = verify_sink_taint_flow(
            project_root=root,
            file_path="app.py",
            func_start_line=5,
            func_end_line=9,
            sink_line=8,
            sanitizers=frozenset({"quote", "escape"}),
        )

        assert is_tainted is False


def test_multi_hop_call_chain_slicing() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        c_file = root / "controller.py"
        s_file = root / "service.py"
        r_file = root / "repo.py"

        c_file.write_text(
            "@app.get('/search')\n"
            "def search_api(request):\n"
            "    q = request.args.get('q')\n"
            "    return run_search(q)\n",
            encoding="utf-8",
        )
        s_file.write_text(
            "def run_search(term):\n    clean = term.strip()\n    return db_find(clean)\n",
            encoding="utf-8",
        )
        r_file.write_text(
            "def db_find(sql_part):\n"
            "    db.execute(f'SELECT * FROM items WHERE name = {sql_part}')\n",
            encoding="utf-8",
        )

        db = IndexerDB(str(root / "cache.db"))
        with db._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY, name TEXT, kind TEXT, file_path TEXT,
                    start_line INTEGER, end_line INTEGER, decorators TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS edges (
                    from_node_id TEXT, to_node_id TEXT, relation_type TEXT
                )
                """
            )
            cols = "id, name, qualified_name, kind, file_path, start_line, end_line, decorators"
            conn.execute(
                f"INSERT INTO nodes ({cols}) "
                "VALUES ('n1', 'search_api', 'search_api', 'function', "
                "'controller.py', 1, 4, '[\"@app.get\"]')"
            )
            conn.execute(
                f"INSERT INTO nodes ({cols}) "
                "VALUES ('n2', 'run_search', 'run_search', 'function', 'service.py', 1, 3, '[]')"
            )
            conn.execute(
                f"INSERT INTO nodes ({cols}) "
                "VALUES ('n3', 'db_find', 'db_find', 'function', 'repo.py', 1, 2, '[]')"
            )
            conn.execute(
                "INSERT INTO edges (from_node_id, to_node_id, relation_type) "
                "VALUES ('n1', 'n2', 'calls')"
            )
            conn.execute(
                "INSERT INTO edges (from_node_id, to_node_id, relation_type) "
                "VALUES ('n2', 'n3', 'calls')"
            )

        finding = SecurityFinding(
            id="SEC-CWE-89-test",
            rule_id="python:CWE-89",
            cwe_id="CWE-89",
            category=FindingCategory.AST_PATTERN,
            severity=Severity.HIGH,
            title="SQL Injection",
            message="Direct execute with format string",
            file_path="repo.py",
            start_line=2,
            end_line=2,
            code_hash="112233445566",
        )

        analyzed = TaintEngine.analyze(db, [finding], project_root=root)
        assert len(analyzed) == 1
        res = analyzed[0]

        assert res.category == FindingCategory.TAINT_REACHABILITY
        assert res.severity == Severity.CRITICAL
        slices = res.metadata.get("taint_slices", [])
        assert len(slices) == 3
        assert slices[0]["symbol"] == "search_api"
        assert slices[1]["symbol"] == "run_search"
        assert slices[2]["symbol"] == "db_find"
        assert "db.execute" in slices[2]["snippet"]


def test_speed_benchmark_under_2_seconds() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        db = IndexerDB(str(root / "perf.db"))

        with db._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY, name TEXT, kind TEXT, file_path TEXT,
                    start_line INTEGER, end_line INTEGER, decorators TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS edges (
                    from_node_id TEXT, to_node_id TEXT, relation_type TEXT
                )
                """
            )

        # Generate 50 source files with chained function calls
        findings: list[SecurityFinding] = []
        cols = "id, name, qualified_name, kind, file_path, start_line, end_line, decorators"
        for i in range(50):
            p = root / f"file_{i}.py"
            p.write_text(
                f"@app.get('/route{i}')\n"
                f"def handler_{i}(request):\n"
                f"    param = request.args.get('x')\n"
                f"    db.execute(param)\n",
                encoding="utf-8",
            )
            with db._get_conn() as conn:
                conn.execute(
                    f"INSERT INTO nodes ({cols}) "
                    f"VALUES ('node_{i}', 'handler_{i}', 'handler_{i}', 'function', "
                    f"'file_{i}.py', 1, 4, '[\"@app.get\"]')"
                )
            findings.append(
                SecurityFinding(
                    id=f"SEC-PERF-{i}",
                    rule_id="python:CWE-89",
                    cwe_id="CWE-89",
                    category=FindingCategory.AST_PATTERN,
                    severity=Severity.HIGH,
                    title="SQL Injection",
                    message="Direct execute with format string",
                    file_path=f"file_{i}.py",
                    start_line=4,
                    end_line=4,
                    code_hash=f"hash_{i:04d}",
                )
            )

        start_t = time.perf_counter()
        results = TaintEngine.analyze(db, findings, project_root=root)
        elapsed = time.perf_counter() - start_t

        assert len(results) == 50
        print(f"\n[BENCHMARK] Analyzed 50 source-to-sink paths in {elapsed:.3f}s")
        assert elapsed < 2.0, f"Expected < 2.0s, got {elapsed:.3f}s"
