"""Tests for Bidirectional BFS trace_execution_path and surgical get_code_slice."""

from pathlib import Path

import pytest

from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.main import reindex_all
from vibegraph.services.graph_service import (
    get_code_slice,
    read_file_lines,
    trace_execution_path,
)


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

    (pkg / "orders.py").write_text(
        "from .billing import charge_customer\n\n"
        "def process_checkout(cart_id: str) -> bool:\n"
        '    """Entry point for checkout workflow."""\n'
        "    return charge_customer(cart_id, 4999)\n\n"
        "def unrelated_helper() -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )

    db = IndexerDB(str(tmp_path / "trace_test.db"))
    reindex_all(db, str(tmp_path), verbose=False)
    return db


def test_bidirectional_bfs_trace_execution_path(tmp_path: Path):
    """Bidirectional BFS should find the 2-hop execution path with exact call_line metadata."""
    db = _create_sample_project(tmp_path)

    trace = trace_execution_path(db, "process_checkout", "save_invoice", max_depth=6)
    assert trace.found is True
    assert len(trace.hops) == 2

    hop1, hop2 = trace.hops
    assert hop1.step_index == 1
    assert hop1.from_node.name == "process_checkout"
    assert hop1.to_node.name == "charge_customer"
    assert hop1.relation_type == "calls"
    assert hop1.call_line == 5

    assert hop2.step_index == 2
    assert hop2.from_node.name == "charge_customer"
    assert hop2.to_node.name == "save_invoice"
    assert hop2.relation_type == "calls"
    assert hop2.call_line == 5

    assert len(trace.node_ids) == 3


def test_trace_execution_path_unconnected(tmp_path: Path):
    """Unconnected symbols should return found=False."""
    db = _create_sample_project(tmp_path)

    trace = trace_execution_path(db, "unrelated_helper", "save_invoice", max_depth=6)
    assert trace.found is False
    assert trace.hops == []


def test_get_code_slice_and_callee_contracts(tmp_path: Path):
    """get_code_slice should return numbered source lines and direct callee contracts."""
    db = _create_sample_project(tmp_path)

    slice_res = get_code_slice(db, tmp_path, "process_checkout", include_callees=True)
    assert slice_res.found is True
    assert slice_res.file_path == "app/orders.py"
    assert slice_res.start_line == 3
    assert slice_res.end_line == 5
    assert "3: def process_checkout(cart_id: str) -> bool:" in slice_res.source_code
    assert "5:     return charge_customer(cart_id, 4999)" in slice_res.source_code
    assert "unrelated_helper" not in slice_res.source_code

    assert len(slice_res.callees) == 1
    callee = slice_res.callees[0]
    assert callee.name == "charge_customer"
    assert callee.file_path == "app/billing.py"
    assert callee.call_line == 5
    assert "(order_id: str, amount: int)" in (callee.signature or "")


def test_read_file_lines_blocks_path_traversal(tmp_path: Path):
    """read_file_lines must reject paths escaping the project root."""
    with pytest.raises(ValueError, match="Path traversal blocked"):
        read_file_lines(tmp_path, "../outside_secret.py", 1, 10)
