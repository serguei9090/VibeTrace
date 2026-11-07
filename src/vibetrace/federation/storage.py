"""SQLite ATTACH federation engine for cross-repository queries."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vibetrace.federation.cross_repo_linker import CrossRepoLink, CrossRepoLinker
from vibetrace.federation.models import (
    FederatedCaller,
    FederatedGuardReport,
    FederatedNode,
    MemberStatus,
    RepoMember,
)


def _sanitize_alias(name: str) -> str:
    """Produce a safe SQL schema identifier from a repository name."""
    clean = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if clean and clean[0].isdigit():
        clean = f"r_{clean}"
    return f"repo_{clean}"


class FederatedSQLiteEngine:
    """Manages an in-memory SQLite session with attached member repository databases."""

    def __init__(self, project_root: Path, members: list[RepoMember]) -> None:
        self.project_root = project_root.resolve()
        self.members = members
        self._conn: sqlite3.Connection | None = None
        self._attached: dict[str, str] = {}  # repo_name -> schema_alias

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            attached: dict[str, str] = {}

            for member in self.members:
                repo_dir = (self.project_root / member.path).resolve()
                if not repo_dir.is_relative_to(self.project_root.resolve()):
                    continue
                db_path = (repo_dir / member.storage_path).resolve()
                if not db_path.is_relative_to(self.project_root.resolve()):
                    continue
                if db_path.exists():
                    alias = _sanitize_alias(member.name)
                    # Deduplicate alias if collision occurs
                    orig_alias = alias
                    counter = 2
                    while alias in attached.values():
                        alias = f"{orig_alias}_{counter}"
                        counter += 1

                    try:
                        conn.execute(f"ATTACH DATABASE ? AS {alias}", (str(db_path),))
                        attached[member.name] = alias
                    except Exception:
                        pass

            self._conn = conn
            self._attached = attached
        return self._conn

    def close(self) -> None:
        """Close the federated SQLite connection and release file locks."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            self._attached.clear()

    def get_member_statuses(self) -> list[MemberStatus]:
        """Inspect and return current indexing and size status for all configured members."""
        statuses: list[MemberStatus] = []
        conn = self._get_conn()

        for member in self.members:
            repo_dir = (self.project_root / member.path).resolve()
            db_path = (repo_dir / member.storage_path).resolve()
            is_indexed = db_path.exists()
            node_count = 0
            edge_count = 0
            db_size = db_path.stat().st_size if is_indexed else 0
            last_mod = None

            if is_indexed:
                mtime = db_path.stat().st_mtime
                last_mod = datetime.fromtimestamp(mtime, tz=UTC).isoformat()
                alias = self._attached.get(member.name)
                if alias:
                    try:
                        row_n = conn.execute(f"SELECT count(*) FROM {alias}.nodes").fetchone()
                        if row_n:
                            node_count = row_n[0]
                        row_e = conn.execute(f"SELECT count(*) FROM {alias}.edges").fetchone()
                        if row_e:
                            edge_count = row_e[0]
                    except Exception:
                        pass

            statuses.append(
                MemberStatus(
                    name=member.name,
                    path=member.path,
                    resolved_path=str(repo_dir),
                    is_indexed=is_indexed,
                    node_count=node_count,
                    edge_count=edge_count,
                    db_size_bytes=db_size,
                    last_modified=last_mod,
                )
            )
        return statuses

    def get_cross_repo_links(self) -> list[CrossRepoLink]:
        """Correlate HTTP routes and client invocations across attached repositories."""
        conn = self._get_conn()
        linker = CrossRepoLinker(conn, self._attached)
        return linker.discover_links()

    def search(
        self,
        query: str,
        repo: str | None = None,
        limit: int = 20,
    ) -> list[FederatedNode]:
        """Perform symbol name and prefix search across one or all member repositories."""
        conn = self._get_conn()
        targets: list[tuple[str, str]] = []

        if repo:
            if repo in self._attached:
                targets.append((repo, self._attached[repo]))
        else:
            targets = list(self._attached.items())

        if not targets:
            return []

        results: list[FederatedNode] = []
        like_term = f"%{query}%"

        for repo_name, alias in targets:
            try:
                cursor = conn.execute(
                    f"SELECT id, name, qualified_name, kind, file_path, "
                    f"start_line, end_line, signature "
                    f"FROM {alias}.nodes "
                    f"WHERE name = ? OR qualified_name = ? OR name LIKE ? "
                    f"ORDER BY CASE WHEN name = ? THEN 0 WHEN name LIKE ? THEN 1 ELSE 2 END "
                    f"LIMIT ?",
                    (query, query, like_term, query, f"{query}%", limit),
                )
                for row in cursor.fetchall():
                    results.append(
                        FederatedNode(
                            repo=repo_name,
                            id=row["id"],
                            name=row["name"],
                            qualified_name=row["qualified_name"],
                            kind=row["kind"],
                            file_path=row["file_path"],
                            start_line=row["start_line"],
                            end_line=row["end_line"],
                            signature=row["signature"],
                        )
                    )
                    if len(results) >= limit:
                        break
            except Exception:
                continue

            if len(results) >= limit:
                break

        return results[:limit]

    def get_cross_repo_guard(
        self,
        symbol_name: str,
        target_repo: str | None = None,
    ) -> FederatedGuardReport:
        """Trace callers of a symbol across all attached member repositories."""
        conn = self._get_conn()
        def_repo = target_repo or ""
        def_file = ""
        target_id = ""

        # 1. Locate definition if target_repo was omitted
        if not def_repo:
            for r_name, alias in self._attached.items():
                try:
                    row = conn.execute(
                        f"SELECT id, file_path FROM {alias}.nodes WHERE name = ? LIMIT 1",
                        (symbol_name,),
                    ).fetchone()
                    if row:
                        def_repo = r_name
                        target_id = row["id"]
                        def_file = row["file_path"]
                        break
                except Exception:
                    continue
        elif def_repo in self._attached:
            alias = self._attached[def_repo]
            try:
                row = conn.execute(
                    f"SELECT id, file_path FROM {alias}.nodes WHERE name = ? LIMIT 1",
                    (symbol_name,),
                ).fetchone()
                if row:
                    target_id = row["id"]
                    def_file = row["file_path"]
            except Exception:
                pass

        callers_by_repo: dict[str, list[dict[str, Any]]] = {}
        total_callers = 0

        # 2. Search edges in EVERY attached repository for calls referencing target
        for r_name, alias in self._attached.items():
            repo_callers: list[dict[str, Any]] = []
            try:
                where_clause = (
                    "WHERE (e.to_node_id = ? OR e.to_node_id = ? "
                    "OR e.to_node_id LIKE ? OR e.to_node_id LIKE ?)"
                )
                cursor = conn.execute(
                    f"SELECT e.from_node_id, e.call_line, n.name AS caller_name, "
                    f"n.file_path AS caller_file, n.kind AS caller_kind "
                    f"FROM {alias}.edges e "
                    f"JOIN {alias}.nodes n ON e.from_node_id = n.id "
                    f"{where_clause} "
                    f"ORDER BY e.call_line ASC",
                    (
                        target_id or symbol_name,
                        symbol_name,
                        f"%::{symbol_name}",
                        f"%.{symbol_name}",
                    ),
                )
                for row in cursor.fetchall():
                    caller = FederatedCaller(
                        calling_repo=r_name,
                        caller_symbol=row["caller_name"],
                        caller_file=row["caller_file"],
                        call_line=row["call_line"],
                        caller_kind=row["caller_kind"] or "function",
                    )
                    repo_callers.append(caller.model_dump())
            except Exception:
                pass

            if repo_callers:
                callers_by_repo[r_name] = repo_callers
                total_callers += len(repo_callers)

        # 3. Check synthesized cross-repo contract links (e.g. client fetch -> backend route)
        for link in self.get_cross_repo_links():
            if link.target_symbol == symbol_name or link.target_symbol == target_id:
                existing = callers_by_repo.get(link.source_repo, [])
                if not any(link.source_symbol in c.get("caller_symbol", "") for c in existing):
                    caller = FederatedCaller(
                        calling_repo=link.source_repo,
                        caller_symbol=f"{link.source_symbol} [{link.contract_key}]",
                        caller_file=link.source_file,
                        call_line=link.source_line,
                        caller_kind="client_route",
                    )
                    callers_by_repo.setdefault(link.source_repo, []).append(caller.model_dump())
                    total_callers += 1

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
