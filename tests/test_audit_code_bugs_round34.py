from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from vibetrace import cli
from vibetrace.indexer.db import IndexerDB, Node
from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.mcp.schemas import (
    CallStackInput,
    ReferencesInput,
    ResponseFormat,
    SearchInput,
    TraceDirection,
)
from vibetrace.mcp.server import (
    vibetrace_find_references,
    vibetrace_get_call_stack,
    vibetrace_search_by_signature,
)


def test_generic_parser_cpp_pointer_declarator():
    """Verify C++ pointer/reference return types extract clean names and signatures."""
    parser = GenericParser("cpp")
    source = b"""
    int* get_data(int id) {
        return nullptr;
    }
    double &get_ref(float val) {
        return x;
    }
    """
    nodes, _ = parser.extract("src/storage.cpp", source)
    fn_nodes = [n for n in nodes if n.kind == "function"]
    assert len(fn_nodes) == 2

    data_fn = next(n for n in fn_nodes if n.name == "get_data")
    assert data_fn.name == "get_data"
    assert not data_fn.name.startswith("*")
    assert data_fn.signature is not None
    assert "(int id)" in data_fn.signature

    ref_fn = next(n for n in fn_nodes if n.name == "get_ref")
    assert ref_fn.name == "get_ref"
    assert not ref_fn.name.startswith("&")
    assert ref_fn.signature is not None
    assert "(float val)" in ref_fn.signature


def test_js_parser_single_param_arrow_function():
    """Verify JS/TS single-parameter arrow functions extract correct (x) signature."""
    parser = JavaScriptParser()
    source = b"""
    const doubleVal = x => x * 2;
    const multi = (a, b) => a * b;
    """
    nodes, _ = parser.extract("src/math.js", source)
    fn_nodes = [n for n in nodes if n.kind == "function"]
    assert len(fn_nodes) == 2

    double_fn = next(n for n in fn_nodes if n.name == "doubleVal")
    assert double_fn.signature == "(x)"

    multi_fn = next(n for n in fn_nodes if n.name == "multi")
    assert multi_fn.signature == "(a, b)"


@pytest.mark.asyncio
async def test_mcp_search_by_signature_pagination_and_json(tmp_path: Path):
    """Verify vibetrace_search_by_signature honors limit, offset, and JSON format."""
    db_path = tmp_path / "test_sig_mcp.db"
    db = IndexerDB(str(db_path))

    for i in range(4):
        node = Node(
            id=f"f{i}.py:fn{i}",
            name=f"fn{i}",
            kind="function",
            file_path=f"f{i}.py",
            start_line=1,
            end_line=5,
            signature="(val: int) -> int",
            centrality=float(i),
        )
        db.upsert_node(node)

    with patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, tmp_path)):
        # Ranking by centrality DESC: fn3 (3.0), fn2 (2.0), fn1 (1.0), fn0 (0.0)
        # offset=1, limit=2 -> fn2, fn1
        out = await vibetrace_search_by_signature(
            SearchInput(
                pattern="val: int",
                limit=2,
                offset=1,
                response_format=ResponseFormat.JSON,
            )
        )
        data = json.loads(out)
        assert len(data) == 2
        assert data[0]["name"] == "fn2"
        assert data[1]["name"] == "fn1"

    db.close()


@pytest.mark.asyncio
async def test_mcp_find_references_kind_and_json(tmp_path: Path):
    """Verify vibetrace_find_references supports kind filtering and JSON response format."""
    db_path = tmp_path / "test_refs_mcp.db"
    db = IndexerDB(str(db_path))

    target = Node(
        id="core.py:service",
        name="service",
        kind="function",
        file_path="core.py",
        start_line=10,
        end_line=20,
    )
    caller = Node(
        id="app.py:main",
        name="main",
        kind="function",
        file_path="app.py",
        start_line=1,
        end_line=5,
    )
    db.upsert_node(target)
    db.upsert_node(caller)

    from vibetrace.indexer.db import Edge

    db.upsert_edge(
        Edge(from_node_id=caller.id, to_node_id=target.id, relation_type="calls", call_line=3)
    )

    with patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, tmp_path)):
        out = await vibetrace_find_references(
            ReferencesInput(
                symbol_name="service",
                kind="calls",
                response_format=ResponseFormat.JSON,
            )
        )
        data = json.loads(out)
        assert data["found"] is True
        assert data["symbol"] == "service"
        assert data["total_references"] == 1
        assert data["targets"][0]["references"][0]["relation_type"] == "calls"

    db.close()


@pytest.mark.asyncio
async def test_mcp_call_stack_json(tmp_path: Path):
    """Verify vibetrace_get_call_stack supports response_format=JSON."""
    db_path = tmp_path / "test_stack_mcp.db"
    db = IndexerDB(str(db_path))

    n1 = Node(
        id="mod.py:entry",
        name="entry",
        kind="function",
        file_path="mod.py",
        start_line=1,
        end_line=5,
    )
    db.upsert_node(n1)

    with patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, tmp_path)):
        out = await vibetrace_get_call_stack(
            CallStackInput(
                node_name="entry",
                file_path="mod.py",
                direction=TraceDirection.BOTH,
                depth=2,
                response_format=ResponseFormat.JSON,
            )
        )
        data = json.loads(out)
        assert data["found"] is True
        assert data["symbol"] == "entry"
        assert data["direction"] == "both"
        assert len(data["nodes"]) == 1

    db.close()


def test_cli_search_sig_offset(tmp_path: Path, capsys):
    """Verify CLI search --sig respects --offset and --limit with --json."""
    db_path = tmp_path / ".vibetrace" / "vibetrace.db"
    db_path.parent.mkdir(parents=True)
    db = IndexerDB(str(db_path))

    for i in range(4):
        node = Node(
            id=f"cli{i}.py:run{i}",
            name=f"run{i}",
            kind="function",
            file_path=f"cli{i}.py",
            start_line=1,
            end_line=5,
            signature="() -> TaskResult",
            centrality=float(i),
        )
        db.upsert_node(node)
    db.close()

    ret = cli.main(
        [
            "search",
            "TaskResult",
            "--sig",
            "--limit",
            "2",
            "--offset",
            "1",
            "--json",
            "--path",
            str(tmp_path),
            "--no-sync",
        ]
    )
    assert ret == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert len(data) == 2
    # Centrality DESC: run3 (3.0), run2 (2.0), run1 (1.0), run0 (0.0)
    # offset=1, limit=2 -> run2, run1
    assert data[0]["name"] == "run2"
    assert data[1]["name"] == "run1"
