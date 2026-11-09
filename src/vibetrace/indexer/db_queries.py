"""AST, lexical, and recursive CTE query operations for IndexerDB."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any, Literal

from vibetrace.indexer.db_retry import with_write_retry

logger = logging.getLogger(__name__)


def _clean_path(path: str) -> str:
    """Normalize Windows backslashes and strip leading redundant './' prefixes."""
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


class DBQueriesMixin:
    """Mixin providing AST, FTS5 BM25 lexical search, and recursive CTE pathfinding."""

    def _get_conn(self) -> Any:
        raise NotImplementedError

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def get_nodes_by_ids(self, node_ids: list[str]) -> dict[str, dict[str, Any]]:
        raise NotImplementedError

    @with_write_retry()
    def link_cross_file_edges(self) -> int:
        """Resolves transitive re-exports and 2-level fallback edges across indexed files."""
        with self._get_conn() as conn:
            unresolved = conn.execute(
                """
                SELECT e.id, e.from_node_id, e.to_node_id, n.file_path AS caller_file,
                       et.target_file, et.target_symbol
                FROM edges e
                JOIN nodes n ON e.from_node_id = n.id
                LEFT JOIN nodes tgt ON e.to_node_id = tgt.id
                LEFT JOIN edge_targets et ON e.to_node_id = et.target_id
                WHERE tgt.id IS NULL
                  AND e.relation_type IN ('calls', 'references', 'inherits')
                """
            ).fetchall()
            if not unresolved:
                return 0

            # Build in-memory lookup maps for fast single-pass linking
            imports_map: dict[tuple[str, str], tuple[str, str]] = {}
            for r in conn.execute(
                "SELECT file_path, local_alias, target_file, target_symbol FROM file_imports"
            ).fetchall():
                imports_map[(r["file_path"], r["local_alias"])] = (
                    r["target_file"],
                    r["target_symbol"],
                )

            nodes_by_file_qual: dict[tuple[str, str], str] = {}
            nodes_by_name: dict[str, list[tuple[str, str]]] = {}
            for r in conn.execute(
                "SELECT id, name, qualified_name, file_path, kind FROM nodes WHERE kind != 'module'"
            ).fetchall():
                fp = r["file_path"]
                qn = r["qualified_name"]
                nm = r["name"]
                nid = r["id"]
                nodes_by_file_qual[(fp, qn)] = nid
                if qn == nm or (fp, nm) not in nodes_by_file_qual:
                    nodes_by_file_qual[(fp, nm)] = nid
                nodes_by_name.setdefault(nm, []).append((fp, nid))

            updates: list[tuple[str, str, int]] = []
            for row in unresolved:
                edge_id = row["id"]
                caller_file = row["caller_file"]
                t_file = row["target_file"] or caller_file
                t_sym = row["target_symbol"] or ""
                if not t_sym:
                    continue

                # Level 1: Follow transitive re-exports in `file_imports` (up to 4 hops)
                cur_file, cur_sym = t_file, t_sym
                resolved_exact_id: str | None = None
                for _ in range(4):
                    prefix = cur_sym.split(".", 1)[0]
                    rest = cur_sym.split(".", 1)[1] if "." in cur_sym else ""
                    imp = imports_map.get((cur_file, cur_sym)) or imports_map.get(
                        (cur_file, prefix)
                    )
                    if not imp:
                        break
                    next_file, next_sym = imp
                    if next_sym == "__module__":
                        cur_file = next_file
                        cur_sym = rest if rest else prefix
                    else:
                        cur_file = next_file
                        cur_sym = f"{next_sym}.{rest}" if rest else next_sym
                    cand_id = nodes_by_file_qual.get((cur_file, cur_sym))
                    if cand_id:
                        resolved_exact_id = cand_id
                        break

                if resolved_exact_id:
                    updates.append((resolved_exact_id, "exact", edge_id))
                    continue

                # Level 2: Heuristic fallback on bare method/function name
                bare_name = t_sym.split(".")[-1]
                candidates = nodes_by_name.get(bare_name, [])
                if not candidates:
                    continue

                # 2a. Check if any candidate resides in a file directly imported by caller_file
                caller_imported_files = {
                    tf for (cf, _), (tf, _) in imports_map.items() if cf == caller_file
                }
                imported_matches = [nid for fp, nid in candidates if fp in caller_imported_files]
                if len(imported_matches) == 1:
                    updates.append((imported_matches[0], "inferred", edge_id))
                elif len(candidates) == 1:
                    # 2b. Unique symbol across the entire repository
                    updates.append((candidates[0][1], "inferred", edge_id))

            if updates:
                conn.executemany(
                    "UPDATE edges SET to_node_id = ?, confidence = ? WHERE id = ?",
                    updates,
                )
                conn.commit()
            return len(updates)

    def get_edge_resolution_metrics(self) -> dict[str, Any]:
        """Compute resolution metrics across internal edges in the database."""
        with self._get_conn() as conn:
            # 1. Total call and reference edges
            rows = conn.execute(
                """
                SELECT e.id, e.from_node_id, e.to_node_id, e.confidence, e.relation_type,
                       n.file_path AS caller_file, et.target_file, et.target_symbol,
                       tgt.id AS resolved_target_id, tgt.name AS resolved_target_name
                FROM edges e
                JOIN nodes n ON e.from_node_id = n.id
                LEFT JOIN nodes tgt ON e.to_node_id = tgt.id
                LEFT JOIN edge_targets et ON e.to_node_id = et.target_id
                WHERE e.relation_type IN ('calls', 'references')
                """
            ).fetchall()

            total_internal = 0
            resolved_ast_exact = 0
            resolved_ast_inferred = 0
            resolved_lsp = 0
            unresolved_items: list[dict[str, Any]] = []

            for r in rows:
                target_file = r["target_file"] or ""
                # Skip pure external third-party/stdlib packages with target_file == 'external'
                if target_file == "external":
                    continue

                total_internal += 1
                if r["resolved_target_id"] is not None:
                    conf = r["confidence"] or "exact"
                    if conf == "lsp":
                        resolved_lsp += 1
                    elif conf == "inferred":
                        resolved_ast_inferred += 1
                    else:
                        resolved_ast_exact += 1
                else:
                    unresolved_items.append(
                        {
                            "edge_id": r["id"],
                            "from_node_id": r["from_node_id"],
                            "caller_file": r["caller_file"],
                            "target_symbol": r["target_symbol"] or r["to_node_id"],
                        }
                    )

            resolved_total = resolved_ast_exact + resolved_ast_inferred + resolved_lsp
            pct = (
                round((resolved_total / total_internal * 100.0), 2) if total_internal > 0 else 100.0
            )

            return {
                "total_internal_call_edges": total_internal,
                "resolved_total": resolved_total,
                "resolved_ast_exact": resolved_ast_exact,
                "resolved_ast_inferred": resolved_ast_inferred,
                "resolved_lsp": resolved_lsp,
                "unresolved_internal_count": len(unresolved_items),
                "unresolved_internal_edges": unresolved_items,
                "resolution_percentage": pct,
            }

    def search_lexical(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search nodes using SQLite FTS5 BM25 ranking."""
        tokens = re.findall(r"\w+", query)
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
                except sqlite3.OperationalError as e:
                    logger.debug("FTS search syntax or operational error for expr %s: %s", expr, e)
                    continue
            return results

    def search_by_signature_pattern(
        self, pattern: str, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Search functions matching SQL LIKE pattern in signature, ranked by centrality."""
        p = pattern if ("%" in pattern or "_" in pattern) else f"%{pattern}%"
        with self._get_conn() as conn:
            query = (
                "SELECT id, name, qualified_name, kind, signature, file_path, "
                "start_line, end_line, centrality "
                "FROM nodes WHERE signature LIKE ? "
                "ORDER BY centrality DESC, file_path ASC, start_line ASC LIMIT ? OFFSET ?"
            )
            cursor = conn.execute(query, (p, limit, offset))
            return [dict(r) for r in cursor.fetchall()]

    def search_ast_exact_or_prefix(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search AST symbols ordered by exact match, prefix match, and substring match."""
        q = query.strip().removesuffix("()").strip()
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
                ORDER BY match_tier ASC, centrality DESC, LENGTH(qualified_name) ASC
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
                   p.path_str || char(31) || e.to_node_id
            FROM edges e
            JOIN path_cte p ON e.from_node_id = p.current_id
            JOIN nodes n ON e.to_node_id = n.id
            WHERE p.depth < ?
              AND instr(
                  char(31) || p.path_str || char(31),
                  char(31) || e.to_node_id || char(31)
              ) = 0{rel_filter}
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
            path_ids = str(row[0]).split("\x1f")
            nodes_by_id = self.get_nodes_by_ids(path_ids)
            if len(nodes_by_id) != len(path_ids):
                return None
            return [nodes_by_id[nid] for nid in path_ids]

    def get_file_structure(
        self, file_path: str, limit: int = 100, offset: int = 0
    ) -> tuple[int, list[dict[str, Any]]]:
        """Return total count and paginated list of symbols in a file."""
        norm_path = _clean_path(file_path)
        with self._get_conn() as conn:
            count_cursor = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE file_path = ?", (norm_path,)
            )
            total = count_cursor.fetchone()[0]

            cursor = conn.execute(
                """SELECT id, name, qualified_name, parent_id, kind, signature,
                          start_line, end_line, decorators, visibility 
                   FROM nodes 
                   WHERE file_path = ? 
                   ORDER BY start_line
                   LIMIT ? OFFSET ?""",
                (norm_path, limit, offset),
            )
            rows = [dict(r) for r in cursor.fetchall()]
        for r in rows:
            raw_dec = r.get("decorators")
            if raw_dec and isinstance(raw_dec, str):
                try:
                    r["decorators"] = json.loads(raw_dec)
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning("Malformed decorators JSON for node %s: %s", r.get("id"), e)
                    r["decorators"] = []
            elif not raw_dec:
                r["decorators"] = []
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
                params.append(_clean_path(file_path))
            query += " ORDER BY file_path ASC, start_line ASC"
            cursor = conn.execute(query, tuple(params))
            return [dict(r) for r in cursor.fetchall()]

    def get_neighbors(
        self,
        node_id: str,
        direction: Literal["up", "down"],
        relation_types: tuple[str, ...] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get callers (up) or callees (down) for a node, optionally filtered by relation types."""
        with self._get_conn() as conn:
            filter_clause = ""
            params: list[Any] = [node_id]
            if relation_types:
                placeholders = ",".join("?" for _ in relation_types)
                filter_clause = f"AND e.relation_type IN ({placeholders})"
                params.extend(relation_types)

            if direction == "up":
                query = f"""
                    SELECT e.from_node_id as neighbor_id, e.relation_type, e.call_line,
                           n.name, n.qualified_name, n.file_path, n.kind,
                           n.start_line, n.end_line, n.signature
                    FROM edges e
                    JOIN nodes n ON e.from_node_id = n.id
                    WHERE e.to_node_id = ? {filter_clause}
                    ORDER BY n.file_path ASC, n.start_line ASC, n.name ASC
                """
            else:
                query = f"""
                    SELECT e.to_node_id as neighbor_id, e.relation_type, e.call_line,
                           n.name, n.qualified_name, n.file_path, n.kind,
                           n.start_line, n.end_line, n.signature
                    FROM edges e
                    JOIN nodes n ON e.to_node_id = n.id
                    WHERE e.from_node_id = ? {filter_clause}
                    ORDER BY n.file_path ASC, n.start_line ASC, n.name ASC
                """
            cursor = conn.execute(query, tuple(params))
            return [dict(r) for r in cursor.fetchall()]

    def get_nodes_in_file(self, file_path: str) -> list[dict[str, Any]]:
        """Get all nodes in a file ordered by line number."""
        norm_path = _clean_path(file_path)
        with self._get_conn() as conn:
            query = (
                "SELECT id, name, qualified_name, kind, file_path, "
                "start_line, end_line, signature, docstring FROM nodes "
                "WHERE file_path = ? ORDER BY start_line ASC, name ASC"
            )
            cursor = conn.execute(query, (norm_path,))
            return [dict(r) for r in cursor.fetchall()]

    def get_incoming_dependents(self, node_id: str) -> list[dict[str, Any]]:
        """Find all incoming dependencies pointing to a node."""
        with self._get_conn() as conn:
            query = """
                SELECT DISTINCT n.id, n.name, n.qualified_name, n.file_path, n.kind, e.relation_type
                FROM edges e
                JOIN nodes n ON e.from_node_id = n.id
                WHERE e.to_node_id = ?
                ORDER BY n.file_path ASC, n.name ASC, e.relation_type ASC
            """
            cursor = conn.execute(query, (node_id,))
            return [dict(r) for r in cursor.fetchall()]

    def get_symbol_references(self, node_id: str) -> list[dict[str, Any]]:
        """Find calls, references, inheritance, implementations, or routes incoming to a node."""
        with self._get_conn() as conn:
            query = """
                SELECT n.name, n.qualified_name, n.file_path, n.start_line,
                       e.relation_type, e.call_line
                FROM edges e
                JOIN nodes n ON e.from_node_id = n.id
                WHERE e.to_node_id = ?
                  AND e.relation_type IN ('calls', 'references', 'inherits', 'implements', 'routes')
                ORDER BY n.file_path ASC, e.call_line ASC, n.name ASC
            """
            cursor = conn.execute(query, (node_id,))
            return [dict(r) for r in cursor.fetchall()]

    def get_file_imports(self, file_path: str) -> list[dict[str, Any]]:
        """Find all imported modules and symbols from a file."""
        norm_path = _clean_path(file_path)
        with self._get_conn() as conn:
            query = """
                SELECT DISTINCT n_to.name, n_to.file_path, n_to.kind
                FROM nodes n_from
                JOIN edges e ON n_from.id = e.from_node_id
                JOIN nodes n_to ON e.to_node_id = n_to.id
                WHERE n_from.file_path = ? AND e.relation_type = 'imports'
                ORDER BY n_to.file_path ASC, n_to.name ASC
            """
            cursor = conn.execute(query, (norm_path,))
            return [dict(r) for r in cursor.fetchall()]

    def get_file_dependents(self, file_path: str) -> list[dict[str, Any]]:
        """Find all project files and symbols that import from the given file."""
        norm_path = _clean_path(file_path)
        with self._get_conn() as conn:
            query = """
                SELECT DISTINCT n_from.file_path as dependent_file,
                                n_from.name as dependent_name,
                                n_to.name as imported_symbol
                FROM nodes n_to
                JOIN edges e ON n_to.id = e.to_node_id
                JOIN nodes n_from ON e.from_node_id = n_from.id
                WHERE n_to.file_path = ? AND e.relation_type = 'imports' AND n_from.file_path != ?
                UNION
                SELECT DISTINCT fi.file_path as dependent_file,
                                fi.local_alias as dependent_name,
                                fi.target_symbol as imported_symbol
                FROM file_imports fi
                WHERE fi.target_file = ? AND fi.file_path != ?
                ORDER BY dependent_file ASC, dependent_name ASC
            """
            cursor = conn.execute(query, (norm_path, norm_path, norm_path, norm_path))
            return [dict(r) for r in cursor.fetchall()]

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

    def get_nodes_missing_embeddings(
        self, model_id: str, dimensions: int | None = None
    ) -> list[dict[str, Any]]:
        """Return nodes that do not yet have embeddings for the specified model and dimension."""
        if dimensions is not None:
            sql = (
                "SELECT n.* FROM nodes n "
                "LEFT JOIN node_embeddings e ON n.id = e.node_id "
                "  AND e.model_id = ? AND e.dimensions = ? "
                "WHERE e.node_id IS NULL "
                "ORDER BY n.file_path ASC, n.start_line ASC, n.id ASC"
            )
            params: tuple[Any, ...] = (model_id, dimensions)
        else:
            sql = (
                "SELECT n.* FROM nodes n "
                "LEFT JOIN node_embeddings e ON n.id = e.node_id AND e.model_id = ? "
                "WHERE e.node_id IS NULL "
                "ORDER BY n.file_path ASC, n.start_line ASC, n.id ASC"
            )
            params = (model_id,)
        with self._get_conn() as conn:
            cursor = conn.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]

    def get_uncalled_private_functions(self, include_tests: bool = False) -> list[dict[str, Any]]:
        """Finds private/internal functions and methods with zero incoming calls or references."""
        test_filter = (
            ""
            if include_tests
            else """
              AND n.file_path NOT LIKE '%tests/%'
              AND n.file_path NOT LIKE '%test_%'
        """
        )
        sql = f"""
            SELECT n.id, n.name, n.qualified_name, n.kind, n.file_path,
                   n.start_line, n.end_line, n.signature, n.decorators
            FROM nodes n
            WHERE n.kind IN ('function', 'method')
              AND (
                  (substr(n.name, 1, 1) = '_' AND substr(n.name, 1, 2) != '__')
                  OR n.visibility = 'private'
              )
              {test_filter}
              AND (n.decorators IS NULL OR n.decorators NOT LIKE '%abstractmethod%')
              AND NOT EXISTS (
                  SELECT 1 FROM nodes p
                  WHERE p.id = n.parent_id AND p.kind IN ('function', 'method')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM edges e
                  WHERE e.to_node_id = n.id
                    AND e.relation_type IN ('calls', 'references', 'routes')
              )
            ORDER BY n.file_path ASC, n.start_line ASC
        """
        with self._get_conn() as conn:
            cursor = conn.execute(sql)
            return [dict(r) for r in cursor.fetchall()]

    def get_statistics(self) -> dict[str, int]:
        """Returns total counts for nodes, edges, and indexed files in the graph."""
        with self._get_conn() as conn:
            node_c = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            edge_c = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            file_c = conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()[0]
            return {
                "nodes": int(node_c or 0),
                "edges": int(edge_c or 0),
                "files": int(file_c or 0),
            }
