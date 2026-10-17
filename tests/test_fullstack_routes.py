"""Test full-stack cross-language client-side route extraction."""

from __future__ import annotations

from vibegraph.indexer.parsers.js_ts_parser import TypeScriptParser


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
