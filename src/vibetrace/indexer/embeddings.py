"""Native Rust EmbeddingGemma 2 ONNX vector embedding and search engine."""

from __future__ import annotations

import logging
import math
import os
import struct
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from vibetrace.config import normalize_embedding_device
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.vector_store import VectorStore

logger = logging.getLogger(__name__)

_NATIVE_EMBED_SUPER_BATCH = 256
_LANCE_FLUSH_BATCH = 512


class EmbeddingConfig(BaseModel):
    """Configuration contract for the local EmbeddingGemma 2 pipeline."""

    codebase: bool = Field(
        default=False,
        description="Whether to automatically compute embeddings for codebase symbols.",
    )
    device: str = Field(
        default="gpu",
        description="Target compute device: 'gpu' (default, DirectML) or 'cpu'.",
    )

    @field_validator("device", mode="before")
    @classmethod
    def _validate_device(cls, value: str | None) -> str:
        return normalize_embedding_device(value)

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


def format_embedding_query(query: str, task: Literal["code", "docs"] = "code") -> str:
    """Format a query using official EmbeddingGemma 2 task prefixes."""
    if task == "docs":
        return f"task: search result | query: {query}"
    return f"task: code retrieval | query: {query}"


def format_embedding_document(text: str, title: str | None = None) -> str:
    """Format a code symbol or document chunk using EmbeddingGemma 2 framing."""
    clean_title = (title or "").strip() or "none"
    return f"title: {clean_title} | text: {text}"


def _embed_texts_native(
    texts: list[str],
    dimensions: int,
    device: str = "gpu",
    model_dir: str | None = None,
) -> list[list[float]]:
    """Compute L2-normalized EmbeddingGemma 2 ONNX vectors via Rust `_rust_core`."""
    from vibetrace.indexer.rust_bridge import _rust_core

    fn = getattr(_rust_core, "py_embed_texts_parallel", None)
    if fn is None:
        raise RuntimeError(
            "Native Rust vector engine (vibetrace._rust_core.py_embed_texts_parallel) "
            "is required but not built."
        )
    norm_device = normalize_embedding_device(device)
    resolved_dir = model_dir if model_dir is not None else os.environ.get("VIBETRACE_MODEL_DIR")
    return [list(v) for v in fn(texts, dimensions, norm_device, resolved_dir)]


