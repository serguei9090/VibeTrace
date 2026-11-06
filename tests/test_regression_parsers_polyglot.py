"""Consolidated regression test suite: test_regression_parsers_polyglot.py."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibetrace.cli_commands import cmd_references, cmd_summary
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.db_queries import _clean_path
from vibetrace.indexer.models import Edge, Node
from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.indexer.parsers.go_parser import GoParser
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.parsers.python_parser import PythonParser
from vibetrace.indexer.parsers.route_extractor import extract_python_router_prefixes
from vibetrace.indexer.parsers.rust_parser import RustParser
from vibetrace.indexer.parsers.ts_parser import TypeScriptParser
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.mcp.schemas import ReferencesInput, ResponseFormat, StructuralSummaryInput
from vibetrace.mcp.server import vibetrace_find_references, vibetrace_get_structural_summary
from vibetrace.server_api import app
from vibetrace.services.graph_service import trace_execution_path
from vibetrace.services.hybrid_search import hybrid_search


def test_get_symbol_references_includes_routes(tmp_path: Path):
    """Verify get_symbol_references returns incoming 'routes' edges for handler functions."""
    db_path = tmp_path / "test_refs_routes.db"
    db = IndexerDB(str(db_path))

    ep_id = hashlib.md5(b"__endpoint__::GET /api/orders").hexdigest()
    ep_node = Node(
        id=ep_id,
        name="GET /api/orders",
        qualified_name="GET /api/orders",
        kind="endpoint",
        file_path="src/routes.py",
        start_line=10,
        end_line=10,
    )

    handler_id = hashlib.md5(b"src/handlers.py::get_orders").hexdigest()
    handler_node = Node(
        id=handler_id,
        name="get_orders",
        qualified_name="handlers.get_orders",
        kind="function",
        file_path="src/handlers.py",
        start_line=25,
        end_line=40,
    )

    db.upsert_node(ep_node)
    db.upsert_node(handler_node)
    db.upsert_edge(
        Edge(
            from_node_id=ep_id,
            to_node_id=handler_id,
            relation_type="routes",
            call_line=10,
        )
    )

    refs = db.get_symbol_references(handler_id)
    assert len(refs) == 1
    assert refs[0]["name"] == "GET /api/orders"
    assert refs[0]["relation_type"] == "routes"
    db.close()


def test_cli_references_accepts_routes_kind(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify CLI references command accepts --kind routes and returns filtered endpoint edges."""
    context_dir = tmp_path / ".vibetrace"
    context_dir.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(context_dir / "vibetrace.db"))

    ep_id = hashlib.md5(b"__endpoint__::POST /api/payment").hexdigest()
    ep_node = Node(
        id=ep_id,
        name="POST /api/payment",
        qualified_name="POST /api/payment",
        kind="endpoint",
        file_path="routes.py",
        start_line=15,
        end_line=15,
    )
    handler_id = hashlib.md5(b"payments.py::process_charge").hexdigest()
    handler_node = Node(
        id=handler_id,
        name="process_charge",
        qualified_name="payments.process_charge",
        kind="function",
        file_path="payments.py",
        start_line=50,
        end_line=75,
    )
    caller_id = hashlib.md5(b"other.py::caller_fn").hexdigest()
    caller_node = Node(
        id=caller_id,
        name="caller_fn",
        qualified_name="other.caller_fn",
        kind="function",
        file_path="other.py",
        start_line=5,
        end_line=10,
    )

    db.upsert_node(ep_node)
    db.upsert_node(handler_node)
    db.upsert_node(caller_node)

    db.upsert_edge(
        Edge(from_node_id=ep_id, to_node_id=handler_id, relation_type="routes", call_line=15)
    )
    db.upsert_edge(
        Edge(from_node_id=caller_id, to_node_id=handler_id, relation_type="calls", call_line=8)
    )
    db.close()

    args = argparse.Namespace(
        symbol="process_charge",
        file=None,
        kind="routes",
        path=str(tmp_path),
        json=True,
        no_sync=True,
    )
    exit_code = cmd_references(args)
    assert exit_code == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert data["target_count"] == 1
    refs = data["targets"][0]["references"]
    assert len(refs) == 1
    assert refs[0]["name"] == "POST /api/payment"
    assert refs[0]["relation_type"] == "routes"


