from __future__ import annotations

import hashlib
from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.parsers.python_parser import PythonParser
from vibetrace.indexer.parsers.route_extractor import extract_python_router_prefixes
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.services.graph_service import trace_execution_path


def test_js_multiple_anonymous_functions_unique_ids(tmp_path: Path):
    """Verify multiple anonymous callbacks in a JS file receive distinct line-anchored IDs."""
    js_file = tmp_path / "server.js"
    code = """
    app.get('/users', (req, res) => {
        fetchUsers();
    });

    app.post('/users', (req, res) => {
        createUser();
    });
    """
    js_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = JavaScriptParser(resolver=resolver, language="javascript")
    nodes, _ = parser.extract(str(js_file), code.encode("utf-8"))

    fn_nodes = [n for n in nodes if n.kind == "function"]
    assert len(fn_nodes) == 2
    names = {n.name for n in fn_nodes}
    assert "<anonymous:L2>" in names
    assert "<anonymous:L6>" in names

    # Verify both can be stored in SQLite without collision or overwriting
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))
    for n in nodes:
        db.upsert_node(n)

    stored_get = db.get_node(fn_nodes[0].id)
    stored_post = db.get_node(fn_nodes[1].id)
    assert stored_get is not None
    assert stored_post is not None
    assert stored_get["id"] != stored_post["id"]


def test_js_inline_arrow_route_handler_linking(tmp_path: Path):
    """Verify Express inline arrow handlers receive incoming routes edges from endpoints."""
    js_file = tmp_path / "routes.js"
    code = """
    app.get('/api/users', (req, res) => {
        fetchUsers();
    });

    function fetchUsers() {
        return [];
    }
    """
    js_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = JavaScriptParser(resolver=resolver, language="javascript")
    nodes, edges = parser.extract(str(js_file), code.encode("utf-8"))

    ep_node = next(n for n in nodes if n.kind == "endpoint")
    fn_node = next(n for n in nodes if n.name == "<anonymous:L2>")

    # Check routes edge exists from endpoint to the inline arrow function
    routes_edges = [
        e
        for e in edges
        if e.relation_type == "routes"
        and e.from_node_id == ep_node.id
        and e.to_node_id == fn_node.id
    ]
    assert len(routes_edges) == 1

    # Check call edge from inline arrow function to fetchUsers
    call_edges = [e for e in edges if e.relation_type == "calls" and e.from_node_id == fn_node.id]
    assert len(call_edges) == 1

    # End-to-end trace from endpoint to fetchUsers in IndexerDB
    db = IndexerDB(str(tmp_path / "trace.db"))
    for n in nodes:
        db.upsert_node(n)
    for e in edges:
        db.upsert_edge(e)

    res = trace_execution_path(db, "GET /api/users", "fetchUsers")
    assert res.found is True


def test_python_external_bare_import_registered(tmp_path: Path):
    """Verify external bare imports (requests, json) register symbols pointing to external."""
    py_file = tmp_path / "service.py"
    code = """
    import requests
    import json as j
    import os.path as osp

    def run():
        pass
    """
    py_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = PythonParser(resolver=resolver)
    nodes, _ = parser.extract(str(py_file), code.encode("utf-8"))

    module_nodes = {n.name: n for n in nodes if n.kind == "module"}
    assert "requests" in module_nodes
    assert module_nodes["requests"].file_path == "external"

    assert "json" in module_nodes
    assert module_nodes["json"].file_path == "external"


def test_python_external_import_calls_target_external(tmp_path: Path):
    """Verify calls on bare imported modules emit edges targeting external rather than local."""
    py_file = tmp_path / "caller.py"
    code = """
    import requests
    import json as j

    def fetch_data():
        resp = requests.get("https://example.com")
        data = j.loads(resp.text)
        return data
    """
    py_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = PythonParser(resolver=resolver)
    _, edges = parser.extract(str(py_file), code.encode("utf-8"))

    call_edges = [e for e in edges if e.relation_type == "calls"]
    expected_get = hashlib.md5(b"external::get").hexdigest()
    expected_loads = hashlib.md5(b"external::loads").hexdigest()

    targets = {e.to_node_id for e in call_edges}
    assert expected_get in targets
    assert expected_loads in targets

    # Ensure no phantom local symbols like caller.py::requests.get were targeted
    local_hash = hashlib.md5(str(py_file).encode()).hexdigest()
    assert not any(e.to_node_id.startswith(local_hash) for e in call_edges)


def test_python_typed_apirouter_prefix_extracted(tmp_path: Path):
    """Verify router: APIRouter = APIRouter(prefix=...) extracts prefix and prepends to route."""
    py_file = tmp_path / "api.py"
    code = """
    from fastapi import APIRouter

    router: APIRouter = APIRouter(prefix="/api/v1")

    @router.get("/users")
    def get_users():
        return []
    """
    py_file.write_text(code, encoding="utf-8")
    prefixes = extract_python_router_prefixes(code)
    assert "router" in prefixes
    assert prefixes["router"] == "/api/v1"

    resolver = ModuleResolver(tmp_path)
    parser = PythonParser(resolver=resolver)
    nodes, _ = parser.extract(str(py_file), code.encode("utf-8"))

    endpoint_nodes = [n for n in nodes if n.kind == "endpoint"]
    names = [n.name for n in endpoint_nodes]
    assert "GET /api/v1/users" in names
    assert "GET /users" not in names


def test_python_typed_blueprint_prefix_extracted(tmp_path: Path):
    """Verify api_bp: Blueprint = Blueprint(..., url_prefix=...) prepends prefix to route."""
    py_file = tmp_path / "views.py"
    code = """
    from flask import Blueprint

    api_bp: Blueprint = Blueprint("api", __name__, url_prefix="/api/v2")

    @api_bp.route("/items")
    def get_items():
        return []
    """
    py_file.write_text(code, encoding="utf-8")
    prefixes = extract_python_router_prefixes(code)
    assert "api_bp" in prefixes
    assert prefixes["api_bp"] == "/api/v2"

    resolver = ModuleResolver(tmp_path)
    parser = PythonParser(resolver=resolver)
    nodes, _ = parser.extract(str(py_file), code.encode("utf-8"))

    endpoint_nodes = [n for n in nodes if n.kind == "endpoint"]
    names = [n.name for n in endpoint_nodes]
    assert "GET /api/v2/items" in names
    assert "GET /items" not in names
