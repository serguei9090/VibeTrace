"""Tests for 360° consolidated symbol diagnosis meta-endpoint across service, MCP, REST, and CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.main import reindex_all
from vibetrace.mcp.schemas import DiagnoseSymbolInput, ResponseFormat
from vibetrace.mcp.tools_engineering import vibetrace_diagnose_symbol
from vibetrace.server_api import app
from vibetrace.services.diagnose_service import DiagnoseService, format_diagnosis_md


def _create_sample_project(tmp_path: Path) -> IndexerDB:
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    (pkg / "storage.py").write_text(
        "def save_invoice(order_id: str, cents: int) -> bool:\n"
        '    """Persist invoice row to database."""\n'
        "    return True\n",
        encoding="utf-8",
    )

    (pkg / "billing.py").write_text(
        "from .storage import save_invoice\n\n"
        "def charge_customer(order_id: str, amount: int) -> bool:\n"
        '    """Charge card and record invoice."""\n'
        "    return save_invoice(order_id, amount)\n",
        encoding="utf-8",
    )

    (pkg / "test_billing.py").write_text(
        "from .billing import charge_customer\n\n"
        "def test_charge() -> None:\n"
        "    assert charge_customer('order_1', 100)\n",
        encoding="utf-8",
    )

    db_dir = tmp_path / ".vibetrace"
    db_dir.mkdir()
    db = IndexerDB(db_dir / "index.db")
    reindex_all(db, str(tmp_path), verbose=False)
    return db


def test_diagnose_service_existing_symbol(tmp_path: Path) -> None:
    db = _create_sample_project(tmp_path)

    # Diagnose save_invoice
    report = DiagnoseService.diagnose_symbol(
        db=db,
        project_root=tmp_path,
        symbol_name="save_invoice",
        max_depth=2,
        include_code=True,
    )

    assert report.found is True
    assert report.symbol == "save_invoice"
    assert "storage.py" in report.file_path
    assert report.start_line > 0
    assert report.end_line >= report.start_line
    assert len(report.code_slice) > 0
    assert "L1_direct" in report.blast_radius
    assert "charge_customer" in report.blast_radius["L1_direct"]
    assert report.token_count > 0

    # Format markdown
    md = format_diagnosis_md(report)
    assert "# 🔬 360° Symbol Diagnosis: `save_invoice`" in md
    assert "Incoming Call Sites" in md
    assert "Blast Radius" in md


def test_diagnose_service_unknown_symbol(tmp_path: Path) -> None:
    db = _create_sample_project(tmp_path)

    report = DiagnoseService.diagnose_symbol(
        db=db,
        project_root=tmp_path,
        symbol_name="non_existent_symbol_xyz123",
        max_depth=2,
    )

    assert report.found is False
    assert "not found" in report.safety_advisory.lower()
    md = format_diagnosis_md(report)
    assert "❌" in md


def test_mcp_diagnose_symbol(tmp_path: Path) -> None:
    _create_sample_project(tmp_path)

    # 1. Markdown mode
    md_res = vibetrace_diagnose_symbol(
        DiagnoseSymbolInput(
            symbol_name="save_invoice",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.MARKDOWN,
        )
    )
    assert "# 🔬 360° Symbol Diagnosis: `save_invoice`" in md_res

    # 2. JSON mode
    json_res = vibetrace_diagnose_symbol(
        DiagnoseSymbolInput(
            symbol_name="save_invoice",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    data = json.loads(json_res)
    assert data["found"] is True
    assert data["symbol"] == "save_invoice"
    assert "blast_radius" in data


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_rest_api_diagnose_endpoint(client: TestClient) -> None:
    # 1. Existing symbol
    resp = client.get("/api/diagnose?symbol=upsert_node&depth=2")
    if resp.status_code == 404 and "not found" in resp.json().get("detail", "").lower():
        pytest.skip("Index not initialized or symbol missing in test DB.")
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert data["symbol"] == "upsert_node"
    assert "code_slice" in data

    # 2. Non-existent symbol -> 404
    resp_404 = client.get("/api/diagnose?symbol=completely_fictional_fn_99999")
    assert resp_404.status_code == 404


def test_caller_guard_and_federation_wildcard_links(tmp_path: Path) -> None:
    """Verify CallerGuard preserves callers with None call_line and CrossRepoLinker matches ANY."""
    from vibetrace.federation.cross_repo_linker import CrossRepoLinker
    from vibetrace.indexer.models import Edge, Node
    from vibetrace.services.caller_guard import CallerGuardService

    db = IndexerDB(tmp_path / "guard.db")
    db.upsert_node(
        Node(
            id="svc.py::target_fn",
            name="target_fn",
            qualified_name="target_fn",
            kind="function",
            file_path="svc.py",
            start_line=1,
            end_line=5,
        )
    )
    db.upsert_node(
        Node(
            id="api.py::caller_one",
            name="caller_one",
            qualified_name="caller_one",
            kind="function",
            file_path="api.py",
            start_line=1,
            end_line=10,
        )
    )
    db.upsert_node(
        Node(
            id="api.py::caller_two",
            name="caller_two",
            qualified_name="caller_two",
            kind="function",
            file_path="api.py",
            start_line=12,
            end_line=20,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="api.py::caller_one",
            to_node_id="svc.py::target_fn",
            relation_type="calls",
            call_line=None,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="api.py::caller_two",
            to_node_id="svc.py::target_fn",
            relation_type="calls",
            call_line=None,
        )
    )

    guard = CallerGuardService.get_caller_guard(db, tmp_path, "target_fn")
    assert guard.total_callers == 2
    assert {c.caller_name for c in guard.callers} == {"caller_one", "caller_two"}

    # Verify CrossRepoLinker matches client GET /api/orders with server ANY /api/orders
    client_db = IndexerDB(tmp_path / "client.db")
    server_db = IndexerDB(tmp_path / "server.db")
    client_db.upsert_node(
        Node(
            id="c.ts::fetchOrders",
            name="fetchOrders",
            qualified_name="fetchOrders",
            kind="function",
            file_path="c.ts",
            start_line=1,
            end_line=5,
        )
    )
    client_db.upsert_node(
        Node(
            id="c.ts::ep",
            name="GET /api/orders",
            qualified_name="GET /api/orders",
            kind="endpoint",
            file_path="c.ts",
            start_line=2,
            end_line=2,
        )
    )
    client_db.upsert_edge(
        Edge(
            from_node_id="c.ts::fetchOrders",
            to_node_id="c.ts::ep",
            relation_type="calls",
            call_line=2,
        )
    )

    server_db.upsert_node(
        Node(
            id="s.java::ep",
            name="ANY /api/orders",
            qualified_name="ANY /api/orders",
            kind="endpoint",
            file_path="s.java",
            start_line=10,
            end_line=15,
        )
    )
    server_db.upsert_node(
        Node(
            id="s.java::handleOrders",
            name="handleOrders",
            qualified_name="handleOrders",
            kind="method",
            file_path="s.java",
            start_line=10,
            end_line=15,
        )
    )
    server_db.upsert_edge(
        Edge(
            from_node_id="s.java::ep",
            to_node_id="s.java::handleOrders",
            relation_type="routes",
            call_line=10,
        )
    )

    import sqlite3

    fed_conn = sqlite3.connect(":memory:")
    fed_conn.row_factory = sqlite3.Row
    fed_conn.execute("ATTACH DATABASE ? AS repo_client", (str(tmp_path / "client.db"),))
    fed_conn.execute("ATTACH DATABASE ? AS repo_server", (str(tmp_path / "server.db"),))
    linker = CrossRepoLinker(fed_conn, {"client": "repo_client", "server": "repo_server"})
    links = linker.discover_links()
    assert len(links) == 1
    assert links[0].source_symbol == "fetchOrders"
    assert links[0].target_symbol == "handleOrders"
    fed_conn.close()
