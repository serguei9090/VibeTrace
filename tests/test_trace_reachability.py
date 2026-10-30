from fastapi.testclient import TestClient

from vibetrace.indexer.db import IndexerDB
from vibetrace.server_api import app
from vibetrace.services.trace_service import (
    get_downstream_reachable_symbols,
    trace_execution_path,
)

client = TestClient(app)


def test_get_downstream_reachable_symbols():
    db = IndexerDB()
    reachable = get_downstream_reachable_symbols(db, "main")
    assert isinstance(reachable, list)
    assert len(reachable) > 0
    # main connects to IndexerDB downstream
    assert "IndexerDB" in reachable
    assert "reindex_all" in reachable


def test_trace_execution_path_with_splits():
    db = IndexerDB()
    res = trace_execution_path(db, "main", "find_nodes_by_name", detect_splits=True)
    assert res.found is True
    assert len(res.hops) >= 3
    assert len(res.split_points) > 0

    # Ensure split points have expected data
    split_names = [sp.symbol_name for sp in res.split_points]
    assert "main" in split_names or "get_context_for_path" in split_names
    for sp in res.split_points:
        assert sp.total_outgoing_branches >= 2
        assert len(sp.branch_names) > 0


def test_api_reachable_endpoint():
    resp = client.get("/api/reachable?from_symbol=main")
    assert resp.status_code == 200
    data = resp.json()
    assert data["from_symbol"] == "main"
    assert data["count"] > 0
    assert "IndexerDB" in data["reachable_symbols"]


def test_api_trace_detect_splits_param():
    resp = client.get("/api/trace?from_symbol=main&to_symbol=find_nodes_by_name&detect_splits=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert "split_points" in data
    assert len(data["split_points"]) > 0
