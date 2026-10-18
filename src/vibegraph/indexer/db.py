import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal

from vibegraph.indexer.db_queries import DBQueriesMixin
from vibegraph.indexer.db_retry import with_write_retry
from vibegraph.indexer.models import Edge, Node

__all__ = ["Edge", "IndexerDB", "Node", "with_write_retry"]


class IndexerDB(DBQueriesMixin):
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            pkg_root = Path(__file__).resolve().parent.parent.parent.parent
            context_dir = pkg_root / "vibegraph_context"
            context_dir.mkdir(exist_ok=True)
            self.db_path = str(context_dir / "vibegraph.db")
        else:
            self.db_path = db_path

        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

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
            except Exception:
                pass

            try:
                cursor = conn.execute("PRAGMA table_info(edges)")
                edge_cols = {row["name"] for row in cursor.fetchall()}
                if edge_cols and "call_line" not in edge_cols:
                    conn.execute("ALTER TABLE edges ADD COLUMN call_line INTEGER")
            except Exception:
                pass

            with open(schema_path, encoding="utf-8") as f:
                conn.executescript(f.read())

    @with_write_retry()
    def upsert_node(self, node: Node) -> None:
        """Insert or replace a node and update FTS5 search index."""
        qualified = node.qualified_name or node.name
        with self._get_conn() as conn:
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
                    node.file_path,
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
                    node.file_path,
                ),
            )
            conn.commit()

    @with_write_retry()
    def upsert_edge(self, edge: Edge) -> None:
        """Insert an edge."""
        sql = (
            "INSERT INTO edges (from_node_id, to_node_id, relation_type, call_line) "
            "VALUES (?, ?, ?, ?)"
        )
        with self._get_conn() as conn:
            conn.execute(
                sql,
                (edge.from_node_id, edge.to_node_id, edge.relation_type, edge.call_line),
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
    ) -> None:
        """Atomically clear old file data and persist new nodes, edges, FTS5, and hash."""
        idx_time = indexed_time if indexed_time is not None else int(time.time())
        node_tuples = [
            (
                n.id,
                n.name,
                n.qualified_name or n.name,
                n.parent_id,
                n.kind,
                n.file_path,
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
                n.file_path,
            )
            for n in nodes
        ]
        edge_tuples = [
            (
                e.from_node_id,
                e.to_node_id,
                e.relation_type,
                e.call_line,
            )
            for e in edges
        ]

        with self._get_conn() as conn:
            # 1. Clear previous data for this file (inbound & outbound edges)
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
                    INSERT INTO edges (from_node_id, to_node_id, relation_type, call_line)
                    VALUES (?, ?, ?, ?)
                    """,
                    edge_tuples,
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

    @with_write_retry()
    def clear_file(self, file_path: str) -> None:
        """Remove all nodes, edges (inbound & outbound), FTS5, and file metadata."""
        with self._get_conn() as conn:
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
            conn.execute("DELETE FROM nodes WHERE file_path = ?", (file_path,))
            conn.execute("DELETE FROM indexed_files WHERE file_path = ?", (file_path,))
            conn.commit()

    def get_all_indexed_file_paths(self) -> set[str]:
        """Retrieve all indexed file paths currently recorded in the database."""
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT file_path FROM indexed_files")
            return {row[0] for row in cursor.fetchall()}

    def get_file_hash(self, file_path: str) -> str | None:
        """Retrieve stored content hash for a file."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT content_hash FROM indexed_files WHERE file_path = ?", (file_path,)
            )
            row = cursor.fetchone()
            return row[0] if row else None

    @with_write_retry()
    def update_file_hash(self, file_path: str, content_hash: str, updated_at: int) -> None:
        """Update or insert content hash for a file."""
        sql = (
            "INSERT OR REPLACE INTO indexed_files (file_path, content_hash, updated_at) "
            "VALUES (?, ?, ?)"
        )
        with self._get_conn() as conn:
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
            conn.execute(sql, (key, value))
            conn.commit()

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_graph_data(self, file_path: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """Return nodes and connected edges for visualization, optionally filtered by file."""
        with self._get_conn() as conn:
            if file_path:
                nodes_cursor = conn.execute("SELECT * FROM nodes WHERE file_path = ?", (file_path,))
            else:
                nodes_cursor = conn.execute("SELECT * FROM nodes")

            nodes = [dict(row) for row in nodes_cursor.fetchall()]
            if not nodes:
                return {"nodes": [], "edges": []}

            if file_path:
                node_ids = tuple(n["id"] for n in nodes)
                placeholders = ",".join("?" for _ in node_ids)
                query = (
                    "SELECT * FROM edges WHERE from_node_id IN "
                    f"({placeholders}) OR to_node_id IN ({placeholders})"
                )
                edges_cursor = conn.execute(query, node_ids + node_ids)
                edges = [dict(row) for row in edges_cursor.fetchall()]
            else:
                edges_cursor = conn.execute("SELECT * FROM edges")
                edges = [dict(row) for row in edges_cursor.fetchall()]

        return {"nodes": nodes, "edges": edges}

    @with_write_retry()
    def update_centrality_scores(self) -> dict[str, float]:
        """Compute and persist log-normalized in-degree graph centrality for dirty nodes."""
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
            conn.execute(sql)
            conn.commit()
            cursor = conn.execute("SELECT id, centrality FROM nodes WHERE centrality > 0")
            return {str(row[0]): float(row[1]) for row in cursor.fetchall()}

    def get_file_structure(
        self, file_path: str, limit: int = 100, offset: int = 0
    ) -> tuple[int, list[dict[str, Any]]]:
        """Return total count and paginated list of symbols in a file."""
        with self._get_conn() as conn:
            count_cursor = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE file_path = ?", (file_path,)
            )
            total = count_cursor.fetchone()[0]

            cursor = conn.execute(
                """SELECT id, name, qualified_name, parent_id, kind, signature,
                          start_line, end_line, decorators, visibility 
                   FROM nodes 
                   WHERE file_path = ? 
                   ORDER BY start_line
                   LIMIT ? OFFSET ?""",
                (file_path, limit, offset),
            )
            rows = [dict(r) for r in cursor.fetchall()]
        return total, rows

    def find_nodes_by_name(self, name: str, file_path: str | None = None) -> list[dict[str, Any]]:
        """Find nodes matching name or qualified_name, optionally scoped to a file."""
        with self._get_conn() as conn:
            query = (
                "SELECT id, name, qualified_name, parent_id, file_path, kind, "
                "start_line, end_line, signature, docstring "
                "FROM nodes WHERE (name = ? OR qualified_name = ?)"
            )
            params: list[Any] = [name, name]
            if file_path:
                query += " AND file_path = ?"
                params.append(file_path)
            cursor = conn.execute(query, tuple(params))
            return [dict(r) for r in cursor.fetchall()]

    def get_neighbors(self, node_id: str, direction: Literal["up", "down"]) -> list[dict[str, Any]]:
        """Get callers (up) or callees (down) for a node."""
        with self._get_conn() as conn:
            if direction == "up":
                query = """
                    SELECT e.from_node_id as neighbor_id, e.relation_type, e.call_line,
                           n.name, n.qualified_name, n.file_path, n.kind,
                           n.start_line, n.end_line, n.signature
                    FROM edges e
                    JOIN nodes n ON e.from_node_id = n.id
                    WHERE e.to_node_id = ?
                """
            else:
                query = """
                    SELECT e.to_node_id as neighbor_id, e.relation_type, e.call_line,
                           n.name, n.qualified_name, n.file_path, n.kind,
                           n.start_line, n.end_line, n.signature
                    FROM edges e
                    JOIN nodes n ON e.to_node_id = n.id
                    WHERE e.from_node_id = ?
                """
            cursor = conn.execute(query, (node_id,))
            return [dict(r) for r in cursor.fetchall()]

    def get_nodes_in_file(self, file_path: str) -> list[dict[str, Any]]:
        """Get all node IDs and names in a file."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT id, name, qualified_name FROM nodes WHERE file_path = ?", (file_path,)
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_incoming_dependents(self, node_id: str) -> list[dict[str, Any]]:
        """Find all incoming dependencies pointing to a node."""
        with self._get_conn() as conn:
            query = """
                SELECT DISTINCT n.id, n.name, n.qualified_name, n.file_path, n.kind, e.relation_type
                FROM edges e
                JOIN nodes n ON e.from_node_id = n.id
                WHERE e.to_node_id = ?
            """
            cursor = conn.execute(query, (node_id,))
            return [dict(r) for r in cursor.fetchall()]

    def get_symbol_references(self, node_id: str) -> list[dict[str, Any]]:
        """Find calls or references incoming to a node."""
        with self._get_conn() as conn:
            query = """
                SELECT n.name, n.qualified_name, n.file_path, n.start_line,
                       e.relation_type, e.call_line
                FROM edges e
                JOIN nodes n ON e.from_node_id = n.id
                WHERE e.to_node_id = ? AND e.relation_type IN ('calls', 'references')
            """
            cursor = conn.execute(query, (node_id,))
            return [dict(r) for r in cursor.fetchall()]

    def get_file_imports(self, file_path: str) -> list[dict[str, Any]]:
        """Find all imported modules and symbols from a file."""
        with self._get_conn() as conn:
            query = """
                SELECT DISTINCT n_to.name, n_to.file_path, n_to.kind
                FROM nodes n_from
                JOIN edges e ON n_from.id = e.from_node_id
                JOIN nodes n_to ON e.to_node_id = n_to.id
                WHERE n_from.file_path = ? AND e.relation_type = 'imports'
            """
            cursor = conn.execute(query, (file_path,))
            return [dict(r) for r in cursor.fetchall()]

    def get_nodes_by_ids(self, node_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch full node dictionaries keyed by node ID."""
        if not node_ids:
            return {}
        placeholders = ",".join("?" for _ in node_ids)
        with self._get_conn() as conn:
            cursor = conn.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders})", tuple(node_ids)
            )
            return {row["id"]: dict(row) for row in cursor.fetchall()}

    @with_write_retry()
    def upsert_embeddings(self, rows: list[tuple[str, bytes, int, str]]) -> None:
        """Batch insert or replace serialized float32 vector embeddings."""
        if not rows:
            return
        sql = (
            "INSERT OR REPLACE INTO node_embeddings (node_id, embedding, dimensions, model_id) "
            "VALUES (?, ?, ?, ?)"
        )
        with self._get_conn() as conn:
            conn.executemany(sql, rows)
            conn.commit()

    def get_all_embeddings(self, model_id: str | None = None) -> list[tuple[str, bytes, int]]:
        """Retrieve all stored node embeddings."""
        with self._get_conn() as conn:
            if model_id:
                cursor = conn.execute(
                    "SELECT node_id, embedding, dimensions FROM node_embeddings WHERE model_id = ?",
                    (model_id,),
                )
            else:
                cursor = conn.execute("SELECT node_id, embedding, dimensions FROM node_embeddings")
            return [(row[0], bytes(row[1]), int(row[2])) for row in cursor.fetchall()]

    def get_nodes_missing_embeddings(self, model_id: str) -> list[dict[str, Any]]:
        """Return nodes that do not yet have embeddings for the specified model."""
        sql = (
            "SELECT n.* FROM nodes n "
            "LEFT JOIN node_embeddings e ON n.id = e.node_id AND e.model_id = ? "
            "WHERE e.node_id IS NULL"
        )
        with self._get_conn() as conn:
            cursor = conn.execute(sql, (model_id,))
            return [dict(r) for r in cursor.fetchall()]
