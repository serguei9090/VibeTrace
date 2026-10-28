"""Tests for Audit Round 53: 100% Vibecoding Superpowers (Caller Guard & Vibe Bundle)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.mcp.schemas import CallerGuardInput, ResponseFormat, VibeBundleInput
from vibetrace.mcp.server import vibetrace_check_caller_guard, vibetrace_get_vibe_bundle
from vibetrace.server_api import app
from vibetrace.services.caller_guard import (
    CallerGuardService,
    format_caller_guard_md,
)
from vibetrace.services.vibe_bundle import (
    VibeBundleService,
    estimate_tokens,
    truncate_to_tokens,
)


@pytest.fixture
def sample_db(tmp_path: Path) -> tuple[IndexerDB, Path]:
    """Create a temporary populated DB with nodes, edges, and test files."""
    db_file = tmp_path / "test_vibetrace.db"
    db = IndexerDB(db_path=str(db_file))

    # Target node: calculate_tax in src/billing.py
    src_file = tmp_path / "src" / "billing.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text(
        "def calculate_tax(amount: float, rate: float = 0.2) -> float:\n    return amount * rate\n",
        encoding="utf-8",
    )

    node_target = Node(
        id="billing:calculate_tax",
        name="calculate_tax",
        qualified_name="calculate_tax",
        kind="function",
        file_path="src/billing.py",
        start_line=1,
        end_line=2,
        signature="(amount: float, rate: float = 0.2) -> float",
        docstring="Compute tax given amount and rate.",
    )
    db.upsert_node(node_target)

    # Caller 1: checkout in src/checkout.py
    check_file = tmp_path / "src" / "checkout.py"
    check_file.write_text(
        "from billing import calculate_tax\n"
        "def checkout(cart):\n"
        "    tax = calculate_tax(cart.total, 0.15)\n"
        "    return cart.total + tax\n",
        encoding="utf-8",
    )
    node_caller1 = Node(
        id="checkout:checkout",
        name="checkout",
        qualified_name="checkout",
        kind="function",
        file_path="src/checkout.py",
        start_line=2,
        end_line=4,
        signature="(cart)",
    )
    db.upsert_node(node_caller1)
    db.upsert_edge(
        Edge(
            from_node_id="checkout:checkout",
            to_node_id="billing:calculate_tax",
            relation_type="calls",
            call_line=3,
        )
    )

    # Caller 2: test_calculate_tax in tests/test_billing.py
    test_file = tmp_path / "tests" / "test_billing.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "from billing import calculate_tax\n"
        "def test_calculate_tax():\n"
        "    assert calculate_tax(100.0, 0.2) == 20.0\n",
        encoding="utf-8",
    )
    node_caller2 = Node(
        id="test_billing:test_calculate_tax",
        name="test_calculate_tax",
        qualified_name="test_calculate_tax",
        kind="function",
        file_path="tests/test_billing.py",
        start_line=2,
        end_line=3,
        signature="()",
    )
    db.upsert_node(node_caller2)
    db.upsert_edge(
        Edge(
            from_node_id="test_billing:test_calculate_tax",
            to_node_id="billing:calculate_tax",
            relation_type="calls",
            call_line=3,
        )
    )

    return db, tmp_path


def test_caller_guard_service_detection(sample_db: tuple[IndexerDB, Path]) -> None:
    db, root = sample_db
    report = CallerGuardService.get_caller_guard(db, root, "calculate_tax")
    assert report.found is True
    assert report.symbol == "calculate_tax"
    assert report.file_path == "src/billing.py"
    assert report.total_callers == 2
    assert "GUARD ALERT" in report.safety_advisory

    # Verify both callers captured with accurate snippets
    caller_names = {c.caller_name for c in report.callers}
    assert "checkout" in caller_names
    assert "test_calculate_tax" in caller_names

    md = format_caller_guard_md(report)
    assert "## Caller Guard Report: `calculate_tax`" in md
    assert "checkout.py:L3" in md


def test_caller_guard_not_found(sample_db: tuple[IndexerDB, Path]) -> None:
    db, root = sample_db
    report = CallerGuardService.get_caller_guard(db, root, "non_existent_func")
    assert report.found is False
    assert "was not found" in report.safety_advisory
    md = format_caller_guard_md(report)
    assert "was not found" in md


def test_vibe_bundle_service_assembly(sample_db: tuple[IndexerDB, Path]) -> None:
    db, root = sample_db
    bundle = VibeBundleService.assemble_bundle(
        db,
        root,
        intent="calculate tax rate calculation",
        target_symbol="calculate_tax",
        token_budget=1000,
    )
    assert bundle.intent == "calculate tax rate calculation"
    assert bundle.target_symbol == "calculate_tax"
    assert bundle.estimated_tokens <= 1000
    assert bundle.caller_guard is not None
    assert bundle.caller_guard.total_callers == 2
    assert bundle.code_slice is not None
    assert "calculate_tax" in bundle.code_slice.source_code

    # Verify sibling test pattern was discovered from AST caller
    assert bundle.test_context is not None
    assert bundle.test_context.found is True
    assert bundle.test_context.test_file == "tests/test_billing.py"
    assert bundle.test_context.test_function == "test_calculate_tax"

    prompt = bundle.formatted_prompt
    assert "# Vibe Bundle:" in prompt
    assert "Pre-Flight Caller Guard" in prompt
    assert "Target Code Implementation" in prompt
    assert "Sibling Test Pattern" in prompt


def test_vibe_bundle_token_truncation() -> None:
    huge_text = "word " * 2000
    assert estimate_tokens(huge_text) > 1000
    truncated = truncate_to_tokens(huge_text, 200)
    assert estimate_tokens(truncated) <= 250
    assert "... [truncated to fit token budget]" in truncated


@pytest.mark.asyncio
async def test_mcp_caller_guard_and_vibe_bundle(sample_db: tuple[IndexerDB, Path]) -> None:
    db, root = sample_db
    with patch("vibetrace.mcp.server.get_context_for_path", return_value=(db, root)):
        # 1. Caller Guard Markdown
        res_guard_md = await vibetrace_check_caller_guard(
            CallerGuardInput(symbol_name="calculate_tax", response_format=ResponseFormat.MARKDOWN)
        )
        assert "Caller Guard Report: `calculate_tax`" in res_guard_md
        assert "GUARD ALERT" in res_guard_md

        # 2. Caller Guard JSON
        res_guard_json = await vibetrace_check_caller_guard(
            CallerGuardInput(symbol_name="calculate_tax", response_format=ResponseFormat.JSON)
        )
        assert '"found": true' in res_guard_json
        assert '"total_callers": 2' in res_guard_json

        # 3. Vibe Bundle Markdown
        res_bundle_md = await vibetrace_get_vibe_bundle(
            VibeBundleInput(
                intent="calculate tax rate calculation",
                target_symbol="calculate_tax",
                response_format=ResponseFormat.MARKDOWN,
            )
        )
        assert "# Vibe Bundle:" in res_bundle_md

        # 4. Vibe Bundle JSON
        res_bundle_json = await vibetrace_get_vibe_bundle(
            VibeBundleInput(
                intent="calculate tax rate calculation",
                target_symbol="calculate_tax",
                response_format=ResponseFormat.JSON,
            )
        )
        assert '"token_budget": 1500' in res_bundle_json


def test_rest_api_vibe_guard_and_bundle(
    sample_db: tuple[IndexerDB, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, root = sample_db
    monkeypatch.setattr("vibetrace.server_api.db", db)
    monkeypatch.setenv("VIBETRACE_PROJECT_ROOT", str(root))

    client = TestClient(app)

    # 1. GET /api/vibe/guard
    res_guard = client.get("/api/vibe/guard?symbol=calculate_tax")
    assert res_guard.status_code == 200
    data_guard = res_guard.json()
    assert data_guard["found"] is True
    assert data_guard["total_callers"] == 2
    assert "calculate_tax" in data_guard["symbol"]

    # 2. GET /api/vibe/guard (not found)
    res_missing = client.get("/api/vibe/guard?symbol=non_existent_symbol_abc")
    assert res_missing.status_code == 404

    # 3. GET /api/vibe/bundle
    res_bundle = client.get(
        "/api/vibe/bundle?intent=calculate+tax+logic&symbol=calculate_tax&budget=1200"
    )
    assert res_bundle.status_code == 200
    data_bundle = res_bundle.json()
    assert data_bundle["token_budget"] == 1200
    assert data_bundle["caller_guard"]["found"] is True
    assert "formatted_prompt" in data_bundle


def test_cli_guard_and_bundle(
    sample_db: tuple[IndexerDB, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, root = sample_db
    from vibetrace.cli import main

    # 1. CLI Guard Human Mode
    code_guard = main(["guard", "calculate_tax", "--root", str(root)])
    assert code_guard == 0
    out_guard, _ = capsys.readouterr()
    assert "Caller Guard Report: `calculate_tax`" in out_guard
    assert "checkout.py:L3" in out_guard

    # 2. CLI Guard JSON Mode
    code_guard_json = main(["guard", "calculate_tax", "--json", "--root", str(root)])
    assert code_guard_json == 0
    out_guard_json, _ = capsys.readouterr()
    data = json.loads(out_guard_json)
    assert data["found"] is True
    assert data["total_callers"] == 2

    # 3. CLI Bundle Human Mode
    code_bundle = main(
        ["bundle", "tax calculation", "--symbol", "calculate_tax", "--root", str(root)]
    )
    assert code_bundle == 0
    out_bundle, _ = capsys.readouterr()
    assert "# Vibe Bundle:" in out_bundle
    assert "Pre-Flight Caller Guard" in out_bundle

    # 4. CLI Bundle JSON Mode
    code_bundle_json = main(
        [
            "bundle",
            "tax calculation",
            "--symbol",
            "calculate_tax",
            "--budget",
            "1200",
            "--json",
            "--root",
            str(root),
        ]
    )
    assert code_bundle_json == 0
    out_bundle_json, _ = capsys.readouterr()
    bundle_data = json.loads(out_bundle_json)
    assert bundle_data["token_budget"] == 1200
    assert bundle_data["caller_guard"]["found"] is True
