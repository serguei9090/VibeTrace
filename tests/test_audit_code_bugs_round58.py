"""Tests for Round 58: MCP and REST API parity & polish.

1. MCP vibetrace_check_caller_guard with staged=True.
2. MCP vibetrace_get_call_stack with all_relations=True/False.
3. MCP vibetrace_get_dependencies with reverse=True/False.
4. REST API /api/vibe/guard?staged=true and /api/dependencies with dependents/circular data.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.mcp.schemas import (
    CallerGuardInput,
    CallStackInput,
    DependenciesInput,
    ResponseFormat,
)
from vibetrace.mcp.server import (
    vibetrace_check_caller_guard,
    vibetrace_get_call_stack,
    vibetrace_get_dependencies,
)
from vibetrace.server_api import app


@pytest.fixture
def test_workspace(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    # Add caller and callee nodes
    caller = Node(
        id="mod_caller",
        name="caller_fn",
        qualified_name="caller_fn",
        kind="function",
        file_path="src/caller.py",
        start_line=10,
        end_line=20,
    )
    target = Node(
        id="mod_target",
        name="target_fn",
        qualified_name="target_fn",
        kind="function",
        file_path="src/target.py",
        start_line=30,
        end_line=40,
    )
    container = Node(
        id="mod_container",
        name="src.target",
        qualified_name="src.target",
        kind="module",
        file_path="src/target.py",
        start_line=1,
        end_line=50,
    )
    db.upsert_node(caller)
    db.upsert_node(target)
    db.upsert_node(container)

    db.upsert_edge(
        Edge(
            from_node_id="mod_caller",
            to_node_id="mod_target",
            relation_type="calls",
            call_line=15,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="mod_container",
            to_node_id="mod_target",
            relation_type="defines",
            call_line=None,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="mod_caller",
            to_node_id="mod_target",
            relation_type="imports",
            call_line=1,
        )
    )
    return db, tmp_path


@pytest.mark.asyncio
async def test_mcp_caller_guard_staged(test_workspace: tuple[IndexerDB, Path]):
    """Verify MCP caller guard supports staged git inspection."""
    db, root = test_workspace
    with patch("vibetrace.mcp.server.get_context_for_path", return_value=(db, root)):
        # Empty staged diff
        res_empty = await vibetrace_check_caller_guard(
            CallerGuardInput(staged=True, response_format=ResponseFormat.MARKDOWN)
        )
        assert "No modified symbol definitions detected" in res_empty

        # JSON response format with empty diff
        res_json = await vibetrace_check_caller_guard(
            CallerGuardInput(staged=True, response_format=ResponseFormat.JSON)
        )
        assert res_json == "[]"


@pytest.mark.asyncio
async def test_mcp_call_stack_and_dependencies(test_workspace: tuple[IndexerDB, Path]):
    """Verify MCP call-stack all_relations and dependencies reverse filter."""
    db, root = test_workspace
    with patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, root)):
        # 1. Call stack execution-only by default
        stack_default = await vibetrace_get_call_stack(
            CallStackInput(node_name="target_fn", all_relations=False)
        )
        assert "caller_fn" in stack_default
        assert "(defines)" not in stack_default

        # 2. Call stack with all_relations=True
        stack_all = await vibetrace_get_call_stack(
            CallStackInput(node_name="target_fn", all_relations=True)
        )
        assert "(defines)" in stack_all

        # 3. Dependencies with reverse=True
        deps_rev = await vibetrace_get_dependencies(
            DependenciesInput(file_path="src/target.py", reverse=True)
        )
        assert "Inbound Dependents" in deps_rev
        assert "src/caller.py" in deps_rev


def test_rest_api_guard_staged_and_dependencies(test_workspace: tuple[IndexerDB, Path]):
    """Verify FastAPI endpoints support staged guard and enriched dependency analysis."""
    db, root = test_workspace
    with (
        patch("vibetrace.server_api.db", db),
        patch("vibetrace.server_api.project_root", root),
    ):
        client = TestClient(app)

        # 1. /api/vibe/guard?staged=true
        resp_guard = client.get("/api/vibe/guard?staged=true")
        assert resp_guard.status_code == 200
        assert isinstance(resp_guard.json(), list)

        # 2. /api/dependencies
        resp_deps = client.get("/api/dependencies?file_path=src/target.py")
        assert resp_deps.status_code == 200
        data = resp_deps.json()
        assert "dependents" in data
        assert "circular" in data
        assert data["counts"]["dependents"] >= 1
