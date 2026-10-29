"""Tests for Round 60: REST API endpoint completeness and modular router verification.

1. GET /api/vibe/guard?staged=true&strict=true returns structured strict verification.
2. GET /api/dependencies?reverse=true returns inverted dependents and circularity checks.
3. GET /api/call-stack?symbol=target_fn&all_relations=true passes relation filter flag.
4. POST /api/reindex triggers doc catalog synchronization when docs=True.
5. GET /api/skill and POST /api/skill manage vibetrace-navigator skill configuration.
6. /api/saved-traces/* CRUD and run endpoints handled via modular router.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.server_api import app


@pytest.fixture
def test_workspace(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    caller = Node(
        id="mod_caller",
        name="caller_fn",
        qualified_name="caller_fn",
        kind="function",
        file_path="src/caller.py",
        start_line=10,
        end_line=20,
    )
    target = Node(
        id="mod_target",
        name="target_fn",
        qualified_name="target_fn",
        kind="function",
        file_path="src/target.py",
        start_line=30,
        end_line=40,
    )
    container = Node(
        id="mod_container",
        name="src.target",
        qualified_name="src.target",
        kind="module",
        file_path="src/target.py",
        start_line=1,
        end_line=50,
    )
    db.upsert_node(caller)
    db.upsert_node(target)
    db.upsert_node(container)

    db.upsert_edge(
        Edge(
            from_node_id="mod_caller",
            to_node_id="mod_target",
            relation_type="calls",
            call_line=15,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="mod_container",
            to_node_id="mod_target",
            relation_type="defines",
            call_line=None,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="mod_caller",
            to_node_id="mod_target",
            relation_type="imports",
            call_line=1,
        )
    )
    return db, tmp_path


def test_guard_strict_endpoint(test_workspace: tuple[IndexerDB, Path]):
    """Verify /api/vibe/guard?staged=true&strict=true returns structured strict status."""
    db, root = test_workspace
    with (
        patch("vibetrace.server_api.db", db),
        patch("vibetrace.server_api.project_root", root),
    ):
        client = TestClient(app)

        # 1. Staged without strict: returns plain list
        resp = client.get("/api/vibe/guard?staged=true")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

        # 2. Staged with strict=true: returns strict payload with violation status
        resp_strict = client.get("/api/vibe/guard?staged=true&strict=true")
        assert resp_strict.status_code == 200
        data = resp_strict.json()
        assert isinstance(data, dict)
        assert data["staged"] is True
        assert data["strict"] is True
        assert "violation" in data
        assert "reports" in data
        assert isinstance(data["reports"], list)


def test_dependencies_reverse_endpoint(test_workspace: tuple[IndexerDB, Path]):
    """Verify /api/dependencies?file_path=...&reverse=true returns inverted dependents."""
    db, root = test_workspace
    with (
        patch("vibetrace.server_api.db", db),
        patch("vibetrace.server_api.project_root", root),
    ):
        client = TestClient(app)

        # 1. Default forward dependencies
        resp_fwd = client.get("/api/dependencies?file_path=src/target.py")
        assert resp_fwd.status_code == 200
        fwd_data = resp_fwd.json()
        assert "internal" in fwd_data
        assert "counts" in fwd_data

        # 2. Reverse dependencies
        resp_rev = client.get("/api/dependencies?file_path=src/target.py&reverse=true")
        assert resp_rev.status_code == 200
        rev_data = resp_rev.json()
        assert rev_data["reverse"] is True
        assert "dependents" in rev_data
        assert any(d["file_path"] == "src/caller.py" for d in rev_data["dependents"])
        assert "circular" in rev_data


def test_call_stack_all_relations(test_workspace: tuple[IndexerDB, Path]):
    """Verify /api/call-stack accepts all_relations parameter."""
    db, root = test_workspace
    with (
        patch("vibetrace.server_api.db", db),
        patch("vibetrace.server_api.project_root", root),
    ):
        client = TestClient(app)

        resp = client.get("/api/call-stack?symbol=target_fn&all_relations=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "symbol" in data
        assert data["symbol"] == "target_fn"
        assert "call_stack" in data


def test_reindex_with_docs(test_workspace: tuple[IndexerDB, Path]):
    """Verify /api/reindex supports docs synchronization flag."""
    db, root = test_workspace

    mock_catalog = MagicMock()
    mock_catalog.name = "core"
    mock_catalog.scope = "local"
    mock_catalog.paths = ["docs"]

    with (
        patch("vibetrace.server_api.db", db),
        patch("vibetrace.server_api.project_root", root),
        patch("vibetrace.indexer.main.reindex_all"),
        patch(
            "vibetrace.docs.service.DocCatalogManager.list_catalogs", return_value=[mock_catalog]
        ),
        patch(
            "vibetrace.docs.service.DocIndexService.index_catalog",
            return_value={"files": 2, "chunks": 5},
        ) as mock_doc_idx,
    ):
        client = TestClient(app)
        resp = client.post("/api/reindex", json={"force": True, "docs": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "docs" in data
        assert data["docs"]["catalogs"] == 1
        assert data["docs"]["files"] == 2
        assert data["docs"]["chunks"] == 5
        mock_doc_idx.assert_called_once()


def test_skill_endpoints(tmp_path: Path):
    """Verify /api/skill GET and POST endpoints."""
    with patch("vibetrace.server_api.project_root", tmp_path):
        client = TestClient(app)

        # 1. GET /api/skill (initially not installed)
        resp_get = client.get("/api/skill")
        assert resp_get.status_code == 200
        get_data = resp_get.json()
        assert "installed" in get_data
        assert "paths" in get_data
        assert get_data["installed"] is False

        # 2. POST /api/skill (installs into project root)
        resp_post = client.post("/api/skill?global_install=false")
        assert resp_post.status_code == 200
        post_data = resp_post.json()
        assert post_data["status"] == "ok"
        assert len(post_data["installed_paths"]) >= 1

        # 3. GET /api/skill after install
        resp_after = client.get("/api/skill")
        assert resp_after.status_code == 200
        assert resp_after.json()["installed"] is True


def test_saved_traces_router(test_workspace: tuple[IndexerDB, Path]):
    """Verify saved-traces endpoints mounted from modular router."""
    db, root = test_workspace
    with (
        patch("vibetrace.server_api.db", db),
        patch("vibetrace.server_api.project_root", root),
    ):
        client = TestClient(app)

        # 1. List saved traces (empty initially)
        resp_list = client.get("/api/saved-traces")
        assert resp_list.status_code == 200
        assert isinstance(resp_list.json(), list)

        # 2. Create a saved trace
        payload = {
            "name": "TestTrace1",
            "description": "Verification trace",
            "from_symbol": "caller_fn",
            "to_symbol": "target_fn",
            "max_depth": 5,
        }
        resp_create = client.post("/api/saved-traces", json=payload)
        assert resp_create.status_code == 200
        created = resp_create.json()
        assert created["name"] == "TestTrace1"
        trace_id = created["id"]

        # 3. Retrieve saved trace by id
        resp_get = client.get(f"/api/saved-traces/{trace_id}")
        assert resp_get.status_code == 200
        assert resp_get.json()["id"] == trace_id

        # 4. Run saved trace
        resp_run = client.get("/api/saved-traces/TestTrace1/run")
        assert resp_run.status_code == 200
        run_data = resp_run.json()
        assert "saved_trace" in run_data
        assert "trace_result" in run_data

        # 5. Delete trace by id
        resp_del = client.delete(f"/api/saved-traces/{trace_id}")
        assert resp_del.status_code == 200
        assert resp_del.json()["status"] == "deleted"

        # 6. Verify deleted
        resp_list_after = client.get("/api/saved-traces")
        assert len(resp_list_after.json()) == 0
