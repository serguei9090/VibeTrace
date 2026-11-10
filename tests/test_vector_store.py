"""Tests for LanceDB vector storage engine and EmbeddingEngine integration."""

from __future__ import annotations

from pathlib import Path

from vibetrace.config import VectorConfig
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.embeddings import EmbeddingConfig, EmbeddingEngine
from vibetrace.indexer.vector_store import LanceDBVectorStore, get_vector_store


def test_lancedb_vector_store_lifecycle(tmp_path: Path):
    """Verify LanceDBVectorStore upserts, searches, and deletes file vectors."""
    store = LanceDBVectorStore(
        uri=tmp_path / "vectors.lance",
        dimensions=256,
        project_name="test-project",
    )
    assert store.is_available() is True
    assert store.has_embeddings() is False
    assert store.count() == 0

    # Upsert batch of vectors
    dim = 256
    vec_a = [0.5] + [0.0] * (dim - 1)
    vec_b = [0.0, 0.5] + [0.0] * (dim - 2)

    records = [
        {
            "node_id": "func_a",
            "vector": vec_a,
            "dimensions": dim,
            "model_id": "test-model",
            "file_path": "src/a.py",
        },
        {
            "node_id": "func_b",
            "vector": vec_b,
            "dimensions": dim,
            "model_id": "test-model",
            "file_path": "src/b.py",
        },
    ]

    added = store.upsert_embeddings(records)
    assert added == 2
    assert store.has_embeddings("test-model") is True
    assert store.count("test-model") == 2

    # Semantic search with query close to vec_a
    query_vec = [0.49] + [0.0] * (dim - 1)
    results = store.search_semantic(query_vec, limit=5, model_id="test-model")
    assert len(results) == 2
    # func_a should rank highest with similarity close to 1.0
    top_node_id, top_score = results[0]
    assert top_node_id == "func_a"
    assert top_score > 0.99

    # Purge vectors for file src/a.py
    store.delete_file_embeddings("src/a.py")
    assert store.count("test-model") == 1
    remaining_results = store.search_semantic(query_vec, limit=5, model_id="test-model")
    assert len(remaining_results) == 1
    assert remaining_results[0][0] == "func_b"


def test_get_vector_store_factory(tmp_path: Path):
    """Factory correctly initializes LanceDBVectorStore relative to project root."""
    cfg = VectorConfig(engine="lancedb", uri="vibetrace_context/vectors.lance", dimensions=256)
    store = get_vector_store(tmp_path, cfg, project_name="factory-test")
    assert store is not None
    assert isinstance(store, LanceDBVectorStore)
    assert Path(store.uri).resolve() == (tmp_path / "vibetrace_context" / "vectors.lance").resolve()


