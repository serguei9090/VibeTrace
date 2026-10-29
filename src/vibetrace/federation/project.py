"""High-level coordinator for multi-repository federated project workspaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vibetrace.federation.config import (
    auto_discover_members,
    find_project_root,
    load_project_manifest,
    save_project_manifest,
)
from vibetrace.federation.cross_repo_linker import CrossRepoLink
from vibetrace.federation.models import (
    FederatedGuardReport,
    FederatedNode,
    MemberStatus,
    ProjectManifest,
    ProjectSettings,
    RepoMember,
)
from vibetrace.federation.storage import FederatedSQLiteEngine


class ProjectWorkspace:
    """Manages multi-repository lifecycle, cross-repo indexing, and federated queries."""

    def __init__(self, root: Path, manifest: ProjectManifest) -> None:
        self.root = root.resolve()
        self.manifest = manifest
        self._engine: FederatedSQLiteEngine | None = None

    @classmethod
    def load(cls, root: Path | None = None) -> ProjectWorkspace:
        """Locate and load an existing federated project workspace."""
        found_root = find_project_root(root)
        if not found_root:
            target = (root or Path.cwd()).resolve()
            raise FileNotFoundError(
                f"No federated project found in '{target}' or parent directories. "
                "Initialize one with `vibetrace project init`."
            )
        manifest = load_project_manifest(found_root)
        return cls(found_root, manifest)

    @classmethod
    def init(
        cls,
        root: Path,
        name: str | None = None,
        description: str = "",
        auto_discover: bool = True,
    ) -> ProjectWorkspace:
        """Initialize and persist a new federated project workspace."""
        root_resolved = root.resolve()
        proj_name = name or root_resolved.name
        members: list[RepoMember] = []
        if auto_discover:
            members = auto_discover_members(root_resolved)

        manifest = ProjectManifest(
            name=proj_name,
            description=description,
            settings=ProjectSettings(storage_backend="sqlite"),
            members=members,
        )
        save_project_manifest(root_resolved, manifest)
        return cls(root_resolved, manifest)

    @property
    def engine(self) -> FederatedSQLiteEngine:
        """Lazy-loaded federated SQLite engine."""
        if self._engine is None:
            self._engine = FederatedSQLiteEngine(self.root, self.manifest.members)
        return self._engine

    def close(self) -> None:
        """Close any active database connections and release resources."""
        if self._engine is not None:
            self._engine.close()
            self._engine = None

    def __enter__(self) -> ProjectWorkspace:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def list_members(self) -> list[MemberStatus]:
        """Return the health, indexing, and size status of all configured member repositories."""
        return self.engine.get_member_statuses()

    def add_member(self, name: str, path: str, description: str = "") -> RepoMember:
        """Register a new member repository in the project manifest."""
        # Check if already exists
        for m in self.manifest.members:
            if m.name == name:
                raise ValueError(f"Member repository '{name}' is already registered.")

        member = RepoMember(name=name, path=path, description=description)
        self.manifest.members.append(member)
        save_project_manifest(self.root, self.manifest)
        self.close()  # Reset engine connection to pick up new member
        return member

    def remove_member(self, name: str) -> bool:
        """Remove a member repository from the project manifest."""
        orig_count = len(self.manifest.members)
        self.manifest.members = [m for m in self.manifest.members if m.name != name]
        if len(self.manifest.members) < orig_count:
            save_project_manifest(self.root, self.manifest)
            self.close()
            return True
        return False

    def index_member(
        self,
        repo_name: str,
        force: bool = False,
        verbose: bool = False,
    ) -> dict[str, Any]:
        """Index a single member repository."""
        member = next((m for m in self.manifest.members if m.name == repo_name), None)
        if not member:
            raise ValueError(f"Member repository '{repo_name}' not found in project.")

        from vibetrace.indexer.db import IndexerDB
        from vibetrace.indexer.main import reindex_all

        repo_dir = (self.root / member.path).resolve()
        if not repo_dir.exists():
            raise FileNotFoundError(f"Member repository directory does not exist: '{repo_dir}'")

        db_file = (repo_dir / member.storage_path).resolve()
        db_file.parent.mkdir(parents=True, exist_ok=True)
        db = IndexerDB(str(db_file))

        reindex_all(
            db,
            str(repo_dir),
            verbose=verbose,
            force=force,
            project_root=repo_dir,
        )
        self.close()  # Invalidate cached DB connection

        node_count = 0
        edge_count = 0
        with db._get_conn() as conn:
            rn = conn.execute("SELECT count(*) FROM nodes").fetchone()
            if rn:
                node_count = rn[0]
            re = conn.execute("SELECT count(*) FROM edges").fetchone()
            if re:
                edge_count = re[0]

        return {
            "name": repo_name,
            "status": "indexed",
            "node_count": node_count,
            "edge_count": edge_count,
        }

    def index_all(
        self,
        force: bool = False,
        verbose: bool = False,
    ) -> list[dict[str, Any]]:
        """Sequentially index all configured member repositories."""
        results: list[dict[str, Any]] = []
        for member in self.manifest.members:
            try:
                res = self.index_member(member.name, force=force, verbose=verbose)
                results.append(res)
            except Exception as e:
                results.append({"name": member.name, "status": "error", "error": str(e)})
        return results

    def search(
        self,
        query: str,
        repo: str | None = None,
        limit: int = 20,
    ) -> list[FederatedNode]:
        """Search symbol definitions across one or all member repositories."""
        return self.engine.search(query, repo=repo, limit=limit)

    def guard(
        self,
        symbol: str,
        target_repo: str | None = None,
    ) -> FederatedGuardReport:
        """Trace cross-repository callers and compute blast radius for a symbol."""
        return self.engine.get_cross_repo_guard(symbol, target_repo=target_repo)

    def discover_cross_repo_links(self) -> list[CrossRepoLink]:
        """Correlate HTTP routes and client invocations across member repositories."""
        return self.engine.get_cross_repo_links()
