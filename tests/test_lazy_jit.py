"""Unit tests for Lazy JIT (Just-In-Time) query synchronization."""

from __future__ import annotations

import subprocess
from pathlib import Path

from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.git_utils import get_git_sha
from vibegraph.indexer.main import reindex_all
from vibegraph.services.graph_service import ensure_index_fresh


def test_lazy_jit_uncommitted_changes(tmp_path: Path) -> None:
    """Verify dirty/uncommitted file edits are auto-indexed JIT before query execution."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    # Git init
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "jit@test.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "JIT User"], cwd=repo_dir, check=True)

    calc_file = repo_dir / "calc.py"
    calc_file.write_text("def add(a, b):\n    return a + b\n")

    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init calc"], cwd=repo_dir, check=True)

    context_dir = repo_dir / "vibegraph_context"
    context_dir.mkdir(exist_ok=True)
    db = IndexerDB(str(context_dir / "vibegraph.db"))

    # Initial index
    reindex_all(db, str(repo_dir), verbose=False, project_root=repo_dir)
    assert get_git_sha(repo_dir) == db.get_meta("last_indexed_git_sha")

    # Initial query confirms add exists, subtract does not
    res_add = db.search_lexical("add")
    assert any(r["name"] == "add" for r in res_add)
    assert not any(r["name"] == "subtract" for r in db.search_lexical("subtract"))

    # Developer or AI agent edits calc.py WITHOUT committing
    calc_file.write_text(
        "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n"
    )

    # Query via get_context_for_path (default auto_sync=True, debounce=0 for test)
    ensure_index_fresh(db, repo_dir, debounce_seconds=0.0)

    # Now subtract is automatically indexed on the fly!
    res_sub = db.search_lexical("subtract")
    assert any(r["name"] == "subtract" for r in res_sub)


def test_incremental_inbound_edge_preservation(tmp_path: Path) -> None:
    """Verify modifying a callee file preserves existing inbound calls from caller files."""
    from vibegraph.indexer.db import Edge, Node

    db_path = str(tmp_path / "edge_preservation.db")
    db = IndexerDB(db_path)

    # 1. Index b.py with helper()
    node_b = Node(
        id="b_helper",
        name="helper",
        qualified_name="helper",
        kind="function",
        file_path="b.py",
        start_line=1,
        end_line=2,
    )
    db.batch_save_file_index("b.py", "hash_b_v1", [node_b], [])

    # 2. Index a.py with main() calling helper()
    node_a = Node(
        id="a_main",
        name="main",
        qualified_name="main",
        kind="function",
        file_path="a.py",
        start_line=1,
        end_line=2,
    )
    edge_ab = Edge(from_node_id="a_main", to_node_id="b_helper", relation_type="calls", call_line=2)
    db.batch_save_file_index("a.py", "hash_a_v1", [node_a], [edge_ab])

    graph_v1 = db.get_graph_data()
    assert len(graph_v1["edges"]) == 1

    # 3. Edit b.py without touching a.py (helper still exists, internal logic modified)
    db.batch_save_file_index("b.py", "hash_b_v2", [node_b], [])
    graph_v2 = db.get_graph_data()
    assert len(graph_v2["edges"]) == 1, "Inbound edge from a.py into b.py was wiped on reindex!"

    # 4. Now modify b.py and delete helper (replaced with other)
    node_b_other = Node(
        id="b_other",
        name="other",
        qualified_name="other",
        kind="function",
        file_path="b.py",
        start_line=1,
        end_line=2,
    )
    db.batch_save_file_index("b.py", "hash_b_v3", [node_b_other], [])
    graph_v3 = db.get_graph_data()
    assert len(graph_v3["edges"]) == 0, "Orphaned edge to deleted helper was not pruned!"
