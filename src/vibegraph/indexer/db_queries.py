"""AST, lexical, and recursive CTE query operations for IndexerDB."""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Literal


class DBQueriesMixin:
    """Mixin providing AST, FTS5 BM25 lexical search, and recursive CTE pathfinding."""

    def _get_conn(self) -> sqlite3.Connection:
        raise NotImplementedError

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def get_nodes_by_ids(self, node_ids: list[str]) -> dict[str, dict[str, Any]]:
        raise NotImplementedError

    def search_lexical(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search nodes using SQLite FTS5 BM25 ranking."""
        tokens = re.findall(r"[A-Za-z0-9_]+", query)
        if not tokens:
            return []

        sql = """
            SELECT n.id, n.name, n.qualified_name, n.kind, n.file_path,
                   n.start_line, n.end_line, n.signature, n.docstring, n.centrality,
                   bm25(nodes_fts) as rank
            FROM nodes_fts f
            JOIN nodes n ON f.node_id = n.id
            WHERE nodes_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        with self._get_conn() as conn:
            and_expr = " AND ".join(f'"{t}"*' for t in tokens)
            or_expr = " OR ".join(f'"{t}"*' for t in tokens)
            results: list[dict[str, Any]] = []
            seen_ids: set[str] = set()

            for expr in (and_expr, or_expr):
                try:
                    cursor = conn.execute(sql, (expr, limit))
                    for row in cursor.fetchall():
                        r_dict = dict(row)
                        if r_dict["id"] not in seen_ids:
                            seen_ids.add(r_dict["id"])
                            results.append(r_dict)
                            if len(results) >= limit:
                                return results
                except sqlite3.OperationalError:
                    continue
            return results

    def search_by_signature_pattern(self, pattern: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search functions matching SQL LIKE pattern in signature."""
        with self._get_conn() as conn:
            query = (
                "SELECT id, name, qualified_name, kind, signature, file_path, start_line, end_line "
                "FROM nodes WHERE signature LIKE ? LIMIT ?"
            )
            cursor = conn.execute(query, (pattern, limit))
            return [dict(r) for r in cursor.fetchall()]

    def search_ast_exact_or_prefix(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search AST symbols ordered by exact match, prefix match, and substring match."""
        q = query.strip()
        if not q:
            return []
        with self._get_conn() as conn:
            sql = """
                SELECT id, name, qualified_name, kind, file_path,
                       start_line, end_line, signature, docstring, centrality,
                       CASE
                           WHEN LOWER(qualified_name) = LOWER(?)
                             OR LOWER(name) = LOWER(?) THEN 0
                           WHEN LOWER(name) LIKE LOWER(?) ESCAPE '\\'
                             OR LOWER(qualified_name) LIKE LOWER(?) ESCAPE '\\' THEN 1
                           ELSE 2
                       END AS match_tier
                FROM nodes
                WHERE LOWER(name) LIKE LOWER(?) ESCAPE '\\'
                   OR LOWER(qualified_name) LIKE LOWER(?) ESCAPE '\\'
                   OR LOWER(signature) LIKE LOWER(?) ESCAPE '\\'
                ORDER BY match_tier ASC, LENGTH(qualified_name) ASC
                LIMIT ?
            """
            escaped_q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            prefix = f"{escaped_q}%"
            sub = f"%{escaped_q}%"
            cursor = conn.execute(sql, (q, q, prefix, prefix, sub, sub, sub, limit))
            return [dict(r) for r in cursor.fetchall()]

    def find_path_recursive_cte(
        self,
        start_id: str,
        target_id: str,
        max_depth: int = 6,
        allowed_relations: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Find shortest execution path using native SQLite recursive CTE query."""
        if start_id == target_id:
            node = self.get_node(start_id)
            return [node] if node else None

        # Orphan protection: verify start and target exist in nodes table
        if not self.get_node(start_id) or not self.get_node(target_id):
            return None

        rel_filter = ""
        params: list[Any] = [start_id, start_id]
        if allowed_relations:
            placeholders = ",".join("?" for _ in allowed_relations)
            rel_filter = f" AND e.relation_type IN ({placeholders})"

        sql = f"""
        WITH RECURSIVE path_cte(current_id, depth, path_str) AS (
            SELECT ? AS current_id, 0 AS depth, ? AS path_str
            UNION ALL
            SELECT e.to_node_id, p.depth + 1,
                   p.path_str || ',' || e.to_node_id
            FROM edges e
            JOIN path_cte p ON e.from_node_id = p.current_id
            JOIN nodes n ON e.to_node_id = n.id
            WHERE p.depth < ?
              AND instr(',' || p.path_str || ',', ',' || e.to_node_id || ',') = 0{rel_filter}
        )
        SELECT path_str FROM path_cte
        WHERE current_id = ?
        ORDER BY depth ASC
        LIMIT 1
        """
        params.append(max_depth)
        if allowed_relations:
            params.extend(allowed_relations)
        params.append(target_id)

        with self._get_conn() as conn:
            cursor = conn.execute(sql, tuple(params))
            row = cursor.fetchone()
            if not row:
                return None
            path_ids = str(row[0]).split(",")
            nodes_by_id = self.get_nodes_by_ids(path_ids)
            if len(nodes_by_id) != len(path_ids):
                return None
            return [nodes_by_id[nid] for nid in path_ids]

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
