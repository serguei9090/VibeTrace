"""Tests for Surface Parity across CLI, MCP, and REST API (Audit Round 43)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.mcp.schemas import (
    CallStackInput,
    DependenciesInput,
    ImpactAnalysisInput,
    ResponseFormat,
    StructuralSummaryInput,
)
from vibetrace.mcp.server import (
    vibetrace_get_call_stack,
    vibetrace_get_dependencies,
    vibetrace_get_structural_summary,
    vibetrace_impact_analysis,
)
from vibetrace.server_api import app


@pytest.fixture
def setup_parity_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up temporary workspace with test database and files."""
    context_dir = tmp_path / ".vibetrace"
    context_dir.mkdir(parents=True, exist_ok=True)
    db_file = context_dir / "vibetrace.db"
    db = IndexerDB(str(db_file))

    # Create dummy source files
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    file_a = src_dir / "service.py"
    file_a.write_text(
        "import math\n\ndef execute():\n    return helper()\n\ndef helper():\n    return 42\n",
        encoding="utf-8",
    )

    node_exec = Node(
        id="src/service.py:execute:3",
        name="execute",
        qualified_name="execute",
        kind="function",
        file_path="src/service.py",
        start_line=3,
        end_line=4,
        signature="def execute() -> int",
    )
    node_help = Node(
        id="src/service.py:helper:6",
        name="helper",
        qualified_name="helper",
        kind="function",
        file_path="src/service.py",
        start_line=6,
        end_line=7,
        signature="def helper() -> int",
    )

    db.upsert_node(node_exec)
    db.upsert_node(node_help)
    db.upsert_edge(
        Edge(
            from_node_id=node_exec.id,
            to_node_id=node_help.id,
            relation_type="calls",
            call_line=4,
        )
    )
    db.update_file_hash("src/service.py", "hash123", 0)

    # Monkeypatch server_api global db & project_root
    import vibetrace.server_api

    monkeypatch.setattr(vibetrace.server_api, "db", db)
    monkeypatch.setattr(vibetrace.server_api, "project_root", str(tmp_path))

    return tmp_path, db


def test_rest_api_hooks_status_and_actions(setup_parity_workspace):
    """Verify REST API /api/hooks parity with CLI hooks."""
    _tmp_path, _ = setup_parity_workspace
    client = TestClient(app)

    # 1. GET /api/hooks (status)
    res = client.get("/api/hooks")
    assert res.status_code == 200
    data = res.json()
    assert "provider" in data
    assert "lefthook" in data
    assert "git" in data

    # 2. POST /api/hooks (uninstall default)
    res_un = client.post("/api/hooks?action=uninstall")
    assert res_un.status_code == 200
    assert res_un.json()["action"] == "uninstall"


def test_rest_api_search_hybrid_flag_parity(setup_parity_workspace):
    """Verify REST API /api/search?hybrid=true returns SearchResult models."""
    _tmp_path, _ = setup_parity_workspace
    client = TestClient(app)

    # Standard AST/FTS search
    res_std = client.get("/api/search", params={"q": "execute"})
    assert res_std.status_code == 200
    items_std = res_std.json()
    assert len(items_std) >= 1
    assert items_std[0]["name"] == "execute"

    # Hybrid 4-stream RRF search parity
    res_hyb = client.get("/api/search", params={"q": "execute", "hybrid": "true"})
    assert res_hyb.status_code == 200
    items_hyb = res_hyb.json()
    assert len(items_hyb) >= 1
    assert "rrf_score" in items_hyb[0]
    assert "match_sources" in items_hyb[0]


@pytest.mark.asyncio
async def test_mcp_scope_path_parity_for_tools(setup_parity_workspace):
    """Verify MCP tools accept scope_path for all structural and graph inspection operations."""
    tmp_path, _ = setup_parity_workspace

    # 1. Structural summary with scope_path
    sum_res = await vibetrace_get_structural_summary(
        StructuralSummaryInput(
            file_path="src/service.py",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    sum_data = json.loads(sum_res)
    assert sum_data["total"] >= 2
    assert any(n["name"] == "execute" for n in sum_data["nodes"])

    # 2. Call stack with scope_path
    cs_res = await vibetrace_get_call_stack(
        CallStackInput(
            node_name="execute",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    cs_data = json.loads(cs_res)
    assert cs_data["found"] is True
    assert cs_data["symbol"] == "execute"

    # 3. Impact analysis with scope_path
    imp_res = await vibetrace_impact_analysis(
        ImpactAnalysisInput(
            file_path="src/service.py",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    imp_data = json.loads(imp_res)
    assert "total_impact" in imp_data
    assert imp_data["file_path"] == "src/service.py"

    # 4. Dependencies with scope_path
    dep_res = await vibetrace_get_dependencies(
        DependenciesInput(
            file_path="src/service.py",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    dep_data = json.loads(dep_res)
    assert dep_data["file_path"] == "src/service.py"
    assert "counts" in dep_data
