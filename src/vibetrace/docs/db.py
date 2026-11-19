"""SQLite storage engine for documentation chunks and FTS5 search."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import sqlite3
import struct
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any

from vibetrace.docs.chunker import HeadingChunk

logger = logging.getLogger(__name__)


class DocDB:
    """Document storage engine supporting FTS5 and vector cosine similarity."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextlib.contextmanager
    def _get_conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA busy_timeout = 30000")
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._get_conn() as conn:
            # Check if all required tables exist
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow') "
                    "AND name IN ('doc_chunks', 'doc_chunks_fts')"
                ).fetchall()
            }
            if "doc_chunks" in tables and "doc_chunks_fts" in tables:
                return

            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS doc_chunks (
                        id TEXT PRIMARY KEY,
                        catalog TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        heading_path TEXT NOT NULL,
                        start_line INTEGER NOT NULL,
                        end_line INTEGER NOT NULL,
                        content TEXT NOT NULL,
                        mtime REAL NOT NULL,
                        embedding BLOB
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_doc_chunks_catalog ON doc_chunks(catalog)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_doc_chunks_file ON doc_chunks(file_path)"
                )

                # Initialize FTS5 table
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(
                        id UNINDEXED,
                        catalog,
                        file_path,
                        heading_path,
                        content,
                        tokenize = 'unicode61 remove_diacritics 2'
                    )
                    """
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_file_mtime(self, catalog: str, file_path: str) -> float | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT mtime FROM doc_chunks WHERE catalog = ? AND file_path = ? LIMIT 1",
                (catalog, file_path),
            ).fetchone()
            return float(row["mtime"]) if row else None

    def get_catalog_file_states(self, catalog: str) -> dict[str, tuple[float, bool]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT file_path,
                       MIN(mtime) AS mtime,
                       MIN(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) AS all_embedded
                FROM doc_chunks
                WHERE catalog = ?
                GROUP BY file_path
                """,
                (catalog,),
            ).fetchall()
            return {str(r["file_path"]): (float(r["mtime"]), bool(r["all_embedded"])) for r in rows}

    @staticmethod
    def _delete_files_in_conn(
        conn: sqlite3.Connection, catalog: str, file_paths: list[str]
    ) -> None:
        if not file_paths:
            return
        chunk_size = 450
        existing_ids: list[str] = []
        for i in range(0, len(file_paths), chunk_size):
            batch = file_paths[i : i + chunk_size]
            ph = ",".join("?" for _ in batch)
            rows = conn.execute(
                f"SELECT id FROM doc_chunks WHERE catalog = ? AND file_path IN ({ph})",
                [catalog, *batch],
            ).fetchall()
            existing_ids.extend(str(r[0]) for r in rows)

        if not existing_ids:
            return

        for i in range(0, len(existing_ids), chunk_size):
            id_batch = existing_ids[i : i + chunk_size]
            ph = ",".join("?" for _ in id_batch)
            conn.execute(f"DELETE FROM doc_chunks_fts WHERE id IN ({ph})", id_batch)

        for i in range(0, len(file_paths), chunk_size):
            batch = file_paths[i : i + chunk_size]
            ph = ",".join("?" for _ in batch)
            conn.execute(
                f"DELETE FROM doc_chunks WHERE catalog = ? AND file_path IN ({ph})",
                [catalog, *batch],
            )

    def delete_file_chunks(self, catalog: str, file_path: str) -> None:
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._delete_files_in_conn(conn, catalog, [file_path])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def upsert_files_batch(
        self,
        catalog: str,
        entries: list[tuple[str, float, list[HeadingChunk], list[list[float]] | None]],
    ) -> int:
        if not entries:
            return 0

        ins_sql = (
            "INSERT INTO doc_chunks ("
            "id, catalog, file_path, heading_path, start_line, "
            "end_line, content, mtime, embedding"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        fts_sql = (
            "INSERT INTO doc_chunks_fts (id, catalog, file_path, heading_path, content) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        all_paths = [fp for fp, _, _, _ in entries]
        chunk_rows: list[tuple[Any, ...]] = []
        fts_rows: list[tuple[Any, ...]] = []

        for file_path, mtime, chunks, embeddings in entries:
            for idx, chunk in enumerate(chunks):
                chunk_key = f"{catalog}:{file_path}:{chunk.start_line}:{chunk.end_line}:{idx}"
                chunk_id = hashlib.sha256(chunk_key.encode("utf-8")).hexdigest()[:24]

                emb_bytes: bytes | None = None
                if embeddings and idx < len(embeddings):
                    vec = embeddings[idx]
                    emb_bytes = struct.pack(f"{len(vec)}f", *vec)

                chunk_rows.append(
                    (
                        chunk_id,
                        catalog,
                        file_path,
                        chunk.heading_path,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.content,
                        mtime,
                        emb_bytes,
                    )
                )
                fts_rows.append(
                    (
                        chunk_id,
                        catalog,
                        file_path,
                        chunk.heading_path,
                        chunk.content,
                    )
                )

        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._delete_files_in_conn(conn, catalog, all_paths)
                if chunk_rows:
                    conn.executemany(ins_sql, chunk_rows)
                    conn.executemany(fts_sql, fts_rows)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return len(chunk_rows)

    def upsert_chunks(
        self,
        catalog: str,
        file_path: str,
        mtime: float,
        chunks: list[HeadingChunk],
        embeddings: list[list[float]] | None = None,
    ) -> int:
        if not chunks:
            self.delete_file_chunks(catalog, file_path)
            return 0
        return self.upsert_files_batch(catalog, [(file_path, mtime, chunks, embeddings)])

    def search_bm25(
        self,
        query: str,
        catalog: str | None = None,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        clean_tokens = [w for w in re_clean_query(query) if w]
        if not clean_tokens:
            return []

        fts_query = " OR ".join(f'"{t}"' for t in clean_tokens)
        sql = """
            SELECT d.id, d.catalog, d.file_path, d.heading_path,
                   d.start_line, d.end_line, d.content, bm25(doc_chunks_fts) as rank
            FROM doc_chunks_fts
            JOIN doc_chunks d ON doc_chunks_fts.id = d.id
            WHERE doc_chunks_fts MATCH ?
        """
        params: list[Any] = [fts_query]
        if catalog:
            sql += " AND d.catalog = ?"
            params.append(catalog)
        sql += " ORDER BY rank ASC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError as e:
                err_msg = str(e).lower()
                if "fts5" in err_msg or "syntax error" in err_msg:
                    logger.debug("FTS5 search syntax error for query %r: %s", fts_query, e)
                else:
                    logger.warning("FTS5 query failed: %s", e)
                return []

    def _search_vector_rust(
        self,
        query_vec: list[float],
        q_norm: float,
        dim: int,
        catalog: str | None,
        limit: int,
        search_fn: Any,
    ) -> list[dict[str, Any]]:
        sql = "SELECT id, embedding FROM doc_chunks WHERE embedding IS NOT NULL"
        params: list[Any] = []
        if catalog:
            sql += " AND catalog = ?"
            params.append(catalog)

        expected_bytes = dim * 4
        candidates: list[tuple[str, bytes]] = []
        mismatched_dim_count = 0
        with self._get_conn() as conn:
            for row in conn.execute(sql, params):
                emb_bytes = row["embedding"]
                if not emb_bytes:
                    continue
                if len(emb_bytes) != expected_bytes:
                    mismatched_dim_count += 1
                    continue
                candidates.append((str(row["id"]), bytes(emb_bytes)))

        if mismatched_dim_count > 0:
            logger.debug(
                "Skipped %d doc embeddings due to dimension mismatch (query dim: %d)",
                mismatched_dim_count,
                dim,
            )

        if not candidates:
            return []

        norm_q = [float(x) / q_norm for x in query_vec]
        raw_hits = search_fn(norm_q, candidates, dim, limit)
        hits = [(str(cid), float(sc)) for cid, sc in raw_hits if float(sc) >= 0.66]
        if not hits:
            return []

        hit_ids = [cid for cid, _ in hits]
        ph = ",".join("?" for _ in hit_ids)
        meta_sql = (
            "SELECT id, catalog, file_path, heading_path, start_line, end_line, content "
            f"FROM doc_chunks WHERE id IN ({ph})"
        )
        with self._get_conn() as conn:
            meta_rows = {str(r["id"]): dict(r) for r in conn.execute(meta_sql, hit_ids)}

        results: list[dict[str, Any]] = []
        for cid, sc in hits:
            item = meta_rows.get(cid)
            if item is not None:
                results.append({"score": sc, **item})
        return results

    def search_vector(
        self,
        query_vec: list[float],
        catalog: str | None = None,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        dim = len(query_vec)
        q_norm = sum(x * x for x in query_vec) ** 0.5
        if q_norm == 0 or limit <= 0:
            return []

        search_fn = None
        if dim >= 64:
            try:
                from vibetrace.indexer.rust_bridge import _rust_core

                search_fn = getattr(_rust_core, "py_search_vectors_parallel", None)
            except ImportError:
                search_fn = None

        if search_fn is not None:
            return self._search_vector_rust(query_vec, q_norm, dim, catalog, limit, search_fn)

        sql = (
            "SELECT id, catalog, file_path, heading_path, start_line, end_line, content, embedding "
            "FROM doc_chunks WHERE embedding IS NOT NULL"
        )
        params: list[Any] = []
        if catalog:
            sql += " AND catalog = ?"
            params.append(catalog)

        scored: list[tuple[float, dict[str, Any]]] = []
        mismatched_dim_count = 0
        with self._get_conn() as conn:
            for row in conn.execute(sql, params):
                emb_bytes = row["embedding"]
                if not emb_bytes:
                    continue
                v_count = len(emb_bytes) // 4
                if v_count != dim:
                    mismatched_dim_count += 1
                    continue
                v = struct.unpack(f"{dim}f", emb_bytes)
                dot = sum(a * b for a, b in zip(query_vec, v, strict=True))
                v_norm = sum(x * x for x in v) ** 0.5
                sim = dot / (q_norm * v_norm) if (q_norm * v_norm) > 0 else 0.0
                if sim < 0.66:
                    continue
                item = dict(row)
                del item["embedding"]
                scored.append((sim, item))

        if mismatched_dim_count > 0:
            logger.debug(
                "Skipped %d doc embeddings due to dimension mismatch (query dim: %d)",
                mismatched_dim_count,
                dim,
            )

        scored.sort(key=lambda s: s[0], reverse=True)
        return [{"score": s, **item} for s, item in scored[:limit]]

    def get_catalogs_summary(self) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT catalog,
                       COUNT(DISTINCT file_path) as doc_count,
                       COUNT(*) as chunk_count
                FROM doc_chunks
                GROUP BY catalog
                ORDER BY catalog ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]


def re_clean_query(query: str) -> list[str]:
    """Extract clean alphanumeric words plus snake/camel sub-tokens for FTS5 queries."""
    tokens: list[str] = []
    seen: set[str] = set()
    for w in re.findall(r"\w+", query):
        w_clean = w.strip()
        if len(w_clean) > 1 and w_clean not in seen:
            seen.add(w_clean)
            tokens.append(w_clean)
        for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", w_clean):
            if len(part) > 2 and part not in seen:
                seen.add(part)
                tokens.append(part)
    return tokens


_DOC_DB_CACHE: dict[str, DocDB] = {}
_CACHE_LOCK = threading.Lock()


def get_global_doc_db() -> DocDB:
    """Return database handle to global user docs store (~/.vibetrace/docs.db)."""
    env_global = os.environ.get("VIBETRACE_GLOBAL_ROOT")
    user_home = Path(env_global) if env_global else Path.home() / ".vibetrace"
    key = str((user_home / "docs.db").resolve())
    with _CACHE_LOCK:
        if key not in _DOC_DB_CACHE:
            _DOC_DB_CACHE[key] = DocDB(user_home / "docs.db")
        return _DOC_DB_CACHE[key]


def get_local_doc_db(project_root: Path) -> DocDB:
    """Return database handle to project-local docs store (.vibetrace/vibetrace.db)."""
    key = str((project_root / ".vibetrace" / "vibetrace.db").resolve())
    with _CACHE_LOCK:
        if key not in _DOC_DB_CACHE:
            _DOC_DB_CACHE[key] = DocDB(project_root / ".vibetrace" / "vibetrace.db")
        return _DOC_DB_CACHE[key]
