"""Tests for Graph Invariants: Disambiguation & Cycle Detection (Audit Round 46)."""

from __future__ import annotations

from pathlib import Path

from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.services.call_tree_service import build_call_tree
from vibetrace.services.graph_service import build_call_stack, compute_impact_analysis
from vibetrace.services.trace_service import trace_execution_path


def test_tracepath_disambiguation_with_identical_symbol_names(tmp_path: Path) -> None:
    """Verify trace_execution_path disambiguates identical symbol names across different files."""
    db_file = tmp_path / "disambig_test.db"
    db = IndexerDB(str(db_file))

    # Worker A in file_a.py
    node_a = Node(
        id="file_a.py:Worker.run:10",
        name="run",
        qualified_name="Worker.run",
        kind="function",
        file_path="src/file_a.py",
        start_line=10,
        end_line=15,
    )
    # Target A in file_a.py
    target_a = Node(
        id="file_a.py:save:20",
        name="save",
        qualified_name="save",
        kind="function",
        file_path="src/file_a.py",
        start_line=20,
        end_line=25,
    )

    # Worker B in file_b.py
    node_b = Node(
        id="file_b.py:Worker.run:10",
        name="run",
        qualified_name="Worker.run",
        kind="function",
        file_path="src/file_b.py",
        start_line=10,
        end_line=15,
    )
    # Target B in file_b.py
    target_b = Node(
        id="file_b.py:save:20",
        name="save",
        qualified_name="save",
        kind="function",
        file_path="src/file_b.py",
        start_line=20,
        end_line=25,
    )

    db.upsert_node(node_a)
    db.upsert_node(target_a)
    db.upsert_node(node_b)
    db.upsert_node(target_b)

    # Connect A -> target_a, B -> target_b
    db.upsert_edge(
        Edge(
            from_node_id=node_a.id,
            to_node_id=target_a.id,
            relation_type="calls",
            call_line=12,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id=node_b.id,
            to_node_id=target_b.id,
            relation_type="calls",
            call_line=12,
        )
    )

    # Trace specifically from file_b to file_b
    res_b = trace_execution_path(
        db,
        "run",
        "save",
        from_file="src/file_b.py",
        to_file="src/file_b.py",
    )
    assert res_b.found is True
    assert len(res_b.hops) == 1
    assert res_b.hops[0].from_node.file_path == "src/file_b.py"
    assert res_b.hops[0].to_node.file_path == "src/file_b.py"

    # Trace specifically from file_a to file_a
    res_a = trace_execution_path(
        db,
        "run",
        "save",
        from_file="src/file_a.py",
        to_file="src/file_a.py",
    )
    assert res_a.found is True
    assert len(res_a.hops) == 1
    assert res_a.hops[0].from_node.file_path == "src/file_a.py"
    assert res_a.hops[0].to_node.file_path == "src/file_a.py"


def test_mutual_recursion_cycle_detection_in_call_tree_and_call_stack(tmp_path: Path) -> None:
    """Verify mutual recursion (A -> B -> A) does not loop infinitely and flags cycles."""
    db_file = tmp_path / "cycle_test.db"
    db = IndexerDB(str(db_file))

    node_ping = Node(
        id="srv.py:ping:5",
        name="ping",
        qualified_name="ping",
        kind="function",
        file_path="src/srv.py",
        start_line=5,
        end_line=10,
    )
    node_pong = Node(
        id="srv.py:pong:15",
        name="pong",
        qualified_name="pong",
        kind="function",
        file_path="src/srv.py",
        start_line=15,
        end_line=20,
    )
    db.upsert_node(node_ping)
    db.upsert_node(node_pong)

    # Cycle: ping -> pong -> ping
    db.upsert_edge(
        Edge(
            from_node_id=node_ping.id,
            to_node_id=node_pong.id,
            relation_type="calls",
            call_line=8,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id=node_pong.id,
            to_node_id=node_ping.id,
            relation_type="calls",
            call_line=18,
        )
    )

    # 1. Call tree cycle detection
    tree_res = build_call_tree(db, "ping", max_depth=5)
    assert tree_res.found is True
    assert tree_res.tree is not None
    # ping -> pong -> ping(cycle=True)
    assert len(tree_res.tree.children) == 1
    child_pong = tree_res.tree.children[0]
    assert child_pong.name == "pong"
    assert len(child_pong.children) == 1
    grandchild_ping = child_pong.children[0]
    assert grandchild_ping.name == "ping"
    assert grandchild_ping.is_cycle is True
    # Verify traversal terminated cleanly at cycle boundary
    assert len(grandchild_ping.children) == 0

    # 2. Call stack cycle detection
    found, output, _nodes = build_call_stack(db, "ping", direction="down", max_depth=5)
    assert found is True
    assert any("CYCLE DETECTED" in line for line in output)


def test_blast_radius_impact_on_empty_and_leaf_files(tmp_path: Path) -> None:
    """Verify impact analysis handles unlinked files, empty files, and leaf nodes gracefully."""
    db_file = tmp_path / "impact_test.db"
    db = IndexerDB(str(db_file))

    # A standalone utility file with no incoming dependents
    leaf_node = Node(
        id="utils.py:format_time:1",
        name="format_time",
        qualified_name="format_time",
        kind="function",
        file_path="src/utils.py",
        start_line=1,
        end_line=5,
    )
    db.upsert_node(leaf_node)

    # 1. Leaf node file with zero incoming callers
    total_impact, impacts = compute_impact_analysis(db, "src/utils.py")
    assert total_impact == 0
    assert impacts == {1: [], 2: [], 3: []}

    # 2. Completely unindexed / non-existent file
    total_impact_none, impacts_none = compute_impact_analysis(db, "src/nonexistent.py")
    assert total_impact_none == 0
    assert impacts_none == {}
