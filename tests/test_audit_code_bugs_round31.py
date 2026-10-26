"""Regression tests for Round 31 audit defects:
1. Multi-language AST parser return type preservation (Python, TS, Go, Rust).
2. CTE trace path deterministic relation priority over ambiguous edges.
3. REST API POST /api/reindex directory traversal security guard.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from vibetrace import server_api
from vibetrace.indexer.db import IndexerDB, Node
from vibetrace.indexer.parsers.go_parser import GoParser
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.parsers.python_parser import PythonParser
from vibetrace.indexer.parsers.rust_parser import RustParser
from vibetrace.services.trace_service import trace_execution_path


def test_python_parser_preserves_return_type():
    code = b"def calculate(total: int) -> float:\n    return float(total)\n"
    nodes, _ = PythonParser().extract("calc.py", code)
    func_node = next(n for n in nodes if n.kind == "function")
    assert func_node.signature == "(total: int) -> float"


def test_typescript_parser_preserves_return_type():
    code = b"function fetchData(id: string): Promise<string> { return Promise.resolve(id); }"
    nodes, _ = JavaScriptParser(language="typescript").extract("fetch.ts", code)
    func_node = next(n for n in nodes if n.kind == "function")
    assert func_node.signature == "(id: string): Promise<string>"


def test_go_parser_preserves_return_type():
    code = b"package main\nfunc Execute(ctx Context) (string, error) { return ctx, nil }"
    nodes, _ = GoParser().extract("main.go", code)
    func_node = next(n for n in nodes if n.kind == "function")
    assert func_node.signature == "(ctx Context) (string, error)"


def test_rust_parser_preserves_return_type():
    code = b"fn process(val: u32) -> Result<String, ()> { Ok(val.to_string()) }"
    nodes, _ = RustParser().extract("lib.rs", code)
    func_node = next(n for n in nodes if n.kind == "function")
    assert func_node.signature == "(val: u32) -> Result<String, ()>"


def test_cte_trace_deterministic_edge_prioritization(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    # Insert two nodes: Caller and Callee
    caller = Node(
        id="file.py:caller",
        name="caller",
        kind="function",
        file_path="file.py",
        start_line=1,
        end_line=5,
        qualified_name="caller",
    )
    callee = Node(
        id="file.py:callee",
        name="callee",
        kind="function",
        file_path="file.py",
        start_line=10,
        end_line=15,
        qualified_name="callee",
    )
    db.upsert_node(caller)
    db.upsert_node(callee)

    # Insert multiple edges between caller and callee in order: references first, then calls
    with db._get_conn() as conn:
        conn.execute(
            "INSERT INTO edges (from_node_id, to_node_id, relation_type, call_line) "
            "VALUES ('file.py:caller', 'file.py:callee', 'references', 2)"
        )
        conn.execute(
            "INSERT INTO edges (from_node_id, to_node_id, relation_type, call_line) "
            "VALUES ('file.py:caller', 'file.py:callee', 'calls', 3)"
        )
        conn.commit()

    # Trace execution path using CTE engine
    result = trace_execution_path(db, from_symbol="caller", to_symbol="callee", engine="cte")
    assert result.found is True
    assert len(result.hops) == 1
    # 'calls' must take precedence over 'references' regardless of insertion order
    assert result.hops[0].relation_type == "calls"
    assert result.hops[0].call_line == 3
    db.close()


def test_api_reindex_rejects_path_traversal(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    secret_file = secret_dir / "keys.py"
    secret_file.write_text("API_KEY = 'secret'", encoding="utf-8")

    db_path = project_root / ".vibetrace" / "vibetrace.db"
    db_path.parent.mkdir(parents=True)
    db = IndexerDB(str(db_path))

    orig_db = server_api.db
    orig_root = server_api.project_root
    try:
        server_api.db = db
        server_api.project_root = project_root
        client = TestClient(server_api.app)

        # Attempt directory traversal outside project root
        resp = client.post("/api/reindex", json={"file_path": "../../secret/keys.py"})
        assert resp.status_code == 403
        assert "Access denied" in resp.json()["detail"]
    finally:
        server_api.db = orig_db
        server_api.project_root = orig_root
        db.close()


def test_signature_search_matches_return_types(tmp_path: Path):
    db_path = tmp_path / "test_sig.db"
    db = IndexerDB(str(db_path))

    node = Node(
        id="svc.py:run",
        name="run",
        kind="function",
        file_path="svc.py",
        start_line=1,
        end_line=10,
        qualified_name="run",
        signature="(timeout: int) -> dict[str, Any]",
    )
    db.upsert_node(node)

    # Search by return type keyword
    results = db.search_by_signature_pattern("dict[str, Any]")
    assert len(results) == 1
    assert results[0]["name"] == "run"

    # Search with arrow
    results_arrow = db.search_by_signature_pattern("-> dict")
    assert len(results_arrow) == 1
    assert results_arrow[0]["name"] == "run"
    db.close()
