"""Test full-stack cross-language client-side route extraction."""

from __future__ import annotations

from vibetrace.indexer.parsers.js_ts_parser import TypeScriptParser


def test_clientside_fetch_and_axios_route_extraction() -> None:
    parser = TypeScriptParser()
    code = b"""
import axios from 'axios';

export function UserProfile() {
    async function loadData() {
        const res = await fetch("/api/users");
        return res.json();
    }

    async function saveData(payload: any) {
        await axios.post("/api/users/update", payload);
    }
}
"""
    nodes, edges = parser.extract("src/UserProfile.tsx", code)

    # Verify endpoint nodes were created
    endpoint_nodes = [n for n in nodes if n.kind == "endpoint"]
    names = [n.name for n in endpoint_nodes]
    assert "GET /api/users" in names
    assert "POST /api/users/update" in names

    # Verify routes edges link from loadData/saveData to the endpoints
    route_edges = [e for e in edges if e.relation_type == "routes"]
    assert len(route_edges) >= 2

    node_map = {n.name: n for n in nodes}
    get_ep = node_map["GET /api/users"]
    post_ep = node_map["POST /api/users/update"]
    assert any(e.to_node_id == get_ep.id for e in route_edges)
    assert any(e.to_node_id == post_ep.id for e in route_edges)


def test_end_to_end_cross_language_fullstack_trace(tmp_path) -> None:
    """Trace from frontend React/TSX function to backend Python handler through shared endpoint."""
    from vibetrace.indexer.db import IndexerDB
    from vibetrace.indexer.main import reindex_all
    from vibetrace.services.graph_service import trace_execution_path

    # Create fullstack project layout
    web_dir = tmp_path / "src" / "web"
    api_dir = tmp_path / "src" / "api"
    web_dir.mkdir(parents=True)
    api_dir.mkdir(parents=True)

    # 1. Frontend TypeScript component
    (web_dir / "Feed.tsx").write_text(
        "export function Feed() {\n"
        "    async function loadFeed() {\n"
        "        const res = await fetch('/api/feed');\n"
        "        return res.json();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    # 2. Backend Python route + service
    (api_dir / "__init__.py").write_text("", encoding="utf-8")
    (api_dir / "server.py").write_text(
        "def query_feed_db():\n"
        "    return ['post1', 'post2']\n\n"
        "@app.get('/api/feed')\n"
        "def get_feed():\n"
        "    return query_feed_db()\n",
        encoding="utf-8",
    )

    ctx_dir = tmp_path / "vibetrace_context"
    ctx_dir.mkdir()
    db = IndexerDB(str(ctx_dir / "vibetrace.db"))
    reindex_all(db, str(tmp_path), verbose=False)

    # Verify both frontend and backend share the SAME canonical endpoint ID
    feed_eps = db.find_nodes_by_name("GET /api/feed")
    assert len(feed_eps) == 1
    canonical_ep = feed_eps[0]

    # Verify incoming edge from loadFeed and outgoing edge to get_feed
    incoming = db.get_neighbors(canonical_ep["id"], "up")
    incoming_names = [n["name"] for n in incoming]
    assert "loadFeed" in incoming_names

    outgoing = db.get_neighbors(canonical_ep["id"], "down")
    outgoing_names = [n["name"] for n in outgoing]
    assert "get_feed" in outgoing_names

    # Trace execution from loadFeed (TSX) -> query_feed_db (Python)
    res = trace_execution_path(db, "loadFeed", "query_feed_db")
    assert res.found is True
    assert len(res.hops) >= 2
    hop_targets = [h.to_node.name for h in res.hops]
    assert "GET /api/feed" in hop_targets
    assert "get_feed" in hop_targets


def test_express_backend_route_extraction() -> None:
    """Express app.get('/users', handleUsers) links endpoint -> handleUsers via routes edge."""
    parser = TypeScriptParser()
    code = b"""
function handleUsers(req: any, res: any) {
    res.json([]);
}

function handleCreate(req: any, res: any) {
    res.send("created");
}

app.get("/api/users", handleUsers);
router.post("/api/users", handleCreate);
"""
    nodes, edges = parser.extract("src/routes.ts", code)

    endpoint_nodes = [n for n in nodes if n.kind == "endpoint"]
    names = [n.name for n in endpoint_nodes]
    assert "GET /api/users" in names
    assert "POST /api/users" in names

    # Outgoing edge from endpoint to handler
    get_ep = next(n for n in endpoint_nodes if n.name == "GET /api/users")
    routes_from_ep = [
        e for e in edges if e.from_node_id == get_ep.id and e.relation_type == "routes"
    ]
    assert len(routes_from_ep) == 1
