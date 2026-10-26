from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibetrace.cli_commands import cmd_summary
from vibetrace.indexer.db import IndexerDB, Node
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.parsers.rust_parser import RustParser
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.mcp.schemas import ResponseFormat, StructuralSummaryInput
from vibetrace.mcp.server import vibetrace_get_structural_summary
from vibetrace.server_api import app


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
