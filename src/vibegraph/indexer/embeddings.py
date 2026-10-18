"""Self-hosted EmbeddingGemma 2 vector engine with 256d Matryoshka truncation."""

from __future__ import annotations

import heapq
import importlib
import math
import sqlite3
import struct
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, Field

from vibegraph.indexer.db import IndexerDB


class EmbeddingConfig(BaseModel):
    """Configuration contract for the local EmbeddingGemma 2 pipeline."""

    model_id: str = Field(
        default="google/embeddinggemma-2",
        description="Primary HuggingFace/SentenceTransformers model identifier.",
    )
    dimensions: int = Field(
        default=256,
        ge=64,
        le=768,
        description="Matryoshka Representation Learning (MRL) target embedding dimension.",
    )
    batch_size: int = Field(
        default=32,
        ge=1,
        le=512,
        description="Batch size for local symbol encoding.",
    )


def build_symbol_document(node: dict[str, Any]) -> str:
    """Format a code symbol into a canonical rich text representation for embedding."""
    kind = node.get("kind") or "symbol"
    qualified = node.get("qualified_name") or node.get("name") or ""
    sig = node.get("signature") or ""
    file_path = node.get("file_path") or ""
    doc = (node.get("docstring") or "").strip()
    header = f"{kind} {qualified}{sig} in {file_path}".strip()
    return f"{header}\n{doc}" if doc else header


def truncate_and_normalize(vec: Sequence[float], dimensions: int = 256) -> list[float]:
    """Apply Matryoshka truncation to `dimensions` followed by L2 normalization."""
    sliced = list(vec[:dimensions])
    if len(sliced) < dimensions:
        sliced.extend([0.0] * (dimensions - len(sliced)))
    norm = math.sqrt(sum(x * x for x in sliced))
    if norm <= 1e-12:
        return sliced
    return [x / norm for x in sliced]


def pack_vector(vec: Sequence[float]) -> bytes:
    """Serialize a float32 vector into a little-endian binary blob."""
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes, dimensions: int | None = None) -> tuple[float, ...]:
    """Deserialize a little-endian float32 binary blob."""
    dim = dimensions if dimensions is not None else len(blob) // 4
    return struct.unpack(f"<{dim}f", blob[: dim * 4])


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Compute dot-product cosine similarity between two L2-normalized vectors."""
    return sum(a * b for a, b in zip(vec_a, vec_b, strict=False))


def try_load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Attempt to load the sqlite-vec extension onto a SQLite connection if installed."""
    try:
        sqlite_vec = importlib.import_module("sqlite_vec")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        return False


class EmbeddingEngine:
    """Manages local EmbeddingGemma 2 encoding and vector similarity search."""

    def __init__(
        self,
        config: EmbeddingConfig | None = None,
        encoder: Callable[[list[str]], Sequence[Sequence[float]]] | None = None,
    ):
        self.config = config or EmbeddingConfig()
        self._encoder = encoder
        self._st_model: Any = None

    def is_available(self) -> bool:
        """Check whether an encoder callable or sentence-transformers is available."""
        if self._encoder is not None:
            return True
        try:
            importlib.import_module("sentence_transformers")
            return True
        except ImportError:
            return False

    def _load_model(self) -> Any:
        if self._st_model is not None:
            return self._st_model

        st_mod = importlib.import_module("sentence_transformers")
        sentence_transformer_cls = st_mod.SentenceTransformer
        try:
            self._st_model = sentence_transformer_cls(self.config.model_id, trust_remote_code=True)
        except Exception as err:
            hf_url = f"https://huggingface.co/{self.config.model_id}"
            raise RuntimeError(
                f"Failed to load embedding model '{self.config.model_id}': {err}\n"
                "Please ensure internet access for auto-download, "
                f"or download the model manually at: {hf_url}"
            ) from err
        return self._st_model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of texts and apply 256d Matryoshka truncation + L2 normalization."""
        if not texts:
            return []

        if self._encoder is not None:
            raw_vectors = self._encoder(texts)
        else:
            model = self._load_model()
            raw_vectors = model.encode(
                texts,
                batch_size=self.config.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
            ).tolist()

        return [
            truncate_and_normalize(vec, dimensions=self.config.dimensions) for vec in raw_vectors
        ]

    def index_missing_embeddings(self, db: IndexerDB) -> int:
        """Compute and store embeddings for any nodes lacking vectors for the current model."""
        if not self.is_available():
            return 0

        missing_nodes = db.get_nodes_missing_embeddings(self.config.model_id)
        if not missing_nodes:
            return 0

        total_indexed = 0
        batch_size = self.config.batch_size
        for idx in range(0, len(missing_nodes), batch_size):
            batch = missing_nodes[idx : idx + batch_size]
            docs = [build_symbol_document(n) for n in batch]
            vectors = self.embed_texts(docs)

            rows = [
                (
                    node["id"],
                    pack_vector(vec),
                    self.config.dimensions,
                    self.config.model_id,
                )
                for node, vec in zip(batch, vectors, strict=True)
            ]
            db.upsert_embeddings(rows)
            total_indexed += len(rows)

        return total_indexed

    def search_semantic(
        self, db: IndexerDB, query: str, limit: int = 20
    ) -> list[tuple[str, float]]:
        """Return (node_id, cosine_similarity) pairs ranked descending."""
        if not self.is_available() or not query.strip() or limit <= 0:
            return []

        query_vecs = self.embed_texts([query])
        if not query_vecs:
            return []
        q_vec = query_vecs[0]
        q_blob = pack_vector(q_vec)

        # Fast path: use sqlite-vec C extension when loadable
        with db._get_conn() as conn:
            if try_load_sqlite_vec(conn):
                try:
                    cursor = conn.execute(
                        """
                        SELECT node_id, (1.0 - vec_distance_cosine(embedding, ?)) AS score
                        FROM node_embeddings
                        WHERE model_id = ?
                        ORDER BY score DESC
                        LIMIT ?
                        """,
                        (q_blob, self.config.model_id, limit),
                    )
                    results = [(row[0], float(row[1])) for row in cursor.fetchall()]
                    if results:
                        return results
                except sqlite3.Error:
                    pass

        # Memory-efficient pure-stdlib fallback: stream batches & maintain top-K min-heap
        top_heap: list[tuple[float, str]] = []  # min-heap storing (score, node_id)
        with db._get_conn() as conn:
            cursor = conn.execute(
                "SELECT node_id, embedding, dimensions FROM node_embeddings WHERE model_id = ?",
                (self.config.model_id,),
            )
            while True:
                batch = cursor.fetchmany(1000)
                if not batch:
                    break
                for node_id, blob, dims in batch:
                    if dims != len(q_vec):
                        continue
                    cand_vec = unpack_vector(blob, dims)
                    score = cosine_similarity(q_vec, cand_vec)
                    if len(top_heap) < limit:
                        heapq.heappush(top_heap, (score, node_id))
                    elif score > top_heap[0][0]:
                        heapq.heapreplace(top_heap, (score, node_id))

        top_heap.sort(key=lambda item: item[0], reverse=True)
        return [(node_id, score) for score, node_id in top_heap]
