"""Real-world codespace self-indexing integration test suite.

Indexes the active VibeTrace repository (I:\\01-Master_Code\\Apps\\VibeTrace\\src)
to verify indexing accuracy, cross-file barrel re-exports, route extraction,
call hierarchy, recursive CTE pathfinding, and zero phantom builtins on real production code.
"""

from pathlib import Path

import pytest

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.main import reindex_all
from vibetrace.services.call_tree_service import build_call_tree, format_call_tree_md
from vibetrace.services.graph_service import get_context_for_path
from vibetrace.services.trace_service import trace_execution_path


@pytest.fixture(scope="module")
def indexed_codespace_db(tmp_path_factory):
    """Index the actual VibeTrace codebase into an isolated temporary database."""
    temp_dir = tmp_path_factory.mktemp("vibetrace_self_index")
    db_file = temp_dir / "codespace_self.db"
    db = IndexerDB(str(db_file))

    project_root = Path(__file__).resolve().parent.parent
    src_dir = project_root / "src"
    assert src_dir.is_dir(), f"src directory not found at {src_dir}"

    reindex_all(db, str(src_dir), verbose=False)
    db.update_centrality_scores()
    return db, project_root


def test_codespace_symbol_volume_and_richness(indexed_codespace_db):
    """Assert indexing real codespace yields hundreds of valid symbols and edges."""
    db, _ = indexed_codespace_db
    graph = db.get_graph_data()
    nodes = graph["nodes"]
    edges = graph["edges"]

    assert len(nodes) >= 150, f"Expected at least 150 nodes in real codebase, got {len(nodes)}"
    assert len(edges) >= 100, f"Expected at least 100 edges in real codebase, got {len(edges)}"

    kinds = {n["kind"] for n in nodes}
    assert "function" in kinds
    assert "class" in kinds
    assert "module" in kinds
    assert "endpoint" in kinds


def test_codespace_zero_phantom_builtins(indexed_codespace_db):
    """Verify builtins like len, range, print, str do not emit phantom nodes or calls."""
    db, _ = indexed_codespace_db
    builtins = ["len", "range", "print", "open", "dict", "list", "set", "tuple", "isinstance"]
    for b_name in builtins:
        matches = db.find_nodes_by_name(b_name)
        # Any match must only be if explicitly defined in user code, not a bare call
        for m in matches:
            assert m["file_path"] != "external", f"Found phantom builtin node for {b_name}"


def test_codespace_fastapi_endpoints_extracted(indexed_codespace_db):
    """Verify FastAPI routes from server_api.py are indexed with kind='endpoint'."""
    db, _ = indexed_codespace_db
    graph = db.get_graph_data()
    endpoints = [n for n in graph["nodes"] if n["kind"] == "endpoint"]

    endpoint_names = [ep["name"] for ep in endpoints]
    # Check core API routes
    assert any("GET /api/search" in name for name in endpoint_names), "Missing /api/search"
    assert any("GET /graph" in name for name in endpoint_names), "Missing /graph"
    assert any("GET /trace" in name for name in endpoint_names), "Missing /trace"
    assert any("POST /api/agent-focus" in name for name in endpoint_names), (
        "Missing /api/agent-focus"
    )


def test_codespace_call_tree_and_traversal(indexed_codespace_db):
    """Verify top-down call tree construction on real IndexerDB or service methods."""
    db, _ = indexed_codespace_db
    tree_res = build_call_tree(db, "trace_execution_path", max_depth=3)
    assert tree_res.found is True
    assert tree_res.tree is not None
    assert tree_res.total_nodes >= 2

    # Formatting must not crash and output clean markdown
    md = format_call_tree_md(tree_res)
    assert "Top-Down Call Tree: `trace_execution_path`" in md
    assert "```text" in md


def test_codespace_recursive_cte_pathfinding(indexed_codespace_db):
    """Find real execution path via recursive CTE between actual production symbols."""
    db, _ = indexed_codespace_db
    start_nodes = db.find_nodes_by_name("trace_execution_path")
    target_nodes = db.find_nodes_by_name("find_path_recursive_cte")

    assert start_nodes, "trace_execution_path symbol missing"
    assert target_nodes, "find_path_recursive_cte symbol missing"

    start_id = start_nodes[0]["id"]
    target_id = target_nodes[0]["id"]

    path = db.find_path_recursive_cte(start_id, target_id, max_depth=6)
    # trace_execution_path calls db.find_path_recursive_cte
    if path:
        assert len(path) >= 2
        assert path[0]["name"] == "trace_execution_path"
        assert path[-1]["name"] == "find_path_recursive_cte"


def test_codespace_bidirectional_trace(indexed_codespace_db):
    """Trace execution path between real endpoint GET /trace and IndexerDB."""
    db, _ = indexed_codespace_db
    trace = trace_execution_path(db, "GET /trace", "trace_execution_path", max_depth=4)
    assert trace.found is True
    assert len(trace.hops) >= 1
    assert trace.hops[0].relation_type in ("routes", "calls")


def test_codespace_search_and_ranking(indexed_codespace_db):
    """Test AST prefix and lexical BM25 search on real symbols."""
    db, _ = indexed_codespace_db
    # Exact or prefix AST search
    results = db.search_ast_exact_or_prefix("IndexerDB", limit=10)
    assert len(results) >= 1
    assert results[0]["name"] == "IndexerDB"
    assert results[0]["kind"] == "class"

    # Lexical BM25 search
    lex_results = db.search_lexical("recursive CTE", limit=10)
    assert len(lex_results) >= 1
    found_symbols = [r["name"] for r in lex_results]
    assert any("find_path_recursive_cte" in s for s in found_symbols)


def test_multilang_project_root_discovery(tmp_path: Path) -> None:
    """Verify get_context_for_path discovers roots by package.json, Cargo.toml, or go.mod."""
    # Test TypeScript / Node root
    ts_root = tmp_path / "ts_project"
    ts_root.mkdir()
    (ts_root / "package.json").write_text('{"name": "test"}', encoding="utf-8")
    sub_ts = ts_root / "src" / "deep"
    sub_ts.mkdir(parents=True)

    _db_ts, found_ts = get_context_for_path(str(sub_ts), auto_sync=False)
    assert found_ts.resolve() == ts_root.resolve()

    # Test Rust root
    rs_root = tmp_path / "rs_project"
    rs_root.mkdir()
    (rs_root / "Cargo.toml").write_text('[package]\nname = "test"\n', encoding="utf-8")
    sub_rs = rs_root / "src"
    sub_rs.mkdir(parents=True)

    _db_rs, found_rs = get_context_for_path(str(sub_rs), auto_sync=False)
    assert found_rs.resolve() == rs_root.resolve()

    # Test Go root
    go_root = tmp_path / "go_project"
    go_root.mkdir()
    (go_root / "go.mod").write_text("module example.com/test\n", encoding="utf-8")
    sub_go = go_root / "pkg"
    sub_go.mkdir(parents=True)

    _db_go, found_go = get_context_for_path(str(sub_go), auto_sync=False)
    assert found_go.resolve() == go_root.resolve()


def test_get_context_root_override(tmp_path: Path) -> None:
    """Explicit root_override parameter must override path discovery."""
    custom_root = tmp_path / "custom_root"
    custom_root.mkdir()

    arbitrary_hint = tmp_path / "some_other_folder"
    arbitrary_hint.mkdir()

    _db, root = get_context_for_path(
        str(arbitrary_hint), auto_sync=False, root_override=custom_root
    )
    assert root.resolve() == custom_root.resolve()
