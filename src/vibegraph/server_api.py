import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vibegraph.indexer.watcher import start_observer
from vibegraph.services.graph_service import (
    build_call_tree,
    get_context_for_path,
    read_file_lines,
    trace_execution_path,
)
from vibegraph.services.saved_traces_service import (
    SavedTraceCreate,
    create_saved_trace,
    delete_saved_trace,
    get_saved_trace_by_name,
    list_saved_traces,
)


class AgentFocusPayload(BaseModel):
    """Payload broadcast to Map Room UI when an AI agent queries VibeGraph."""

    type: str = "agent_focus"
    event_type: str
    node_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    from_symbol: str | None = None
    to_symbol: str | None = None


# Global Managers
class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

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
root_override = os.environ.get("VIBEGRAPH_PROJECT_ROOT")
db, project_root = get_context_for_path(".", root_override=root_override)


# Lifespan for Watcher
@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()

    def on_change() -> None:
        asyncio.run_coroutine_threadsafe(manager.broadcast("refresh"), loop)

    observer = start_observer(str(project_root), db, on_change)
    yield
    observer.stop()
    observer.join()


app = FastAPI(title="VibeGraph API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/graph")
def get_graph(file_path: str | None = None):
    """
    Return nodes and edges.
    If file_path is provided, filter nodes by that file (plus connected edges).
    """
    return db.get_graph_data(file_path)


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
    res = build_call_tree(db, symbol, max_depth=depth, file_path=file_path)
    return res.model_dump()


@app.get("/source")
def get_source(
    file_path: str = Query(..., min_length=1),
    start_line: int | None = Query(default=None, ge=1),
    end_line: int | None = Query(default=None, ge=1),
):
    """Safely return a 1-indexed source code slice from a project file."""
    try:
        snippet, s_line, e_line = read_file_lines(
            Path(project_root), file_path, start_line, end_line
        )
        return {
            "file_path": file_path,
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


@app.get("/api/saved-traces")
def get_saved_traces_endpoint():
    """List all saved architectural trace queries."""
    return [t.model_dump() for t in list_saved_traces(db)]


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
        return """
        <!DOCTYPE html>
        <html>
        <head><title>VibeGraph Map Room</title></head>
        <body style="font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem;">
            <h1>VibeGraph API Running</h1>
            <p>The Map Room Web UI is not built yet.</p>
            <pre style="background: #1e293b; padding: 1rem; border-radius: 8px;">
bun run --cwd src/web build
            </pre>
            <p>Or run the live development server on port 5173:</p>
            <pre style="background: #1e293b; padding: 1rem; border-radius: 8px;">
bun run --cwd src/web dev
            </pre>
        </body>
        </html>
        """


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
