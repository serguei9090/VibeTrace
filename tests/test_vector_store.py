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
