"""Hybrid search service combining AST, SQLite FTS5 BM25, and EmbeddingGemma 2 via RRF."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.embeddings import EmbeddingEngine


class SearchResult(BaseModel):
    """Typed output contract for a hybrid search hit."""

    node_id: str
    name: str
    qualified_name: str
    kind: str
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    signature: str | None = None
    docstring: str | None = None
    rrf_score: float = Field(description="Reciprocal Rank Fusion combined score.")
    match_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Search streams surfacing this symbol ('ast', 'fts5', 'semantic', 'centrality')"
        ),
    )
    centrality: float = Field(
        default=0.0,
        description="Graph in-degree log-normalized centrality score.",
    )


def hybrid_search(
    db: IndexerDB,
    query: str,
    limit: int = 15,
    embedding_engine: EmbeddingEngine | None = None,
    rrf_k: int = 60,
) -> list[SearchResult]:
    """Execute 4-stream Reciprocal Rank Fusion over AST, FTS5, Semantic, and Graph Centrality.

    Formula:
        RRF_Score(d) = sum_{s in Streams} (w_s / (rrf_k + rank_s(d)))
    where rank_s(d) is the 1-based rank of symbol d in stream s.
    """
    clean_query = query.strip()
    if not clean_query or limit <= 0:
        return []

    pool_limit = max(limit * 2, 30)

    # Stream weights
    weight_ast = 1.2
    weight_fts5 = 1.0
    weight_semantic = 1.1
    weight_centrality = 0.8

    scores: dict[str, float] = {}
    sources: dict[str, list[str]] = {}
    node_cache: dict[str, dict[str, Any]] = {}

    def _accumulate(node_id: str, rank_1based: int, weight: float, source_tag: str) -> None:
        scores[node_id] = scores.get(node_id, 0.0) + (weight / (rrf_k + rank_1based))
        src_list = sources.setdefault(node_id, [])
        if source_tag not in src_list:
            src_list.append(source_tag)

    # Stream 1: Exact / Prefix / Substring AST symbol & signature match
    ast_hits = db.search_ast_exact_or_prefix(clean_query, limit=pool_limit)
    for idx, row in enumerate(ast_hits, start=1):
        nid = row["id"]
        node_cache[nid] = row
        _accumulate(nid, idx, weight_ast, "ast")

    # Stream 2: SQLite FTS5 BM25 lexical search
    fts_hits = db.search_lexical(clean_query, limit=pool_limit)
    for idx, row in enumerate(fts_hits, start=1):
        nid = row["id"]
        node_cache.setdefault(nid, row)
        _accumulate(nid, idx, weight_fts5, "fts5")

    # Stream 3: Local EmbeddingGemma 2 semantic cosine search (optional / graceful fallback)
    engine = embedding_engine or EmbeddingEngine()
    semantic_hits = engine.search_semantic(db, clean_query, limit=pool_limit)
    if semantic_hits:
        missing_ids = [nid for nid, _ in semantic_hits if nid not in node_cache]
        if missing_ids:
            node_cache.update(db.get_nodes_by_ids(missing_ids))

        for idx, (nid, _sim) in enumerate(semantic_hits, start=1):
            if nid in node_cache:
                _accumulate(nid, idx, weight_semantic, "semantic")

    # Stream 4: Graph Centrality boost for candidate pool
    centrality_candidates = [
        (nid, float(node_cache[nid].get("centrality") or 0.0))
        for nid in node_cache
        if float(node_cache[nid].get("centrality") or 0.0) > 0.0
    ]
    centrality_candidates.sort(key=lambda x: x[1], reverse=True)
    for idx, (nid, _cent) in enumerate(centrality_candidates, start=1):
        _accumulate(nid, idx, weight_centrality, "centrality")

    # Sort combined hits by descending RRF score
    ranked_ids = sorted(scores.keys(), key=lambda nid: scores[nid], reverse=True)[:limit]

    results: list[SearchResult] = []
    for nid in ranked_ids:
        row = node_cache.get(nid)
        if not row:
            continue
        results.append(
            SearchResult(
                node_id=nid,
                name=row["name"],
                qualified_name=row.get("qualified_name") or row["name"],
                kind=row.get("kind") or "function",
                file_path=row["file_path"],
                start_line=row.get("start_line"),
                end_line=row.get("end_line"),
                signature=row.get("signature"),
                docstring=row.get("docstring"),
                rrf_score=round(scores[nid], 6),
                match_sources=sources.get(nid, []),
                centrality=float(row.get("centrality") or 0.0),
            )
        )

    return results
