"""Self-hosted EmbeddingGemma 2 vector engine with 256d Matryoshka truncation."""

from __future__ import annotations

import heapq
import importlib
import logging
import math
import struct
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.vector_store import VectorStore

logger = logging.getLogger(__name__)


class EmbeddingConfig(BaseModel):
    """Configuration contract for the local EmbeddingGemma 2 pipeline."""

    codebase: bool = Field(
        default=False,
        description="Whether to automatically compute embeddings for codebase symbols.",
    )
    device: str = Field(
        default="auto",
        description="Target compute device: 'auto' (CUDA with CPU fallback), 'gpu', or 'cpu'.",
    )
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
    if len(blob) < dim * 4:
        raise ValueError(
            f"Truncated embedding blob: expected at least {dim * 4} bytes, got {len(blob)}"
        )
    return struct.unpack(f"<{dim}f", blob[: dim * 4])


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Compute dot-product cosine similarity between two L2-normalized vectors."""
    return sum(a * b for a, b in zip(vec_a, vec_b, strict=False))


class EmbeddingEngine:
    """Manages local EmbeddingGemma 2 encoding and vector similarity search."""

    def __init__(
        self,
        config: EmbeddingConfig | None = None,
        encoder: Callable[[list[str]], Sequence[Sequence[float]]] | None = None,
        vector_store: VectorStore | None = None,
        project_root: Path | None = None,
    ):
        if config is None and project_root is not None:
            try:
                from vibetrace.config import load_config

                cfg = load_config(project_root)
                self.config = EmbeddingConfig(
                    codebase=cfg.vectors.codebase,
                    device=cfg.vectors.device,
                    model_id=cfg.vectors.model_id,
                    dimensions=cfg.vectors.dimensions,
                    batch_size=cfg.vectors.batch_size,
                )
            except Exception:
                self.config = EmbeddingConfig()
        else:
            self.config = config or EmbeddingConfig()
        self._encoder = encoder
        self.vector_store = vector_store
        self.project_root = project_root
        if self.vector_store is None and project_root is not None:
            self._init_vector_store(project_root)
        self._st_model: Any = None

    def _init_vector_store(self, root: Path) -> None:
        """Initialize LanceDB vector store scoped to specified project root."""
        try:
            from vibetrace.config import load_config
            from vibetrace.indexer.vector_store import get_vector_store

            cfg = load_config(root)
            vec_cfg = cfg.vectors.model_copy(
                update={
                    "dimensions": self.config.dimensions,
                    "model_id": self.config.model_id,
                }
            )
            self.vector_store = get_vector_store(
                root,
                vec_cfg,
                project_name=cfg.project.name,
            )
        except Exception as e:
            logger.warning("Failed to initialize vector store: %s", e)
            self.vector_store = None

    def _resolve_vector_store(self, db: IndexerDB) -> None:
        """Ensure vector store is initialized, inferring workspace root from DB path if needed."""
        if self.vector_store is not None:
            return
        root = self.project_root
        if root is None and hasattr(db, "db_path") and db.db_path and db.db_path != ":memory:":
            p = Path(db.db_path).resolve()
            is_sub = p.parent.name in (".vibetrace", "vibetrace_context")
            root = p.parent.parent if is_sub else p.parent
        if root is not None:
            self._init_vector_store(root)

    def is_available(self) -> bool:
        """Check whether an encoder callable or sentence-transformers is available."""
        if self._encoder is not None:
            return True
        try:
            importlib.import_module("sentence_transformers")
            return True
        except ImportError:
            return False

    def _resolve_device(self) -> str:
        """Resolve target device string based on configuration and hardware availability."""
        dev = (self.config.device or "auto").strip().lower()
        if dev in ("cuda", "gpu"):
            return "cuda"
        if dev == "cpu":
            return "cpu"

        # Auto detection: check torch CUDA availability without hard-crashing if torch missing
        try:
            torch_mod = importlib.import_module("torch")
            if getattr(torch_mod, "cuda", None) and torch_mod.cuda.is_available():
                return "cuda"
        except Exception:
            pass
        return "cpu"

    def _load_model(self) -> Any:
        if self._st_model is not None:
            return self._st_model

        st_mod = importlib.import_module("sentence_transformers")
        sentence_transformer_cls = st_mod.SentenceTransformer
        device = self._resolve_device()
        try:
            self._st_model = sentence_transformer_cls(self.config.model_id, device=device)
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

    def index_missing_embeddings(
        self,
        db: IndexerDB,
        verbose: bool = False,
        progress_callback: Callable[[int, int, int], None] | None = None,
    ) -> int:
        """Compute and store embeddings for any nodes lacking vectors for model and dim."""
        if not self.is_available():
            return 0

        self._resolve_vector_store(db)
        missing_nodes = db.get_nodes_missing_embeddings(
            self.config.model_id, dimensions=self.config.dimensions
        )
        if not missing_nodes:
            return 0

        total_nodes = len(missing_nodes)
        batch_size = self.config.batch_size
        total_batches = (total_nodes + batch_size - 1) // batch_size
        dev = self._resolve_device()

        total_indexed = 0
        for batch_idx, idx in enumerate(range(0, total_nodes, batch_size), start=1):
            batch = missing_nodes[idx : idx + batch_size]
            docs = [build_symbol_document(n) for n in batch]
            vectors = self.embed_texts(docs)

            if self.vector_store and self.vector_store.is_available():
                lance_records = [
                    {
                        "node_id": node["id"],
                        "name": node.get("name", ""),
                        "kind": node.get("kind", ""),
                        "vector": vec,
                        "dimensions": self.config.dimensions,
                        "model_id": self.config.model_id,
                        "file_path": node.get("file_path", ""),
                    }
                    for node, vec in zip(batch, vectors, strict=True)
                ]
                try:
                    self.vector_store.upsert_embeddings(lance_records)
                except Exception as e:
                    logger.warning("LanceDB upsert failed; continuing to SQLite: %s", e)

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

            if verbose:
                pct = (total_indexed / total_nodes) * 100.0
                print(
                    f"[{batch_idx}/{total_batches} {pct:5.1f}%] "
                    f"Vectorized {total_indexed}/{total_nodes} symbols on {dev.upper()}..."
                )

            if progress_callback:
                progress_callback(batch_idx, total_batches, total_indexed)

        return total_indexed

    def delete_file_embeddings(self, file_path: str) -> None:
        """Purge vectors belonging to a file path from the underlying vector store."""
        if self.vector_store and self.vector_store.is_available():
            self.vector_store.delete_file_embeddings(file_path)

    def search_semantic(
        self, db: IndexerDB, query: str, limit: int = 20
    ) -> list[tuple[str, float]]:
        """Return (node_id, cosine_similarity) pairs ranked descending."""
        if not self.is_available() or not query.strip() or limit <= 0:
            return []

        self._resolve_vector_store(db)

        # Fast path 1: High-scale disk-backed LanceDB vector store if populated
        if (
            self.vector_store
            and self.vector_store.is_available()
            and self.vector_store.has_embeddings(self.config.model_id)
        ):
            query_vecs = self.embed_texts([query])
            if query_vecs:
                store_results = self.vector_store.search_semantic(
                    query_vecs[0], limit=limit, model_id=self.config.model_id
                )
                if store_results:
                    return store_results

        # Fast check: avoid expensive neural forward-pass if database has no vectors
        with db._get_conn() as conn:
            has_embeddings = conn.execute(
                "SELECT 1 FROM node_embeddings WHERE model_id = ? LIMIT 1",
                (self.config.model_id,),
            ).fetchone()
            if not has_embeddings:
                return []

        query_vecs = self.embed_texts([query])
        if not query_vecs:
            return []
        q_vec = query_vecs[0]

        # Memory-efficient pure-stdlib fallback: stream batches & maintain top-K min-heap
        top_heap: list[tuple[float, str]] = []  # min-heap storing (score, node_id)
        sql_scan = (
            "SELECT node_id, embedding, dimensions FROM node_embeddings "
            "WHERE model_id = ? AND dimensions = ?"
        )
        with db._get_conn() as conn:
            cursor = conn.execute(sql_scan, (self.config.model_id, self.config.dimensions))
            while True:
                batch = cursor.fetchmany(1000)
                if not batch:
                    break
                for node_id, blob, dims in batch:
                    if dims != len(q_vec):
                        continue
                    try:
                        cand_vec = unpack_vector(blob, dims)
                    except (struct.error, ValueError):
                        continue
                    score = cosine_similarity(q_vec, cand_vec)
                    if len(top_heap) < limit:
                        heapq.heappush(top_heap, (score, node_id))
                    elif score > top_heap[0][0]:
                        heapq.heapreplace(top_heap, (score, node_id))

        top_heap.sort(key=lambda item: item[0], reverse=True)
        return [(node_id, score) for score, node_id in top_heap]
