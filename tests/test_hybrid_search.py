"""Tests for Hybrid RRF Search (AST + SQLite FTS5 BM25 + EmbeddingGemma 2 256d MRL)."""

from pathlib import Path

from vibegraph.indexer.db import IndexerDB, Node
from vibegraph.indexer.embeddings import (
    EmbeddingConfig,
    EmbeddingEngine,
    build_symbol_document,
    truncate_and_normalize,
    unpack_vector,
)
from vibegraph.services.hybrid_search import hybrid_search


def _populate_sample_db(tmp_path: Path) -> IndexerDB:
    db = IndexerDB(str(tmp_path / "hybrid_test.db"))
    db.upsert_node(
        Node(
            id="n1",
            name="authenticate_jwt",
            qualified_name="AuthService.authenticate_jwt",
            kind="function",
            file_path="src/auth/jwt.py",
            start_line=10,
            end_line=35,
            signature="(token: str) -> UserClaims",
            docstring="Verify RS256 bearer token signature and expiration timestamp.",
        )
    )
    db.upsert_node(
        Node(
            id="n2",
            name="connect_pool",
            qualified_name="DatabasePool.connect_pool",
            kind="function",
            file_path="src/db/pool.py",
            start_line=5,
            end_line=28,
            signature="(dsn: str, max_conns: int = 10)",
            docstring="Initialize asynchronous SQLite connection pool with WAL pragma.",
        )
    )
    db.upsert_node(
        Node(
            id="n3",
            name="dispatch_webhook",
            qualified_name="WebhookWorker.dispatch_webhook",
            kind="function",
            file_path="src/workers/webhook.py",
            start_line=40,
            end_line=88,
            signature="(url: str, payload: dict)",
            docstring="Send signed HTTP POST event notification with exponential backoff retry.",
        )
    )
    return db


def test_matryoshka_truncation_and_l2_normalization():
    """Vectors longer than 256d should be truncated to 256d and L2-normalized to unit length."""
    raw_768 = [float(i + 1) for i in range(768)]
    mrl_256 = truncate_and_normalize(raw_768, dimensions=256)

    assert len(mrl_256) == 256
    norm_sq = sum(x * x for x in mrl_256)
    assert abs(norm_sq - 1.0) < 1e-6


def test_hybrid_search_ast_and_fts5_fallback(tmp_path: Path):
    """When embeddings are not indexed, hybrid_search should rank via AST + FTS5 BM25."""
    db = _populate_sample_db(tmp_path)

    # Exact / prefix symbol query -> n1 should be #1 with both 'ast' and 'fts5'
    results = hybrid_search(db, "AuthService.authenticate_jwt", limit=5)
    assert len(results) >= 1
    assert results[0].node_id == "n1"
    assert "ast" in results[0].match_sources
    assert "fts5" in results[0].match_sources

    # Lexical docstring query -> n3 matches "exponential backoff retry" via FTS5
    doc_results = hybrid_search(db, "exponential backoff retry", limit=5)
    assert len(doc_results) >= 1
    assert doc_results[0].node_id == "n3"
    assert "fts5" in doc_results[0].match_sources


def test_embedding_engine_and_three_stream_rrf(tmp_path: Path):
    """Verify 256d MRL embedding indexing and 3-stream RRF ranking."""
    db = _populate_sample_db(tmp_path)

    # Deterministic keyword-sensitive test encoder returning 384d vectors truncated to 256d
    def fake_gemma_encoder(texts: list[str]) -> list[list[float]]:
        vecs: list[list[float]] = []
        for t in texts:
            lower = t.lower()
            v = [0.01] * 384
            if any(k in lower for k in ("jwt", "bearer", "credential", "login", "auth")):
                v[0] = 10.0
            if any(k in lower for k in ("sqlite", "pool", "database", "wal")):
                v[1] = 10.0
            if any(k in lower for k in ("webhook", "backoff", "http")):
                v[2] = 10.0
            vecs.append(v)
        return vecs

    config = EmbeddingConfig(model_id="google/embeddinggemma-2", dimensions=256, batch_size=2)
    engine = EmbeddingEngine(config=config, encoder=fake_gemma_encoder)

    # Index all 3 symbols
    indexed_count = engine.index_missing_embeddings(db)
    assert indexed_count == 3
    # Second call should find 0 missing embeddings
    assert engine.index_missing_embeddings(db) == 0

    stored = db.get_all_embeddings(config.model_id)
    assert len(stored) == 3
    for _nid, blob, dims in stored:
        assert dims == 256
        unpacked = unpack_vector(blob, 256)
        assert len(unpacked) == 256

    # Query using a purely semantic synonym ("user login credential verification")
    # that does NOT appear verbatim in name/docstring, plus verify 3-stream RRF fusion
    sem_results = hybrid_search(db, "user login credential", limit=3, embedding_engine=engine)
    assert len(sem_results) == 3
    assert sem_results[0].node_id == "n1"
    assert "semantic" in sem_results[0].match_sources

    # Query matching all 3 streams ("authenticate_jwt bearer")
    all_stream_results = hybrid_search(db, "authenticate_jwt", limit=3, embedding_engine=engine)
    assert all_stream_results[0].node_id == "n1"
    assert set(all_stream_results[0].match_sources) == {"ast", "fts5", "semantic"}

    doc_str = build_symbol_document(all_stream_results[0].model_dump())
    assert "AuthService.authenticate_jwt" in doc_str


def test_search_semantic_zero_and_negative_limit(tmp_path: Path):
    """search_semantic should safely return empty list when limit <= 0 without IndexError."""
    db = _populate_sample_db(tmp_path)
    config = EmbeddingConfig(model_id="test-model", dimensions=64)
    engine = EmbeddingEngine(config=config, encoder=lambda texts: [[0.5] * 64 for _ in texts])
    engine.index_missing_embeddings(db)

    assert engine.search_semantic(db, "query", limit=0) == []
    assert engine.search_semantic(db, "query", limit=-1) == []


def test_node_embeddings_index_query_plan(tmp_path: Path):
    """Verify composite index on node_embeddings exists and is selected by SQLite planner."""
    db = _populate_sample_db(tmp_path)
    with db._get_conn() as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'node_embeddings'"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        assert "idx_node_embeddings_model_id" in indexes
        assert "idx_node_embeddings_model_node" in indexes

        # Verify query plan uses index for model_id lookups
        plan_sql = (
            "EXPLAIN QUERY PLAN SELECT node_id, embedding, dimensions "
            "FROM node_embeddings WHERE model_id = ?"
        )
        plan_cursor = conn.execute(plan_sql, ("test-model",))
        plan_rows = [row[3] for row in plan_cursor.fetchall()]
        assert any("idx_node_embeddings" in line for line in plan_rows)
