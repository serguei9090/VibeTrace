"""Regression tests for Round 32 audit defects and parity additions:
1. CLI call-stack subcommand parity and JSON mode.
2. Multi-symbol call stack state leakage isolation in build_call_stack.
3. REST API /api/ namespace parity (/api/graph, /api/trace, /api/call-tree, /api/source).
4. Shared format_impact_analysis_md deduplication.
5. Shared find_symbol_references deduplication across MCP, CLI, and REST.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from vibetrace import server_api
from vibetrace.cli import build_parser
from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.mcp.formatters import format_impact_analysis_md
from vibetrace.services.graph_service import build_call_stack, find_symbol_references


def test_cli_call_stack_parser_and_execution(tmp_path: Path, capsys):
    db_path = tmp_path / ".vibetrace" / "vibetrace.db"
    db_path.parent.mkdir(parents=True)
    db = IndexerDB(str(db_path))

    caller = Node(
        id="a.py:caller",
        name="caller",
        kind="function",
        file_path="a.py",
        start_line=1,
        end_line=5,
        qualified_name="caller",
    )
    callee = Node(
        id="a.py:callee",
        name="callee",
        kind="function",
        file_path="a.py",
        start_line=10,
        end_line=15,
        qualified_name="callee",
    )
    db.upsert_node(caller)
    db.upsert_node(callee)
    db.upsert_edge(
        Edge(
            from_node_id="a.py:caller",
            to_node_id="a.py:callee",
            relation_type="calls",
            call_line=3,
        )
    )
    db.close()

    parser = build_parser()

    # 1. Test CLI execution (text output)
    args = parser.parse_args(
        ["call-stack", "callee", "--direction", "up", "--path", str(tmp_path), "--no-sync"]
    )
    code = args.func(args)
    captured = capsys.readouterr()
    assert code == 0
    assert "caller" in captured.out
    assert "Callers (Incoming):" in captured.out

    # 2. Test CLI execution (json output)
    cmd_args = [
        "call-stack",
        "callee",
        "--direction",
        "up",
        "--path",
        str(tmp_path),
        "--no-sync",
        "--json",
    ]
    args_json = parser.parse_args(cmd_args)
    code_json = args_json.func(args_json)
    captured_json = capsys.readouterr()
    assert code_json == 0
    data = json.loads(captured_json.out)
    assert data["found"] is True
    assert data["symbol"] == "callee"
    assert data["direction"] == "up"
    assert any("caller" in line for line in data["call_stack"])


def test_build_call_stack_multi_symbol_isolation(tmp_path: Path):
    """Verify that when 2 symbols share the same name, caller check does not leak across nodes."""
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    # Node 1: worker.py:run (HAS callers)
    worker_run = Node(
        id="worker.py:run",
        name="run",
        kind="function",
        file_path="worker.py",
        start_line=1,
        end_line=5,
        qualified_name="run",
    )
    # Node 2: task.py:run (HAS NO callers)
    task_run = Node(
        id="task.py:run",
        name="run",
        kind="function",
        file_path="task.py",
        start_line=1,
        end_line=5,
        qualified_name="run",
    )
    # Caller for worker.py:run
    invoker = Node(
        id="main.py:start",
        name="start",
        kind="function",
        file_path="main.py",
        start_line=10,
        end_line=15,
        qualified_name="start",
    )

    db.upsert_node(worker_run)
    db.upsert_node(task_run)
    db.upsert_node(invoker)
    db.upsert_edge(
        Edge(
            from_node_id="main.py:start",
            to_node_id="worker.py:run",
            relation_type="calls",
            call_line=12,
        )
    )

    found, output, nodes = build_call_stack(db, "run", direction="up", max_depth=2)
    assert found is True
    assert len(nodes) == 2

    full_text = "\n".join(output)
    # Both traces must be present
    assert "Trace for `run` (function) in `worker.py`" in full_text
    assert "Trace for `run` (function) in `task.py`" in full_text
    # worker.py:run has callers from start
    assert "start" in full_text
    # task.py:run MUST explicitly state "(no callers found)" without leaking from worker.py
    assert "(no callers found)" in full_text
    db.close()


def test_rest_api_namespaced_endpoints(tmp_path: Path):
    """Verify /api/ aliases for /graph, /trace, /call-tree, and /source."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    source_file = project_root / "mod.py"
    source_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    db_path = project_root / ".vibetrace" / "vibetrace.db"
    db_path.parent.mkdir(parents=True)
    db = IndexerDB(str(db_path))

    node = Node(
        id="mod.py:hello",
        name="hello",
        kind="function",
        file_path="mod.py",
        start_line=1,
        end_line=2,
        qualified_name="hello",
    )
    db.upsert_node(node)
    db.update_file_hash("mod.py", "dummy_hash", 12345)

    orig_db = server_api.db
    orig_root = server_api.project_root
    try:
        server_api.db = db
        server_api.project_root = project_root
        client = TestClient(server_api.app)

        # /api/graph
        resp_graph = client.get("/api/graph")
        assert resp_graph.status_code == 200
        assert any(n["id"] == "mod.py:hello" for n in resp_graph.json()["nodes"])

        # /api/source
        src_params = {"file_path": "mod.py", "start_line": 1, "end_line": 2}
        resp_source = client.get("/api/source", params=src_params)
        assert resp_source.status_code == 200
        assert "hello" in resp_source.json()["source_code"]

        # /api/call-tree
        resp_tree = client.get("/api/call-tree", params={"symbol": "hello"})
        assert resp_tree.status_code == 200
        assert resp_tree.json()["found"] is True

        # /api/trace
        trace_params = {"from_symbol": "hello", "to_symbol": "hello"}
        resp_trace = client.get("/api/trace", params=trace_params)
        assert resp_trace.status_code == 200
    finally:
        server_api.db = orig_db
        server_api.project_root = orig_root
        db.close()


def test_shared_format_impact_analysis():
    # Empty impact
    empty_out = format_impact_analysis_md("test.py", 0, {})
    assert "Impact Analysis for `test.py`" in empty_out
    assert "No external dependencies found" in empty_out

    # Multi-level impact
    levels = {1: ["func_a in a.py"], 2: ["func_b in b.py"]}
    multi_out = format_impact_analysis_md("test.py", 2, levels)
    assert "**Total Impact**: 2 components affected" in multi_out
    assert "### Level 1: Direct Impact" in multi_out
    assert "func_a in a.py" in multi_out
    assert "### Level 2: Secondary Impact" in multi_out


def test_shared_find_symbol_references(tmp_path: Path):
    db_path = tmp_path / "refs.db"
    db = IndexerDB(str(db_path))

    target = Node(
        id="lib.py:compute",
        name="compute",
        kind="function",
        file_path="lib.py",
        start_line=1,
        end_line=5,
        qualified_name="compute",
    )
    caller = Node(
        id="app.py:execute",
        name="execute",
        kind="function",
        file_path="app.py",
        start_line=10,
        end_line=20,
        qualified_name="execute",
    )
    db.upsert_node(target)
    db.upsert_node(caller)
    db.upsert_edge(
        Edge(
            from_node_id="app.py:execute",
            to_node_id="lib.py:compute",
            relation_type="calls",
            call_line=12,
        )
    )

    found, targets, summaries = find_symbol_references(db, "compute")
    assert found is True
    assert len(targets) == 1
    assert len(summaries) == 1
    assert len(summaries[0]["references"]) == 1
    assert summaries[0]["references"][0]["name"] == "execute"

    # Non-existent symbol
    not_found, _, _ = find_symbol_references(db, "non_existent_func")
    assert not_found is False
    db.close()
