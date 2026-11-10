import json
import logging
import math
import sqlite3
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from vibetrace.indexer.db_queries import DBQueriesMixin, _clean_path
from vibetrace.indexer.db_retry import with_write_retry
from vibetrace.indexer.models import Edge, Node

logger = logging.getLogger(__name__)

__all__ = ["Edge", "IndexerDB", "Node", "_clean_path", "with_write_retry"]


class IndexerDB(DBQueriesMixin):
    def __init__(self, db_path: str | Path | None = None, vector_store: Any | None = None):
        if db_path is None:
            pkg_root = Path(__file__).resolve().parent.parent.parent.parent
            if (pkg_root / ".vibetrace").exists():
                context_dir = pkg_root / ".vibetrace"
            else:
                context_dir = pkg_root / "vibetrace_context"
            context_dir.mkdir(exist_ok=True)
            self.db_path = str(context_dir / "vibetrace.db")
        else:
            self.db_path = str(db_path)
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._is_memory = self.db_path == ":memory:" or (
            isinstance(self.db_path, str) and "mode=memory" in self.db_path
        )
        self._mem_conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        if self._is_memory:
            self._mem_conn = sqlite3.connect(
                self.db_path,
                timeout=30.0,
                uri="mode=memory" in str(self.db_path),
                check_same_thread=False,
            )
            self._mem_conn.row_factory = sqlite3.Row

        self.vector_store = vector_store
        self._init_db()

    def _resolve_vector_store(self) -> None:
        if getattr(self, "vector_store", None) is not None:
            return
        try:
            if hasattr(self, "db_path") and self.db_path and self.db_path != ":memory:":
                p = Path(self.db_path).resolve()
                is_sub = p.parent.name in (".vibetrace", "vibetrace_context")
                root = p.parent.parent if is_sub else p.parent
                from vibetrace.config import load_config
                from vibetrace.indexer.vector_store import get_vector_store

                cfg = load_config(root)
                self.vector_store = get_vector_store(
                    root, cfg.vectors, project_name=cfg.project.name
                )
        except Exception:
            self.vector_store = None

    @contextmanager
    def _get_conn(self) -> Generator[sqlite3.Connection, None, None]:
        if getattr(self, "_is_memory", False) and self._mem_conn is not None:
            with self._lock:
                with self._mem_conn:
                    yield self._mem_conn
            return

        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def close(self) -> None:
        """Close connection resources if in-memory database is active."""
        if getattr(self, "_is_memory", False) and self._mem_conn is not None:
            try:
                self._mem_conn.close()
            except Exception:
                pass
            self._mem_conn = None

    def _init_db(self) -> None:
        """Initialize the database schema if it doesn't exist, applying migrations."""
        schema_path = Path(__file__).parent / "schema.sql"
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema file not found at {schema_path}")

        with self._get_conn() as conn:
            # Check if existing tables need column migrations before creating indexes
            try:
                cursor = conn.execute("PRAGMA table_info(nodes)")
                cols = {row["name"] for row in cursor.fetchall()}
                if cols and "qualified_name" not in cols:
                    conn.execute("ALTER TABLE nodes ADD COLUMN qualified_name TEXT")
                    conn.execute(
                        "UPDATE nodes SET qualified_name = name WHERE qualified_name IS NULL"
                    )
                if cols and "parent_id" not in cols:
                    conn.execute("ALTER TABLE nodes ADD COLUMN parent_id TEXT")
                if cols and "centrality" not in cols:
                    conn.execute("ALTER TABLE nodes ADD COLUMN centrality REAL DEFAULT 0.0")
            except Exception as e:
                logger.warning("Error running migration on nodes table: %s", e)

            try:
                cursor = conn.execute("PRAGMA table_info(edges)")
                edge_cols = {row["name"] for row in cursor.fetchall()}
                if edge_cols and "call_line" not in edge_cols:
                    conn.execute("ALTER TABLE edges ADD COLUMN call_line INTEGER")
                if edge_cols and "confidence" not in edge_cols:
                    conn.execute("ALTER TABLE edges ADD COLUMN confidence TEXT DEFAULT 'exact'")
            except Exception as e:
                logger.warning("Error running migration on edges table: %s", e)

            with open(schema_path, encoding="utf-8") as f:
                conn.executescript(f.read())

    @with_write_retry()
    def upsert_node(self, node: Node) -> None:
        """Insert or replace a node and update FTS5 search index."""
        clean_fp = _clean_path(node.file_path)
        qualified = node.qualified_name or node.name
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT OR REPLACE INTO nodes (
                    id, name, qualified_name, parent_id, kind, file_path, start_line, end_line,
                    signature, docstring, decorators, visibility, centrality
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.id,
                    node.name,
                    qualified,
                    node.parent_id,
                    node.kind,
                    clean_fp,
                    node.start_line,
                    node.end_line,
                    node.signature,
                    node.docstring,
                    json.dumps(node.decorators) if node.decorators else None,
                    node.visibility,
                    node.centrality,
                ),
            )
            # Maintain FTS5 index
            conn.execute("DELETE FROM nodes_fts WHERE node_id = ?", (node.id,))
            conn.execute(
                """
                INSERT INTO nodes_fts (
                    node_id, name, qualified_name, signature, docstring, file_path
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    node.id,
                    node.name,
                    qualified,
                    node.signature or "",
                    node.docstring or "",
                    clean_fp,
                ),
            )
            conn.commit()

    @with_write_retry()
    def upsert_edge(self, edge: Edge) -> None:
        """Insert an edge."""
        sql = (
            "INSERT INTO edges (from_node_id, to_node_id, relation_type, call_line, confidence) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                sql,
                (
                    edge.from_node_id,
                    edge.to_node_id,
                    edge.relation_type,
                    edge.call_line,
                    getattr(edge, "confidence", "exact"),
                ),
            )
            conn.commit()

    @with_write_retry()
    def batch_save_file_index(
        self,
        file_path: str,
        content_hash: str,
        nodes: list[Node],
        edges: list[Edge],
        indexed_time: int | None = None,
        imported_symbols: dict[str, tuple[str, str]] | None = None,
        target_symbols: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        """Atomically clear old file data and persist new nodes, edges, FTS5, and hash."""
        file_path = _clean_path(file_path)
        self._resolve_vector_store()
        idx_time = indexed_time if indexed_time is not None else int(time.time())
        node_tuples = [
            (
                n.id,
                n.name,
                n.qualified_name or n.name,
                n.parent_id,
                n.kind,
                _clean_path(n.file_path),
                n.start_line,
                n.end_line,
                n.signature,
                n.docstring,
                json.dumps(n.decorators) if n.decorators else None,
                n.visibility,
                n.centrality,
            )
            for n in nodes
        ]
        fts_tuples = [
            (
                n.id,
                n.name,
                n.qualified_name or n.name,
                n.signature or "",
                n.docstring or "",
                n.file_path.replace("\\", "/"),
            )
            for n in nodes
        ]
        edge_tuples = list(
            dict.fromkeys(
                (
                    e.from_node_id,
                    e.to_node_id,
                    e.relation_type,
                    e.call_line,
                    getattr(e, "confidence", "exact"),
                )
                for e in edges
            )
        )

        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Detect symbols deleted from this file to cleanly prune orphaned inbound edges
            old_node_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT id FROM nodes WHERE file_path = ?", (file_path,)
                ).fetchall()
            }
            new_node_ids = {n[0] for n in node_tuples}
            deleted_node_ids = old_node_ids - new_node_ids

            # 1. Clear previous outbound edges and unreferenced edge_targets defined by this file
            conn.execute(
                "DELETE FROM edges "
                "WHERE from_node_id IN (SELECT id FROM nodes WHERE file_path = ?)",
                (file_path,),
            )
            conn.execute(
                "DELETE FROM edge_targets "
                "WHERE target_id NOT IN (SELECT DISTINCT to_node_id FROM edges)"
            )
            # Clean up associated symbol docs and taint contracts for deleted symbols
            if deleted_node_ids:
                deleted_list = list(deleted_node_ids)
                chunk_size = 500
                for i in range(0, len(deleted_list), chunk_size):
                    chunk = deleted_list[i : i + chunk_size]
                    placeholders = ",".join("?" for _ in chunk)
                    conn.execute(
                        f"DELETE FROM edges WHERE to_node_id IN ({placeholders})",
                        chunk,
                    )
                    conn.execute(
                        f"DELETE FROM symbol_doc_links WHERE symbol_id IN ({placeholders})",
                        chunk,
                    )

            escaped_prefix = file_path.replace("\\", "\\\\") + "%"
            conn.execute(
                "DELETE FROM function_taint_contracts WHERE symbol_id LIKE ? ESCAPE '\\'",
                (escaped_prefix,),
            )

            conn.execute(
                "DELETE FROM nodes_fts WHERE node_id IN (SELECT id FROM nodes WHERE file_path = ?)",
                (file_path,),
            )
            conn.execute(
                "DELETE FROM node_embeddings WHERE node_id IN "
                "(SELECT id FROM nodes WHERE file_path = ?)",
                (file_path,),
            )
            conn.execute(
                "DELETE FROM symbol_doc_links "
                "WHERE symbol_id IN (SELECT id FROM nodes WHERE file_path = ?)",
                (file_path,),
            )
            conn.execute("DELETE FROM nodes WHERE file_path = ?", (file_path,))

            # 2. Batch insert nodes and maintain FTS5
            if node_tuples:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO nodes (
                        id, name, qualified_name, parent_id, kind, file_path,
                        start_line, end_line, signature, docstring,
                        decorators, visibility, centrality
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    node_tuples,
                )
                conn.executemany(
                    """
                    INSERT INTO nodes_fts (
                        node_id, name, qualified_name, signature, docstring, file_path
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    fts_tuples,
                )

            # 3. Batch insert edges
            if edge_tuples:
                conn.executemany(
                    """
                    INSERT INTO edges (
                        from_node_id, to_node_id, relation_type, call_line, confidence
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    edge_tuples,
                )

            # 3b. Persist cross-file import mappings for transitive re-export & heuristic linking
            conn.execute("DELETE FROM file_imports WHERE file_path = ?", (file_path,))
            if imported_symbols:
                import_tuples = [
                    (file_path, alias, _clean_path(tgt_f), tgt_sym)
                    for alias, (tgt_f, tgt_sym) in imported_symbols.items()
                    if tgt_f and tgt_f != "external"
                ]
                if import_tuples:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO file_imports (
                            file_path, local_alias, target_file, target_symbol
                        ) VALUES (?, ?, ?, ?)
                        """,
                        import_tuples,
                    )

            if target_symbols:
                target_tuples = [
                    (tid, _clean_path(t_file), t_sym)
                    for tid, (t_file, t_sym) in target_symbols.items()
                    if t_file and t_sym
                ]
                if target_tuples:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO edge_targets (
                            target_id, target_file, target_symbol
                        ) VALUES (?, ?, ?)
                        """,
                        target_tuples,
                    )

            # 4. Update file hash tracking
            conn.execute(
                """
                INSERT OR REPLACE INTO indexed_files (file_path, content_hash, updated_at)
                VALUES (?, ?, ?)
                """,
                (file_path, content_hash, idx_time),
            )
            conn.commit()

        if self.vector_store and self.vector_store.is_available():
            try:
                self.vector_store.delete_file_embeddings(file_path)
            except Exception as e:
                logger.warning("Failed to delete old file embeddings for %s: %s", file_path, e)

    @with_write_retry()
    def clear_file(self, file_path: str) -> None:
        """Remove all nodes, edges (inbound & outbound), FTS5, and file metadata."""
        file_path = _clean_path(file_path)
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                DELETE FROM edges 
                WHERE from_node_id IN (SELECT id FROM nodes WHERE file_path = ?)
                   OR to_node_id IN (SELECT id FROM nodes WHERE file_path = ?)
                """,
                (file_path, file_path),
            )
            conn.execute(
                "DELETE FROM nodes_fts WHERE node_id IN (SELECT id FROM nodes WHERE file_path = ?)",
                (file_path,),
            )
            conn.execute(
                "DELETE FROM node_embeddings WHERE node_id IN "
                "(SELECT id FROM nodes WHERE file_path = ?)",
                (file_path,),
            )
            conn.execute(
                "DELETE FROM symbol_doc_links "
                "WHERE symbol_id IN (SELECT id FROM nodes WHERE file_path = ?)",
                (file_path,),
            )
            escaped_prefix = file_path.replace("\\", "\\\\") + "%"
            conn.execute(
                "DELETE FROM function_taint_contracts WHERE symbol_id LIKE ? ESCAPE '\\'",
                (escaped_prefix,),
            )
            conn.execute("DELETE FROM nodes WHERE file_path = ?", (file_path,))

            conn.execute("DELETE FROM file_imports WHERE file_path = ?", (file_path,))
            conn.execute(
                "DELETE FROM edge_targets "
                "WHERE target_id NOT IN (SELECT DISTINCT to_node_id FROM edges)"
            )
            conn.execute("DELETE FROM indexed_files WHERE file_path = ?", (file_path,))
            conn.commit()

        self._resolve_vector_store()
        if self.vector_store and self.vector_store.is_available():
            try:
                self.vector_store.delete_file_embeddings(file_path)
            except Exception as e:
                logger.warning("Failed to delete file embeddings for %s: %s", file_path, e)

    def get_all_indexed_file_paths(self) -> set[str]:
        """Retrieve all indexed file paths currently recorded in the database."""
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT file_path FROM indexed_files")
            return {row[0] for row in cursor.fetchall()}

    def get_all_file_hashes(self) -> dict[str, str]:
        """Retrieve a mapping of all indexed file paths to their stored SHA-256 content hashes."""
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT file_path, content_hash FROM indexed_files")
            return {
                row[0]: row[1]
                for row in cursor.fetchall()
                if row[0] is not None and row[1] is not None
            }

    @with_write_retry()
    def batch_save_workspace_index(
        self,
        file_records: list[
            tuple[
                str,
                str,
                list[Node],
                list[Edge],
                dict[str, tuple[str, str]] | None,
                dict[str, tuple[str, str]] | None,
            ]
        ],
        deleted_files: list[str] | None = None,
        indexed_time: int | None = None,
    ) -> int:
        """Atomically persist multiple extracted files and purge deleted files."""
        if not file_records and not deleted_files:
            return 0

        self._resolve_vector_store()
        idx_time = indexed_time if indexed_time is not None else int(time.time())
        touched_paths: list[str] = []

        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")

            # 1. Purge explicitly deleted files
            for del_path in deleted_files or []:
                clean_del = _clean_path(del_path)
                touched_paths.append(clean_del)
                conn.execute(
                    """
                    DELETE FROM edges
                    WHERE from_node_id IN (SELECT id FROM nodes WHERE file_path = ?)
                       OR to_node_id IN (SELECT id FROM nodes WHERE file_path = ?)
                    """,
                    (clean_del, clean_del),
                )
                conn.execute(
                    "DELETE FROM nodes_fts "
                    "WHERE node_id IN (SELECT id FROM nodes WHERE file_path = ?)",
                    (clean_del,),
                )
                conn.execute(
                    "DELETE FROM node_embeddings "
                    "WHERE node_id IN (SELECT id FROM nodes WHERE file_path = ?)",
                    (clean_del,),
                )
                conn.execute(
                    "DELETE FROM symbol_doc_links "
                    "WHERE symbol_id IN (SELECT id FROM nodes WHERE file_path = ?)",
                    (clean_del,),
                )
                esc = clean_del.replace("\\", "\\\\") + "%"
                conn.execute(
                    "DELETE FROM function_taint_contracts WHERE symbol_id LIKE ? ESCAPE '\\'",
                    (esc,),
                )
                conn.execute("DELETE FROM nodes WHERE file_path = ?", (clean_del,))
                conn.execute("DELETE FROM file_imports WHERE file_path = ?", (clean_del,))
                conn.execute("DELETE FROM indexed_files WHERE file_path = ?", (clean_del,))

            # 2. Batch process all modified/added file records
            all_node_tuples: list[tuple[Any, ...]] = []
            all_fts_tuples: list[tuple[Any, ...]] = []
            all_edge_tuples: list[tuple[Any, ...]] = []
            all_import_tuples: list[tuple[str, str, str, str]] = []
            all_target_tuples: list[tuple[str, str, str]] = []
            all_hash_tuples: list[tuple[str, str, int]] = []
            all_deleted_symbol_ids: list[str] = []

            for (
                raw_fp,
                content_hash,
                nodes,
                edges,
                imported_symbols,
                target_symbols,
            ) in file_records:
                fp = _clean_path(raw_fp)
                touched_paths.append(fp)

                old_ids = {
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM nodes WHERE file_path = ?", (fp,)
                    ).fetchall()
                }
                new_ids = {n.id for n in nodes}
                all_deleted_symbol_ids.extend(old_ids - new_ids)

                conn.execute(
                    "DELETE FROM edges "
                    "WHERE from_node_id IN (SELECT id FROM nodes WHERE file_path = ?)",
                    (fp,),
                )
                esc_fp = fp.replace("\\", "\\\\") + "%"
                conn.execute(
                    "DELETE FROM function_taint_contracts WHERE symbol_id LIKE ? ESCAPE '\\'",
                    (esc_fp,),
                )
                conn.execute(
                    "DELETE FROM nodes_fts "
                    "WHERE node_id IN (SELECT id FROM nodes WHERE file_path = ?)",
                    (fp,),
                )
                conn.execute(
                    "DELETE FROM node_embeddings "
                    "WHERE node_id IN (SELECT id FROM nodes WHERE file_path = ?)",
                    (fp,),
                )
                conn.execute(
                    "DELETE FROM symbol_doc_links "
                    "WHERE symbol_id IN (SELECT id FROM nodes WHERE file_path = ?)",
                    (fp,),
                )
                conn.execute("DELETE FROM nodes WHERE file_path = ?", (fp,))
                conn.execute("DELETE FROM file_imports WHERE file_path = ?", (fp,))

                for n in nodes:
                    clean_n_fp = _clean_path(n.file_path)
                    all_node_tuples.append(
                        (
                            n.id,
                            n.name,
                            n.qualified_name or n.name,
                            n.parent_id,
                            n.kind,
                            clean_n_fp,
                            n.start_line,
                            n.end_line,
                            n.signature,
                            n.docstring,
                            json.dumps(n.decorators) if n.decorators else None,
                            n.visibility,
                            n.centrality,
                        )
                    )
                    all_fts_tuples.append(
                        (
                            n.id,
                            n.name,
                            n.qualified_name or n.name,
                            n.signature or "",
                            n.docstring or "",
                            clean_n_fp,
                        )
                    )

                for e in edges:
                    all_edge_tuples.append(
                        (
                            e.from_node_id,
                            e.to_node_id,
                            e.relation_type,
                            e.call_line,
                            getattr(e, "confidence", "exact"),
                        )
                    )

                if imported_symbols:
                    for alias, (tgt_f, tgt_sym) in imported_symbols.items():
                        if tgt_f and tgt_f != "external":
                            all_import_tuples.append((fp, alias, _clean_path(tgt_f), tgt_sym))

                if target_symbols:
                    for tid, (t_file, t_sym) in target_symbols.items():
                        if t_file and t_sym:
                            all_target_tuples.append((tid, _clean_path(t_file), t_sym))

                all_hash_tuples.append((fp, content_hash, idx_time))

            if all_deleted_symbol_ids:
                chunk_size = 500
                for i in range(0, len(all_deleted_symbol_ids), chunk_size):
                    chunk = all_deleted_symbol_ids[i : i + chunk_size]
                    placeholders = ",".join("?" for _ in chunk)
                    conn.execute(
                        f"DELETE FROM edges WHERE to_node_id IN ({placeholders})",
                        chunk,
                    )
                    conn.execute(
                        f"DELETE FROM symbol_doc_links WHERE symbol_id IN ({placeholders})",
                        chunk,
                    )

            if all_node_tuples:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO nodes (
                        id, name, qualified_name, parent_id, kind, file_path,
                        start_line, end_line, signature, docstring,
                        decorators, visibility, centrality
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    all_node_tuples,
                )
                conn.executemany(
                    """
                    INSERT INTO nodes_fts (
                        node_id, name, qualified_name, signature, docstring, file_path
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    all_fts_tuples,
                )

            if all_edge_tuples:
                unique_edges = list(dict.fromkeys(all_edge_tuples))
                conn.executemany(
                    """
                    INSERT INTO edges (
                        from_node_id, to_node_id, relation_type, call_line, confidence
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    unique_edges,
                )

            if all_import_tuples:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO file_imports (
                        file_path, local_alias, target_file, target_symbol
                    ) VALUES (?, ?, ?, ?)
                    """,
                    all_import_tuples,
                )

            if all_target_tuples:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO edge_targets (
                        target_id, target_file, target_symbol
                    ) VALUES (?, ?, ?)
                    """,
                    all_target_tuples,
                )

            conn.execute(
                "DELETE FROM edge_targets "
                "WHERE target_id NOT IN (SELECT DISTINCT to_node_id FROM edges)"
            )

            if all_hash_tuples:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO indexed_files (file_path, content_hash, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    all_hash_tuples,
                )

            conn.commit()

        if self.vector_store and self.vector_store.is_available():
            for fp in touched_paths:
                try:
                    self.vector_store.delete_file_embeddings(fp)
                except Exception as e:
                    logger.warning("Failed to delete old file embeddings for %s: %s", fp, e)

        return len(file_records)

    def get_file_hash(self, file_path: str) -> str | None:
        """Retrieve stored content hash for a file."""
        file_path = _clean_path(file_path)
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT content_hash FROM indexed_files WHERE file_path = ?", (file_path,)
            )
            row = cursor.fetchone()
            return row[0] if row else None

    @with_write_retry()
    def update_file_hash(self, file_path: str, content_hash: str, updated_at: int) -> None:
        """Update or insert content hash for a file."""
        file_path = _clean_path(file_path)
        sql = (
            "INSERT OR REPLACE INTO indexed_files (file_path, content_hash, updated_at) "
            "VALUES (?, ?, ?)"
        )
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(sql, (file_path, content_hash, updated_at))
            conn.commit()

    def get_meta(self, key: str) -> str | None:
        """Retrieve a metadata value by key."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    @with_write_retry()
    def set_meta(self, key: str, value: str) -> None:
        """Upsert a metadata key-value pair."""
        sql = (
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP"
        )
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(sql, (key, value))
            conn.commit()

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_graph_data(self, file_path: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """Return nodes and connected edges for visualization, optionally filtered by file."""
        clean_fp = _clean_path(file_path) if file_path else None
        with self._get_conn() as conn:
            if clean_fp:
                escaped = (
                    clean_fp.rstrip("/")
                    .replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                pattern = f"{escaped}/%"
                nodes_cursor = conn.execute(
                    "SELECT * FROM nodes WHERE file_path = ? OR file_path LIKE ? ESCAPE '\\'",
                    (clean_fp, pattern),
                )
            else:
                pattern = ""
                nodes_cursor = conn.execute("SELECT * FROM nodes")

            nodes = [dict(row) for row in nodes_cursor.fetchall()]
            if not nodes:
                return {"nodes": [], "edges": []}

            if clean_fp:
                edges_cursor = conn.execute(
                    """
                    SELECT * FROM edges WHERE from_node_id IN (
                        SELECT id FROM nodes WHERE file_path = ? OR file_path LIKE ? ESCAPE '\\'
                    ) OR to_node_id IN (
                        SELECT id FROM nodes WHERE file_path = ? OR file_path LIKE ? ESCAPE '\\'
                    )
                    """,
                    (clean_fp, pattern, clean_fp, pattern),
                )
                edges = [dict(row) for row in edges_cursor.fetchall()]
            else:
                edges_cursor = conn.execute("SELECT * FROM edges")
                edges = [dict(row) for row in edges_cursor.fetchall()]

        return {"nodes": nodes, "edges": edges}

    @with_write_retry()
    def update_centrality_scores(
        self, use_pagerank: bool = False, damping: float = 0.85, max_iter: int = 20
    ) -> dict[str, float]:
        """Compute and persist graph centrality for nodes.

        Defaults to log-normalized in-degree centrality (`ROUND(LN(1.0 + COUNT(*)), 4)`).
        If `use_pagerank=True`, executes iterative power convergence PageRank with damping factor.
        """
        if not use_pagerank:
            sql = """
            WITH edge_counts AS (
                SELECT to_node_id, ROUND(LN(1.0 + COUNT(*)), 4) AS score
                FROM edges
                WHERE relation_type IN ('calls', 'references', 'inherits', 'implements', 'imports')
                GROUP BY to_node_id
            )
            UPDATE nodes
            SET centrality = COALESCE(edge_counts.score, 0.0)
            FROM nodes n
            LEFT JOIN edge_counts ON n.id = edge_counts.to_node_id
            WHERE nodes.id = n.id AND nodes.centrality != COALESCE(edge_counts.score, 0.0);
            """
            with self._get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(sql)
                conn.commit()
                cursor = conn.execute("SELECT id, centrality FROM nodes WHERE centrality > 0")
                return {str(row[0]): float(row[1]) for row in cursor.fetchall()}

        return self._compute_and_save_pagerank(damping=damping, max_iter=max_iter)

    def _compute_and_save_pagerank(
        self, damping: float = 0.85, max_iter: int = 20
    ) -> dict[str, float]:
        """Iterative PageRank calculation over the directed symbol call graph."""
        with self._get_conn() as conn:
            # 1. Fetch all node IDs
            cur = conn.execute("SELECT id FROM nodes")
            node_ids = [str(r[0]) for r in cur.fetchall()]
            n = len(node_ids)
            if n == 0:
                return {}

            # 2. Fetch outgoing edges and build adjacency + out-degree
            rel_types = "('calls', 'references', 'inherits', 'implements', 'imports')"
            cur = conn.execute(
                f"SELECT from_node_id, to_node_id FROM edges WHERE relation_type IN {rel_types}"
            )
            out_degrees: dict[str, int] = {}
            incoming: dict[str, list[str]] = {nid: [] for nid in node_ids}
            for u, v in cur.fetchall():
                out_degrees[u] = out_degrees.get(u, 0) + 1
                if v in incoming:
                    incoming[v].append(u)

            # 3. Iterative power method
            ranks = dict.fromkeys(node_ids, 1.0 / n)
            base_score = (1.0 - damping) / n

            for _ in range(max_iter):
                new_ranks: dict[str, float] = {}
                for nid in node_ids:
                    in_sum = sum(
                        ranks[u] / out_degrees[u]
                        for u in incoming[nid]
                        if out_degrees.get(u, 0) > 0
                    )
                    new_ranks[nid] = base_score + damping * in_sum
                ranks = new_ranks

            # Normalize to log scale for consistent downstream hybrid search weighting
            normalized_scores = {
                nid: round(float(math.log(1.0 + r * n * 10.0)), 4) for nid, r in ranks.items()
            }

            # 4. Batch update SQLite
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                "UPDATE nodes SET centrality = ? WHERE id = ?",
                [(score, nid) for nid, score in normalized_scores.items()],
            )
            conn.commit()
            return normalized_scores

    def get_nodes_by_ids(self, node_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch full node dictionaries keyed by node ID with parameter chunking."""
        if not node_ids:
            return {}
        result: dict[str, dict[str, Any]] = {}
        chunk_size = 500
        with self._get_conn() as conn:
            for i in range(0, len(node_ids), chunk_size):
                chunk = node_ids[i : i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                cursor = conn.execute(
                    f"SELECT * FROM nodes WHERE id IN ({placeholders})", tuple(chunk)
                )
                for row in cursor.fetchall():
                    result[row["id"]] = dict(row)
        return result

    @with_write_retry()
    def prune_orphaned_edges(self) -> int:
        """Prunes exact edges whose target node no longer exists in the nodes table."""
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                DELETE FROM edges
                WHERE confidence = 'exact'
                  AND to_node_id NOT IN (SELECT id FROM nodes)
                """
            )
            deleted = cursor.rowcount
            if deleted > 0:
                conn.execute(
                    "DELETE FROM edge_targets "
                    "WHERE target_id NOT IN (SELECT DISTINCT to_node_id FROM edges)"
                )
                conn.commit()
            return max(0, deleted)

    @with_write_retry()
    def purge_orphaned_embeddings(self) -> int:
        """Purge vector embeddings whose referenced node_id no longer exists in nodes table."""
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                DELETE FROM node_embeddings
                WHERE node_id NOT IN (SELECT id FROM nodes)
                """
            )
            deleted = cursor.rowcount
            conn.commit()
            return max(0, deleted)