class EmbeddingEngine:
    """Manages native Rust EmbeddingGemma 2 encoding and similarity search."""

    def __init__(
        self,
        config: EmbeddingConfig | None = None,
        encoder: Callable[[list[str]], Sequence[Sequence[float]]] | None = None,
        vector_store: VectorStore | None = None,
        project_root: Path | None = None,
        model_dir: str | None = None,
        device: str | None = None,
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
            except ValueError:
                raise
            except Exception:
                self.config = EmbeddingConfig()
        else:
            self.config = config or EmbeddingConfig()
        if device is not None:
            self.config.device = normalize_embedding_device(device)
        self._encoder = encoder
        self.vector_store = vector_store
        self.project_root = project_root
        self.model_dir = model_dir
        self._provider_logged = False
        self._active_provider: str | None = None
        if self.vector_store is None and project_root is not None:
            self._init_vector_store(project_root)

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
        """Check whether an encoder callable or native Rust core is available."""
        if self._encoder is not None:
            return True
        try:
            from vibetrace.indexer.rust_bridge import _rust_core

            return hasattr(_rust_core, "py_embed_texts_parallel")
        except ImportError:
            return False

    def _resolve_device(self) -> str:
        """Resolve validated target device ('gpu' default, or 'cpu') for ONNX Runtime."""
        return normalize_embedding_device(self.config.device)

    def _log_active_provider(self, requested_device: str) -> None:
        """Query and log the active ONNX Runtime execution provider once per engine."""
        if self._provider_logged:
            return
        active = requested_device
        try:
            from vibetrace.indexer.rust_bridge import _rust_core

            info_fn = getattr(_rust_core, "py_embedding_runtime_info", None)
            if info_fn is not None:
                resolved_dir = (
                    self.model_dir
                    if self.model_dir is not None
                    else os.environ.get("VIBETRACE_MODEL_DIR")
                )
                info = info_fn(resolved_dir)
                reported = str(info.get("active_provider", "")).strip().lower()
                if reported and reported != "uninitialized":
                    active = reported
        except Exception:
            pass
        self._active_provider = active
        self._provider_logged = True
        logger.info("[Embeddings] Provider: %s (requested=%s)", active, requested_device)
        if active == "cpu":
            logger.warning("[Embeddings] Running on CPU (slower than GPU DirectML).")

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of texts via custom encoder or EmbeddingGemma 2 Rust engine."""
        if not texts:
            return []

        if self._encoder is not None:
            raw_vectors = self._encoder(texts)
            return [
                truncate_and_normalize(vec, dimensions=self.config.dimensions)
                for vec in raw_vectors
            ]

        requested_device = self._resolve_device()
        vectors = _embed_texts_native(
            texts,
            self.config.dimensions,
            device=requested_device,
            model_dir=self.model_dir,
        )
        self._log_active_provider(requested_device)
        return vectors

    def embed_query(self, query: str, task: Literal["code", "docs"] = "code") -> list[float]:
        """Encode a single search query with task-specific EmbeddingGemma 2 prefix."""
        if self._encoder is not None:
            vecs = self.embed_texts([query])
            return vecs[0] if vecs else []
        formatted = format_embedding_query(query, task=task)
        vecs = self.embed_texts([formatted])
        return vecs[0] if vecs else []

    def embed_documents(self, items: Sequence[tuple[str | None, str]]) -> list[list[float]]:
        """Encode `(title, text)` pairs with EmbeddingGemma 2 document framing."""
        if not items:
            return []
        if self._encoder is not None:
            return self.embed_texts([text for _, text in items])
        formatted = [format_embedding_document(text, title=title) for title, text in items]
        return self.embed_texts(formatted)

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
        batch_size = (
            max(self.config.batch_size, _NATIVE_EMBED_SUPER_BATCH)
            if self._encoder is None
            else self.config.batch_size
        )
        total_batches = (total_nodes + batch_size - 1) // batch_size
        dev = self._resolve_device()
        has_vector_store = bool(self.vector_store and self.vector_store.is_available())
        lance_buffer: list[dict[str, Any]] = []

        total_indexed = 0
        last_pct_logged = -1
        for batch_idx, idx in enumerate(range(0, total_nodes, batch_size), start=1):
            batch = missing_nodes[idx : idx + batch_size]
            doc_items = [
                (n.get("qualified_name") or n.get("name"), build_symbol_document(n)) for n in batch
            ]
            vectors = self.embed_documents(doc_items)
            if batch_idx == 1 and verbose:
                active = self._active_provider or dev
                print(f"  [Embeddings] Provider: {active} (requested={dev})", flush=True)
                if active == "cpu":
                    print(
                        "  [Embeddings] Warning: Running on CPU (slower than GPU DirectML).",
                        flush=True,
                    )

            if has_vector_store:
                lance_buffer.extend(
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
                )

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

            if (
                self.vector_store is not None
                and has_vector_store
                and lance_buffer
                and (len(lance_buffer) >= _LANCE_FLUSH_BATCH or total_indexed == total_nodes)
            ):
                try:
                    self.vector_store.upsert_embeddings(lance_buffer)
                except Exception as e:
                    logger.warning("LanceDB upsert failed; continuing to SQLite: %s", e)
                lance_buffer.clear()

            if verbose:
                pct = int((total_indexed / total_nodes) * 100.0)
                if pct >= last_pct_logged + 10 or total_indexed == total_nodes:
                    last_pct_logged = pct
                    active_label = (self._active_provider or dev).upper()
                    print(
                        f"  [Embeddings] {pct:3d}% ({total_indexed}/{total_nodes} symbols) "
                        f"on {active_label}...",
                        flush=True,
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
        q_vec: list[float] | None = None
        if (
            self.vector_store
            and self.vector_store.is_available()
            and self.vector_store.has_embeddings(self.config.model_id)
        ):
            q_vec = self.embed_query(query, task="code")
            if q_vec:
                store_results = self.vector_store.search_semantic(
                    q_vec, limit=limit, model_id=self.config.model_id
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

        if q_vec is None:
            q_vec = self.embed_query(query, task="code")
        if not q_vec:
            return []

        # Fast path 2: Parallel Rust cosine similarity over SQLite blobs
        from vibetrace.indexer.rust_bridge import _rust_core

        search_fn = getattr(_rust_core, "py_search_vectors_parallel", None)
        if search_fn is None:
            raise RuntimeError(
                "Native Rust vector search (vibetrace._rust_core.py_search_vectors_parallel) "
                "is required but not built."
            )

        sql_scan = (
            "SELECT node_id, embedding, dimensions FROM node_embeddings "
            "WHERE model_id = ? AND dimensions = ?"
        )
        with db._get_conn() as conn:
            raw_rows = conn.execute(
                sql_scan, (self.config.model_id, self.config.dimensions)
            ).fetchall()
        candidates = [(r[0], bytes(r[1])) for r in raw_rows if r[1]]
        if not candidates:
            return []
        hits = search_fn([float(x) for x in q_vec], candidates, self.config.dimensions, limit)
        return [(str(nid), float(sc)) for nid, sc in hits]
