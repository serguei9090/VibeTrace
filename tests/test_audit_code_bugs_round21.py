"""Round 21 regression tests: vector store auto-purging and server_api path normalization."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from vibetrace.indexer.db import IndexerDB
from vibetrace.server_api import app


def test_db_clear_file_purges_vector_store(tmp_path: Path):
    """Verify IndexerDB.clear_file automatically calls delete_file_embeddings on vector_store."""
    mock_store = MagicMock()
    mock_store.is_available.return_value = True

    db = IndexerDB(db_path=str(tmp_path / "test.db"), vector_store=mock_store)
    db.clear_file("src/deleted.py")

    mock_store.delete_file_embeddings.assert_called_once_with("src/deleted.py")


def test_db_batch_save_file_index_purges_vector_store(tmp_path: Path):
    """Verify IndexerDB.batch_save_file_index purges old vector records for re-indexed files."""
    mock_store = MagicMock()
    mock_store.is_available.return_value = True

    db = IndexerDB(db_path=str(tmp_path / "test.db"), vector_store=mock_store)
    # Call batch_save_file_index with empty nodes to verify purge hook runs
    db.batch_save_file_index("src/updated.py", "hash123", [], [])

    mock_store.delete_file_embeddings.assert_called_once_with("src/updated.py")


def test_server_api_source_with_absolute_and_unnormalized_path(tmp_path: Path):
    """Verify /source endpoint resolves both relative and absolute paths for indexed files."""
    test_file = tmp_path / "sample.py"
    test_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    from vibetrace import server_api

    old_root = server_api.project_root
    old_db = server_api.db
    try:
        server_api.project_root = tmp_path
        server_api.db = IndexerDB(db_path=str(tmp_path / "server_test.db"))
        # Seed indexed file
        with server_api.db._get_conn() as conn:
            conn.execute(
                "INSERT INTO indexed_files (file_path, content_hash, updated_at) VALUES (?, ?, ?)",
                ("sample.py", "hash1", 100.0),
            )
            conn.commit()

        client = TestClient(app)

        # 1. Relative path query
        res_rel = client.get("/source", params={"file_path": "./sample.py"})
        assert res_rel.status_code == 200
        assert res_rel.json()["file_path"] == "sample.py"
        assert "def hello():" in res_rel.json()["source_code"]

        # 2. Absolute path query
        res_abs = client.get("/source", params={"file_path": str(test_file)})
        assert res_abs.status_code == 200
        assert res_abs.json()["file_path"] == "sample.py"
        assert "def hello():" in res_abs.json()["source_code"]

        # 3. Graph query with absolute path
        res_graph = client.get("/graph", params={"file_path": str(test_file)})
        assert res_graph.status_code == 200
        assert "nodes" in res_graph.json()
    finally:
        server_api.project_root = old_root
        server_api.db = old_db
