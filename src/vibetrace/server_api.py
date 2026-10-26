import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vibetrace.indexer.watcher import start_observer, stop_observer
from vibetrace.services.graph_service import (
    build_call_stack,
    build_call_tree,
    categorize_file_dependencies,
    compute_impact_analysis,
    get_code_slice,
    get_context_for_path,
    normalize_path,
    read_file_lines,
    trace_execution_path,
)
from vibetrace.services.saved_traces_service import (
    SavedTraceCreate,
    create_saved_trace,
    delete_saved_trace,
    get_saved_trace_by_id,
    get_saved_trace_by_name,
    list_saved_traces,
)


class ReindexPayload(BaseModel):
    """Payload to trigger project or file reindexing via REST API."""

    file_path: str | None = None
    force: bool = False
    embed: bool = False


class AgentFocusPayload(BaseModel):
    """Payload broadcast to Map Room UI when an AI agent queries VibeTrace."""

    type: str = "agent_focus"
    event_type: str
    node_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    from_symbol: str | None = None
    to_symbol: str | None = None


# Global Managers
class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)

    async def broadcast(self, message: str) -> None:
        conns = list(self.active_connections)
        if not conns:
            return

        async def _safe_send(ws: WebSocket) -> None:
            try:
                await ws.send_text(message)
            except Exception:
                self.disconnect(ws)

        await asyncio.gather(*(_safe_send(c) for c in conns), return_exceptions=True)


manager = ConnectionManager()
root_override = os.environ.get("VIBETRACE_PROJECT_ROOT")
db, project_root = get_context_for_path(".", root_override=root_override)


# Lifespan for Watcher
@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()

    def on_change() -> None:
        asyncio.run_coroutine_threadsafe(manager.broadcast("refresh"), loop)

    observer = None
    try:
        if Path(project_root).is_dir():
            observer = start_observer(str(project_root), db, on_change)
    except Exception as exc:
        print(f"Warning: Failed to start filesystem observer on {project_root}: {exc}")

    yield

    if observer is not None:
        stop_observer(observer)
    try:
        db.close()
    except Exception:
        pass


