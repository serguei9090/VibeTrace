import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.watcher import start_observer


# Global Managers
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)


manager = ConnectionManager()
db = IndexerDB()


# Lifespan for Watcher
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Callback to trigger WS broadcast
    # We need to run the async broadcast from the sync watchdog callback
    loop = asyncio.get_running_loop()

    def on_change():
        # Schedule the broadcast coroutine in the event loop
        asyncio.run_coroutine_threadsafe(manager.broadcast("refresh"), loop)

    # Start watching current directory (or passed arg)
    # Ideally should be configurable. For now, "." (root of project)
    observer = start_observer(".", db, on_change)
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
