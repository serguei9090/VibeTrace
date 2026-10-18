"""Tests for web framework route extractor and Endpoint node synthesis."""

from pathlib import Path

from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.main import reindex_all
from vibegraph.services.graph_service import trace_execution_path


def _create_route_test_project(tmp_path: Path) -> IndexerDB:
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    (pkg / "db_layer.py").write_text(
        "def query_db() -> list[str]:\n    return ['item1', 'item2']\n",
        encoding="utf-8",
    )

    (pkg / "api.py").write_text(
        "from .db_layer import query_db\n\n"
        "@app.get('/items')\n"
        "def get_items() -> list[str]:\n"
        '    """Fetch items endpoint."""\n'
        "    return query_db()\n\n"
        "@router.post('/items')\n"
        "def create_item(payload: dict) -> bool:\n"
        "    return True\n\n"
        "@app.route('/login', methods=['GET', 'POST'])\n"
        "def handle_login() -> str:\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )

    ctx_dir = tmp_path / "vibegraph_context"
    ctx_dir.mkdir()
    db = IndexerDB(str(ctx_dir / "vibegraph.db"))
    reindex_all(db, str(tmp_path), verbose=False)
    return db


def test_fastapi_and_flask_route_extraction(tmp_path: Path) -> None:
    """Route extractor should synthesize Endpoint nodes and connect them via 'routes' edges."""
    db = _create_route_test_project(tmp_path)

    # 1. FastAPI GET /items
    get_ep = db.find_nodes_by_name("GET /items")
    assert len(get_ep) == 1
    assert get_ep[0]["kind"] == "endpoint"
    assert get_ep[0]["signature"] == "GET /items"

    # Outgoing callee from GET /items should be get_items via 'routes'
    callees = db.get_neighbors(get_ep[0]["id"], "down")
    callee_names = [c["name"] for c in callees]
    assert "get_items" in callee_names
    route_rel = next(c for c in callees if c["name"] == "get_items")
    assert route_rel["relation_type"] == "routes"

    # 2. FastAPI POST /items
    post_ep = db.find_nodes_by_name("POST /items")
    assert len(post_ep) == 1
    assert post_ep[0]["kind"] == "endpoint"

    # 3. Flask GET /login and POST /login
    login_get = db.find_nodes_by_name("GET /login")
    login_post = db.find_nodes_by_name("POST /login")
    assert len(login_get) == 1
    assert len(login_post) == 1


def test_tracepath_from_endpoint_to_database(tmp_path: Path) -> None:
    """Bidirectional BFS should trace from external HTTP route down to database function."""
    db = _create_route_test_project(tmp_path)

    # Trace: "GET /items" -> "get_items" -> "query_db"
    trace = trace_execution_path(db, "GET /items", "query_db", max_depth=5)
    assert trace.found is True
    assert len(trace.hops) == 2

    hop1, hop2 = trace.hops
    assert hop1.from_node.name == "GET /items"
    assert hop1.to_node.name == "get_items"
    assert hop1.relation_type == "routes"

    assert hop2.from_node.name == "get_items"
    assert hop2.to_node.name == "query_db"
    assert hop2.relation_type == "calls"


def test_fastapi_router_prefix_extraction(tmp_path: Path) -> None:
    """Route extractor should prepend router prefix when defined via APIRouter(prefix=...)."""
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "users.py").write_text(
        "from fastapi import APIRouter\n\n"
        "router = APIRouter(prefix='/api/v1')\n\n"
        "@router.get('/users')\n"
        "def list_users():\n"
        "    return []\n",
        encoding="utf-8",
    )
    ctx_dir = tmp_path / "vibegraph_context"
    ctx_dir.mkdir()
    db = IndexerDB(str(ctx_dir / "vibegraph.db"))
    reindex_all(db, str(tmp_path), verbose=False)

    users_ep = db.find_nodes_by_name("GET /api/v1/users")
    assert len(users_ep) == 1
    assert users_ep[0]["kind"] == "endpoint"
