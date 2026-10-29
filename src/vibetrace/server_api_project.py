"""REST API router for federated multi-repository project workspaces."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from vibetrace.federation.config import find_project_root
from vibetrace.federation.project import ProjectWorkspace


def get_project_router() -> APIRouter:
    """Create and configure the federated project workspace API router."""
    router = APIRouter(prefix="/api/project", tags=["project-federation"])

    def _resolve_project_root() -> Path | None:
        active_root = Path(os.environ.get("VIBETRACE_PROJECT_ROOT", Path.cwd())).resolve()
        return find_project_root(active_root)

    @router.get("")
    def get_project_status_endpoint() -> dict[str, Any]:
        """Return project manifest and member repository health status."""
        active_root = Path(os.environ.get("VIBETRACE_PROJECT_ROOT", Path.cwd())).resolve()
        found = _resolve_project_root()
        if not found:
            return {
                "is_federated": False,
                "project_name": active_root.name,
                "storage": "sqlite",
                "manifest_path": "",
                "member_count": 1,
                "members": [
                    {
                        "name": active_root.name,
                        "path": ".",
                        "database_path": str(active_root / ".vibetrace" / "vibetrace.db"),
                        "exists": (active_root / ".vibetrace" / "vibetrace.db").exists(),
                        "node_count": 0,
                    }
                ],
                "cross_repo_link_count": 0,
            }

        ws = ProjectWorkspace.load(found)
        try:
            links = ws.discover_cross_repo_links()
            return {
                "is_federated": True,
                "project_name": ws.manifest.name,
                "storage": ws.manifest.settings.storage_backend,
                "manifest_path": str(found),
                "member_count": len(ws.manifest.members),
                "manifest": ws.manifest.model_dump(),
                "members": [m.model_dump() for m in ws.list_members()],
                "cross_repo_link_count": len(links),
            }
        finally:
            ws.close()

    @router.get("/search")
    def search_project_endpoint(
        query: str = Query(..., min_length=1),
        repo: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[dict[str, Any]]:
        """Search symbol definitions across one or all member repositories."""
        found = _resolve_project_root()
        if not found:
            return []

        ws = ProjectWorkspace.load(found)
        try:
            results = ws.search(query, repo=repo, limit=limit)
            return [r.model_dump() for r in results]
        finally:
            ws.close()

    @router.get("/guard")
    def guard_project_endpoint(
        symbol: str = Query(..., min_length=1),
        repo: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Perform cross-repository blast-radius analysis for a symbol."""
        found = _resolve_project_root()
        if not found:
            raise HTTPException(
                status_code=404,
                detail="No federated project workspace found in current directory or parents.",
            )

        ws = ProjectWorkspace.load(found)
        try:
            report = ws.guard(symbol, target_repo=repo)
            return report.model_dump()
        finally:
            ws.close()

    @router.get("/links")
    def get_cross_repo_links_endpoint() -> list[dict[str, Any]]:
        """Discover cross-repository HTTP routes and RPC service contracts."""
        found = _resolve_project_root()
        if not found:
            return []

        ws = ProjectWorkspace.load(found)
        try:
            links = ws.discover_cross_repo_links()
            return [lnk.model_dump() for lnk in links]
        finally:
            ws.close()

    return router