def test_embedding_engine_with_lancedb_integration(tmp_path: Path):
    """EmbeddingEngine transparently routes vector storage and search through LanceDB."""
    dim = 256
    store = LanceDBVectorStore(
        uri=tmp_path / "vectors.lance",
        dimensions=dim,
        project_name="engine-test",
    )

    # Use deterministic mock encoder callable
    def mock_encoder(texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for text in texts:
            val = 0.8 if "auth" in text else 0.1
            vec = [val] + [0.0] * (dim - 1)
            results.append(vec)
        return results

    db = IndexerDB(db_path=str(tmp_path / "test.db"))
    # Seed nodes
    with db._get_conn() as conn:
        conn.execute(
            """
            INSERT INTO nodes (id, name, qualified_name, kind, file_path, start_line, end_line)
            VALUES ('node_auth', 'login', 'AuthService.login', 'function', 'auth.py', 1, 10),
                   ('node_util', 'format', 'Utils.format', 'function', 'util.py', 1, 5)
            """
        )
        conn.commit()

    engine = EmbeddingEngine(
        config=EmbeddingConfig(dimensions=dim, model_id="mock-model"),
        encoder=mock_encoder,
        vector_store=store,
    )

    # Index missing embeddings
    indexed = engine.index_missing_embeddings(db)
    assert indexed == 2
    assert store.count("mock-model") == 2

    # Semantic search through LanceDB
    hits = engine.search_semantic(db, "authenticate user", limit=5)
    assert hits
    assert hits[0][0] == "node_auth"
    assert hits[0][1] > 0.99

    # Purge file embeddings via engine
    engine.delete_file_embeddings("auth.py")
    assert store.count("mock-model") == 1


def test_lancedb_robustness_and_sql_quote_escapes(tmp_path: Path):
    """Verify LanceDBVectorStore handles extraneous fields and single-quote SQL escapes safely."""
    dim = 256
    store = LanceDBVectorStore(
        uri=tmp_path / "vectors_escapes.lance",
        dimensions=dim,
        project_name="escape-test",
    )
    vec = [0.7] + [0.0] * (dim - 1)

    # Records with extraneous fields and quotes in identifiers
    records = [
        {
            "node_id": "user's_func_1",
            "name": "func_1",
            "kind": "function",
            "vector": vec,
            "dimensions": dim,
            "model_id": "quote's_model",
            "file_path": "src/user's_file.py",
            "extraneous_key_1": "should_be_ignored",
            "extraneous_key_2": 12345,
        }
    ]

    added = store.upsert_embeddings(records)
    assert added == 1
    assert store.has_embeddings("quote's_model") is True
    assert store.count("quote's_model") == 1

    # Search with quoted model_id
    results = store.search_semantic(vec, limit=5, model_id="quote's_model")
    assert len(results) == 1
    assert results[0][0] == "user's_func_1"

    # Purge with single quote in path
    store.delete_file_embeddings("src/user's_file.py")
    assert store.count("quote's_model") == 0


def test_vector_store_project_and_model_isolation(tmp_path: Path) -> None:
    """Verify that project_name and model_id isolate counts, upserts, and searches."""
    dim = 256
    vec1 = [0.9] + [0.0] * (dim - 1)
    vec2 = [0.0, 0.9] + [0.0] * (dim - 2)

    store_a = LanceDBVectorStore(
        uri=tmp_path / "shared_vectors.lance",
        dimensions=dim,
        project_name="proj-alpha",
    )
    store_b = LanceDBVectorStore(
        uri=tmp_path / "shared_vectors.lance",
        dimensions=dim,
        project_name="proj-beta",
    )

    # 1. Project A indexes node_1 under model_x
    store_a.upsert_embeddings(
        [
            {
                "node_id": "common_node",
                "name": "foo",
                "kind": "function",
                "vector": vec1,
                "dimensions": dim,
                "model_id": "model_x",
                "file_path": "a.py",
            }
        ]
    )

    # 2. Project B indexes node_1 under model_y
    store_b.upsert_embeddings(
        [
            {
                "node_id": "common_node",
                "name": "foo",
                "kind": "function",
                "vector": vec2,
                "dimensions": dim,
                "model_id": "model_y",
                "file_path": "b.py",
            }
        ]
    )

    # Isolation checks
    assert store_a.count() == 1
    assert store_b.count() == 1
    assert store_a.count(model_id="model_x") == 1
    assert store_a.count(model_id="model_y") == 0
    assert store_b.count(model_id="model_x") == 0
    assert store_b.count(model_id="model_y") == 1

    # Multi-model upsert in Project A: adding model_z must not wipe model_x
    store_a.upsert_embeddings(
        [
            {
                "node_id": "common_node",
                "name": "foo",
                "kind": "function",
                "vector": vec1,
                "dimensions": dim,
                "model_id": "model_z",
                "file_path": "a.py",
            }
        ]
    )
    assert store_a.count() == 2
    assert store_a.count(model_id="model_x") == 1
    assert store_a.count(model_id="model_z") == 1
    # Project B remains untouched
    assert store_b.count() == 1

    # Engine factory validation
    import pytest

    from vibetrace.config import VectorConfig

    cfg_unsupported = VectorConfig(engine="unknown-engine")
    with pytest.raises(ValueError, match="Unsupported vector engine"):
        get_vector_store(tmp_path, cfg_unsupported)

    cfg_sqlite_vec = VectorConfig(engine="sqlite-vec")
    with pytest.raises(NotImplementedError, match="sqlite-vec"):
        get_vector_store(tmp_path, cfg_sqlite_vec)


def test_embeddings_config_and_purge_orphaned(tmp_path: Path):
    """Verify VectorConfig codebase toggle, device resolution, and orphaned vector purging."""
    from unittest.mock import patch

    from vibetrace.config import VectorConfig, VibeTraceConfig
    from vibetrace.indexer.db import IndexerDB, Node
    from vibetrace.indexer.embeddings import EmbeddingConfig, EmbeddingEngine
    from vibetrace.mcp.formatters import format_semantic_search_md
    from vibetrace.services.hybrid_search import SearchResult
    from vibetrace.services.reindex_service import ReindexService

    # 1. Config contract
    vcfg = VectorConfig(codebase=True, device="gpu")
    assert vcfg.codebase is True
    assert vcfg.device == "gpu"

    # 2. EmbeddingEngine device resolution
    eng_gpu = EmbeddingEngine(config=EmbeddingConfig(device="gpu"))
    assert eng_gpu._resolve_device() == "cuda"

    eng_cpu = EmbeddingEngine(config=EmbeddingConfig(device="cpu"))
    assert eng_cpu._resolve_device() == "cpu"

    eng_auto = EmbeddingEngine(config=EmbeddingConfig(device="auto"))
    with patch("importlib.import_module") as mock_import:
        mock_torch = mock_import.return_value
        mock_torch.cuda.is_available.return_value = False
        assert eng_auto._resolve_device() == "cpu"

    # 3. Purge orphaned embeddings in SQLite DB
    db = IndexerDB(str(tmp_path / "test_purge.db"))
    node_alive = Node(
        id="alive_func",
        name="alive_func",
        qualified_name="alive_func",
        kind="function",
        file_path="a.py",
        start_line=1,
        end_line=10,
    )
    db.upsert_node(node_alive)

    # Insert embeddings for both alive_func and a deleted_func
    from vibetrace.indexer.embeddings import pack_vector

    dummy_vec = pack_vector([0.1] * 256)
    db.upsert_embeddings(
        [
            ("alive_func", dummy_vec, 256, "model_test"),
            ("deleted_func", dummy_vec, 256, "model_test"),
        ]
    )

    # Before purge: both exist
    with db._get_conn() as conn:
        assert conn.execute("SELECT count(*) FROM node_embeddings").fetchone()[0] == 2

    # Run purge
    purged = db.purge_orphaned_embeddings()
    assert purged == 1

    # After purge: only alive_func exists
    with db._get_conn() as conn:
        rows = conn.execute("SELECT node_id FROM node_embeddings").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "alive_func"

    # Verify LanceDB orphan purge also removes stale vectors not in SQLite nodes
    lance_store = LanceDBVectorStore(
        uri=tmp_path / "purge_vectors.lance",
        dimensions=256,
        project_name="purge-test",
    )
    lance_store.upsert_embeddings(
        [
            {
                "node_id": "alive_func",
                "vector": [0.1] * 256,
                "dimensions": 256,
                "model_id": "model_test",
                "file_path": "a.py",
            },
            {
                "node_id": "ghost_orphan_func",
                "vector": [0.2] * 256,
                "dimensions": 256,
                "model_id": "model_test",
                "file_path": "ghost.py",
            },
        ]
    )
    assert lance_store.count("model_test") == 2
    db.vector_store = lance_store
    purged_lance = db.purge_orphaned_embeddings()
    assert purged_lance == 1
    assert lance_store.count("model_test") == 1

    # 4. Agent next step recommendations in format_semantic_search_md
    hit = SearchResult(
        node_id="alive_func",
        name="alive_func",
        qualified_name="pkg.module.alive_func",
        file_path="a.py",
        kind="function",
        start_line=1,
        end_line=10,
        signature="def alive_func():",
        docstring="Docstring",
        match_sources=["vector"],
        rrf_score=0.85,
    )
    out_md = format_semantic_search_md("find func", [hit])
    assert "Recommended Next Actions for AI Agent" in out_md
    assert "vibetrace slice pkg.module.alive_func" in out_md
    assert "vibetrace trace pkg.module.alive_func" in out_md

    # 5. ReindexService should_embed resolution
    with patch("vibetrace.services.reindex_service.reindex_all") as mock_reindex:
        mock_reindex.return_value = {}
        with patch.object(db, "prune_orphaned_edges", return_value=0):
            with patch.object(db, "purge_orphaned_embeddings", return_value=0):
                # When embed=False explicitly passed, does not embed
                with patch.object(EmbeddingEngine, "index_missing_embeddings") as mock_index_emb:
                    ReindexService.execute_reindex(
                        db, tmp_path, embed=False, docs=False, verbose=False
                    )
                    mock_index_emb.assert_not_called()

                # When embed=None and config has codebase=True, it embeds
                with patch("vibetrace.config.load_config") as mock_load_cfg:
                    mock_load_cfg.return_value = VibeTraceConfig(
                        vectors=VectorConfig(codebase=True),
                    )
                    with (
                        patch.object(EmbeddingEngine, "is_available", return_value=True),
                        patch.object(
                            EmbeddingEngine, "index_missing_embeddings", return_value=5
                        ) as mock_index_emb,
                    ):
                        res = ReindexService.execute_reindex(
                            db, tmp_path, embed=None, docs=False, verbose=False
                        )
                        mock_index_emb.assert_called_once()
                        assert res.get("embedded_count") == 5
