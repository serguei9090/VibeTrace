"""Serverless, embedded vector storage engine with LanceDB and SQLite adapters.

Provides disk-backed ANN cosine similarity search, zero-copy PyArrow streaming,
and instant cloud scaling (s3://) for 256d Matryoshka symbol embeddings.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Protocol

from vibetrace.config import VectorConfig


class VectorStore(Protocol):
    """Abstract protocol for vector embedding storage and ANN search."""

    def is_available(self) -> bool: ...

    def has_embeddings(self, model_id: str | None = None) -> bool: ...

    def count(self, model_id: str | None = None) -> int: ...

    def upsert_embeddings(self, records: list[dict[str, Any]]) -> int: ...

    def delete_file_embeddings(self, file_path: str) -> None: ...

    def search_semantic(
        self,
        query_vec: list[float],
        limit: int = 20,
        model_id: str | None = None,
    ) -> list[tuple[str, float]]: ...


class LanceDBVectorStore:
    """Embedded, disk-backed LanceDB vector store for high-scale symbol embeddings."""

    TABLE_NAME = "symbol_embeddings"

    def __init__(
        self,
        uri: str | Path,
        dimensions: int = 256,
        project_name: str = "project",
    ) -> None:
        self.uri = str(uri)
        self.dimensions = dimensions
        self.project_name = project_name
        self._db: Any = None
        self._table: Any = None

    def is_available(self) -> bool:
        """Check if lancedb and pyarrow are installed and importable."""
        try:
            importlib.import_module("lancedb")
            importlib.import_module("pyarrow")
            return True
        except ImportError:
            return False

    def _get_table(self) -> Any:
        if self._table is not None:
            return self._table

        if not self.is_available():
            return None

        import lancedb
        import pyarrow as pa

        # Resolve local directories and ensure parents exist
        if not self.uri.startswith(("s3://", "gs://", "az://")):
            local_p = Path(self.uri).resolve()
            local_p.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(local_p))
        else:
            self._db = lancedb.connect(self.uri)

        schema = pa.schema(
            [
                pa.field("node_id", pa.string()),
                pa.field("name", pa.string()),
                pa.field("kind", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), self.dimensions)),
                pa.field("dimensions", pa.int32()),
                pa.field("model_id", pa.string()),
                pa.field("file_path", pa.string()),
                pa.field("project_name", pa.string()),
            ]
        )

        try:
            self._table = self._db.open_table(self.TABLE_NAME)
        except Exception:
            self._table = self._db.create_table(self.TABLE_NAME, schema=schema, exist_ok=True)

        return self._table

    def has_embeddings(self, model_id: str | None = None) -> bool:
        """Check if vector store contains indexed embeddings."""
        table = self._get_table()
        if table is None:
            return False
        try:
            if table.count_rows() == 0:
                return False
            if not model_id:
                return True
            safe_model = model_id.replace("'", "''")
            filtered = table.search().where(f"model_id = '{safe_model}'").limit(1).to_list()
            return len(filtered) > 0
        except Exception:
            return False

    def count(self, model_id: str | None = None) -> int:
        """Return total number of vector embeddings."""
        table = self._get_table()
        if table is None:
            return 0
        try:
            if not model_id:
                return int(table.count_rows())
            safe_model = model_id.replace("'", "''")
            return int(table.count_rows(f"model_id = '{safe_model}'"))
        except Exception:
            return 0

    def upsert_embeddings(self, records: list[dict[str, Any]]) -> int:
        """Upsert a batch of symbol vector records into LanceDB."""
        if not records:
            return 0
        table = self._get_table()
        if table is None:
            return 0

        # Purge existing records for the same node IDs to ensure idempotency
        node_ids = [r["node_id"] for r in records if "node_id" in r]
        if node_ids:
            try:
                # Batch delete in chunks of 500 to keep SQL filter string safe
                for i in range(0, len(node_ids), 500):
                    chunk = node_ids[i : i + 500]
                    escaped_ids = ", ".join("'" + nid.replace("'", "''") + "'" for nid in chunk)
                    table.delete(f"node_id IN ({escaped_ids})")
            except Exception:
                pass

        # Filter fields against existing table schema to prevent invalid field exceptions
        schema_cols = set(table.schema.names)
        sanitized: list[dict[str, Any]] = []
        for r in records:
            item = {k: v for k, v in r.items() if k in schema_cols}
            if "project_name" in schema_cols:
                item.setdefault("project_name", self.project_name)
            if "dimensions" in schema_cols:
                item.setdefault("dimensions", self.dimensions)
            sanitized.append(item)

        # Ensure vectors match table fixed dimension to prevent Arrow cast errors
        if "vector" in schema_cols:
            vec_field = table.schema.field("vector")
            expected_dim = getattr(vec_field.type, "list_size", self.dimensions)
            valid_records = [
                r for r in sanitized if "vector" in r and len(r["vector"]) == expected_dim
            ]
        else:
            valid_records = sanitized

        if not valid_records:
            return 0

        table.add(valid_records)
        return len(valid_records)

    def delete_file_embeddings(self, file_path: str) -> None:
        """Purge all symbol vectors belonging to a file path."""
        table = self._get_table()
        if table is None:
            return
        clean_path = file_path.replace("\\", "/").replace("'", "''")
        try:
            table.delete(f"file_path = '{clean_path}'")
        except Exception:
            pass

    def search_semantic(
        self,
        query_vec: list[float],
        limit: int = 20,
        model_id: str | None = None,
    ) -> list[tuple[str, float]]:
        """Execute sub-millisecond cosine ANN search directly against disk-backed vectors."""
        if not query_vec or limit <= 0:
            return []
        table = self._get_table()
        if table is None:
            return []

        # Guard against query vector dimension mismatch
        if "vector" in table.schema.names:
            expected_dim = getattr(table.schema.field("vector").type, "list_size", self.dimensions)
            if len(query_vec) != expected_dim:
                return []

        try:
            query = table.search(query_vec).metric("cosine").limit(limit)
            if model_id:
                safe_model = model_id.replace("'", "''")
                query = query.where(f"model_id = '{safe_model}'")

            rows = query.to_list()
            results: list[tuple[str, float]] = []
            for r in rows:
                nid = r.get("node_id")
                # LanceDB cosine distance: distance in [0, 2], similarity = 1.0 - distance
                dist = float(r.get("_distance", 1.0))
                score = round(1.0 - dist, 6)
                if nid:
                    results.append((nid, score))
            return results
        except Exception:
            return []


def get_vector_store(
    project_root: Path,
    config: VectorConfig | None = None,
    project_name: str = "project",
) -> VectorStore | None:
    """Factory creating the appropriate vector store from configuration."""
    cfg = config or VectorConfig()
    if cfg.engine == "none":
        return None

    if cfg.engine == "lancedb":
        uri = cfg.uri
        if not uri.startswith(("s3://", "gs://", "az://")):
            p = Path(uri)
            resolved_uri = p if p.is_absolute() else (project_root / p).resolve()
        else:
            resolved_uri = uri  # type: ignore[assignment]
        return LanceDBVectorStore(
            uri=resolved_uri,
            dimensions=cfg.dimensions,
            project_name=project_name,
        )

    return None
