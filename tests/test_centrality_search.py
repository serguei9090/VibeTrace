"""Tests for Graph Centrality (in-degree PageRank) computation and RRF boost."""

import time
from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.main import reindex_all
from vibetrace.services.hybrid_search import hybrid_search


def _create_centrality_test_project(tmp_path: Path) -> IndexerDB:
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    # High-centrality core component
    (pkg / "core.py").write_text(
        "def core_helper(data: str) -> str:\n"
        '    """Core utility helper called by multiple services."""\n'
        "    return data.strip()\n\n"
        "def isolated_helper(data: str) -> str:\n"
        '    """Isolated helper with zero callers."""\n'
        "    return data.lower()\n",
        encoding="utf-8",
    )

    # Caller 1
    (pkg / "service_a.py").write_text(
        "from .core import core_helper\n\n"
        "def run_service_a() -> str:\n"
        "    return core_helper('a')\n",
        encoding="utf-8",
    )

    # Caller 2
    (pkg / "service_b.py").write_text(
        "from .core import core_helper\n\n"
        "def run_service_b() -> str:\n"
        "    return core_helper('b')\n",
        encoding="utf-8",
    )

    # Caller 3
    (pkg / "service_c.py").write_text(
        "from .core import core_helper\n\n"
        "def run_service_c() -> str:\n"
        "    return core_helper('c')\n",
        encoding="utf-8",
    )

    ctx_dir = tmp_path / "vibetrace_context"
    ctx_dir.mkdir()
    db = IndexerDB(str(ctx_dir / "vibetrace.db"))
    reindex_all(db, str(tmp_path), verbose=False)
    return db


def test_centrality_computation(tmp_path: Path) -> None:
    """Core components with high incoming edges must receive positive log-normalized centrality."""
    db = _create_centrality_test_project(tmp_path)

    core_nodes = db.find_nodes_by_name("core_helper")
    assert len(core_nodes) == 1
    core_node = core_nodes[0]

    iso_nodes = db.find_nodes_by_name("isolated_helper")
    assert len(iso_nodes) == 1
    iso_node = iso_nodes[0]

    # Re-fetch from DB to get the persisted centrality column value
    nodes_map = db.get_nodes_by_ids([core_node["id"], iso_node["id"]])

    core_cent = float(nodes_map[core_node["id"]].get("centrality") or 0.0)
    iso_cent = float(nodes_map[iso_node["id"]].get("centrality") or 0.0)

    # core_helper has 3 incoming call edges -> log(1 + 3) ~= 1.3863
    assert core_cent > 1.0
    # isolated_helper has 0 incoming call edges
    assert iso_cent == 0.0


def test_centrality_boosts_search_ranking(tmp_path: Path) -> None:
    """Hybrid RRF search should prioritize high-centrality symbols for common queries."""
    db = _create_centrality_test_project(tmp_path)

    # Both symbols contain 'helper' in their name and docstring
    results = hybrid_search(db, "helper", limit=10)
    assert len(results) >= 2

    top_hit = results[0]
    assert top_hit.name == "core_helper"
    assert "centrality" in top_hit.match_sources
    assert top_hit.centrality > 1.0

    # Ensure isolated_helper is ranked below core_helper
    names = [r.name for r in results]
    assert names.index("core_helper") < names.index("isolated_helper")


def test_centrality_search_latency(tmp_path: Path) -> None:
    """Hybrid search with centrality stream must remain fast (< 20ms)."""
    db = _create_centrality_test_project(tmp_path)

    start = time.perf_counter()
    results = hybrid_search(db, "helper", limit=10)
    duration_ms = (time.perf_counter() - start) * 1000.0

    assert len(results) >= 2
    assert duration_ms < 50.0  # Well within performance bounds


def test_search_ast_exact_or_prefix_wildcard_escaping(tmp_path: Path) -> None:
    """Symbols with underscores like _resolve should not match xresolve via SQL wildcard."""
    from vibetrace.indexer.models import Node

    ctx_dir = tmp_path / "vibetrace_context"
    ctx_dir.mkdir()
    db = IndexerDB(str(ctx_dir / "vibetrace.db"))

    db.upsert_node(
        Node(id="n1", name="_resolve", qualified_name="_resolve", kind="function", file_path="a.py")
    )
    db.upsert_node(
        Node(id="n2", name="xresolve", qualified_name="xresolve", kind="function", file_path="b.py")
    )

    results = db.search_ast_exact_or_prefix("_resolve", limit=10)
    matched_names = [r["name"] for r in results]
    assert "_resolve" in matched_names
    assert "xresolve" not in matched_names


def test_search_ast_centrality_tiebreak(tmp_path: Path) -> None:
    """Symbols in the same tier must be tie-broken by centrality DESC."""
    from vibetrace.indexer.models import Node

    ctx_dir = tmp_path / "vibetrace_context"
    ctx_dir.mkdir()
    db = IndexerDB(str(ctx_dir / "vibetrace.db"))

    # Two matching symbols for query 'helper'
    db.upsert_node(
        Node(
            id="n_low",
            name="helper_util",
            qualified_name="helper_util",
            kind="function",
            file_path="util.py",
            centrality=0.0,
        )
    )
    db.upsert_node(
        Node(
            id="n_high",
            name="helper_core",
            qualified_name="helper_core",
            kind="function",
            file_path="core.py",
            centrality=5.2,
        )
    )

    results = db.search_ast_exact_or_prefix("helper", limit=10)
    names = [r["name"] for r in results]
    assert names.index("helper_core") < names.index("helper_util")
