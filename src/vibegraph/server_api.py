import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from vibegraph.indexer.watcher import start_observer
from vibegraph.services.graph_service import (
    get_context_for_path,
    read_file_lines,
    trace_execution_path,
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
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception:
                self.disconnect(connection)


manager = ConnectionManager()
db, project_root = get_context_for_path(".")


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
):
    """Compute shortest execution tracepath(s) between two symbols via Bidirectional BFS."""
    res = trace_execution_path(db, from_symbol, to_symbol, max_depth=max_depth)
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
    except WebSocketDisconnect:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
