"""Tests verifying fixes for audit findings in Round 27."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.indexer.main import reindex_all
from vibetrace.mcp.schemas import ReindexInput
from vibetrace.mcp.server import vibetrace_reindex_project
from vibetrace.services.call_tree_service import build_call_tree, format_call_tree_md
from vibetrace.services.hybrid_search import hybrid_search


def test_call_tree_traverses_routes_relation(tmp_path: Path):
    """Verify call tree automatically traverses 'routes' relation from endpoint to handler."""
    db = IndexerDB(str(tmp_path / "test.db"))

    endpoint_node = Node(
        id="endpoint_users",
        name="GET /api/users",
        qualified_name="GET /api/users",
        kind="endpoint",
        file_path="src/routes.py",
        start_line=10,
        end_line=10,
    )
    handler_node = Node(
        id="func_get_users",
        name="get_users",
        qualified_name="routes.get_users",
        kind="function",
        file_path="src/routes.py",
        start_line=12,
        end_line=15,
    )
    db.upsert_node(endpoint_node)
    db.upsert_node(handler_node)

    # Web endpoint route connection
    db.upsert_edge(
        Edge(
            from_node_id="endpoint_users",
            to_node_id="func_get_users",
            relation_type="routes",
            call_line=10,
        )
    )

    res = build_call_tree(db, "GET /api/users", max_depth=2)
    assert res.found is True
    assert res.tree is not None
    assert len(res.tree.children) == 1
    assert res.tree.children[0].name == "get_users"
    assert res.tree.children[0].relation_type == "routes"


def test_format_call_tree_md_external_root(tmp_path: Path):
    """Verify format_call_tree_md formats external root symbols cleanly without :L1."""
    db = IndexerDB(str(tmp_path / "test.db"))
    ext_node = Node(
        id="ext_pkg",
        name="external_lib",
        qualified_name="external_lib",
        kind="module",
        file_path="external",
        start_line=1,
        end_line=1,
    )
    db.upsert_node(ext_node)

    res = build_call_tree(db, "external_lib", max_depth=1)
    md = format_call_tree_md(res)
    assert "(external)" in md
    assert "(external:L1)" not in md


@pytest.mark.asyncio
async def test_mcp_reindex_resolves_relative_path_against_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify vibetrace_reindex_project resolves relative path against root not cwd."""
    project_root = tmp_path / "my_project"
    project_root.mkdir(parents=True)
    target_file = project_root / "src" / "worker.py"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("def work(): pass\n", encoding="utf-8")

    db = IndexerDB(str(project_root / "vibetrace.db"))

    captured_target = []

    def mock_reindex(db_arg, target_path_str, **kwargs):
        captured_target.append(target_path_str)

    monkeypatch.setattr("vibetrace.mcp.server.reindex_all", mock_reindex)
    monkeypatch.setattr(
        "vibetrace.mcp.server._get_context_for_path",
        lambda p, auto_sync=False: (db, project_root),
    )

    # Pass relative path "src/worker.py"
    input_params = ReindexInput(path="src/worker.py")
    res = await vibetrace_reindex_project(input_params)
    assert "Successfully reindexed" in res
    assert len(captured_target) == 1
    assert captured_target[0] == str(target_file.resolve())


def test_single_file_reindex_skips_centrality_on_skip_or_failure(tmp_path: Path):
    """Verify reindex_all skips centrality recalculation when single file is skipped."""
    db = IndexerDB(str(tmp_path / "test.db"))
    db.update_centrality_scores = MagicMock()

    unsupported_file = tmp_path / "data.csv"
    unsupported_file.write_text("a,b,c\n1,2,3\n", encoding="utf-8")

    reindex_all(db, str(unsupported_file), verbose=False, project_root=tmp_path)
    db.update_centrality_scores.assert_not_called()


def test_in_memory_db_path_guard(tmp_path: Path):
    """Verify in-memory SQLite instances skip disk path resolution."""
    db = IndexerDB(":memory:")
    # Should not raise exception or resolve ":memory:" on Windows
    db._resolve_vector_store()
    assert db.vector_store is None

    # Hybrid search on in-memory DB should run without error
    results = hybrid_search(db, "test_query")
    assert results == []