app = FastAPI(title="VibeTrace API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # Loopback only: a wildcard would let any website read project source via the API.
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/graph")
def get_graph(file_path: str | None = None):
    """
    Return nodes and edges.
    If file_path is provided, filter nodes by that file (plus connected edges).
    """
    if file_path:
        norm_file = normalize_path(file_path, Path(project_root))
        return db.get_graph_data(norm_file)
    return db.get_graph_data(None)


@app.get("/trace")
def get_trace(
    from_symbol: str = Query(..., min_length=1),
    to_symbol: str = Query(..., min_length=1),
    max_depth: int = Query(default=6, ge=1, le=15),
    engine: str = Query(default="bfs", pattern="^(bfs|cte)$"),
):
    """Compute shortest execution tracepath(s) between two symbols via BFS or recursive CTE."""
    res = trace_execution_path(db, from_symbol, to_symbol, max_depth=max_depth, engine=engine)
    return res.model_dump()


@app.get("/call-tree")
def get_call_tree(
    symbol: str = Query(..., min_length=1),
    depth: int = Query(default=3, ge=1, le=10),
    file_path: str | None = Query(default=None),
):
    """Explore top-down execution call-tree from an entry point."""
    norm_file = normalize_path(file_path, Path(project_root)) if file_path else None
    res = build_call_tree(db, symbol, max_depth=depth, file_path=norm_file)
    return res.model_dump()


@app.get("/call-stack")
@app.get("/api/call-stack")
def get_call_stack_endpoint(
    symbol: str = Query(..., min_length=1),
    direction: Literal["up", "down", "both"] = Query(default="both"),
    depth: int = Query(default=3, ge=1, le=10),
    file_path: str | None = Query(default=None),
):
    """Explore bidirectional execution call stack (callers up, callees down, or both)."""
    norm_file = normalize_path(file_path, Path(project_root)) if file_path else None
    found, output, start_nodes = build_call_stack(
        db,
        symbol,
        direction=direction,
        max_depth=depth,
        file_path=norm_file,
    )
    if not found:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found in index")
    return {
        "found": True,
        "symbol": symbol,
        "direction": direction,
        "depth": depth,
        "call_stack": output,
        "nodes": [n["id"] for n in start_nodes],
    }


@app.get("/source")
def get_source(
    file_path: str = Query(..., min_length=1),
    start_line: int | None = Query(default=None, ge=1),
    end_line: int | None = Query(default=None, ge=1),
):
    """Safely return a 1-indexed source code slice from an indexed project file."""
    # Serve only indexed source files so secrets (.env, keys) are never exposed.
    norm_path = normalize_path(file_path, Path(project_root))
    if db.get_file_hash(norm_path) is None:
        raise HTTPException(status_code=404, detail=f"File not indexed: '{file_path}'")
    try:
        snippet, s_line, e_line = read_file_lines(
            Path(project_root), norm_path, start_line, end_line
        )
        return {
            "file_path": norm_path,
            "start_line": s_line,
            "end_line": e_line,
            "source_code": snippet,
        }
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@app.get("/api/search")
def search_nodes_endpoint(
    q: str = Query(..., min_length=1),
    kind: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Search code symbols and endpoints by name, qualified name, or lexical query."""
    fetch_limit = limit if not kind else max(limit * 5, 50)
    ast_results = db.search_ast_exact_or_prefix(q, limit=fetch_limit)
    if kind:
        ast_results = [r for r in ast_results if r.get("kind") == kind]

    if len(ast_results) >= limit:
        return ast_results[:limit]

    fts_results = db.search_lexical(q, limit=fetch_limit)
    if kind:
        fts_results = [r for r in fts_results if r.get("kind") == kind]

    seen_ids = {r["id"] for r in ast_results}
    combined = list(ast_results)
    for r in fts_results:
        if r["id"] not in seen_ids:
            seen_ids.add(r["id"])
            combined.append(r)
            if len(combined) >= limit:
                break

    return combined[:limit]


@app.get("/api/search/signature")
def search_signature_endpoint(
    pattern: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Search function signatures matching a SQL LIKE pattern or substring."""
    return db.search_by_signature_pattern(pattern, limit=limit)


@app.get("/api/saved-traces")
def get_saved_traces_endpoint():
    """List all saved architectural trace queries."""
    return [t.model_dump() for t in list_saved_traces(db)]


@app.get("/api/saved-traces/{trace_id}")
def get_saved_trace_endpoint(trace_id: int):
    """Get a single saved architectural trace query by ID."""
    saved = get_saved_trace_by_id(db, trace_id)
    if not saved:
        raise HTTPException(status_code=404, detail=f"Saved trace with ID {trace_id} not found")
    return saved.model_dump()


@app.post("/api/saved-traces")
def post_saved_trace_endpoint(payload: SavedTraceCreate):
    """Save or update an architectural trace query."""
    return create_saved_trace(db, payload).model_dump()


@app.delete("/api/saved-traces/{trace_id}")
def delete_saved_trace_endpoint(trace_id: int):
    """Delete a saved trace query by ID."""
    deleted = delete_saved_trace(db, trace_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Saved trace not found")
    return {"status": "deleted", "id": trace_id}


@app.get("/api/saved-traces/{name}/run")
def run_saved_trace_endpoint(name: str):
    """Execute a saved trace query by name."""
    saved = get_saved_trace_by_name(db, name)
    if not saved:
        raise HTTPException(status_code=404, detail=f"Saved trace '{name}' not found")
    res = trace_execution_path(db, saved.from_symbol, saved.to_symbol, max_depth=saved.max_depth)
    return {
        "saved_trace": saved.model_dump(),
        "trace_result": res.model_dump(),
    }


@app.post("/api/agent-focus")
async def post_agent_focus(payload: AgentFocusPayload):
    """Broadcast live AI agent activity to connected Map Room WebSocket clients."""
    await manager.broadcast(payload.model_dump_json())
    return {"status": "broadcast", "clients": len(manager.active_connections)}


@app.get("/api/impact")
def get_impact_endpoint(file_path: str = Query(..., min_length=1)):
    """Compute 3-level blast radius impact analysis for a file."""
    norm_path = normalize_path(file_path, Path(project_root))
    if db.get_file_hash(norm_path) is None and not db.get_nodes_in_file(norm_path):
        raise HTTPException(status_code=404, detail=f"File not indexed: '{file_path}'")
    total_impact, impacts_by_level = compute_impact_analysis(db, norm_path)
    return {
        "file_path": norm_path,
        "total_impact": total_impact,
        "levels": impacts_by_level,
    }


@app.get("/api/slice")
def get_slice_endpoint(
    symbol: str = Query(..., min_length=1),
    file_path: str | None = Query(default=None),
    no_callees: bool = Query(default=False),
):
    """Retrieve surgical source code slice and callee contracts for a symbol."""
    norm_file = normalize_path(file_path, Path(project_root)) if file_path else None
    res = get_code_slice(
        db, Path(project_root), symbol, file_path=norm_file, include_callees=not no_callees
    )
    if not res.found:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found")
    return res.model_dump()


@app.get("/api/references")
def get_references_endpoint(
    symbol: str = Query(..., min_length=1),
    file_path: str | None = Query(default=None),
    kind: str | None = Query(default=None),
):
    """Find all incoming calls, references, inheritance, or implementations to a symbol."""
    norm_file = normalize_path(file_path, Path(project_root)) if file_path else None
    clean_symbol = symbol.strip().removesuffix("()")
    targets = db.find_nodes_by_name(clean_symbol, file_path=norm_file)
    if not targets:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found in index")

    target_summaries: list[dict[str, Any]] = []
    total_refs = 0
    for t in targets:
        refs = db.get_symbol_references(t["id"])
        if kind:
            refs = [r for r in refs if r.get("relation_type") == kind]
        total_refs += len(refs)
        target_summaries.append(
            {
                "id": t["id"],
                "name": t["name"],
                "qualified_name": t.get("qualified_name"),
                "file_path": t["file_path"],
                "start_line": t["start_line"],
                "references": refs,
            }
        )
    return {
        "symbol": clean_symbol,
        "target_count": len(targets),
        "total_references": total_refs,
        "targets": target_summaries,
    }


@app.get("/api/dependencies")
def get_dependencies_endpoint(file_path: str = Query(..., min_length=1)):
    """Categorize file dependencies into internal, third-party, and standard library."""
    norm_path = normalize_path(file_path, Path(project_root))
    if db.get_file_hash(norm_path) is None and not db.get_nodes_in_file(norm_path):
        raise HTTPException(status_code=404, detail=f"File not indexed: '{file_path}'")
    internal, third_party, stdlib = categorize_file_dependencies(db, norm_path)
    return {
        "file_path": norm_path,
        "counts": {
            "internal": len(internal),
            "third_party": len(third_party),
            "stdlib": len(stdlib),
            "total": len(internal) + len(third_party) + len(stdlib),
        },
        "internal": internal,
        "third_party": third_party,
        "stdlib": stdlib,
    }


@app.get("/api/summary")
def get_summary_endpoint(
    file_path: str = Query(..., min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Get structural outline (functions, classes, methods) of an indexed file."""
    norm_path = normalize_path(file_path, Path(project_root))
    if db.get_file_hash(norm_path) is None and not db.get_nodes_in_file(norm_path):
        raise HTTPException(status_code=404, detail=f"File not indexed: '{file_path}'")
    total, rows = db.get_file_structure(norm_path, limit, offset)
    return {
        "file_path": norm_path,
        "total": total,
        "nodes": rows,
    }


@app.post("/api/reindex")
def post_reindex_endpoint(payload: ReindexPayload | None = None):
    """Reindex the workspace or a single file on demand."""
    from vibetrace.indexer.main import index_file, reindex_all

    eff_payload = payload or ReindexPayload()
    if eff_payload.file_path:
        norm_path = normalize_path(eff_payload.file_path, Path(project_root))
        abs_target = (Path(project_root) / norm_path).resolve()
        root_resolved = Path(project_root).resolve()
        if not abs_target.is_relative_to(root_resolved):
            msg = f"Access denied: file path '{eff_payload.file_path}' is outside project root."
            raise HTTPException(status_code=403, detail=msg)
        if not abs_target.exists():
            raise HTTPException(
                status_code=404, detail=f"File not found: '{eff_payload.file_path}'"
            )
        indexed = index_file(
            db, str(abs_target), project_root=Path(project_root), force=eff_payload.force
        )
        return {
            "status": "ok",
            "mode": "single_file",
            "file_path": norm_path,
            "indexed": indexed,
        }

    reindex_all(
        db,
        str(project_root),
        verbose=False,
        force=eff_payload.force,
        project_root=Path(project_root),
    )
    embedded_count = 0
    if eff_payload.embed:
        from vibetrace.indexer.embeddings import EmbeddingEngine

        engine = EmbeddingEngine(project_root=Path(project_root))
        if engine.is_available():
            embedded_count = engine.index_missing_embeddings(db)
    return {"status": "ok", "mode": "workspace", "embedded_count": embedded_count}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        manager.disconnect(websocket)


# Mount static frontend if built (src/web/dist)
web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if not web_dist.exists():
    web_dist = Path(__file__).resolve().parent.parent.parent / "src" / "web" / "dist"

if web_dist.exists() and (web_dist / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="static_web")
else:

    @app.get("/", response_class=HTMLResponse)
    def index_fallback():
        return (
            "<!DOCTYPE html><html><head><title>VibeTrace Map Room</title></head>"
            "<body style='font-family: sans-serif; background: #0f172a; "
            "color: #f8fafc; padding: 2rem;'><h1>VibeTrace API Running</h1>"
            "<p>The Map Room Web UI is not built yet.</p>"
            "<pre style='background: #1e293b; padding: 1rem; border-radius: 8px;'>"
            "bun run --cwd src/web dev</pre></body></html>"
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
