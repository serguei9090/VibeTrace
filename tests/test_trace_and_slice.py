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


def test_cte_trace_execution_path_preserves_metadata(tmp_path: Path):
    """CTE trace path finding should preserve authentic relation_type and call_line metadata."""
    db = _create_sample_project(tmp_path)

    trace = trace_execution_path(db, "process_checkout", "save_invoice", max_depth=6, engine="cte")
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


def test_trace_execution_path_pure_cycle_graceful_empty(tmp_path: Path):
    """When only cyclic paths exist between frontier bridges,

    tracepath gracefully returns found=False instead of crashing.
    """
    from vibegraph.indexer.models import Edge, Node

    db = IndexerDB(str(tmp_path / "cycle_test.db"))
    nodes = [
        Node(
            id="A",
            name="func_a",
            qualified_name="func_a",
            kind="function",
            file_path="app.py",
            start_line=1,
            end_line=5,
        ),
        Node(
            id="B",
            name="func_b",
            qualified_name="func_b",
            kind="function",
            file_path="app.py",
            start_line=6,
            end_line=10,
        ),
        Node(
            id="C",
            name="func_c",
            qualified_name="func_c",
            kind="function",
            file_path="app.py",
            start_line=11,
            end_line=15,
        ),
    ]
    edges = [
        Edge(from_node_id="A", to_node_id="B", relation_type="calls", call_line=3),
        Edge(from_node_id="B", to_node_id="A", relation_type="calls", call_line=8),
    ]
    db.batch_save_file_index("app.py", "hash1", nodes, edges)

    trace = trace_execution_path(db, "func_a", "func_c", max_depth=4)
    assert trace.found is False
    assert trace.hops == []


def test_graph_traverser_diamond_dag_no_false_cycle(tmp_path: Path):
    """Diamond DAG (A -> B -> D and A -> C -> D) must NOT flag D as circular dependency."""
    from vibegraph.indexer.models import Edge, Node
    from vibegraph.services.graph_service import GraphTraverser

    db = IndexerDB(str(tmp_path / "diamond_test.db"))
    nodes = [
        Node(
            id="A",
            name="entry",
            qualified_name="entry",
            kind="function",
            file_path="app.py",
            start_line=1,
            end_line=2,
        ),
        Node(
            id="B",
            name="svc_b",
            qualified_name="svc_b",
            kind="function",
            file_path="app.py",
            start_line=3,
            end_line=4,
        ),
        Node(
            id="C",
            name="svc_c",
            qualified_name="svc_c",
            kind="function",
            file_path="app.py",
            start_line=5,
            end_line=6,
        ),
        Node(
            id="D",
            name="common_util",
            qualified_name="common_util",
            kind="function",
            file_path="app.py",
            start_line=7,
            end_line=8,
        ),
    ]
    edges = [
        Edge(from_node_id="A", to_node_id="B", relation_type="calls", call_line=2),
        Edge(from_node_id="A", to_node_id="C", relation_type="calls", call_line=2),
        Edge(from_node_id="B", to_node_id="D", relation_type="calls", call_line=4),
        Edge(from_node_id="C", to_node_id="D", relation_type="calls", call_line=6),
    ]
    db.batch_save_file_index("app.py", "hash1", nodes, edges)

    traverser = GraphTraverser(db)
    traverser.traverse("A", 1, 3, "down", 0, path_stack=["entry"])
    full_output = "\n".join(traverser.output)

    assert "circular dependency" not in full_output
    assert "common_util" in full_output
