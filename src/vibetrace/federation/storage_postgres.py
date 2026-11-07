"""PostgreSQL storage backend adapter for enterprise centralized multi-repo federation."""

from __future__ import annotations

import logging
from typing import Any

from vibetrace.federation.models import (
    FederatedCaller,
    FederatedGuardReport,
    FederatedNode,
    MemberStatus,
    RepoMember,
)

logger = logging.getLogger(__name__)

POSTGRES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vibetrace_projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vibetrace_repos (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES vibetrace_projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vibetrace_nodes (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES vibetrace_projects(id) ON DELETE CASCADE,
    repo_id TEXT REFERENCES vibetrace_repos(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    file_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    signature TEXT
);

CREATE TABLE IF NOT EXISTS vibetrace_edges (
    id BIGSERIAL PRIMARY KEY,
    project_id TEXT REFERENCES vibetrace_projects(id) ON DELETE CASCADE,
    repo_id TEXT REFERENCES vibetrace_repos(id) ON DELETE CASCADE,
    from_node_id TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    call_line INTEGER
);

CREATE INDEX IF NOT EXISTS idx_vt_nodes_search ON vibetrace_nodes(name, qualified_name);
CREATE INDEX IF NOT EXISTS idx_vt_nodes_repo ON vibetrace_nodes(project_id, repo_id);
CREATE INDEX IF NOT EXISTS idx_vt_edges_lookup ON vibetrace_edges(to_node_id, project_id);
"""


class PostgresStorageBackend:
    """Enterprise PostgreSQL storage backend for centralized project federation."""

    def __init__(
        self,
        postgres_url: str,
        project_id: str,
        members: list[RepoMember],
        connection: Any | None = None,
    ) -> None:
        self.postgres_url = postgres_url
        self.project_id = project_id
        self.members = members
        self._conn = connection

    def _get_connection(self) -> Any:
        if self._conn is not None:
            return self._conn
        try:
            import importlib

            psycopg = importlib.import_module("psycopg")
            self._conn = psycopg.connect(self.postgres_url)
            return self._conn
        except ImportError as err:
            raise ImportError(
                "PostgreSQL driver 'psycopg' is required for enterprise backend. "
                "Install it using `uv pip install psycopg[binary]`."
            ) from err

    def init_schema(self) -> None:
        """Initialize required PostgreSQL tables and indices."""
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(POSTGRES_SCHEMA_SQL)
        conn.commit()

    def close(self) -> None:
        """Close active PostgreSQL connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def get_member_statuses(self) -> list[MemberStatus]:
        """Query repository statistics from PostgreSQL."""
        conn = self._get_connection()
        statuses: list[MemberStatus] = []

        for member in self.members:
            node_count = 0
            edge_count = 0
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM vibetrace_nodes "
                        "WHERE project_id = %s AND repo_id = %s",
                        (self.project_id, member.name),
                    )
                    row = cur.fetchone()
                    if row:
                        node_count = row[0]

                    cur.execute(
                        "SELECT COUNT(*) FROM vibetrace_edges "
                        "WHERE project_id = %s AND repo_id = %s",
                        (self.project_id, member.name),
                    )
                    row_e = cur.fetchone()
                    if row_e:
                        edge_count = row_e[0]
            except Exception as e:
                logger.warning("Error fetching Postgres status for member %s: %s", member.name, e)
                try:
                    conn.rollback()
                except Exception:
                    pass

            statuses.append(
                MemberStatus(
                    name=member.name,
                    path=member.path,
                    resolved_path=member.path,
                    is_indexed=node_count > 0,
                    node_count=node_count,
                    edge_count=edge_count,
                    db_size_bytes=0,
                )
            )
        return statuses

    def search(
        self,
        query: str,
        repo: str | None = None,
        limit: int = 20,
    ) -> list[FederatedNode]:
        """Search symbol definitions in PostgreSQL."""
        conn = self._get_connection()
        like_term = f"%{query}%"

        sql = (
            "SELECT repo_id, id, name, qualified_name, kind, file_path, "
            "start_line, end_line, signature "
            "FROM vibetrace_nodes "
            "WHERE project_id = %s AND (name = %s OR qualified_name = %s OR name ILIKE %s) "
        )
        params: list[Any] = [self.project_id, query, query, like_term]

        if repo:
            sql += "AND repo_id = %s "
            params.append(repo)

        sql += "ORDER BY CASE WHEN name = %s THEN 0 ELSE 1 END LIMIT %s"
        params.extend([query, limit])

        results: list[FederatedNode] = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                for row in cur.fetchall():
                    results.append(
                        FederatedNode(
                            repo=row[0],
                            id=row[1],
                            name=row[2],
                            qualified_name=row[3],
                            kind=row[4],
                            file_path=row[5],
                            start_line=row[6],
                            end_line=row[7],
                            signature=row[8],
                        )
                    )
        except Exception:
            return []

        return results

    def get_cross_repo_guard(
        self,
        symbol_name: str,
        target_repo: str | None = None,
    ) -> FederatedGuardReport:
        """Trace callers referencing symbol_name in PostgreSQL."""
        conn = self._get_connection()
        def_repo = target_repo or ""
        def_file = ""
        target_id = ""

        try:
            with conn.cursor() as cur:
                if not def_repo:
                    cur.execute(
                        "SELECT repo_id, id, file_path FROM vibetrace_nodes "
                        "WHERE project_id = %s AND name = %s "
                        "ORDER BY repo_id ASC, file_path ASC, start_line ASC LIMIT 1",
                        (self.project_id, symbol_name),
                    )
                else:
                    cur.execute(
                        "SELECT repo_id, id, file_path FROM vibetrace_nodes "
                        "WHERE project_id = %s AND repo_id = %s AND name = %s "
                        "ORDER BY file_path ASC, start_line ASC LIMIT 1",
                        (self.project_id, def_repo, symbol_name),
                    )
                row = cur.fetchone()
                if row:
                    def_repo = row[0]
                    target_id = row[1]
                    def_file = row[2]
        except Exception as e:
            logger.warning("Error looking up definition for %s in Postgres: %s", symbol_name, e)
            try:
                conn.rollback()
            except Exception:
                pass

        callers_by_repo: dict[str, list[dict[str, Any]]] = {}
        total_callers = 0

        try:
            with conn.cursor() as cur:
                sql = (
                    "SELECT e.repo_id, e.call_line, n.name, n.file_path, n.kind "
                    "FROM vibetrace_edges e "
                    "JOIN vibetrace_nodes n "
                    "  ON e.from_node_id = n.id AND e.project_id = n.project_id "
                    "WHERE e.project_id = %s AND "
                    "(e.to_node_id = %s OR e.to_node_id = %s "
                    " OR e.to_node_id LIKE %s OR e.to_node_id LIKE %s) "
                    "ORDER BY e.call_line ASC"
                )
                cur.execute(
                    sql,
                    (
                        self.project_id,
                        target_id or symbol_name,
                        symbol_name,
                        f"%::{symbol_name}",
                        f"%.{symbol_name}",
                    ),
                )
                for r_id, call_l, caller_name, caller_file, caller_kind in cur.fetchall():
                    caller = FederatedCaller(
                        calling_repo=r_id,
                        caller_symbol=caller_name,
                        caller_file=caller_file,
                        call_line=call_l,
                        caller_kind=caller_kind or "function",
                    )
                    callers_by_repo.setdefault(r_id, []).append(caller.model_dump())
                    total_callers += 1
        except Exception as e:
            logger.warning("Error looking up callers for %s in Postgres: %s", symbol_name, e)
            try:
                conn.rollback()
            except Exception:
                pass

        risk = "LOW"
        if total_callers > 10 or len(callers_by_repo) > 1:
            risk = "HIGH"
        elif total_callers > 3:
            risk = "MEDIUM"

        advisory = (
            f"Pre-flight clear: 0 callers across all project repositories for '{symbol_name}'."
            if total_callers == 0
            else f"Cross-repo blast radius: {total_callers} caller(s) across "
            f"{len(callers_by_repo)} repository(ies) must be verified "
            f"before modifying '{symbol_name}'."
        )

        return FederatedGuardReport(
            target_symbol=symbol_name,
            target_repo=def_repo or (target_repo or "unknown"),
            target_file=def_file or "unknown",
            total_callers=total_callers,
            callers_by_repo=callers_by_repo,
            risk_level=risk,
            safety_advisory=advisory,
        )