@pytest.mark.asyncio
async def test_mcp_references_accepts_routes_kind(tmp_path: Path):
    """Verify FastMCP vibetrace_find_references correctly filters by kind='routes'."""
    context_dir = tmp_path / ".vibetrace"
    context_dir.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(context_dir / "vibetrace.db"))

    ep_id = hashlib.md5(b"__endpoint__::DELETE /api/items").hexdigest()
    ep_node = Node(
        id=ep_id,
        name="DELETE /api/items",
        qualified_name="DELETE /api/items",
        kind="endpoint",
        file_path="routes.py",
        start_line=20,
        end_line=20,
    )
    handler_id = hashlib.md5(b"items.py::delete_item").hexdigest()
    handler_node = Node(
        id=handler_id,
        name="delete_item",
        qualified_name="items.delete_item",
        kind="function",
        file_path="items.py",
        start_line=30,
        end_line=45,
    )

    db.upsert_node(ep_node)
    db.upsert_node(handler_node)
    db.upsert_edge(
        Edge(from_node_id=ep_id, to_node_id=handler_id, relation_type="routes", call_line=20)
    )
    db.close()

    res_json = await vibetrace_find_references(
        ReferencesInput(
            symbol_name="delete_item",
            kind="routes",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    data = json.loads(res_json)
    assert data["symbol"] == "delete_item"
    assert data["total_references"] == 1
    refs = data["targets"][0]["references"]
    assert len(refs) == 1
    assert refs[0]["name"] == "DELETE /api/items"
    assert refs[0]["relation_type"] == "routes"


def test_express_member_expression_controller_route(tmp_path: Path):
    """Verify router.get('/users', userController.getUsers) links endpoint to controller method."""
    controllers_dir = tmp_path / "controllers"
    controllers_dir.mkdir(parents=True, exist_ok=True)
    (controllers_dir / "userController.js").write_text(
        "export const userController = {\n  getUsers(req, res) { res.send([]); }\n};\n",
        encoding="utf-8",
    )

    routes_file = tmp_path / "routes.js"
    routes_code = (
        'import { userController } from "./controllers/userController";\n'
        'router.get("/api/users", userController.getUsers);\n'
    )
    routes_file.write_text(routes_code, encoding="utf-8")

    resolver = ModuleResolver(tmp_path)
    parser = JavaScriptParser(resolver=resolver, language="javascript")
    nodes, edges = parser.extract("routes.js", routes_code.encode("utf-8"))

    # Verify endpoint node was created
    ep_nodes = [n for n in nodes if n.kind == "endpoint"]
    assert len(ep_nodes) == 1
    assert ep_nodes[0].name == "GET /api/users"

    # Verify 'routes' edge links endpoint directly to userController.getUsers
    routes_edges = [
        e for e in edges if e.from_node_id == ep_nodes[0].id and e.relation_type == "routes"
    ]
    assert len(routes_edges) == 1
    expected_handler_id = hashlib.md5(
        b"controllers/userController.js::userController.getUsers"
    ).hexdigest()
    assert routes_edges[0].to_node_id == expected_handler_id


def test_destructured_bare_http_method_extraction(tmp_path: Path):
    """Verify destructured bare client call post('/api/items') extracts as POST /api/items."""
    client_code = (
        'import { post } from "axios";\n'
        "function createItem(data) {\n"
        '    return post("/api/items", data);\n'
        "}\n"
    )
    parser = JavaScriptParser(language="javascript")
    nodes, _ = parser.extract("client.js", client_code.encode("utf-8"))

    ep_nodes = [n for n in nodes if n.kind == "endpoint"]
    assert len(ep_nodes) == 1
    assert ep_nodes[0].name == "POST /api/items"


def test_go_cross_file_receiver_method_linking(tmp_path: Path):
    """Verify a Go method in handlers.go resolves its receiver struct declared in models.go."""
    pkg_dir = tmp_path / "pkg" / "auth"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    models_content = "package auth\n\ntype Session struct {\n\tToken string\n}\n"
    (pkg_dir / "models.go").write_text(models_content, encoding="utf-8")

    handlers_content = (
        'package auth\n\nfunc (s *Session) IsValid() bool {\n\treturn s.Token != ""\n}\n'
    )
    (pkg_dir / "handlers.go").write_text(handlers_content, encoding="utf-8")

    resolver = ModuleResolver(tmp_path)
    parser = GoParser(resolver=resolver)
    _, edges = parser.extract("pkg/auth/handlers.go", handlers_content.encode("utf-8"))

    # Expected true struct ID in models.go
    expected_struct_id = hashlib.md5(b"pkg/auth/models.go::Session").hexdigest()
    method_id = hashlib.md5(b"pkg/auth/handlers.go::Session.IsValid").hexdigest()

    defines_edges = [
        e for e in edges if e.from_node_id == expected_struct_id and e.to_node_id == method_id
    ]
    assert len(defines_edges) == 1
    assert defines_edges[0].relation_type == "defines"


def test_js_external_es_import_registered(tmp_path: Path):
    """Verify external ES imports (e.g. axios, zod, path) create external module nodes and edges."""
    js_file = tmp_path / "client.ts"
    code = """
    import axios from 'axios';
    import { z } from 'zod';
    import * as path from 'path';

    export function fetchUser(id: string) {
        return axios.get(`/api/user/${id}`);
    }
    """
    js_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = JavaScriptParser(resolver=resolver, language="typescript")
    nodes, edges = parser.extract(str(js_file), code.encode("utf-8"))

    module_nodes = {n.name: n for n in nodes if n.kind == "module"}
    assert "axios" in module_nodes
    assert module_nodes["axios"].file_path == "external"

    assert "zod" in module_nodes
    assert module_nodes["zod"].file_path == "external"

    assert "path" in module_nodes
    assert module_nodes["path"].file_path == "external"

    # Verify calls to axios.get target external module rather than local file
    call_edges = [e for e in edges if e.relation_type == "calls"]
    assert len(call_edges) >= 1
    # Target of axios.get must start with external file hash prefix, not local file hash
    local_file_hash = hashlib.md5(str(js_file).encode()).hexdigest()
    assert not any(e.to_node_id.startswith(local_file_hash) for e in call_edges)


def test_js_external_commonjs_require_registered(tmp_path: Path):
    """Verify external CommonJS require statements create external module nodes and import edges."""
    js_file = tmp_path / "server.js"
    code = """
    const express = require('express');
    const { get, set } = require('lodash');

    const app = express();
    """
    js_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = JavaScriptParser(resolver=resolver, language="javascript")
    nodes, edges = parser.extract(str(js_file), code.encode("utf-8"))

    module_nodes = {n.name: n for n in nodes if n.kind == "module"}
    assert "express" in module_nodes
    assert module_nodes["express"].file_path == "external"

    assert "lodash" in module_nodes
    assert module_nodes["lodash"].file_path == "external"

    import_edges = [e for e in edges if e.relation_type == "imports"]
    assert len(import_edges) >= 2


def test_rust_bare_crate_use_declaration(tmp_path: Path):
    """Verify bare crate use declarations (e.g. 'use anyhow;') create module nodes and edges."""
    rs_file = tmp_path / "main.rs"
    code = """
    use anyhow;
    use tracing;

    fn main() {
        println!("hello");
    }
    """
    rs_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = RustParser(resolver=resolver)
    nodes, edges = parser.extract(str(rs_file), code.encode("utf-8"))

    module_nodes = {n.name: n for n in nodes if n.kind == "module"}
    assert "anyhow" in module_nodes
    assert module_nodes["anyhow"].file_path == "external"

    assert "tracing" in module_nodes
    assert module_nodes["tracing"].file_path == "external"

    import_edges = [e for e in edges if e.relation_type == "imports"]
    assert len(import_edges) >= 2


def test_rust_bare_crate_aliased_use_declaration(tmp_path: Path):
    """Verify aliased bare and qualified crate use declarations in Rust."""
    rs_file = tmp_path / "lib.rs"
    code = """
    use anyhow as ah;
    use serde::Serialize as Ser;

    pub fn run() {}
    """
    rs_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = RustParser(resolver=resolver)
    nodes, edges = parser.extract(str(rs_file), code.encode("utf-8"))

    module_nodes = {n.name: n for n in nodes if n.kind == "module"}
    assert "anyhow" in module_nodes
    assert module_nodes["anyhow"].file_path == "external"

    import_edges = [e for e in edges if e.relation_type == "imports"]
    assert len(import_edges) >= 2


def test_get_file_structure_deserializes_decorators(tmp_path: Path):
    """Verify db.get_file_structure deserializes JSON decorators into a native list[str]."""
    db_path = tmp_path / "test_struct.db"
    db = IndexerDB(str(db_path))

    node_id = hashlib.md5(b"src/app.py::get_users").hexdigest()
    node = Node(
        id=node_id,
        name="get_users",
        qualified_name="app.get_users",
        kind="function",
        file_path="src/app.py",
        start_line=15,
        end_line=25,
        signature="async def get_users() -> list[User]",
        decorators=['@app.get("/users")', "@requires_auth"],
        visibility="public",
    )
    db.upsert_node(node)

    total, rows = db.get_file_structure("src/app.py")
    assert total == 1
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row["decorators"], list)
    assert row["decorators"] == ['@app.get("/users")', "@requires_auth"]


@pytest.mark.asyncio
async def test_summary_contract_parity_across_cli_mcp_rest(tmp_path: Path, monkeypatch, capsys):
    """Verify CLI --json, FastMCP, and REST API all return structured decorators lists."""
    proj_dir = tmp_path / "project"
    proj_dir.mkdir()
    app_file = proj_dir / "src" / "api.py"
    app_file.parent.mkdir(parents=True)
    app_file.write_text("# api file", encoding="utf-8")

    db_path = proj_dir / ".vibetrace" / "index.db"
    db_path.parent.mkdir(parents=True)
    db = IndexerDB(str(db_path))

    node_id = hashlib.md5(b"src/api.py::create_item").hexdigest()
    node = Node(
        id=node_id,
        name="create_item",
        qualified_name="api.create_item",
        kind="function",
        file_path="src/api.py",
        start_line=10,
        end_line=20,
        signature="def create_item(item: Item)",
        decorators=['@app.post("/items")'],
        visibility="public",
    )
    db.upsert_node(node)
    db.update_file_hash("src/api.py", "mockhash", 12345)

    # 1. MCP FastMCP tool check
    monkeypatch.setattr("vibetrace.mcp.server._get_context_for_path", lambda path: (db, proj_dir))
    mcp_res = await vibetrace_get_structural_summary(
        StructuralSummaryInput(
            file_path=str(app_file),
            response_format=ResponseFormat.JSON,
        )
    )
    mcp_data = json.loads(mcp_res)
    assert mcp_data["total"] == 1
    assert mcp_data["nodes"][0]["decorators"] == ['@app.post("/items")']

    # 2. CLI --json check
    cli_args = argparse.Namespace(
        file_path=str(app_file),
        root=str(proj_dir),
        limit=50,
        offset=0,
        json=True,
        no_sync=True,
    )
    monkeypatch.setattr(
        "vibetrace.cli_commands.get_context_for_path", lambda path, **kwargs: (db, proj_dir)
    )
    cmd_summary(cli_args)
    cli_out = capsys.readouterr().out
    cli_data = json.loads(cli_out)
    assert cli_data["total"] == 1
    assert isinstance(cli_data["nodes"][0]["decorators"], list)
    assert cli_data["nodes"][0]["decorators"] == ['@app.post("/items")']

    # 3. REST API GET /api/summary check
    monkeypatch.setattr("vibetrace.server_api.db", db)
    monkeypatch.setattr("vibetrace.server_api.project_root", str(proj_dir))
    client = TestClient(app)
    resp = client.get("/api/summary", params={"file_path": "src/api.py"})
    assert resp.status_code == 200
    rest_data = resp.json()
    assert rest_data["total"] == 1
    assert isinstance(rest_data["nodes"][0]["decorators"], list)
    assert rest_data["nodes"][0]["decorators"] == ['@app.post("/items")']


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


def test_csharp_using_directive_imports_registered(tmp_path: Path):
    """Verify C# using directives create module nodes and import edges in GenericParser."""
    cs_file = tmp_path / "Controller.cs"
    code = """
    using System;
    using System.Collections.Generic;
    using Proj = MyProject.Services;

    namespace MyApp {
        public class Controller {
            public void Index() {}
        }
    }
    """
    cs_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = GenericParser(resolver=resolver, language="c_sharp")
    nodes, edges = parser.extract(str(cs_file), code.encode("utf-8"))

    module_nodes = {n.name: n for n in nodes if n.kind == "module"}
    assert "System" in module_nodes
    assert module_nodes["System"].file_path == "external"

    assert "Generic" in module_nodes
    assert module_nodes["Generic"].file_path == "external"

    assert "Proj" in module_nodes
    assert module_nodes["Proj"].file_path == "external"

    import_edges = [e for e in edges if e.relation_type == "imports"]
    assert len(import_edges) >= 3


def test_cpp_standard_include_registered(tmp_path: Path):
    """Verify C++ standard library includes (<vector>, <iostream>) create external module nodes."""
    cpp_file = tmp_path / "main.cpp"
    code = """
    #include <vector>
    #include <iostream>

    int main() {
        return 0;
    }
    """
    cpp_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = GenericParser(resolver=resolver, language="cpp")
    nodes, edges = parser.extract(str(cpp_file), code.encode("utf-8"))

    module_nodes = {n.name: n for n in nodes if n.kind == "module"}
    assert "vector" in module_nodes
    assert module_nodes["vector"].file_path == "external"

    assert "iostream" in module_nodes
    assert module_nodes["iostream"].file_path == "external"

    import_edges = [e for e in edges if e.relation_type == "imports"]
    assert len(import_edges) >= 2


def test_go_dot_import_wildcard_call_resolution(tmp_path: Path):
    """Verify Go dot-import (import . "fmt") enables bare call resolution to external module."""
    go_file = tmp_path / "main.go"
    code = """
    package main

    import . "fmt"

    func main() {
        Println("hello world")
    }
    """
    go_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = GoParser(resolver=resolver)
    _, edges = parser.extract(str(go_file), code.encode("utf-8"))

    call_edges = [e for e in edges if e.relation_type == "calls"]
    expected_println = hashlib.md5(b"external::Println").hexdigest()

    targets = {e.to_node_id for e in call_edges}
    assert expected_println in targets


def test_find_nodes_by_name_deterministic_order(tmp_path: Path):
    """Verify find_nodes_by_name returns nodes sorted deterministically by file_path and line."""
    db = IndexerDB(str(tmp_path / "test.db"))

    # Upsert nodes in reverse order
    node_b = Node(
        id=hashlib.md5(b"src/b.py::execute").hexdigest(),
        name="execute",
        qualified_name="execute",
        kind="function",
        file_path="src/b.py",
        start_line=50,
        end_line=60,
    )
    node_a2 = Node(
        id=hashlib.md5(b"src/a.py::execute:L80").hexdigest(),
        name="execute",
        qualified_name="execute",
        kind="function",
        file_path="src/a.py",
        start_line=80,
        end_line=90,
    )
    node_a1 = Node(
        id=hashlib.md5(b"src/a.py::execute:L20").hexdigest(),
        name="execute",
        qualified_name="execute",
        kind="function",
        file_path="src/a.py",
        start_line=20,
        end_line=30,
    )

    db.upsert_node(node_b)
    db.upsert_node(node_a2)
    db.upsert_node(node_a1)

    results = db.find_nodes_by_name("execute")
    assert len(results) == 3
    # Must be ordered by file_path ASC, start_line ASC: a.py:L20, a.py:L80, b.py:L50
    assert results[0]["file_path"] == "src/a.py"
    assert results[0]["start_line"] == 20
    assert results[1]["file_path"] == "src/a.py"
    assert results[1]["start_line"] == 80
    assert results[2]["file_path"] == "src/b.py"
    assert results[2]["start_line"] == 50


def test_get_symbol_references_deterministic_order(tmp_path: Path):
    """Verify get_symbol_references returns references deterministically ordered."""
    db = IndexerDB(str(tmp_path / "test_refs.db"))

    target_id = hashlib.md5(b"src/core.py::target").hexdigest()
    target_node = Node(
        id=target_id,
        name="target",
        qualified_name="target",
        kind="function",
        file_path="src/core.py",
        start_line=10,
        end_line=20,
    )
    caller_b = Node(
        id=hashlib.md5(b"src/z.py::caller_b").hexdigest(),
        name="caller_b",
        qualified_name="caller_b",
        kind="function",
        file_path="src/z.py",
        start_line=15,
        end_line=25,
    )
    caller_a = Node(
        id=hashlib.md5(b"src/a.py::caller_a").hexdigest(),
        name="caller_a",
        qualified_name="caller_a",
        kind="function",
        file_path="src/a.py",
        start_line=40,
        end_line=50,
    )

    db.upsert_node(target_node)
    db.upsert_node(caller_b)
    db.upsert_node(caller_a)

    # Insert edges in reverse alphabetical order
    db.upsert_edge(
        Edge(
            from_node_id=caller_b.id,
            to_node_id=target_id,
            relation_type="calls",
            call_line=20,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id=caller_a.id,
            to_node_id=target_id,
            relation_type="calls",
            call_line=45,
        )
    )

    refs = db.get_symbol_references(target_id)
    assert len(refs) == 2
    # Must be ordered by file_path ASC, call_line ASC: src/a.py first, then src/z.py
    assert refs[0]["file_path"] == "src/a.py"
    assert refs[1]["file_path"] == "src/z.py"


def test_get_neighbors_deterministic_order(tmp_path: Path):
    """Verify get_neighbors returns upward/downward neighbors in deterministic file/line order."""
    db = IndexerDB(str(tmp_path / "test_neighbors.db"))

    hub_id = hashlib.md5(b"src/hub.py::hub").hexdigest()
    hub = Node(
        id=hub_id,
        name="hub",
        qualified_name="hub",
        kind="function",
        file_path="src/hub.py",
        start_line=10,
        end_line=20,
    )
    n2 = Node(
        id=hashlib.md5(b"src/y.py::n2").hexdigest(),
        name="n2",
        qualified_name="n2",
        kind="function",
        file_path="src/y.py",
        start_line=30,
        end_line=40,
    )
    n1 = Node(
        id=hashlib.md5(b"src/x.py::n1").hexdigest(),
        name="n1",
        qualified_name="n1",
        kind="function",
        file_path="src/x.py",
        start_line=15,
        end_line=25,
    )

    db.upsert_node(hub)
    db.upsert_node(n2)
    db.upsert_node(n1)

    db.upsert_edge(Edge(from_node_id=hub_id, to_node_id=n2.id, relation_type="calls", call_line=12))
    db.upsert_edge(Edge(from_node_id=hub_id, to_node_id=n1.id, relation_type="calls", call_line=14))

    down = db.get_neighbors(hub_id, direction="down")
    assert len(down) == 2
    # Must be sorted by file_path ASC: src/x.py then src/y.py
    assert down[0]["file_path"] == "src/x.py"
    assert down[1]["file_path"] == "src/y.py"


def test_js_aliased_named_exports(tmp_path: Path) -> None:
    """Verify that export { local as alias } creates an exported alias node and references edge."""
    code = b"""
function computeTotal(x) {
    return x * 2;
}

function internalHelper() {
    return 1;
}

export { computeTotal as calcTotal, internalHelper };
"""
    parser = JavaScriptParser()
    nodes, edges = parser.extract("src/math.js", code)

    # 1. Verify calcTotal alias node exists with exported visibility
    alias_node = next((n for n in nodes if n.name == "calcTotal"), None)
    assert alias_node is not None
    assert alias_node.visibility == "exported"
    assert alias_node.kind == "variable"

    # 2. Verify references edge connects calcTotal to computeTotal
    ref_edge = next((e for e in edges if e.from_node_id == alias_node.id), None)
    assert ref_edge is not None
    assert ref_edge.relation_type == "references"
    compute_node = next((n for n in nodes if n.name == "computeTotal"), None)
    assert compute_node is not None
    assert ref_edge.to_node_id == compute_node.id

    # 3. Verify internalHelper visibility was upgraded to exported
    helper_node = next((n for n in nodes if n.name == "internalHelper"), None)
    assert helper_node is not None
    assert helper_node.visibility == "exported"


def test_fetch_method_options_extraction() -> None:
    """Verify that fetch('/api/...', { method: 'POST' }) captures the non-GET HTTP verb."""
    code = b"""
async function submitData(payload) {
    return await fetch('/api/data', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
}

async function deleteData(id) {
    return await fetch('/api/data/' + id, {
        method: "DELETE"
    });
}
"""
    parser = JavaScriptParser()
    nodes, _ = parser.extract("src/client.js", code)

    endpoints = [n for n in nodes if n.kind == "endpoint"]
    endpoint_names = [e.name for e in endpoints]

    assert "POST /api/data" in endpoint_names
    assert "GET /api/data" not in endpoint_names
    assert "DELETE /api/data/" in endpoint_names


def test_rust_enum_and_type_item_definitions() -> None:
    """Verify that RustParser extracts enum_item, its variants, and type_item aliases."""
    code = b"""
pub enum Status {
    Pending,
    Active,
    Completed(i32),
}

pub type MyResult<T> = Result<T, String>;
"""
    parser = RustParser()
    nodes, edges = parser.extract("src/models.rs", code)

    # 1. Verify Status enum node
    status_node = next((n for n in nodes if n.name == "Status"), None)
    assert status_node is not None
    assert status_node.kind == "enum"

    # 2. Verify enum variants
    variant_names = [n.name for n in nodes if (n.qualified_name or "").startswith("Status.")]
    assert "Pending" in variant_names
    assert "Active" in variant_names
    assert "Completed" in variant_names

    # 3. Verify defines edge from Status to variants
    variant_node = next((n for n in nodes if n.name == "Pending"), None)
    assert variant_node is not None
    def_edge = next(
        (e for e in edges if e.from_node_id == status_node.id and e.to_node_id == variant_node.id),
        None,
    )
    assert def_edge is not None
    assert def_edge.relation_type == "defines"

    # 4. Verify type alias
    type_node = next((n for n in nodes if n.name == "MyResult"), None)
    assert type_node is not None
    assert type_node.kind == "struct"


def test_generic_parser_enum_declarations() -> None:
    """Verify that GenericParser extracts enums across Java, C#, and C++."""
    # Java
    java_code = b"""
public enum OrderState {
    CREATED,
    PAID,
    CANCELLED
}
"""
    j_nodes, _ = GenericParser("java").extract("src/OrderState.java", java_code)
    j_enum = next((n for n in j_nodes if n.name == "OrderState"), None)
    assert j_enum is not None
    assert j_enum.kind == "enum"

    # C#
    csharp_code = b"""
public enum ConnectionStatus {
    Disconnected,
    Connecting,
    Connected
}
"""
    cs_nodes, _ = GenericParser("c_sharp").extract("src/Status.cs", csharp_code)
    cs_enum = next((n for n in cs_nodes if n.name == "ConnectionStatus"), None)
    assert cs_enum is not None
    assert cs_enum.kind == "enum"

    # C++
    cpp_code = b"""
enum class UserRole {
    Admin,
    Member,
    Guest
};
"""
    cpp_nodes, _ = GenericParser("cpp").extract("src/roles.hpp", cpp_code)
    cpp_enum = next((n for n in cpp_nodes if n.name == "UserRole"), None)
    assert cpp_enum is not None
    assert cpp_enum.kind == "enum"


def test_db_queries_ordering_determinism(tmp_path: Path) -> None:
    """Verify that file, incoming dependent, and import queries have deterministic order."""
    db = IndexerDB(str(tmp_path / "test.db"))

    # Insert nodes out of line order
    node_c = Node(
        id="f::c",
        name="c",
        qualified_name="c",
        kind="function",
        file_path="src/file.py",
        start_line=30,
        end_line=40,
    )
    node_a = Node(
        id="f::a",
        name="a",
        qualified_name="a",
        kind="function",
        file_path="src/file.py",
        start_line=10,
        end_line=20,
    )
    node_b = Node(
        id="f::b",
        name="b",
        qualified_name="b",
        kind="function",
        file_path="src/file.py",
        start_line=20,
        end_line=25,
    )
    for n in (node_c, node_a, node_b):
        db.upsert_node(n)

    # 1. get_nodes_in_file sorted by start_line ASC
    file_nodes = db.get_nodes_in_file("src/file.py")
    assert [n["name"] for n in file_nodes] == ["a", "b", "c"]

    # 2. get_incoming_dependents sorted deterministically
    caller_z = Node(
        id="z::caller",
        name="zCaller",
        qualified_name="zCaller",
        kind="function",
        file_path="src/z.py",
        start_line=5,
        end_line=10,
    )
    caller_m = Node(
        id="m::caller",
        name="mCaller",
        qualified_name="mCaller",
        kind="function",
        file_path="src/m.py",
        start_line=5,
        end_line=10,
    )
    for c in (caller_z, caller_m):
        db.upsert_node(c)
    db.upsert_edge(
        Edge(from_node_id="z::caller", to_node_id="f::a", relation_type="calls", call_line=6)
    )
    db.upsert_edge(
        Edge(from_node_id="m::caller", to_node_id="f::a", relation_type="calls", call_line=6)
    )

    dependents = db.get_incoming_dependents("f::a")
    assert dependents[0]["file_path"] == "src/m.py"
    assert dependents[1]["file_path"] == "src/z.py"

    # 3. get_file_imports sorted deterministically
    imp_b = Node(
        id="imp::b",
        name="beta",
        qualified_name="beta",
        kind="module",
        file_path="src/beta.py",
        start_line=1,
        end_line=1,
    )
    imp_a = Node(
        id="imp::a",
        name="alpha",
        qualified_name="alpha",
        kind="module",
        file_path="src/alpha.py",
        start_line=1,
        end_line=1,
    )
    for imp in (imp_b, imp_a):
        db.upsert_node(imp)
    db.upsert_edge(
        Edge(from_node_id="f::a", to_node_id="imp::b", relation_type="imports", call_line=1)
    )
    db.upsert_edge(
        Edge(from_node_id="f::a", to_node_id="imp::a", relation_type="imports", call_line=2)
    )

    imports = db.get_file_imports("src/file.py")
    assert [imp["name"] for imp in imports] == ["alpha", "beta"]


def test_hybrid_search_rrf_tie_breaking_stability(tmp_path: Path) -> None:
    """Verify that hybrid search with equal RRF scores produces stable, non-drifting pagination."""
    db = IndexerDB(str(tmp_path / "test.db"))

    # Four distinct nodes matching lexical query 'service'
    nodes = [
        Node(
            id=f"svc::{ch}",
            name=f"service_{ch}",
            qualified_name=f"service_{ch}",
            kind="function",
            file_path=f"src/{ch}.py",
            start_line=10,
            end_line=20,
            docstring="Shared service handler for events",
        )
        for ch in ("d", "c", "b", "a")
    ]
    for n in nodes:
        db.upsert_node(n)

    page1 = hybrid_search(db, "service", limit=2, offset=0)
    page2 = hybrid_search(db, "service", limit=2, offset=2)

    assert len(page1) == 2
    assert len(page2) == 2

    # Verify no item appears on both page 1 and page 2
    page1_ids = {r.node_id for r in page1}
    page2_ids = {r.node_id for r in page2}
    assert page1_ids.isdisjoint(page2_ids)

    # Verify deterministic tie-breaking sorted by file_path/name
    assert page1[0].file_path == "src/d.py"
    assert page1[1].file_path == "src/c.py"
    assert page2[0].file_path == "src/b.py"
    assert page2[1].file_path == "src/a.py"


def test_python_pep695_type_alias():
    parser = PythonParser()
    code = b"""
type Point = tuple[float, float]
type ListOrSet[T] = list[T] | set[T]
"""
    nodes, edges = parser.extract("geometry.py", code)
    node_map = {n.name: n for n in nodes}

    assert "Point" in node_map
    assert node_map["Point"].kind == "interface"
    assert "Point" in (node_map["Point"].signature or "")

    assert "ListOrSet" in node_map
    assert node_map["ListOrSet"].kind == "interface"

    defines_edges = [
        e for e in edges if e.relation_type == "defines" and e.to_node_id == node_map["Point"].id
    ]
    assert len(defines_edges) == 1
    assert defines_edges[0].from_node_id == node_map["geometry"].id


def test_python_enum_detection_and_variants():
    parser = PythonParser()
    code = b"""
from enum import Enum, IntEnum

class Color(Enum):
    RED = 'red'
    GREEN = 'green'

class Priority(IntEnum):
    HIGH = 1
    LOW = 2

def get_color():
    return Color.RED
"""
    nodes, edges = parser.extract("colors.py", code)
    node_map = {n.name: n for n in nodes}

    assert "Color" in node_map
    assert node_map["Color"].kind == "enum"

    assert "Priority" in node_map
    assert node_map["Priority"].kind == "enum"

    assert "RED" in node_map
    assert node_map["RED"].kind == "variable"
    assert node_map["RED"].qualified_name == "Color.RED"
    assert node_map["RED"].parent_id == node_map["Color"].id

    assert "HIGH" in node_map
    assert node_map["HIGH"].kind == "variable"
    assert node_map["HIGH"].qualified_name == "Priority.HIGH"
    assert node_map["HIGH"].parent_id == node_map["Priority"].id

    defines_red = [
        e
        for e in edges
        if e.from_node_id == node_map["Color"].id
        and e.to_node_id == node_map["RED"].id
        and e.relation_type == "defines"
    ]
    assert len(defines_red) == 1

    # Verify intra-file attribute references Color.RED
    color_refs = [
        e
        for e in edges
        if e.from_node_id == node_map["get_color"].id
        and e.to_node_id == node_map["RED"].id
        and e.relation_type == "references"
    ]
    assert len(color_refs) == 1


def test_go_const_and_top_level_var_extraction():
    parser = GoParser()
    code = b"""
package main

const (
    StatusOK = 200
    statusNotFound = 404
)
const MaxRetries = 3

var ErrNotFound = 1
var internalCounter = 0

func Worker() {
    localVar := 5
}
"""
    nodes, edges = parser.extract("server.go", code)
    node_map = {n.name: n for n in nodes}

    assert "StatusOK" in node_map
    assert node_map["StatusOK"].kind == "variable"
    assert node_map["StatusOK"].visibility == "public"

    assert "statusNotFound" in node_map
    assert node_map["statusNotFound"].kind == "variable"
    assert node_map["statusNotFound"].visibility == "private"

    assert "MaxRetries" in node_map
    assert node_map["MaxRetries"].kind == "variable"
    assert node_map["MaxRetries"].visibility == "public"

    assert "ErrNotFound" in node_map
    assert node_map["ErrNotFound"].kind == "variable"
    assert node_map["ErrNotFound"].visibility == "public"

    assert "internalCounter" in node_map
    assert node_map["internalCounter"].kind == "variable"
    assert node_map["internalCounter"].visibility == "private"

    # Ensure local variable inside function is not emitted as a package-level DBNode
    assert "localVar" not in node_map

    # Ensure defines edges from module to top-level constants and vars
    module_id = node_map["server.go"].id
    defines_status = [
        e
        for e in edges
        if e.from_node_id == module_id
        and e.to_node_id == node_map["StatusOK"].id
        and e.relation_type == "defines"
    ]
    assert len(defines_status) == 1


def test_js_custom_router_variable_route_linking():
    parser = JavaScriptParser()
    code = b"""
function handleAuth(req, res) {}
function getUser(req, res) {}
function clientAction() {
    fetch('/api/auth/token');
}

const authRouter = express.Router();
authRouter.post('/api/auth/login', handleAuth);

const userRoutes = express.Router();
userRoutes.get('/api/users/me', getUser);
"""
    nodes, edges = parser.extract("routes.js", code)
    node_map = {n.name: n for n in nodes}

    assert "POST /api/auth/login" in node_map
    assert "GET /api/users/me" in node_map

    # Backend routes link to their respective handler functions
    auth_routes = [
        e
        for e in edges
        if e.from_node_id == node_map["POST /api/auth/login"].id
        and e.to_node_id == node_map["handleAuth"].id
        and e.relation_type == "routes"
    ]
    assert len(auth_routes) == 1

    user_routes = [
        e
        for e in edges
        if e.from_node_id == node_map["GET /api/users/me"].id
        and e.to_node_id == node_map["getUser"].id
        and e.relation_type == "routes"
    ]
    assert len(user_routes) == 1

    # Client fetch links from caller to endpoint
    client_calls = [
        e
        for e in edges
        if e.from_node_id == node_map["clientAction"].id
        and e.to_node_id == node_map["GET /api/auth/token"].id
        and e.relation_type == "routes"
    ]
    assert len(client_calls) == 1


def test_db_queries_get_nodes_missing_embeddings_deterministic_order(tmp_path: Path):
    db_file = tmp_path / "test_missing.db"
    db = IndexerDB(str(db_file))

    # Insert out of order
    n3 = Node(id="id-3", name="n3", kind="function", file_path="b.py", start_line=50, end_line=60)
    n1 = Node(id="id-1", name="n1", kind="function", file_path="a.py", start_line=10, end_line=20)
    n2 = Node(id="id-2", name="n2", kind="function", file_path="a.py", start_line=30, end_line=40)

    for n in [n3, n1, n2]:
        db.upsert_node(n)

    missing = db.get_nodes_missing_embeddings("model-1")
    returned_ids = [m["id"] for m in missing]
    assert returned_ids == ["id-1", "id-2", "id-3"]
    db.close()


def test_modified_file_line_counts_round42():
    root = Path(__file__).parent.parent
    files_to_check = [
        root / "src" / "vibetrace" / "indexer" / "parsers" / "python_parser.py",
        root / "src" / "vibetrace" / "indexer" / "parsers" / "go_parser.py",
        root / "src" / "vibetrace" / "indexer" / "parsers" / "route_extractor.py",
        root / "src" / "vibetrace" / "indexer" / "db_queries.py",
    ]
    for path in files_to_check:
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 600, f"{path.name} has {lines} lines; must be <= 600"


def test_python_match_case_pattern_references() -> None:
    """Verify Python match-case statements extract references to classes and dotted symbols."""
    code = b"""
class Point:
    pass

class Status:
    OK = 200

def evaluate(val):
    match val:
        case Point(x, y):
            return 1
        case Status.OK:
            return 2
        case _:
            return 0
"""
    parser = PythonParser()
    nodes, edges = parser.extract("app.py", code)

    node_names = {n.name: n.id for n in nodes}
    assert "Point" in node_names
    assert "Status" in node_names
    assert "evaluate" in node_names

    eval_id = node_names["evaluate"]
    point_id = node_names["Point"]
    status_ok_id = parser._get_id("app.py", "Status.OK")

    ref_edges = [e for e in edges if e.from_node_id == eval_id and e.relation_type == "references"]
    referenced_ids = {e.to_node_id for e in ref_edges}

    assert point_id in referenced_ids
    assert status_ok_id in referenced_ids


def test_rust_constants_and_impl_consts_extraction() -> None:
    """Verify Rust const_item nodes (top-level and in impl blocks) are extracted as variables."""
    code = b"""
pub const MAX_BUFFER_SIZE: usize = 4096;

pub struct Cache;

impl Cache {
    pub const DEFAULT_TTL: u64 = 300;

    pub fn new() -> Self {
        Cache
    }
}
"""
    parser = RustParser()
    nodes, edges = parser.extract("cache.rs", code)

    names = {n.name: n for n in nodes}
    assert "MAX_BUFFER_SIZE" in names
    assert names["MAX_BUFFER_SIZE"].kind == "variable"

    assert "DEFAULT_TTL" in names
    assert names["DEFAULT_TTL"].kind == "variable"
    assert names["DEFAULT_TTL"].qualified_name == "Cache.DEFAULT_TTL"

    cache_id = names["Cache"].id
    ttl_id = names["DEFAULT_TTL"].id
    assert any(
        e.from_node_id == cache_id and e.to_node_id == ttl_id and e.relation_type == "defines"
        for e in edges
    )


def test_typescript_namespace_and_module_extraction() -> None:
    """Verify TypeScript namespace and module blocks create module nodes with scoped children."""
    code = b"""
export namespace Validation {
    export interface StringValidator {
        isAcceptable(s: string): boolean;
    }

    export function check(): boolean {
        return true;
    }
}
"""
    parser = TypeScriptParser()
    nodes, edges = parser.extract("validator.ts", code)

    names = {n.name: n for n in nodes}
    assert "Validation" in names
    assert names["Validation"].kind == "module"

    assert "StringValidator" in names
    assert names["StringValidator"].qualified_name == "Validation.StringValidator"
    assert names["StringValidator"].kind == "interface"

    assert "check" in names
    assert names["check"].qualified_name == "Validation.check"
    assert names["check"].kind == "function"

    ns_id = names["Validation"].id
    fn_id = names["check"].id
    assert any(
        e.from_node_id == ns_id and e.to_node_id == fn_id and e.relation_type == "defines"
        for e in edges
    )


def test_cpp_constructor_member_initializer_call_edges(tmp_path: Path) -> None:
    """Verify C++ member initialization list calls (createService()) emit call edges."""
    code = b"""
class Service {
public:
    Service() {}
};

Service createService() {
    return Service();
}

class Worker {
public:
    Worker(int x) : id(x), svc(createService()) {
    }
private:
    int id;
    Service svc;
};
"""
    parser = GenericParser("cpp")
    nodes, edges = parser.extract("worker.cpp", code)

    names = {n.name: n for n in nodes}
    assert "Worker" in names
    assert "createService" in names

    worker_ctor_id = names["Worker"].id
    svc_fn_id = names["createService"].id

    assert any(
        e.from_node_id == worker_ctor_id
        and e.to_node_id == svc_fn_id
        and e.relation_type == "calls"
        for e in edges
    )


@pytest.fixture
def all_parsers():
    """Return an instance of each language parser."""
    return [
        (PythonParser(), "test.py"),
        (JavaScriptParser(), "test.js"),
        (TypeScriptParser(), "test.ts"),
        (GoParser(), "test.go"),
        (RustParser(), "test.rs"),
        (GenericParser("java"), "Test.java"),
        (GenericParser("cpp"), "test.cpp"),
        (GenericParser("c_sharp"), "Test.cs"),
        (GenericParser("ruby"), "test.rb"),
        (GenericParser("php"), "test.php"),
    ]


def test_parsers_resilience_on_random_binary_fuzz(all_parsers):
    """Verify that completely random byte streams and binary fuzz never crash any parser."""
    random.seed(42)

    # Generate 25 distinct corrupt/malformed byte sequences
    fuzz_samples: list[bytes] = [
        b"\x00" * 50,
        b"\xff\xfe\xfd\x00\x01\x02" * 10,
        b"".join(bytes([random.randint(0, 255)]) for _ in range(500)),
        b"def \x00\x01(a, \xff): \xfe\n",
        b"class \x80\x81\x82 { int \x00; }\n",
        b"func \x00() { return \xff }\n",
        b"fn \x00() -> \xff { }\n",
    ]

    for _ in range(18):
        length = random.randint(10, 300)
        chunk = bytearray(random.getrandbits(8) for _ in range(length))
        fuzz_samples.append(bytes(chunk))

    for parser, filename in all_parsers:
        p_name = type(parser).__name__
        for idx, sample in enumerate(fuzz_samples):
            try:
                nodes, edges = parser.extract(filename, sample)
                assert isinstance(nodes, list)
                assert isinstance(edges, list)
            except Exception as err:
                pytest.fail(f"{p_name} crashed on fuzz #{idx} for {filename}: {err}")


def test_parsers_resilience_on_deeply_nested_trees(all_parsers):
    """Verify parsers handle deeply nested structures (depth 120) without stack overflow."""
    depth = 120

    nested_python = ("(" * depth) + "1" + (")" * depth)
    nested_braces = ("{" * depth) + "x = 1;" + ("}" * depth)
    nested_brackets = ("[" * depth) + "'val'" + ("]" * depth)

    samples = [
        nested_python.encode("utf-8"),
        nested_braces.encode("utf-8"),
        nested_brackets.encode("utf-8"),
    ]

    for parser, filename in all_parsers:
        for s in samples:
            nodes, edges = parser.extract(filename, s)
            assert isinstance(nodes, list)
            assert isinstance(edges, list)


def test_parsers_resilience_on_unicode_and_emojis(all_parsers):
    """Verify parsers gracefully parse Unicode identifiers, emojis, RTL characters."""
    unicode_code = """
# 🚀 Unicode Stress Test
class 宇宙Class:
    def 계산_함수(self, פונקציה: str) -> bool:
        변수 = "مرحبا بالعالم"
        return True

fn_call = 宇宙Class().계산_함수("✨")
"""
    code_bytes = unicode_code.encode("utf-8")

    for parser, filename in all_parsers:
        nodes, edges = parser.extract(filename, code_bytes)
        assert isinstance(nodes, list)
        assert isinstance(edges, list)


def test_parsers_resilience_on_unterminated_tokens(all_parsers):
    """Verify parsers gracefully handle unclosed quotes, comments, and partial definitions."""
    unterminated_samples = [
        b'def broken_string():\n    msg = "unterminated string\ndef next_func(): pass\n',
        b"/* Unclosed multiline comment that spans to end of file\nclass Foo { int x;\n",
        b"def incomplete_params(a, b, :",
        b"class UnclosedClass {\n    public void method() {\n",
        b"func incompleteGo(a string, b int",
        b"fn incompleteRust<T: Display>(val: T",
    ]

    for parser, filename in all_parsers:
        for s in unterminated_samples:
            nodes, edges = parser.extract(filename, s)
            assert isinstance(nodes, list)
            assert isinstance(edges, list)


def test_cross_platform_path_normalization_fuzz():
    """Verify path normalization properties across arbitrary Windows, POSIX, and mixed slashes."""
    test_cases = [
        ("src\\vibetrace\\db.py", "src/vibetrace/db.py"),
        ("./src/vibetrace/db.py", "src/vibetrace/db.py"),
        (".\\src\\vibetrace\\db.py", "src/vibetrace/db.py"),
        ("./././src/db.py", "src/db.py"),
        ("src/services/../services/trace_service.py", "src/services/../services/trace_service.py"),
        ("C:\\Project\\VibeGraph\\src\\file.py", "C:/Project/VibeGraph/src/file.py"),
    ]

    for raw, expected in test_cases:
        assert _clean_path(raw) == expected

    # Random combinatorial path fuzzing
    random.seed(1337)
    separators = ["/", "\\", "\\\\", "//"]
    segments = ["src", "app", "components", "utils", "index.ts", "test.py", ".", ".."]

    for _ in range(50):
        length = random.randint(2, 6)
        chosen_segs = [random.choice(segments) for _ in range(length)]
        sep = random.choice(separators)
        raw_path = sep.join(chosen_segs)

        cleaned = _clean_path(raw_path)
        # Invariant 1: Cleaned paths must never contain backslashes
        assert "\\" not in cleaned
        # Invariant 2: Cleaned paths must never have a leading redundant './'
        assert not cleaned.startswith("./")


def test_monorepo_go_subfolder_and_package_sibling_resolution(tmp_path: Path):
    """Verify monorepo subfolder go.mod detection and cross-file package symbol resolution."""
    # Setup monorepo structure:
    # workspace/
    #   backend-go/
    #     go.mod (module myorg/backend)
    #     cmd/main.go (calls app.NewService() and app.Helper())
    #     internal/app/
    #       service.go (defines func NewService)
    #       helpers.go (defines func Helper)
    backend = tmp_path / "backend-go"
    (backend / "cmd").mkdir(parents=True)
    (backend / "internal" / "app").mkdir(parents=True)

    (backend / "go.mod").write_text("module myorg/backend\n\ngo 1.22\n", encoding="utf-8")
    (backend / "internal" / "app" / "service.go").write_text(
        "package app\n\nfunc NewService() *Service { return &Service{} }\n",
        encoding="utf-8",
    )
    (backend / "internal" / "app" / "helpers.go").write_text(
        'package app\n\nfunc Helper() string { return "ok" }\n',
        encoding="utf-8",
    )
    main_go = (
        "package main\n\n"
        'import "myorg/backend/internal/app"\n\n'
        "func main() {\n"
        "    s := app.NewService()\n"
        "    _ = app.Helper()\n"
        "}\n"
    )
    (backend / "cmd" / "main.go").write_text(main_go, encoding="utf-8")

    resolver = ModuleResolver(tmp_path)
    assert "myorg/backend" in resolver.go_modules
    assert resolver.go_modules["myorg/backend"] == "backend-go"

    parser = GoParser(resolver)
    _nodes, edges = parser.extract(
        "backend-go/cmd/main.go",
        (backend / "cmd" / "main.go").read_bytes(),
    )

    # Verify calls
    calls = [e for e in edges if e.relation_type == "calls"]
    assert len(calls) == 2

    # Verify NewService resolved to service.go, and Helper resolved to helpers.go
    service_id = parser._get_id("backend-go/internal/app/service.go", "NewService")
    helper_id = parser._get_id("backend-go/internal/app/helpers.go", "Helper")

    assert any(c.to_node_id == service_id for c in calls)
    assert any(c.to_node_id == helper_id for c in calls)


def test_monorepo_rust_subfolder_cargo_toml_detection(tmp_path: Path):
    """Verify monorepo subfolder Cargo.toml package detection."""
    tauri = tmp_path / "src-tauri"
    (tauri / "src").mkdir(parents=True)

    (tauri / "Cargo.toml").write_text(
        '[package]\nname = "my_desktop_app"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (tauri / "src" / "lib.rs").write_text("pub fn init() {}\n", encoding="utf-8")
    (tauri / "src" / "main.rs").write_text(
        "fn main() { my_desktop_app::init(); }\n",
        encoding="utf-8",
    )

    resolver = ModuleResolver(tmp_path)
    assert "my_desktop_app" in resolver.cargo_packages
    assert resolver.cargo_packages["my_desktop_app"] == "src-tauri"

    resolved = resolver.resolve("my_desktop_app/init", "src-tauri/src/main.rs")
    assert resolved in ("src-tauri/src/init.rs", "src-tauri/src/lib.rs", "src-tauri/src/init")


def test_modified_file_line_counts_round48():
    """Ensure resolver.py and base.py adhere strictly to Rule 12 size limits (<= 600 LOC)."""
    root = Path(__file__).parent.parent
    files_to_check = [
        root / "src" / "vibetrace" / "indexer" / "resolver.py",
        root / "src" / "vibetrace" / "indexer" / "parsers" / "base.py",
    ]
    for path in files_to_check:
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 600, f"{path.name} has {lines} lines; must be <= 600"
