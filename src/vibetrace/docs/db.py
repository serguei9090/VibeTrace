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

    def delete_file_chunks(self, catalog: str, file_path: str) -> None:
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Delete from FTS5
                del_sql = (
                    "DELETE FROM doc_chunks_fts WHERE id IN ("
                    "SELECT id FROM doc_chunks WHERE catalog = ? AND file_path = ?)"
                )
                conn.execute(del_sql, (catalog, file_path))
                conn.execute(
                    "DELETE FROM doc_chunks WHERE catalog = ? AND file_path = ?",
                    (catalog, file_path),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

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

        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # 1. Clean previous chunks for this file atomically within the same transaction
                conn.execute(
                    "DELETE FROM doc_chunks_fts WHERE id IN ("
                    "SELECT id FROM doc_chunks WHERE catalog = ? AND file_path = ?)",
                    (catalog, file_path),
                )
                conn.execute(
                    "DELETE FROM doc_chunks WHERE catalog = ? AND file_path = ?",
                    (catalog, file_path),
                )

                # 2. Insert new chunks
                for idx, chunk in enumerate(chunks):
                    chunk_key = f"{catalog}:{file_path}:{chunk.start_line}:{chunk.end_line}:{idx}"
                    chunk_id = hashlib.sha256(chunk_key.encode("utf-8")).hexdigest()[:24]

                    emb_bytes: bytes | None = None
                    if embeddings and idx < len(embeddings):
                        vec = embeddings[idx]
                        emb_bytes = struct.pack(f"{len(vec)}f", *vec)

                    ins_sql = (
                        "INSERT INTO doc_chunks ("
                        "id, catalog, file_path, heading_path, start_line, "
                        "end_line, content, mtime, embedding"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    )
                    conn.execute(
                        ins_sql,
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
                        ),
                    )

                    conn.execute(
                        """
                        INSERT INTO doc_chunks_fts (id, catalog, file_path, heading_path, content)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            chunk_id,
                            catalog,
                            file_path,
                            chunk.heading_path,
                            chunk.content,
                        ),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return len(chunks)

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

    def search_vector(
        self,
        query_vec: list[float],
        catalog: str | None = None,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        dim = len(query_vec)
        q_norm = sum(x * x for x in query_vec) ** 0.5
        if q_norm == 0:
            return []

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
    """Extract clean alphanumeric words for FTS5 queries."""
    return [w.strip() for w in re.findall(r"\w+", query) if len(w.strip()) > 1]


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
