import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel


class Node(BaseModel):
    id: str
    name: str
    kind: Literal[
        "function", "class", "module", "interface", "variable", "struct", "trait", "impl", "method"
    ]
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    signature: str | None = None
    docstring: str | None = None
    decorators: list[str] | None = None
    visibility: str | None = None


class Edge(BaseModel):
    from_node_id: str
    to_node_id: str
    relation_type: Literal[
        "calls", "defines", "inherits", "references", "imports", "implements", "returns"
    ]


class IndexerDB:
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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize the database schema if it doesn't exist."""
        schema_path = Path(__file__).parent / "schema.sql"
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema file not found at {schema_path}")

        with self._get_conn() as conn:
            with open(schema_path, encoding="utf-8") as f:
                conn.executescript(f.read())

    def upsert_node(self, node: Node) -> None:
        """Insert or replace a node."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO nodes (
                    id, name, kind, file_path, start_line, end_line, signature, docstring,
                    decorators, visibility
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.id,
                    node.name,
                    node.kind,
                    node.file_path,
                    node.start_line,
                    node.end_line,
                    node.signature,
                    node.docstring,
                    json.dumps(node.decorators) if node.decorators else None,
                    node.visibility,
                ),
            )
            conn.commit()

    def upsert_edge(self, edge: Edge) -> None:
        """Insert an edge."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO edges (from_node_id, to_node_id, relation_type)
                VALUES (?, ?, ?)
                """,
                (edge.from_node_id, edge.to_node_id, edge.relation_type),
            )
            conn.commit()

    def clear_file(self, file_path: str) -> None:
        """Remove all nodes and edges associated with a file."""
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT id FROM nodes WHERE file_path = ?", (file_path,))
            node_ids = [row[0] for row in cursor.fetchall()]

            if not node_ids:
                return

            placeholders = ",".join("?" for _ in node_ids)
            conn.execute(f"DELETE FROM edges WHERE from_node_id IN ({placeholders})", node_ids)
            conn.execute("DELETE FROM nodes WHERE file_path = ?", (file_path,))
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
                """SELECT id, name, kind, signature, start_line, end_line, decorators, visibility 
                   FROM nodes 
                   WHERE file_path = ? 
                   ORDER BY start_line
                   LIMIT ? OFFSET ?""",
                (file_path, limit, offset),
            )
            rows = [dict(r) for r in cursor.fetchall()]
        return total, rows

    def find_nodes_by_name(self, name: str, file_path: str | None = None) -> list[dict[str, Any]]:
        """Find nodes matching a name, optionally scoped to a file."""
        with self._get_conn() as conn:
            query = "SELECT id, name, file_path, kind FROM nodes WHERE name = ?"
            params: list[Any] = [name]
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
                    SELECT e.from_node_id as neighbor_id, e.relation_type, n.name, n.file_path 
                    FROM edges e
                    JOIN nodes n ON e.from_node_id = n.id
                    WHERE e.to_node_id = ?
                """
            else:
                query = """
                    SELECT e.to_node_id as neighbor_id, e.relation_type, n.name, n.file_path 
                    FROM edges e
                    JOIN nodes n ON e.to_node_id = n.id
                    WHERE e.from_node_id = ?
                """
            cursor = conn.execute(query, (node_id,))
            return [dict(r) for r in cursor.fetchall()]

    def get_nodes_in_file(self, file_path: str) -> list[dict[str, Any]]:
        """Get all node IDs and names in a file."""
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT id, name FROM nodes WHERE file_path = ?", (file_path,))
            return [dict(r) for r in cursor.fetchall()]

    def get_incoming_dependents(self, node_id: str) -> list[dict[str, Any]]:
        """Find all incoming dependencies pointing to a node."""
        with self._get_conn() as conn:
            query = """
                SELECT DISTINCT n.id, n.name, n.file_path, n.kind, e.relation_type
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
                SELECT n.name, n.file_path, n.start_line, e.relation_type
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

    def search_by_signature_pattern(self, pattern: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search functions matching SQL LIKE pattern in signature."""
        with self._get_conn() as conn:
            query = (
                "SELECT name, signature, file_path, start_line "
                "FROM nodes WHERE signature LIKE ? LIMIT ?"
            )
            cursor = conn.execute(query, (pattern, limit))
            return [dict(r) for r in cursor.fetchall()]
