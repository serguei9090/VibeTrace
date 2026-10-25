"""Unit and integration tests for Round 30 audit fixes:
- Signature pattern wildcard wrapping in IndexerDB.search_by_signature_pattern
- CLI search --sig / --signature flag
- REST API POST /api/reindex
- REST API GET /api/search/signature
- REST API GET /call-stack and /api/call-stack
- FastAPI lifespan db.close() cleanup
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from vibetrace.cli import main as cli_main
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Node
from vibetrace.server_api import app, lifespan
from vibetrace.services.graph_service import build_call_stack


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test.db"
    db_inst = IndexerDB(str(db_file))
    yield db_inst
    db_inst.close()


def test_signature_search_wildcard_wrapping(test_db: IndexerDB):
    test_db.batch_save_file_index(
        file_path="src/service.py",
        content_hash="hash1",
        nodes=[
            Node(
                id="n1",
                name="handle_req",
                qualified_name="handle_req",
                parent_id=None,
                file_path="src/service.py",
                kind="function",
                start_line=1,
                end_line=10,
                signature="(db: IndexerDB, key: str) -> bool",
            )
        ],
        edges=[],
    )

    # Bare substring without % should automatically match
    res_bare = test_db.search_by_signature_pattern("IndexerDB")
    assert len(res_bare) == 1
    assert res_bare[0]["name"] == "handle_req"

    # Explicit SQL wildcard should also work
    res_wildcard = test_db.search_by_signature_pattern("%key: str%")
    assert len(res_wildcard) == 1

    # Non-matching pattern
    res_none = test_db.search_by_signature_pattern("float")
    assert len(res_none) == 0


def test_cli_search_with_signature_flag(capsys: pytest.CaptureFixture[str]):
    # Test CLI search with --sig and --json
    code = cli_main(["search", "IndexerDB", "--sig", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert isinstance(results, list)

    # Test CLI search with --sig and human markdown output
    code_text = cli_main(["search", "IndexerDB", "--sig"])
    assert code_text == 0
    captured_text = capsys.readouterr()
    assert "## Signature Search:" in captured_text.out


def test_api_search_signature_endpoint():
    client = TestClient(app)
    res = client.get("/api/search/signature", params={"pattern": "IndexerDB", "limit": 10})
    assert res.status_code == 200
    results = res.json()
    assert isinstance(results, list)


def test_api_reindex_endpoint(tmp_path: Path):
    client = TestClient(app)

    # Test single-file 404 for non-existent file
    res_404 = client.post("/api/reindex", json={"file_path": "non_existent_file.py"})
    assert res_404.status_code == 404

    # Test single-file reindex on existing file
    res_file = client.post("/api/reindex", json={"file_path": "src/vibetrace/cli.py"})
    assert res_file.status_code == 200
    payload = res_file.json()
    assert payload["status"] == "ok"
    assert payload["mode"] == "single_file"

    # Test workspace reindex
    res_ws = client.post("/api/reindex", json={})
    assert res_ws.status_code == 200
    assert res_ws.json()["status"] == "ok"


def test_api_call_stack_endpoint():
    client = TestClient(app)

    # Test valid symbol call stack (both directions)
    res = client.get(
        "/call-stack", params={"symbol": "upsert_node", "direction": "both", "depth": 2}
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["found"] is True
    assert payload["symbol"] == "upsert_node"
    assert len(payload["call_stack"]) > 0

    # Test call-stack via /api/call-stack alias
    res_alias = client.get(
        "/api/call-stack", params={"symbol": "upsert_node", "direction": "up", "depth": 1}
    )
    assert res_alias.status_code == 200
    assert res_alias.json()["direction"] == "up"

    # Test missing symbol returns 404
    res_404 = client.get("/call-stack", params={"symbol": "completely_ghost_node"})
    assert res_404.status_code == 404


def test_build_call_stack_service(test_db: IndexerDB):
    test_db.batch_save_file_index(
        file_path="src/flow.py",
        content_hash="hash_flow",
        nodes=[
            Node(
                id="a",
                name="entrypoint",
                qualified_name="entrypoint",
                parent_id=None,
                file_path="src/flow.py",
                kind="function",
                start_line=1,
                end_line=5,
            )
        ],
        edges=[],
    )

    found, output, nodes = build_call_stack(test_db, "entrypoint", direction="both", max_depth=2)
    assert found is True
    assert len(nodes) == 1
    assert any("entrypoint" in line for line in output)


@pytest.mark.asyncio
async def test_lifespan_closes_db():
    from vibetrace import server_api

    mock_db = MagicMock()
    orig_db = server_api.db
    server_api.db = mock_db
    try:
        async with lifespan(server_api.app):
            pass
        mock_db.close.assert_called_once()
    finally:
        server_api.db = orig_db
