"""Regression tests for Round 33 audit defects:
1. Java, C++, C# function signature and return type extraction in GenericParser.
2. Deterministic centrality ranking and offset pagination in search_by_signature_pattern.
3. REST API /api/search/signature pagination (limit and offset).
4. MCP vibetrace_search_by_signature output formatting via format_signature_search_md.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibetrace import server_api
from vibetrace.indexer.db import IndexerDB, Node
from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.mcp.schemas import SearchInput
from vibetrace.mcp.server import vibetrace_search_by_signature


def test_java_signature_and_return_type_extraction():
    parser = GenericParser("java")
    code = (
        b"class Service { public String processOrder(int orderId, boolean fast) { return null; } }"
    )
    nodes, _ = parser.extract("Service.java", code)
    func_node = next(n for n in nodes if n.kind == "function" and n.name == "processOrder")
    assert func_node.signature == "(int orderId, boolean fast) -> String"


def test_cpp_signature_and_return_type_extraction():
    parser = GenericParser("cpp")
    code = b"double calculateMetrics(int samples, float rate) { return 0.0; }"
    nodes, _ = parser.extract("calc.cpp", code)
    func_node = next(n for n in nodes if n.kind == "function" and n.name == "calculateMetrics")
    assert func_node.signature == "(int samples, float rate) -> double"


def test_csharp_signature_and_return_type_extraction():
    parser = GenericParser("c_sharp")
    code = b"class Controller { public async Task<bool> Validate(string token) { return true; } }"
    nodes, _ = parser.extract("Controller.cs", code)
    func_node = next(n for n in nodes if n.kind == "function" and n.name == "Validate")
    assert func_node.signature == "(string token) -> Task<bool>"


def test_signature_search_deterministic_centrality_and_offset(tmp_path: Path):
    db_path = tmp_path / "sig_test.db"
    db = IndexerDB(str(db_path))

    # Insert 3 nodes with identical signature pattern but different centrality
    node_low = Node(
        id="a.py:low",
        name="low",
        kind="function",
        file_path="a.py",
        start_line=1,
        end_line=5,
        qualified_name="low",
        signature="(x: int) -> bool",
        centrality=0.1,
    )
    node_high = Node(
        id="b.py:high",
        name="high",
        kind="function",
        file_path="b.py",
        start_line=1,
        end_line=5,
        qualified_name="high",
        signature="(x: int) -> bool",
        centrality=0.9,
    )
    node_mid = Node(
        id="c.py:mid",
        name="mid",
        kind="function",
        file_path="c.py",
        start_line=1,
        end_line=5,
        qualified_name="mid",
        signature="(x: int) -> bool",
        centrality=0.5,
    )

    db.upsert_node(node_low)
    db.upsert_node(node_high)
    db.upsert_node(node_mid)

    # 1. Verify deterministic order: high (0.9) -> mid (0.5) -> low (0.1)
    results = db.search_by_signature_pattern("-> bool", limit=10, offset=0)
    assert len(results) == 3
    assert results[0]["name"] == "high"
    assert results[1]["name"] == "mid"
    assert results[2]["name"] == "low"

    # 2. Verify pagination offset
    paginated = db.search_by_signature_pattern("-> bool", limit=2, offset=1)
    assert len(paginated) == 2
    assert paginated[0]["name"] == "mid"
    assert paginated[1]["name"] == "low"

    db.close()


def test_api_search_signature_with_pagination(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    db_path = project_root / ".vibetrace" / "vibetrace.db"
    db_path.parent.mkdir(parents=True)
    db = IndexerDB(str(db_path))

    for i in range(5):
        node = Node(
            id=f"file{i}.py:fn{i}",
            name=f"fn{i}",
            kind="function",
            file_path=f"file{i}.py",
            start_line=1,
            end_line=5,
            qualified_name=f"fn{i}",
            signature=f"(arg: int) -> Res{i}",
            centrality=float(i),
        )
        db.upsert_node(node)

    orig_db = server_api.db
    orig_root = server_api.project_root
    try:
        server_api.db = db
        server_api.project_root = project_root
        client = TestClient(server_api.app)

        query_params = {"pattern": "arg: int", "limit": 2, "offset": 1}
        resp = client.get("/api/search/signature", params=query_params)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # Ranked by centrality DESC: fn4 (4.0), fn3 (3.0), fn2 (2.0), ...
        # offset=1 should skip fn4 and yield fn3 and fn2
        assert data[0]["name"] == "fn3"
        assert data[1]["name"] == "fn2"
    finally:
        server_api.db = orig_db
        server_api.project_root = orig_root
        db.close()


@pytest.mark.asyncio
async def test_mcp_search_by_signature_uses_formatter(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "mcp_sig.db"
    db = IndexerDB(str(db_path))

    node = Node(
        id="calc.cpp:compute",
        name="compute",
        kind="function",
        file_path="calc.cpp",
        start_line=10,
        end_line=15,
        qualified_name="compute",
        signature="(val: int) -> double",
        centrality=0.8,
    )
    db.upsert_node(node)

    monkeypatch.setattr(
        "vibetrace.mcp.server._get_context_for_path",
        lambda _: (db, tmp_path),
    )

    out = await vibetrace_search_by_signature(SearchInput(pattern="double"))
    assert "## Signature Search: `double`" in out
    assert "**`compute`**" in out
    assert "(val: int) -> double" in out
    assert "calc.cpp:L10" in out
    db.close()
