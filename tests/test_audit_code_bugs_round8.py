"""Audit verification tests for Round 8 code critique fixes.

Covers:
1. Trailing parens '()' and whitespace in symbol queries (trace, slice, call-tree, MCP).
2. Go var_declaration expression_list AST unwrap, composite literal pointers, multi-var.
3. ModuleResolver get_module_name fallback for unregistered/package root paths.
4. FastAPI GET /api/saved-traces/{trace_id} endpoint and saved_traces_service.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.indexer.parsers.go_parser import GoParser
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.mcp.schemas import CallStackInput, ReferencesInput
from vibetrace.mcp.server import vibetrace_find_references, vibetrace_get_call_stack
from vibetrace.server_api import app
from vibetrace.services.call_tree_service import build_call_tree
from vibetrace.services.saved_traces_service import (
    SavedTraceCreate,
    create_saved_trace,
    get_saved_trace_by_id,
)
from vibetrace.services.trace_service import get_code_slice, trace_execution_path


@pytest.fixture
def temp_project(tmp_path: Path):
    """Sets up a temporary indexed project for query testing."""
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    src_file = tmp_path / "app.py"
    src_file.write_text(
        "def helper():\n    return 42\n\ndef main():\n    return helper()\n",
        encoding="utf-8",
    )

    n_main = Node(
        id="app.py::main",
        name="main",
        qualified_name="app.main",
        kind="function",
        file_path="app.py",
        start_line=4,
        end_line=5,
    )
    n_helper = Node(
        id="app.py::helper",
        name="helper",
        qualified_name="app.helper",
        kind="function",
        file_path="app.py",
        start_line=1,
        end_line=2,
    )
    db.batch_save_file_index(
        "app.py",
        "hash123",
        [n_main, n_helper],
        [
            Edge(
                from_node_id="app.py::main",
                to_node_id="app.py::helper",
                relation_type="calls",
                call_line=5,
            )
        ],
    )
    return db, tmp_path


def test_symbol_query_with_trailing_parens_and_spaces(temp_project):
    """Verify tracepath, slice, and call-tree resolve symbols with trailing '()'."""
    db, root = temp_project

    # Tracepath resolution
    res_bfs = trace_execution_path(db, "main() ", "helper()", engine="bfs")
    assert res_bfs.found is True
    assert len(res_bfs.hops) == 1

    res_cte = trace_execution_path(db, "main()", " helper() ", engine="cte")
    assert res_cte.found is True

    # Code slice resolution
    slice_res = get_code_slice(db, root, "helper()")
    assert slice_res.found is True
    assert slice_res.name == "helper"
    assert "return 42" in slice_res.source_code

    # Call tree resolution
    tree_res = build_call_tree(db, "main()")
    assert tree_res.found is True
    assert tree_res.tree is not None
    assert tree_res.tree.name == "main"
    assert len(tree_res.tree.children) == 1
    assert tree_res.tree.children[0].name == "helper"


@pytest.mark.asyncio
async def test_mcp_symbol_parens_resilience(temp_project, monkeypatch):
    """Verify MCP tools handle symbol names with trailing '()'."""
    db, root = temp_project
    monkeypatch.setattr(
        "vibetrace.mcp.server._get_context_for_path",
        lambda _: (db, root),
    )

    # find_references
    ref_out = await vibetrace_find_references(
        ReferencesInput(symbol_name="helper()", scope_path=str(root))
    )
    assert "References to `helper()`" in ref_out
    assert "Used by `main` in `app.py`" in ref_out

    # get_call_stack
    stack_out = await vibetrace_get_call_stack(
        CallStackInput(node_name="main()", file_path="app.py", depth=2)
    )
    assert "Trace for `main`" in stack_out
    assert "helper" in stack_out


def test_go_var_declaration_unwrapping_and_multivar(tmp_path: Path):
    """Verify Go var_spec expressions and multi-var definitions unwrap properly."""
    go_code = """
package main

type Calculator struct{}

func (c *Calculator) Compute() int {
    return 42
}

func NewCalculator() *Calculator {
    return &Calculator{}
}

func Run() {
    var calc = NewCalculator()
    calc.Compute()

    var a, b Calculator
    a.Compute()
    b.Compute()

    var ptr = &Calculator{}
    ptr.Compute()
}
"""
    parser = GoParser(ModuleResolver(tmp_path))
    nodes, edges = parser.extract("main.go", go_code.encode("utf-8"))

    compute_node = next(n for n in nodes if n.name == "Compute")
    compute_calls = [
        e for e in edges if e.relation_type == "calls" and e.to_node_id == compute_node.id
    ]
    # All 4 calls (calc, a, b, ptr) should resolve to Calculator.Compute
    assert len(compute_calls) == 4


def test_module_resolver_get_module_name_fallbacks(tmp_path: Path):
    """Verify canonical module name resolution handles ./ prefixes and fallbacks."""
    resolver = ModuleResolver(tmp_path)

    # Normalization of ./ prefix
    assert resolver.get_module_name("./src/pkg/__init__.py") == "pkg"
    assert resolver.get_module_name("pkg/__init__.py") == "pkg"

    # Index files
    assert resolver.get_module_name("src/components/index.ts") == "components"
    assert resolver.get_module_name("rust_crate/src/mod.rs") == "rust_crate"

    # Regular files
    assert resolver.get_module_name("./src/utils/calc.py") == "utils.calc"
    assert resolver.get_module_name("lib/network/http.go") == "network.http"


def test_fastapi_saved_trace_by_id_endpoint(tmp_path: Path, monkeypatch):
    """Verify GET /api/saved-traces/{trace_id} retrieves or returns 404."""
    db_path = tmp_path / "saved.db"
    db = IndexerDB(str(db_path))
    monkeypatch.setattr("vibetrace.server_api.db", db)

    saved = create_saved_trace(
        db,
        SavedTraceCreate(
            name="auth_flow",
            description="Trace from login to token verification",
            from_symbol="login",
            to_symbol="verify_token",
            max_depth=5,
        ),
    )

    # Service layer check
    fetched = get_saved_trace_by_id(db, saved.id)
    assert fetched is not None
    assert fetched.name == "auth_flow"
    assert fetched.from_symbol == "login"
    assert get_saved_trace_by_id(db, 99999) is None

    # HTTP endpoint check
    client = TestClient(app)
    resp = client.get(f"/api/saved-traces/{saved.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == saved.id
    assert data["name"] == "auth_flow"
    assert data["from_symbol"] == "login"

    # Non-existent ID -> 404
    resp_404 = client.get("/api/saved-traces/99999")
    assert resp_404.status_code == 404
