"""Tests for Round 63: REST API and FastMCP surface parity for project federation.

1. REST API /api/project endpoints (manifest/members, search, guard, links).
2. MCP tools for project federation (vibetrace_project_status, search, guard, links).
"""

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from vibetrace.federation.project import ProjectWorkspace
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.mcp.schemas import (
    ProjectGuardInput,
    ProjectLinksInput,
    ProjectSearchInput,
    ProjectStatusInput,
    ResponseFormat,
)
from vibetrace.mcp.tools_project import (
    vibetrace_project_guard,
    vibetrace_project_links,
    vibetrace_project_search,
    vibetrace_project_status,
)
from vibetrace.server_api import app


def setup_federated_environment(tmp_path: Path):
    """Create a mock workspace with client and server route contracts."""
    project_root = tmp_path / "api_suite"
    project_root.mkdir()

    fe_dir = project_root / "frontend"
    be_dir = project_root / "backend"
    fe_dir.mkdir()
    be_dir.mkdir()

    (fe_dir / "package.json").write_text('{"name": "frontend"}', encoding="utf-8")
    (be_dir / "pyproject.toml").write_text('[project]\nname = "backend"', encoding="utf-8")

    # Frontend DB
    fe_db_dir = fe_dir / ".vibetrace"
    fe_db_dir.mkdir()
    fe_db = IndexerDB(str(fe_db_dir / "vibetrace.db"))

    fe_fn = Node(
        id="fe_pay_btn",
        name="onPaymentSubmit",
        qualified_name="frontend.pay.onPaymentSubmit",
        kind="function",
        file_path="src/pay.ts",
        start_line=12,
        end_line=28,
    )
    fe_endpoint = Node(
        id="fe_ep_checkout",
        name="POST /api/v1/checkout",
        qualified_name="endpoint:POST /api/v1/checkout",
        kind="endpoint",
        file_path="src/pay.ts",
        start_line=18,
        end_line=18,
    )
    fe_db.upsert_node(fe_fn)
    fe_db.upsert_node(fe_endpoint)
    fe_db.upsert_edge(
        Edge(
            from_node_id="fe_pay_btn",
            to_node_id="fe_ep_checkout",
            relation_type="calls",
            call_line=18,
        )
    )

    # Backend DB
    be_db_dir = be_dir / ".vibetrace"
    be_db_dir.mkdir()
    be_db = IndexerDB(str(be_db_dir / "vibetrace.db"))

    be_endpoint = Node(
        id="be_ep_checkout",
        name="POST /api/v1/checkout",
        qualified_name="endpoint:POST /api/v1/checkout",
        kind="endpoint",
        file_path="src/routes.py",
        start_line=10,
        end_line=10,
    )
    be_handler = Node(
        id="be_handler_checkout",
        name="handle_checkout",
        qualified_name="backend.routes.handle_checkout",
        kind="function",
        file_path="src/routes.py",
        start_line=11,
        end_line=45,
    )
    be_db.upsert_node(be_endpoint)
    be_db.upsert_node(be_handler)
    be_db.upsert_edge(
        Edge(
            from_node_id="be_ep_checkout",
            to_node_id="be_handler_checkout",
            relation_type="routes",
            call_line=10,
        )
    )

    ProjectWorkspace.init(project_root, name="ApiSuite", auto_discover=True)
    return project_root


def test_rest_api_project_endpoints(tmp_path: Path):
    """Verify REST API /api/project endpoints."""
    project_root = setup_federated_environment(tmp_path)

    with patch.dict("os.environ", {"VIBETRACE_PROJECT_ROOT": str(project_root)}):
        client = TestClient(app)

        # 1. GET /api/project (status)
        resp_status = client.get("/api/project")
        assert resp_status.status_code == 200
        data_status = resp_status.json()
        assert "manifest" in data_status
        assert data_status["manifest"]["name"] == "ApiSuite"
        assert len(data_status["members"]) == 2

        # 2. GET /api/project/search
        resp_search = client.get("/api/project/search?query=checkout")
        assert resp_search.status_code == 200
        data_search = resp_search.json()
        assert len(data_search) >= 1
        assert any(r["name"] == "handle_checkout" for r in data_search)

        # 3. GET /api/project/guard
        resp_guard = client.get("/api/project/guard?symbol=handle_checkout")
        assert resp_guard.status_code == 200
        data_guard = resp_guard.json()
        assert data_guard["target_symbol"] == "handle_checkout"
        assert data_guard["total_callers"] >= 1
        assert "frontend" in data_guard["callers_by_repo"]

        # 4. GET /api/project/links
        resp_links = client.get("/api/project/links")
        assert resp_links.status_code == 200
        data_links = resp_links.json()
        assert len(data_links) >= 1
        assert data_links[0]["contract_key"] == "POST /api/v1/checkout"


async def test_mcp_project_tools(tmp_path: Path):
    """Verify FastMCP project federation tools."""
    project_root = setup_federated_environment(tmp_path)

    # 1. Status tool
    res_status = await vibetrace_project_status(
        ProjectStatusInput(scope_path=str(project_root), response_format=ResponseFormat.MARKDOWN)
    )
    assert "Federated Project Workspace: `ApiSuite`" in res_status
    assert "frontend" in res_status
    assert "backend" in res_status

    # 2. Search tool
    res_search = await vibetrace_project_search(
        ProjectSearchInput(
            query="checkout",
            scope_path=str(project_root),
            response_format=ResponseFormat.MARKDOWN,
        )
    )
    assert "handle_checkout" in res_search
    assert "[backend]" in res_search

    # 3. Guard tool
    res_guard = await vibetrace_project_guard(
        ProjectGuardInput(
            symbol="handle_checkout",
            scope_path=str(project_root),
            response_format=ResponseFormat.MARKDOWN,
        )
    )
    assert "Cross-Repository Blast Radius Guard: `handle_checkout`" in res_guard
    assert "[frontend]" in res_guard
    assert "onPaymentSubmit" in res_guard

    # 4. Links tool
    res_links = await vibetrace_project_links(
        ProjectLinksInput(scope_path=str(project_root), response_format=ResponseFormat.JSON)
    )
    assert "POST /api/v1/checkout" in res_links
    assert "frontend" in res_links
