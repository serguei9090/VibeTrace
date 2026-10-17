"""Tests for native SQLite recursive CTE execution path finding."""

from __future__ import annotations

from pathlib import Path

from vibegraph.indexer.db import Edge, IndexerDB, Node


def _make_node(node_id: str, name: str) -> Node:
    return Node(
        id=node_id,
        file_path="src/main.py",
        name=name,
        qualified_name=f"main.{name}",
        parent_id=None,
        kind="function",
        start_line=1,
        end_line=5,
        signature=f"def {name}():",
        docstring="",
        decorators=[],
        visibility="public",
    )


def test_recursive_cte_linear_path(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    nodes = [_make_node(f"n_{i}", f"func_{i}") for i in range(4)]
    for n in nodes:
        db.upsert_node(n)

    # n_0 -> n_1 -> n_2 -> n_3
    edges = [
        Edge(from_node_id="n_0", to_node_id="n_1", relation_type="calls", call_line=2),
        Edge(from_node_id="n_1", to_node_id="n_2", relation_type="calls", call_line=3),
        Edge(from_node_id="n_2", to_node_id="n_3", relation_type="calls", call_line=4),
    ]
    for e in edges:
        db.upsert_edge(e)

    path = db.find_path_recursive_cte("n_0", "n_3")
    assert path is not None
    assert [n["id"] for n in path] == ["n_0", "n_1", "n_2", "n_3"]


def test_recursive_cte_cycle_and_branch(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    nodes = [_make_node(f"n_{i}", f"func_{i}") for i in range(5)]
    for n in nodes:
        db.upsert_node(n)

    # n_0 -> n_1 -> n_2 -> n_0 (cycle)
    # n_1 -> n_4 (branch to target)
    edges = [
        Edge(from_node_id="n_0", to_node_id="n_1", relation_type="calls", call_line=2),
        Edge(from_node_id="n_1", to_node_id="n_2", relation_type="calls", call_line=3),
        Edge(from_node_id="n_2", to_node_id="n_0", relation_type="calls", call_line=4),
        Edge(from_node_id="n_1", to_node_id="n_4", relation_type="calls", call_line=5),
    ]
    for e in edges:
        db.upsert_edge(e)

    path = db.find_path_recursive_cte("n_0", "n_4")
    assert path is not None
    assert [n["id"] for n in path] == ["n_0", "n_1", "n_4"]


def test_recursive_cte_depth_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    nodes = [_make_node(f"n_{i}", f"func_{i}") for i in range(4)]
    for n in nodes:
        db.upsert_node(n)

    edges = [
        Edge(from_node_id="n_0", to_node_id="n_1", relation_type="calls", call_line=2),
        Edge(from_node_id="n_1", to_node_id="n_2", relation_type="calls", call_line=3),
        Edge(from_node_id="n_2", to_node_id="n_3", relation_type="calls", call_line=4),
    ]
    for e in edges:
        db.upsert_edge(e)

    # Distance is 3 edges, max_depth is 2 -> should return None
    path = db.find_path_recursive_cte("n_0", "n_3", max_depth=2)
    assert path is None

    # max_depth 3 -> finds path
    path = db.find_path_recursive_cte("n_0", "n_3", max_depth=3)
    assert path is not None
    assert len(path) == 4


def test_recursive_cte_unreachable_and_same_node(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    nodes = [_make_node("n_0", "func_0"), _make_node("n_1", "func_1")]
    for n in nodes:
        db.upsert_node(n)

    # Disconnected nodes
    assert db.find_path_recursive_cte("n_0", "n_1") is None

    # Same node
    same_path = db.find_path_recursive_cte("n_0", "n_0")
    assert same_path is not None
    assert len(same_path) == 1
    assert same_path[0]["id"] == "n_0"
