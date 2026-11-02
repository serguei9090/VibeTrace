"""REST API endpoints for documentation catalogs and RAG retrieval."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from vibetrace.docs.db import get_global_doc_db, get_local_doc_db
from vibetrace.docs.schemas import DocCatalogInfo, DocChunkItem
from vibetrace.docs.service import DocCatalogManager, DocIndexService, DocSearchService

docs_router = APIRouter(prefix="/api/docs", tags=["docs"])


def _get_project_root() -> Path:
    env_root = os.environ.get("VIBETRACE_PROJECT_ROOT")
    if env_root and Path(env_root).is_dir():
        return Path(env_root).resolve()
    return Path.cwd().resolve()


class AddCatalogRequest(BaseModel):
    name: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    global_scope: bool = False


class IndexCatalogRequest(BaseModel):
    catalog: str | None = None
    global_scope: bool = False
    force: bool = False


@docs_router.get("/catalogs", response_model=list[DocCatalogInfo])
def list_catalogs() -> list[DocCatalogInfo]:
    """List all configured documentation catalogs with local precedence."""
    return DocCatalogManager.list_catalogs(project_root=_get_project_root())


@docs_router.post("/catalogs")
def add_catalog(body: AddCatalogRequest) -> dict[str, Any]:
    """Add a directory path to a local or global documentation catalog."""
    root = _get_project_root()
    DocCatalogManager.add_path(
        catalog_name=body.name,
        path_str=body.path,
        global_scope=body.global_scope,
        project_root=root,
    )
    return {"status": "ok", "catalog": body.name, "path": body.path, "global": body.global_scope}


@docs_router.delete("/catalogs/{name}")
def remove_catalog(name: str, global_scope: bool = False) -> dict[str, Any]:
    """Remove a documentation catalog from configuration."""
    root = _get_project_root()
    ok = DocCatalogManager.remove_catalog(
        catalog_name=name, global_scope=global_scope, project_root=root
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"Catalog '{name}' not found")
    return {"status": "ok", "catalog": name, "global": global_scope}


@docs_router.post("/index")
def index_documentation(body: IndexCatalogRequest) -> dict[str, Any]:
    """Trigger indexing for documentation catalogs."""
    root = _get_project_root()
    cats = DocCatalogManager.list_catalogs(project_root=root)
    if body.catalog:
        cats = [c for c in cats if c.name == body.catalog]
        if not cats:
            raise HTTPException(status_code=404, detail=f"Catalog '{body.catalog}' not found")

    if body.global_scope:
        cats = [c for c in cats if c.scope == "global"]

    total_files = 0
    total_chunks = 0
    results: dict[str, Any] = {}

    for c in cats:
        db = get_global_doc_db() if c.scope == "global" else get_local_doc_db(root)
        base_dir = Path.home() if c.scope == "global" else root
        res = DocIndexService.index_catalog(c.name, c.paths, db, base_dir, force=body.force)
        results[c.name] = res
        total_files += res["files"]
        total_chunks += res["chunks"]

    return {
        "status": "ok",
        "total_files": total_files,
        "total_chunks": total_chunks,
        "catalogs": results,
    }


@docs_router.get("/search", response_model=list[DocChunkItem])
def search_documentation(
    q: str = Query(..., min_length=1, description="Search query string"),
    catalog: str | None = Query(None, description="Scope search to specific catalog"),
    scope: Literal["all", "local", "global"] = Query("local", description="Search scope"),
    limit: int = Query(5, ge=1, le=50, description="Maximum chunks to return"),
) -> list[DocChunkItem]:
    """Execute hybrid RRF search over documentation chunks."""
    root = _get_project_root()
    return DocSearchService.search(
        query=q,
        catalog=catalog,
        scope=scope,
        project_root=root,
        limit=limit,
    )


@docs_router.get("/check")
def check_docs_drift() -> dict[str, Any]:
    """Audit documentation for dead or broken codebase symbol references."""
    from vibetrace.docs.symbol_linker import SymbolDocLinker
    from vibetrace.indexer.db import IndexerDB

    root = _get_project_root()
    db_path = root / ".vibetrace" / "index.db"
    if not db_path.is_file():
        raise HTTPException(status_code=400, detail="VibeTrace index not found. Run index first.")
    db = IndexerDB(db_path)
    report = SymbolDocLinker.audit_doc_links(root, db)
    return report.model_dump()


@docs_router.post("/sync-links")
def sync_doc_links() -> dict[str, Any]:
    """Synchronize bi-directional links between documentation and AST symbols."""
    from vibetrace.docs.symbol_linker import SymbolDocLinker
    from vibetrace.indexer.db import IndexerDB

    root = _get_project_root()
    db_path = root / ".vibetrace" / "index.db"
    if not db_path.is_file():
        raise HTTPException(status_code=400, detail="VibeTrace index not found. Run index first.")
    db = IndexerDB(db_path)
    count = SymbolDocLinker.sync_links(root, db)
    return {"status": "ok", "synced_links": count}
