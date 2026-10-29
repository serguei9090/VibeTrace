"""Cross-repository contract linker for HTTP routes, RPC services, and shared types."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from pydantic import BaseModel, Field


def _normalize_route(route_str: str) -> str:
    """Normalize HTTP method and path for robust cross-repo matching.

    Examples:
        'GET /api/v1/users/' -> 'GET /api/v1/users'
        'POST http://localhost:8000/api/checkout' -> 'POST /api/checkout'
        '/api/checkout' -> 'ANY /api/checkout'
    """
    clean = route_str.strip()
    # Strip URL origin if present
    clean = re.sub(r"^https?://[^/]+", "", clean)
    parts = clean.split(maxsplit=1)
    if len(parts) == 2:
        method = parts[0].upper()
        path = parts[1].rstrip("/")
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{method} {path}"
    path = clean.rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"ANY {path}"


class CrossRepoLink(BaseModel):
    """Synthesized cross-repository contract edge between a client and a server handler."""

    source_repo: str
    source_symbol: str
    source_file: str
    source_line: int | None = None
    target_repo: str
    target_symbol: str
    target_file: str
    target_line: int | None = None
    protocol: str = "http"
    contract_key: str = Field(
        ..., description="Matched contract identifier (e.g., 'POST /api/checkout')"
    )


class CrossRepoLinker:
    """Discovers and establishes cross-repository links across attached member databases."""

    def __init__(self, conn: sqlite3.Connection, attached_schemas: dict[str, str]) -> None:
        self.conn = conn
        self.attached = attached_schemas

    def discover_links(self) -> list[CrossRepoLink]:
        """Correlate client-side invocations with server routes across repositories."""
        # 1. Map client requests: contract_key -> list of caller nodes
        client_calls: dict[str, list[dict[str, Any]]] = {}
        # 2. Map server routes: contract_key -> list of handler nodes
        server_handlers: dict[str, list[dict[str, Any]]] = {}

        ep_filter = (
            "WHERE ep.kind = 'endpoint' OR ep.name LIKE 'GET %' OR ep.name LIKE 'POST %' "
            "OR ep.name LIKE 'PUT %' OR ep.name LIKE 'DELETE %' OR ep.name LIKE 'PATCH %'"
        )

        for repo_name, alias in self.attached.items():
            try:
                sql_client = (
                    f"SELECT n.name AS caller_name, n.file_path, e.call_line, "
                    f"ep.name AS endpoint_name "
                    f"FROM {alias}.edges e "
                    f"JOIN {alias}.nodes n ON e.from_node_id = n.id "
                    f"JOIN {alias}.nodes ep ON e.to_node_id = ep.id "
                    f"{ep_filter}"
                )
                cur_client = self.conn.execute(sql_client)
                for row in cur_client.fetchall():
                    norm_k = _normalize_route(row["endpoint_name"])
                    client_calls.setdefault(norm_k, []).append(
                        {
                            "repo": repo_name,
                            "symbol": row["caller_name"],
                            "file": row["file_path"],
                            "line": row["call_line"],
                        }
                    )
            except Exception:
                pass

            try:
                sql_server = (
                    f"SELECT ep.name AS endpoint_name, n.name AS handler_name, "
                    f"n.file_path, n.start_line "
                    f"FROM {alias}.edges e "
                    f"JOIN {alias}.nodes ep ON e.from_node_id = ep.id "
                    f"JOIN {alias}.nodes n ON e.to_node_id = n.id "
                    f"WHERE (e.relation_type = 'routes' OR ep.kind = 'endpoint') "
                    f"  AND (ep.name LIKE 'GET %' OR ep.name LIKE 'POST %' "
                    f"       OR ep.name LIKE 'PUT %' OR ep.name LIKE 'DELETE %' "
                    f"       OR ep.name LIKE 'PATCH %')"
                )
                cur_server = self.conn.execute(sql_server)
                for row in cur_server.fetchall():
                    norm_k = _normalize_route(row["endpoint_name"])
                    server_handlers.setdefault(norm_k, []).append(
                        {
                            "repo": repo_name,
                            "symbol": row["handler_name"],
                            "file": row["file_path"],
                            "line": row["start_line"],
                        }
                    )
            except Exception:
                pass

        links: list[CrossRepoLink] = []

        # Correlate client calls with server handlers across different repositories
        for contract_key, callers in client_calls.items():
            handlers = server_handlers.get(contract_key, [])
            for c in callers:
                for h in handlers:
                    if c["repo"] != h["repo"]:
                        links.append(
                            CrossRepoLink(
                                source_repo=c["repo"],
                                source_symbol=c["symbol"],
                                source_file=c["file"],
                                source_line=c["line"],
                                target_repo=h["repo"],
                                target_symbol=h["symbol"],
                                target_file=h["file"],
                                target_line=h["line"],
                                protocol="http",
                                contract_key=contract_key,
                            )
                        )

        return links
