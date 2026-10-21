"""Unit and integration tests for saved architectural trace queries."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from vibetrace.indexer.db import IndexerDB
from vibetrace.services.saved_traces_service import (
    SavedTraceCreate,
    create_saved_trace,
    delete_saved_trace,
    get_saved_trace_by_name,
    list_saved_traces,
)


def test_saved_traces_crud_service(tmp_path: Path) -> None:
    """Verify CRUD lifecycle in saved_traces_service."""
    db_path = str(tmp_path / "test_traces.db")
    db = IndexerDB(db_path)

    # Initially empty
    assert len(list_saved_traces(db)) == 0

    # Create trace
    payload = SavedTraceCreate(
        name="AuthToDatabase",
        description="Path from login handler to user SQL repository",
        from_symbol="login",
        to_symbol="query_user",
        max_depth=5,
    )
    saved = create_saved_trace(db, payload)
    assert saved.id > 0
    assert saved.name == "AuthToDatabase"
    assert saved.from_symbol == "login"
    assert saved.to_symbol == "query_user"
    assert saved.max_depth == 5

    # List
    all_traces = list_saved_traces(db)
    assert len(all_traces) == 1
    assert all_traces[0].name == "AuthToDatabase"

    # Get by name
    found = get_saved_trace_by_name(db, "AuthToDatabase")
    assert found is not None
    assert found.id == saved.id

    # Update on conflict
    payload_updated = SavedTraceCreate(
        name="AuthToDatabase",
        description="Updated description",
        from_symbol="login",
        to_symbol="query_user_v2",
        max_depth=8,
    )
    updated = create_saved_trace(db, payload_updated)
    assert updated.id == saved.id
    assert updated.to_symbol == "query_user_v2"
    assert updated.max_depth == 8

    # Delete
    deleted = delete_saved_trace(db, saved.id)
    assert deleted is True
    assert len(list_saved_traces(db)) == 0
    assert delete_saved_trace(db, 9999) is False


def test_saved_traces_api_endpoints() -> None:
    """Verify REST endpoints for saved traces in server_api."""
    from vibetrace.server_api import app

    client = TestClient(app)

    # 1. Create a saved trace via POST
    create_res = client.post(
        "/api/saved-traces",
        json={
            "name": "ApiToGraph",
            "description": "Trace from GET /graph to get_graph",
            "from_symbol": "GET /graph",
            "to_symbol": "get_graph",
            "max_depth": 4,
        },
    )
    assert create_res.status_code == 200
    trace_data = create_res.json()
    assert trace_data["name"] == "ApiToGraph"
    trace_id = trace_data["id"]

    # 2. List saved traces via GET
    list_res = client.get("/api/saved-traces")
    assert list_res.status_code == 200
    traces = list_res.json()
    assert any(t["name"] == "ApiToGraph" for t in traces)

    # 3. Run saved trace via GET /api/saved-traces/{name}/run
    run_res = client.get("/api/saved-traces/ApiToGraph/run")
    assert run_res.status_code == 200
    run_data = run_res.json()
    assert "saved_trace" in run_data
    assert "trace_result" in run_data

    # 4. Delete saved trace
    del_res = client.delete(f"/api/saved-traces/{trace_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"

    # 404 for deleted
    not_found_res = client.get("/api/saved-traces/ApiToGraph/run")
    assert not_found_res.status_code == 404
