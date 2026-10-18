"""AST, lexical, and recursive CTE query operations for IndexerDB."""

from __future__ import annotations

import re
import sqlite3
from typing import Any


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
                           WHEN LOWER(name) LIKE LOWER(?)
                             OR LOWER(qualified_name) LIKE LOWER(?) THEN 1
                           ELSE 2
                       END AS match_tier
                FROM nodes
                WHERE LOWER(name) LIKE LOWER(?)
                   OR LOWER(qualified_name) LIKE LOWER(?)
                   OR LOWER(signature) LIKE LOWER(?)
                ORDER BY match_tier ASC, LENGTH(qualified_name) ASC
                LIMIT ?
            """
            prefix = f"{q}%"
            sub = f"%{q}%"
            cursor = conn.execute(sql, (q, q, prefix, prefix, sub, sub, sub, limit))
            return [dict(r) for r in cursor.fetchall()]

    def find_path_recursive_cte(
        self, start_id: str, target_id: str, max_depth: int = 6
    ) -> list[dict[str, Any]] | None:
        """Find shortest execution path using native SQLite recursive CTE query."""
        if start_id == target_id:
            node = self.get_node(start_id)
            return [node] if node else None

        # Orphan protection: verify start and target exist in nodes table
        if not self.get_node(start_id) or not self.get_node(target_id):
            return None

        sql = """
        WITH RECURSIVE path_cte(current_id, depth, path_str) AS (
            SELECT ? AS current_id, 0 AS depth, ? AS path_str
            UNION ALL
            SELECT e.to_node_id, p.depth + 1,
                   p.path_str || ',' || e.to_node_id
            FROM edges e
            JOIN path_cte p ON e.from_node_id = p.current_id
            JOIN nodes n ON e.to_node_id = n.id
            WHERE p.depth < ?
              AND instr(',' || p.path_str || ',', ',' || e.to_node_id || ',') = 0
        )
        SELECT path_str FROM path_cte
        WHERE current_id = ?
        ORDER BY depth ASC
        LIMIT 1
        """
        with self._get_conn() as conn:
            cursor = conn.execute(sql, (start_id, start_id, max_depth, target_id))
            row = cursor.fetchone()
            if not row:
                return None
            path_ids = str(row[0]).split(",")
            nodes_by_id = self.get_nodes_by_ids(path_ids)
            if len(nodes_by_id) != len(path_ids):
                return None
            return [nodes_by_id[nid] for nid in path_ids]
