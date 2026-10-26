from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from vibetrace.cli_commands import cmd_search
from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.indexer.lsp_bridge import resolve_unresolved_edges
from vibetrace.mcp.schemas import ResponseFormat, SemanticSearchInput
from vibetrace.mcp.server import vibetrace_semantic_search
from vibetrace.server_api import app
from vibetrace.server_api import db as api_db
from vibetrace.services.hybrid_search import hybrid_search


def test_get_graph_data_directory_subsystem_filtering(tmp_path: Path):
    """Verify IndexerDB.get_graph_data correctly queries directory prefixes."""
    db_path = tmp_path / "test_graph.db"
    db = IndexerDB(str(db_path))

    node1 = Node(
        id="n1",
        name="func_a",
        qualified_name="pkg.subsystem.func_a",
        kind="function",
        file_path="src/subsystem/mod_a.py",
        start_line=1,
        end_line=10,
    )
    node2 = Node(
        id="n2",
        name="func_b",
        qualified_name="pkg.subsystem.func_b",
        kind="function",
        file_path="src/subsystem/mod_b.py",
        start_line=1,
        end_line=15,
    )
    node3 = Node(
        id="n3",
        name="func_c",
        qualified_name="other.mod_c",
        kind="function",
        file_path="other/mod_c.py",
        start_line=1,
        end_line=20,
    )
    for n in [node1, node2, node3]:
        db.upsert_node(n)
    for e in [
        Edge(from_node_id="n1", to_node_id="n2", relation_type="calls"),
        Edge(from_node_id="n1", to_node_id="n3", relation_type="calls"),
    ]:
        db.upsert_edge(e)

    # 1. Directory query for "src"
    dir_graph = db.get_graph_data(file_path="src")
    assert len(dir_graph["nodes"]) == 2
    assert {n["id"] for n in dir_graph["nodes"]} == {"n1", "n2"}
    assert len(dir_graph["edges"]) == 2  # Both edges touch at least one node in src

    # 2. Directory query for "src/subsystem"
    sub_graph = db.get_graph_data(file_path="src/subsystem")
    assert len(sub_graph["nodes"]) == 2

    # 3. Exact file query
    file_graph = db.get_graph_data(file_path="src/subsystem/mod_a.py")
    assert len(file_graph["nodes"]) == 1
    assert file_graph["nodes"][0]["id"] == "n1"

    # 4. Non-matching directory
    empty_graph = db.get_graph_data(file_path="nonexistent")
    assert empty_graph == {"nodes": [], "edges": []}
    db.close()


def test_lsp_bridge_calculates_indentation_offset(tmp_path: Path):
    """Verify resolve_unresolved_edges passes first non-whitespace column offset to LSP."""
    db_path = tmp_path / "test_lsp.db"
    db = IndexerDB(str(db_path))

    caller_file = tmp_path / "caller.py"
    caller_content = (
        "def main():\n"
        "    # line 2\n"
        "        indented_service_call()\n"  # line 3: 8 spaces indent
    )
    caller_file.write_text(caller_content, encoding="utf-8")

    # Insert unresolved edge in caller.py at line 3
    node_caller = Node(
        id="node_caller",
        name="main",
        qualified_name="caller.main",
        kind="function",
        file_path="caller.py",
        start_line=1,
        end_line=4,
    )
    db.upsert_node(node_caller)
    db.upsert_edge(
        Edge(
            from_node_id="node_caller",
            to_node_id="unresolved_target_service",
            relation_type="calls",
            call_line=3,
        )
    )

    captured_calls: list[tuple[Path, int, int]] = []

    mock_client = MagicMock()

    def fake_get_definition(file_path: Path, line: int, character: int = 0):
        captured_calls.append((file_path, line, character))
        return None  # No definition to rewire, but position must be recorded

    mock_client.get_definition.side_effect = fake_get_definition

    resolve_unresolved_edges(db, project_root=tmp_path, lsp_client=mock_client)

    assert len(captured_calls) == 1
    call_path, call_line, char_offset = captured_calls[0]
    assert call_path == caller_file
    assert call_line == 3
    assert char_offset == 8  # 8 spaces of indentation on line 3
    db.close()


