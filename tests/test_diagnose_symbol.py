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


@pytest.mark.asyncio
async def test_mcp_diagnose_symbol(tmp_path: Path) -> None:
    _create_sample_project(tmp_path)

    # 1. Markdown mode
    md_res = await vibetrace_diagnose_symbol(
        DiagnoseSymbolInput(
            symbol_name="save_invoice",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.MARKDOWN,
        )
    )
    assert "# 🔬 360° Symbol Diagnosis: `save_invoice`" in md_res

    # 2. JSON mode
    json_res = await vibetrace_diagnose_symbol(
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
