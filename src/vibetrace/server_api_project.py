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

    def _get_workspace() -> ProjectWorkspace:
        active_root = Path(os.environ.get("VIBETRACE_PROJECT_ROOT", Path.cwd())).resolve()
        found = find_project_root(active_root)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=f"No federated project workspace found in '{active_root}' or parents.",
            )
        return ProjectWorkspace.load(found)

    @router.get("")
    def get_project_status_endpoint() -> dict[str, Any]:
        """Return project manifest and member repository health status."""
        ws = _get_workspace()
        try:
            return {
                "manifest": ws.manifest.model_dump(),
                "members": [m.model_dump() for m in ws.list_members()],
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
        ws = _get_workspace()
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
        ws = _get_workspace()
        try:
            report = ws.guard(symbol, target_repo=repo)
            return report.model_dump()
        finally:
            ws.close()

    @router.get("/links")
    def get_cross_repo_links_endpoint() -> list[dict[str, Any]]:
        """Discover cross-repository HTTP routes and RPC service contracts."""
        ws = _get_workspace()
        try:
            links = ws.discover_cross_repo_links()
            return [lnk.model_dump() for lnk in links]
        finally:
            ws.close()

    return router
