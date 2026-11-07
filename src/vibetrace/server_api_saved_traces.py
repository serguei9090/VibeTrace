"""REST API router for saved architectural trace queries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException

from vibetrace.indexer.db import IndexerDB
from vibetrace.services.graph_service import trace_execution_path
from vibetrace.services.saved_traces_service import (
    SavedTraceCreate,
    create_saved_trace,
    delete_saved_trace,
    delete_saved_trace_by_name,
    get_saved_trace_by_id,
    get_saved_trace_by_name,
    list_saved_traces,
)


def get_saved_traces_router(get_db: Callable[[], IndexerDB]) -> APIRouter:
    """Create and configure the saved-traces API router."""
    router = APIRouter(prefix="/api/saved-traces", tags=["saved-traces"])

    @router.get("")
    def get_saved_traces_endpoint() -> list[dict[str, Any]]:
        return [t.model_dump() for t in list_saved_traces(get_db())]

    @router.get("/{trace_id}")
    def get_saved_trace_endpoint(trace_id: int) -> dict[str, Any]:
        saved = get_saved_trace_by_id(get_db(), trace_id)
        if not saved:
            raise HTTPException(status_code=404, detail=f"Saved trace with ID {trace_id} not found")
        return saved.model_dump()

    @router.post("")
    def post_saved_trace_endpoint(payload: SavedTraceCreate) -> dict[str, Any]:
        return create_saved_trace(get_db(), payload).model_dump()

    @router.delete("/by-name/{name}")
    def delete_saved_trace_by_name_endpoint(name: str) -> dict[str, Any]:
        deleted = delete_saved_trace_by_name(get_db(), name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Saved trace '{name}' not found")
        return {"status": "deleted", "name": name}

    @router.delete("/{trace_id}")
    def delete_saved_trace_endpoint(trace_id: int) -> dict[str, Any]:
        deleted = delete_saved_trace(get_db(), trace_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Saved trace not found")
        return {"status": "deleted", "id": trace_id}

    @router.get("/{name}/run")
    def run_saved_trace_endpoint(name: str) -> dict[str, Any]:
        db = get_db()
        saved = get_saved_trace_by_name(db, name)
        if not saved:
            raise HTTPException(status_code=404, detail=f"Saved trace '{name}' not found")
        res = trace_execution_path(
            db, saved.from_symbol, saved.to_symbol, max_depth=saved.max_depth
        )
        return {
            "saved_trace": saved.model_dump(),
            "trace_result": res.model_dump(),
        }

    return router