def test_hybrid_search_offset_pagination(tmp_path: Path):
    """Verify hybrid_search slices results correctly with limit and offset."""
    db_path = tmp_path / "test_pagination.db"
    db = IndexerDB(str(db_path))

    nodes = [
        Node(
            id=f"nid_{i}",
            name=f"calc_tax_rate_{i}",
            qualified_name=f"finance.calc_tax_rate_{i}",
            kind="function",
            file_path="finance.py",
            start_line=i * 10,
            end_line=i * 10 + 5,
        )
        for i in range(1, 7)
    ]
    for n in nodes:
        db.upsert_node(n)

    page1 = hybrid_search(db, "calc_tax_rate", limit=2, offset=0)
    page2 = hybrid_search(db, "calc_tax_rate", limit=2, offset=2)
    page3 = hybrid_search(db, "calc_tax_rate", limit=2, offset=4)

    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 2

    ids1 = {r.node_id for r in page1}
    ids2 = {r.node_id for r in page2}
    ids3 = {r.node_id for r in page3}

    assert ids1.isdisjoint(ids2)
    assert ids2.isdisjoint(ids3)
    assert ids1.isdisjoint(ids3)
    db.close()


def test_rest_api_search_offset_pagination(tmp_path: Path):
    """Verify GET /api/search paginates results via offset query parameter."""
    db_path = tmp_path / "test_api_search.db"
    db = IndexerDB(str(db_path))

    nodes = [
        Node(
            id=f"api_nid_{i}",
            name=f"dispatch_order_{i}",
            qualified_name=f"orders.dispatch_order_{i}",
            kind="function",
            file_path="orders.py",
            start_line=i * 10,
            end_line=i * 10 + 5,
        )
        for i in range(1, 6)
    ]
    for n in nodes:
        db.upsert_node(n)

    orig_conn = api_db._get_conn
    api_db._get_conn = db._get_conn

    client = TestClient(app)
    try:
        resp1 = client.get("/api/search", params={"q": "dispatch_order", "limit": 2, "offset": 0})
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert len(data1) == 2

        resp2 = client.get("/api/search", params={"q": "dispatch_order", "limit": 2, "offset": 2})
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert len(data2) == 2

        ids1 = {r["id"] for r in data1}
        ids2 = {r["id"] for r in data2}
        assert ids1.isdisjoint(ids2)
    finally:
        api_db._get_conn = orig_conn
        db.close()


@pytest.mark.asyncio
async def test_mcp_semantic_search_offset_pagination(tmp_path: Path):
    """Verify FastMCP vibetrace_search_semantic respects offset parameter."""
    context_dir = tmp_path / ".vibetrace"
    context_dir.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(context_dir / "vibetrace.db"))

    nodes = [
        Node(
            id=f"mcp_nid_{i}",
            name=f"execute_pipeline_{i}",
            qualified_name=f"pipeline.execute_pipeline_{i}",
            kind="function",
            file_path="pipeline.py",
            start_line=i * 10,
            end_line=i * 10 + 5,
        )
        for i in range(1, 6)
    ]
    for n in nodes:
        db.upsert_node(n)
    db.close()

    res_page1 = await vibetrace_semantic_search(
        SemanticSearchInput(
            query="execute_pipeline",
            limit=2,
            offset=0,
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    data1 = json.loads(res_page1)
    assert len(data1) == 2

    res_page2 = await vibetrace_semantic_search(
        SemanticSearchInput(
            query="execute_pipeline",
            limit=2,
            offset=2,
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    data2 = json.loads(res_page2)
    assert len(data2) == 2

    ids1 = {r["node_id"] for r in data1}
    ids2 = {r["node_id"] for r in data2}
    assert ids1.isdisjoint(ids2)


def test_cli_search_offset_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify CLI search command passes offset flag to hybrid_search."""
    context_dir = tmp_path / ".vibetrace"
    context_dir.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(context_dir / "vibetrace.db"))

    nodes = [
        Node(
            id=f"cli_nid_{i}",
            name=f"audit_rule_{i}",
            qualified_name=f"rules.audit_rule_{i}",
            kind="function",
            file_path="rules.py",
            start_line=i * 10,
            end_line=i * 10 + 5,
        )
        for i in range(1, 6)
    ]
    for n in nodes:
        db.upsert_node(n)
    db.close()

    args1 = argparse.Namespace(
        path=str(tmp_path),
        query="audit_rule",
        signature=False,
        limit=2,
        offset=0,
        json=True,
        no_sync=True,
    )
    cmd_search(args1)
    out1, _ = capsys.readouterr()
    data1 = json.loads(out1)
    assert len(data1) == 2

    args2 = argparse.Namespace(
        path=str(tmp_path),
        query="audit_rule",
        signature=False,
        limit=2,
        offset=2,
        json=True,
        no_sync=True,
    )
    cmd_search(args2)
    out2, _ = capsys.readouterr()
    data2 = json.loads(out2)
    assert len(data2) == 2

    ids1 = {r["node_id"] for r in data1}
    ids2 = {r["node_id"] for r in data2}
    assert ids1.isdisjoint(ids2)
