from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest

from vibetrace.cli_commands import cmd_references
from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.indexer.parsers.go_parser import GoParser
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.mcp.schemas import ReferencesInput, ResponseFormat
from vibetrace.mcp.server import vibetrace_find_references


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
