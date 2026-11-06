"""Consolidated regression test suite: test_regression_api_and_server.py."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vibetrace.cli import main
from vibetrace.cli import main as cli_main
from vibetrace.cli_commands import cmd_index, cmd_search
from vibetrace.docs.db import DocDB
from vibetrace.docs.service import DocCatalogManager, DocIndexService
from vibetrace.federation.config import (
    auto_discover_members,
    find_project_root,
    load_project_manifest,
    save_project_manifest,
)
from vibetrace.federation.models import ProjectManifest, ProjectSettings, RepoMember
from vibetrace.federation.project import ProjectWorkspace
from vibetrace.federation.storage_postgres import POSTGRES_SCHEMA_SQL, PostgresStorageBackend
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.hooks import HOOK_SCRIPT_BLOCK, install_lefthook_config
from vibetrace.indexer.lsp_bridge import resolve_unresolved_edges
from vibetrace.indexer.models import Edge, Node
from vibetrace.indexer.watcher import CodeChangeHandler
from vibetrace.mcp.schemas import (
    CallerGuardInput,
    CallStackInput,
    DependenciesInput,
    ImpactAnalysisInput,
    ProjectGuardInput,
    ProjectLinksInput,
    ProjectSearchInput,
    ProjectStatusInput,
    ResponseFormat,
    SemanticSearchInput,
    StructuralSummaryInput,
    VibeBundleInput,
)
from vibetrace.mcp.server import (
    vibetrace_check_caller_guard,
    vibetrace_get_call_stack,
    vibetrace_get_dependencies,
    vibetrace_get_structural_summary,
    vibetrace_get_vibe_bundle,
    vibetrace_impact_analysis,
    vibetrace_semantic_search,
)
from vibetrace.mcp.tools_project import (
    vibetrace_project_guard,
    vibetrace_project_links,
    vibetrace_project_search,
    vibetrace_project_status,
)
from vibetrace.server_api import app
from vibetrace.server_api import db as api_db
from vibetrace.services.caller_guard import (
    CallerGuardService,
    format_caller_guard_md,
    format_staged_guard_md,
    parse_git_diff_line_ranges,
)
from vibetrace.services.hybrid_search import hybrid_search
from vibetrace.services.vibe_bundle import (
    VibeBundleService,
    estimate_tokens,
    truncate_to_tokens,
)


def test_get_graph_data_directory_subsystem_filtering(tmp_path: Path):
    """Verify IndexerDB.get_graph_data correctly queries directory prefixes."""
    db_path = tmp_path / "test_graph.db"
    db = IndexerDB(str(db_path))

    node1 = Node(
        id="n1",
        name="func_a",
        qualified_name="pkg.subsystem.func_a",
        kind="function",
        file_path="src/subsystem/mod_a.py",
        start_line=1,
        end_line=10,
    )
    node2 = Node(
        id="n2",
        name="func_b",
        qualified_name="pkg.subsystem.func_b",
        kind="function",
        file_path="src/subsystem/mod_b.py",
        start_line=1,
        end_line=15,
    )
    node3 = Node(
        id="n3",
        name="func_c",
        qualified_name="other.mod_c",
        kind="function",
        file_path="other/mod_c.py",
        start_line=1,
        end_line=20,
    )
    for n in [node1, node2, node3]:
        db.upsert_node(n)
    for e in [
        Edge(from_node_id="n1", to_node_id="n2", relation_type="calls"),
        Edge(from_node_id="n1", to_node_id="n3", relation_type="calls"),
    ]:
        db.upsert_edge(e)

    # 1. Directory query for "src"
    dir_graph = db.get_graph_data(file_path="src")
    assert len(dir_graph["nodes"]) == 2
    assert {n["id"] for n in dir_graph["nodes"]} == {"n1", "n2"}
    assert len(dir_graph["edges"]) == 2  # Both edges touch at least one node in src

    # 2. Directory query for "src/subsystem"
    sub_graph = db.get_graph_data(file_path="src/subsystem")
    assert len(sub_graph["nodes"]) == 2

    # 3. Exact file query
    file_graph = db.get_graph_data(file_path="src/subsystem/mod_a.py")
    assert len(file_graph["nodes"]) == 1
    assert file_graph["nodes"][0]["id"] == "n1"

    # 4. Non-matching directory
    empty_graph = db.get_graph_data(file_path="nonexistent")
    assert empty_graph == {"nodes": [], "edges": []}
    db.close()


def test_lsp_bridge_calculates_indentation_offset(tmp_path: Path):
    """Verify resolve_unresolved_edges passes first non-whitespace column offset to LSP."""
    db_path = tmp_path / "test_lsp.db"
    db = IndexerDB(str(db_path))

    caller_file = tmp_path / "caller.py"
    caller_content = (
        "def main():\n"
        "    # line 2\n"
        "        indented_service_call()\n"  # line 3: 8 spaces indent
    )
    caller_file.write_text(caller_content, encoding="utf-8")

    # Insert unresolved edge in caller.py at line 3
    node_caller = Node(
        id="node_caller",
        name="main",
        qualified_name="caller.main",
        kind="function",
        file_path="caller.py",
        start_line=1,
        end_line=4,
    )
    db.upsert_node(node_caller)
    db.upsert_edge(
        Edge(
            from_node_id="node_caller",
            to_node_id="unresolved_target_service",
            relation_type="calls",
            call_line=3,
        )
    )

    captured_calls: list[tuple[Path, int, int]] = []

    mock_client = MagicMock()

    def fake_get_definition(file_path: Path, line: int, character: int = 0):
        captured_calls.append((file_path, line, character))
        return None  # No definition to rewire, but position must be recorded

    mock_client.get_definition.side_effect = fake_get_definition

    resolve_unresolved_edges(db, project_root=tmp_path, lsp_client=mock_client)

    assert len(captured_calls) == 1
    call_path, call_line, char_offset = captured_calls[0]
    assert call_path == caller_file
    assert call_line == 3
    assert char_offset == 8  # 8 spaces of indentation on line 3
    db.close()


def test_hybrid_search_offset_pagination(tmp_path: Path):
    """Verify hybrid_search slices results correctly with limit and offset."""
    db_path = tmp_path / "test_pagination.db"
    db = IndexerDB(str(db_path))

    nodes = [
        Node(
            id=f"nid_{i}",
            name=f"calc_tax_rate_{i}",
            qualified_name=f"finance.calc_tax_rate_{i}",
            kind="function",
            file_path="finance.py",
            start_line=i * 10,
            end_line=i * 10 + 5,
        )
        for i in range(1, 7)
    ]
    for n in nodes:
        db.upsert_node(n)

    page1 = hybrid_search(db, "calc_tax_rate", limit=2, offset=0)
    page2 = hybrid_search(db, "calc_tax_rate", limit=2, offset=2)
    page3 = hybrid_search(db, "calc_tax_rate", limit=2, offset=4)

    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 2

    ids1 = {r.node_id for r in page1}
    ids2 = {r.node_id for r in page2}
    ids3 = {r.node_id for r in page3}

    assert ids1.isdisjoint(ids2)
    assert ids2.isdisjoint(ids3)
    assert ids1.isdisjoint(ids3)
    db.close()


def test_rest_api_search_offset_pagination(tmp_path: Path):
    """Verify GET /api/search paginates results via offset query parameter."""
    db_path = tmp_path / "test_api_search.db"
    db = IndexerDB(str(db_path))

    nodes = [
        Node(
            id=f"api_nid_{i}",
            name=f"dispatch_order_{i}",
            qualified_name=f"orders.dispatch_order_{i}",
            kind="function",
            file_path="orders.py",
            start_line=i * 10,
            end_line=i * 10 + 5,
        )
        for i in range(1, 6)
    ]
    for n in nodes:
        db.upsert_node(n)

    orig_conn = api_db._get_conn
    api_db._get_conn = db._get_conn

    client = TestClient(app)
    try:
        resp1 = client.get("/api/search", params={"q": "dispatch_order", "limit": 2, "offset": 0})
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert len(data1) == 2

        resp2 = client.get("/api/search", params={"q": "dispatch_order", "limit": 2, "offset": 2})
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert len(data2) == 2

        ids1 = {r["id"] for r in data1}
        ids2 = {r["id"] for r in data2}
        assert ids1.isdisjoint(ids2)
    finally:
        api_db._get_conn = orig_conn
        db.close()


@pytest.mark.asyncio
async def test_mcp_semantic_search_offset_pagination(tmp_path: Path):
    """Verify FastMCP vibetrace_search_semantic respects offset parameter."""
    context_dir = tmp_path / ".vibetrace"
    context_dir.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(context_dir / "vibetrace.db"))

    nodes = [
        Node(
            id=f"mcp_nid_{i}",
            name=f"execute_pipeline_{i}",
            qualified_name=f"pipeline.execute_pipeline_{i}",
            kind="function",
            file_path="pipeline.py",
            start_line=i * 10,
            end_line=i * 10 + 5,
        )
        for i in range(1, 6)
    ]
    for n in nodes:
        db.upsert_node(n)
    db.close()

    res_page1 = await vibetrace_semantic_search(
        SemanticSearchInput(
            query="execute_pipeline",
            limit=2,
            offset=0,
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    data1 = json.loads(res_page1)
    assert len(data1) == 2

    res_page2 = await vibetrace_semantic_search(
        SemanticSearchInput(
            query="execute_pipeline",
            limit=2,
            offset=2,
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    data2 = json.loads(res_page2)
    assert len(data2) == 2

    ids1 = {r["node_id"] for r in data1}
    ids2 = {r["node_id"] for r in data2}
    assert ids1.isdisjoint(ids2)


def test_cli_search_offset_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify CLI search command passes offset flag to hybrid_search."""
    context_dir = tmp_path / ".vibetrace"
    context_dir.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(context_dir / "vibetrace.db"))

    nodes = [
        Node(
            id=f"cli_nid_{i}",
            name=f"audit_rule_{i}",
            qualified_name=f"rules.audit_rule_{i}",
            kind="function",
            file_path="rules.py",
            start_line=i * 10,
            end_line=i * 10 + 5,
        )
        for i in range(1, 6)
    ]
    for n in nodes:
        db.upsert_node(n)
    db.close()

    args1 = argparse.Namespace(
        path=str(tmp_path),
        query="audit_rule",
        signature=False,
        limit=2,
        offset=0,
        json=True,
        no_sync=True,
    )
    cmd_search(args1)
    out1, _ = capsys.readouterr()
    data1 = json.loads(out1)
    assert len(data1) == 2

    args2 = argparse.Namespace(
        path=str(tmp_path),
        query="audit_rule",
        signature=False,
        limit=2,
        offset=2,
        json=True,
        no_sync=True,
    )
    cmd_search(args2)
    out2, _ = capsys.readouterr()
    data2 = json.loads(out2)
    assert len(data2) == 2

    ids1 = {r["node_id"] for r in data1}
    ids2 = {r["node_id"] for r in data2}
    assert ids1.isdisjoint(ids2)


@pytest.fixture
def setup_parity_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up temporary workspace with test database and files."""
    context_dir = tmp_path / ".vibetrace"
    context_dir.mkdir(parents=True, exist_ok=True)
    db_file = context_dir / "vibetrace.db"
    db = IndexerDB(str(db_file))

    # Create dummy source files
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    file_a = src_dir / "service.py"
    file_a.write_text(
        "import math\n\ndef execute():\n    return helper()\n\ndef helper():\n    return 42\n",
        encoding="utf-8",
    )

    node_exec = Node(
        id="src/service.py:execute:3",
        name="execute",
        qualified_name="execute",
        kind="function",
        file_path="src/service.py",
        start_line=3,
        end_line=4,
        signature="def execute() -> int",
    )
    node_help = Node(
        id="src/service.py:helper:6",
        name="helper",
        qualified_name="helper",
        kind="function",
        file_path="src/service.py",
        start_line=6,
        end_line=7,
        signature="def helper() -> int",
    )

    db.upsert_node(node_exec)
    db.upsert_node(node_help)
    db.upsert_edge(
        Edge(
            from_node_id=node_exec.id,
            to_node_id=node_help.id,
            relation_type="calls",
            call_line=4,
        )
    )
    db.update_file_hash("src/service.py", "hash123", 0)

    # Monkeypatch server_api global db & project_root
    import vibetrace.server_api

    monkeypatch.setattr(vibetrace.server_api, "db", db)
    monkeypatch.setattr(vibetrace.server_api, "project_root", str(tmp_path))

    return tmp_path, db


def test_rest_api_hooks_status_and_actions(setup_parity_workspace):
    """Verify REST API /api/hooks parity with CLI hooks."""
    _tmp_path, _ = setup_parity_workspace
    client = TestClient(app)

    # 1. GET /api/hooks (status)
    res = client.get("/api/hooks")
    assert res.status_code == 200
    data = res.json()
    assert "provider" in data
    assert "lefthook" in data
    assert "git" in data

    # 2. POST /api/hooks (uninstall default)
    res_un = client.post("/api/hooks?action=uninstall")
    assert res_un.status_code == 200
    assert res_un.json()["action"] == "uninstall"


def test_rest_api_search_hybrid_flag_parity(setup_parity_workspace):
    """Verify REST API /api/search?hybrid=true returns SearchResult models."""
    _tmp_path, _ = setup_parity_workspace
    client = TestClient(app)

    # Standard AST/FTS search
    res_std = client.get("/api/search", params={"q": "execute"})
    assert res_std.status_code == 200
    items_std = res_std.json()
    assert len(items_std) >= 1
    assert items_std[0]["name"] == "execute"

    # Hybrid 4-stream RRF search parity
    res_hyb = client.get("/api/search", params={"q": "execute", "hybrid": "true"})
    assert res_hyb.status_code == 200
    items_hyb = res_hyb.json()
    assert len(items_hyb) >= 1
    assert "rrf_score" in items_hyb[0]
    assert "match_sources" in items_hyb[0]


@pytest.mark.asyncio
async def test_mcp_scope_path_parity_for_tools(setup_parity_workspace):
    """Verify MCP tools accept scope_path for all structural and graph inspection operations."""
    tmp_path, _ = setup_parity_workspace

    # 1. Structural summary with scope_path
    sum_res = await vibetrace_get_structural_summary(
        StructuralSummaryInput(
            file_path="src/service.py",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    sum_data = json.loads(sum_res)
    assert sum_data["total"] >= 2
    assert any(n["name"] == "execute" for n in sum_data["nodes"])

    # 2. Call stack with scope_path
    cs_res = await vibetrace_get_call_stack(
        CallStackInput(
            node_name="execute",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    cs_data = json.loads(cs_res)
    assert cs_data["found"] is True
    assert cs_data["symbol"] == "execute"

    # 3. Impact analysis with scope_path
    imp_res = await vibetrace_impact_analysis(
        ImpactAnalysisInput(
            file_path="src/service.py",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    imp_data = json.loads(imp_res)
    assert "total_impact" in imp_data
    assert imp_data["file_path"] == "src/service.py"

    # 4. Dependencies with scope_path
    dep_res = await vibetrace_get_dependencies(
        DependenciesInput(
            file_path="src/service.py",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    dep_data = json.loads(dep_res)
    assert dep_data["file_path"] == "src/service.py"
    assert "counts" in dep_data


@pytest.fixture
def cross_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Setup isolated workspace with both code index and doc index."""
    proj_root = tmp_path / "cross_proj"
    proj_root.mkdir()
    global_dir = tmp_path / "cross_global" / ".vibetrace"
    global_dir.mkdir(parents=True)
    monkeypatch.setenv("VIBETRACE_GLOBAL_ROOT", str(global_dir))
    monkeypatch.setenv("VIBETRACE_PROJECT_ROOT", str(proj_root))

    # 1. Setup code db with a code symbol
    code_dir = proj_root / "src"
    code_dir.mkdir()
    code_file = code_dir / "pipeline.py"
    code_file.write_text(
        "def run_ingestion_pipeline():\n    '''Execute high-speed ingestion.'''\n    pass\n",
        encoding="utf-8",
    )

    db_path = proj_root / ".vibetrace" / "vibetrace.db"
    from vibetrace.indexer.models import Node

    code_db = IndexerDB(db_path)
    code_db.upsert_node(
        Node(
            id="src/pipeline.py::run_ingestion_pipeline",
            file_path="src/pipeline.py",
            name="run_ingestion_pipeline",
            qualified_name="run_ingestion_pipeline",
            kind="function",
            start_line=1,
            end_line=3,
            signature="def run_ingestion_pipeline()",
            docstring="Execute high-speed ingestion.",
        )
    )

    # 2. Setup doc db with an architecture doc
    docs_dir = proj_root / "docs"
    docs_dir.mkdir()
    doc_file = docs_dir / "pipeline_spec.md"
    doc_file.write_text(
        "# Data Ingestion Pipeline\n\nHigh-speed ingestion architecture specification.\n",
        encoding="utf-8",
    )

    doc_db = DocDB(db_path)
    DocIndexService.index_catalog("core", [str(docs_dir)], doc_db, proj_root)

    return {
        "proj_root": proj_root,
        "docs_dir": docs_dir,
        "db_path": db_path,
        "code_db": code_db,
    }


def test_cli_cross_search_markdown_and_json(cross_workspace, capsys):
    """Verify CLI search --with-docs outputs unified docs and code in markdown and JSON."""
    proj_root = cross_workspace["proj_root"]

    # 1. Markdown mode
    code = main(["search", "ingestion pipeline", "--with-docs", "--root", str(proj_root)])
    assert code == 0
    captured = capsys.readouterr()
    assert "Unified Search Results" in captured.out
    assert "### 📚 Documentation Context" in captured.out
    assert "### 💻 Code Symbols" in captured.out
    assert "pipeline_spec.md" in captured.out
    assert "run_ingestion_pipeline" in captured.out

    # 2. JSON mode
    code_json = main(
        ["search", "ingestion pipeline", "--with-docs", "--root", str(proj_root), "--json"]
    )
    assert code_json == 0
    json_out = capsys.readouterr().out
    payload = json.loads(json_out)
    assert "code" in payload
    assert "docs" in payload
    assert len(payload["code"]) >= 1
    assert len(payload["docs"]) >= 1


@pytest.mark.asyncio
async def test_mcp_cross_search(cross_workspace):
    """Verify MCP vibetrace_semantic_search with include_docs=True."""
    proj_root = cross_workspace["proj_root"]

    # 1. Markdown mode
    md_res = await vibetrace_semantic_search(
        SemanticSearchInput(
            query="ingestion pipeline",
            scope_path=str(proj_root),
            include_docs=True,
            response_format=ResponseFormat.MARKDOWN,
        )
    )
    assert "### 📚 Documentation Context" in md_res
    assert "### 💻 Code Symbols" in md_res
    assert "run_ingestion_pipeline" in md_res

    # 2. JSON mode
    json_res = await vibetrace_semantic_search(
        SemanticSearchInput(
            query="ingestion pipeline",
            scope_path=str(proj_root),
            include_docs=True,
            response_format=ResponseFormat.JSON,
        )
    )
    data = json.loads(json_res)
    assert "code_symbols" in data
    assert "doc_chunks" in data
    assert len(data["code_symbols"]) >= 1
    assert len(data["doc_chunks"]) >= 1

    # 3. Default include_docs=False backward compatibility
    compat_res = await vibetrace_semantic_search(
        SemanticSearchInput(
            query="ingestion pipeline",
            scope_path=str(proj_root),
            include_docs=False,
            response_format=ResponseFormat.JSON,
        )
    )
    compat_data = json.loads(compat_res)
    assert isinstance(compat_data, list)


def test_rest_api_search_with_docs(cross_workspace, monkeypatch: pytest.MonkeyPatch):
    """Verify GET /api/search with with_docs=true."""
    monkeypatch.setattr("vibetrace.server_api.db", cross_workspace["code_db"])
    client = TestClient(app)

    res = client.get("/api/search?q=ingestion&with_docs=true")
    assert res.status_code == 200
    data = res.json()
    assert "code" in data
    assert "docs" in data
    assert len(data["code"]) >= 1
    assert len(data["docs"]) >= 1


@pytest.fixture
def sample_db(tmp_path: Path) -> tuple[IndexerDB, Path]:
    """Create a temporary populated DB with nodes, edges, and test files."""
    db_file = tmp_path / ".vibetrace" / "vibetrace.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(db_path=str(db_file))

    # Target node: calculate_tax in src/billing.py
    src_file = tmp_path / "src" / "billing.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text(
        "def calculate_tax(amount: float, rate: float = 0.2) -> float:\n    return amount * rate\n",
        encoding="utf-8",
    )

    node_target = Node(
        id="billing:calculate_tax",
        name="calculate_tax",
        qualified_name="calculate_tax",
        kind="function",
        file_path="src/billing.py",
        start_line=1,
        end_line=2,
        signature="(amount: float, rate: float = 0.2) -> float",
        docstring="Compute tax given amount and rate.",
    )
    db.upsert_node(node_target)

    # Caller 1: checkout in src/checkout.py
    check_file = tmp_path / "src" / "checkout.py"
    check_file.write_text(
        "from billing import calculate_tax\n"
        "def checkout(cart):\n"
        "    tax = calculate_tax(cart.total, 0.15)\n"
        "    return cart.total + tax\n",
        encoding="utf-8",
    )
    node_caller1 = Node(
        id="checkout:checkout",
        name="checkout",
        qualified_name="checkout",
        kind="function",
        file_path="src/checkout.py",
        start_line=2,
        end_line=4,
        signature="(cart)",
    )
    db.upsert_node(node_caller1)
    db.upsert_edge(
        Edge(
            from_node_id="checkout:checkout",
            to_node_id="billing:calculate_tax",
            relation_type="calls",
            call_line=3,
        )
    )

    # Caller 2: test_calculate_tax in tests/test_billing.py
    test_file = tmp_path / "tests" / "test_billing.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "from billing import calculate_tax\n"
        "def test_calculate_tax():\n"
        "    assert calculate_tax(100.0, 0.2) == 20.0\n",
        encoding="utf-8",
    )
    node_caller2 = Node(
        id="test_billing:test_calculate_tax",
        name="test_calculate_tax",
        qualified_name="test_calculate_tax",
        kind="function",
        file_path="tests/test_billing.py",
        start_line=2,
        end_line=3,
        signature="()",
    )
    db.upsert_node(node_caller2)
    db.upsert_edge(
        Edge(
            from_node_id="test_billing:test_calculate_tax",
            to_node_id="billing:calculate_tax",
            relation_type="calls",
            call_line=3,
        )
    )

    return db, tmp_path


def test_caller_guard_service_detection(sample_db: tuple[IndexerDB, Path]) -> None:
    db, root = sample_db
    report = CallerGuardService.get_caller_guard(db, root, "calculate_tax")
    assert report.found is True
    assert report.symbol == "calculate_tax"
    assert report.file_path == "src/billing.py"
    assert report.total_callers == 2
    assert "GUARD ALERT" in report.safety_advisory

    # Verify both callers captured with accurate snippets
    caller_names = {c.caller_name for c in report.callers}
    assert "checkout" in caller_names
    assert "test_calculate_tax" in caller_names

    md = format_caller_guard_md(report)
    assert "## Caller Guard Report: `calculate_tax`" in md
    assert "checkout.py:L3" in md


def test_caller_guard_not_found(sample_db: tuple[IndexerDB, Path]) -> None:
    db, root = sample_db
    report = CallerGuardService.get_caller_guard(db, root, "non_existent_func")
    assert report.found is False
    assert "was not found" in report.safety_advisory
    md = format_caller_guard_md(report)
    assert "was not found" in md


def test_vibe_bundle_service_assembly(sample_db: tuple[IndexerDB, Path]) -> None:
    db, root = sample_db
    bundle = VibeBundleService.assemble_bundle(
        db,
        root,
        intent="calculate tax rate calculation",
        target_symbol="calculate_tax",
        token_budget=1000,
    )
    assert bundle.intent == "calculate tax rate calculation"
    assert bundle.target_symbol == "calculate_tax"
    assert bundle.estimated_tokens <= 1000
    assert bundle.caller_guard is not None
    assert bundle.caller_guard.total_callers == 2
    assert bundle.code_slice is not None
    assert "calculate_tax" in bundle.code_slice.source_code

    # Verify sibling test pattern was discovered from AST caller
    assert bundle.test_context is not None
    assert bundle.test_context.found is True
    assert bundle.test_context.test_file == "tests/test_billing.py"
    assert bundle.test_context.test_function == "test_calculate_tax"

    prompt = bundle.formatted_prompt
    assert "# Vibe Bundle:" in prompt
    assert "Pre-Flight Caller Guard" in prompt
    assert "Target Code Implementation" in prompt
    assert "Sibling Test Pattern" in prompt


def test_vibe_bundle_token_truncation() -> None:
    huge_text = "word " * 2000
    assert estimate_tokens(huge_text) > 1000
    truncated = truncate_to_tokens(huge_text, 200)
    assert estimate_tokens(truncated) <= 250
    assert "... [truncated to fit token budget]" in truncated


@pytest.mark.asyncio
async def test_mcp_caller_guard_and_vibe_bundle(sample_db: tuple[IndexerDB, Path]) -> None:
    db, root = sample_db
    with patch("vibetrace.mcp.server.get_context_for_path", return_value=(db, root)):
        # 1. Caller Guard Markdown
        res_guard_md = await vibetrace_check_caller_guard(
            CallerGuardInput(symbol_name="calculate_tax", response_format=ResponseFormat.MARKDOWN)
        )
        assert "Caller Guard Report: `calculate_tax`" in res_guard_md
        assert "GUARD ALERT" in res_guard_md

        # 2. Caller Guard JSON
        res_guard_json = await vibetrace_check_caller_guard(
            CallerGuardInput(symbol_name="calculate_tax", response_format=ResponseFormat.JSON)
        )
        assert '"found": true' in res_guard_json
        assert '"total_callers": 2' in res_guard_json

        # 3. Vibe Bundle Markdown
        res_bundle_md = await vibetrace_get_vibe_bundle(
            VibeBundleInput(
                intent="calculate tax rate calculation",
                target_symbol="calculate_tax",
                response_format=ResponseFormat.MARKDOWN,
            )
        )
        assert "# Vibe Bundle:" in res_bundle_md

        # 4. Vibe Bundle JSON
        res_bundle_json = await vibetrace_get_vibe_bundle(
            VibeBundleInput(
                intent="calculate tax rate calculation",
                target_symbol="calculate_tax",
                response_format=ResponseFormat.JSON,
            )
        )
        assert '"token_budget": 1500' in res_bundle_json


def test_rest_api_vibe_guard_and_bundle(
    sample_db: tuple[IndexerDB, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, root = sample_db
    monkeypatch.setattr("vibetrace.server_api.db", db)
    monkeypatch.setenv("VIBETRACE_PROJECT_ROOT", str(root))

    client = TestClient(app)

    # 1. GET /api/vibe/guard
    res_guard = client.get("/api/vibe/guard?symbol=calculate_tax")
    assert res_guard.status_code == 200
    data_guard = res_guard.json()
    assert data_guard["found"] is True
    assert data_guard["total_callers"] == 2
    assert "calculate_tax" in data_guard["symbol"]

    # 2. GET /api/vibe/guard (not found)
    res_missing = client.get("/api/vibe/guard?symbol=non_existent_symbol_abc")
    assert res_missing.status_code == 404

    # 3. GET /api/vibe/bundle
    res_bundle = client.get(
        "/api/vibe/bundle?intent=calculate+tax+logic&symbol=calculate_tax&budget=1200"
    )
    assert res_bundle.status_code == 200
    data_bundle = res_bundle.json()
    assert data_bundle["token_budget"] == 1200
    assert data_bundle["caller_guard"]["found"] is True
    assert "formatted_prompt" in data_bundle


def test_cli_guard_and_bundle(
    sample_db: tuple[IndexerDB, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, root = sample_db
    from vibetrace.cli import main

    # 1. CLI Guard Human Mode
    code_guard = main(["guard", "calculate_tax", "--root", str(root)])
    assert code_guard == 0
    out_guard, _ = capsys.readouterr()
    assert "Caller Guard Report: `calculate_tax`" in out_guard
    assert "checkout.py:L3" in out_guard

    # 2. CLI Guard JSON Mode
    code_guard_json = main(["guard", "calculate_tax", "--json", "--root", str(root)])
    assert code_guard_json == 0
    out_guard_json, _ = capsys.readouterr()
    data = json.loads(out_guard_json)
    assert data["found"] is True
    assert data["total_callers"] == 2

    # 3. CLI Bundle Human Mode
    code_bundle = main(
        ["bundle", "tax calculation", "--symbol", "calculate_tax", "--root", str(root)]
    )
    assert code_bundle == 0
    out_bundle, _ = capsys.readouterr()
    assert "# Vibe Bundle:" in out_bundle
    assert "Pre-Flight Caller Guard" in out_bundle

    # 4. CLI Bundle JSON Mode
    code_bundle_json = main(
        [
            "bundle",
            "tax calculation",
            "--symbol",
            "calculate_tax",
            "--budget",
            "1200",
            "--json",
            "--root",
            str(root),
        ]
    )
    assert code_bundle_json == 0
    out_bundle_json, _ = capsys.readouterr()
    bundle_data = json.loads(out_bundle_json)
    assert bundle_data["token_budget"] == 1200
    assert bundle_data["caller_guard"]["found"] is True


@pytest.fixture
def web_ui_db(tmp_path: Path) -> tuple[IndexerDB, Path]:
    """Create a temporary populated DB matching Web UI expectations."""
    db_file = tmp_path / ".vibetrace" / "vibetrace.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(db_path=str(db_file))

    # Source symbol
    src_file = tmp_path / "src" / "api.ts"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text(
        "export function fetchData(): Promise<any> {\n    return fetch('/data');\n}\n",
        encoding="utf-8",
    )

    node_target = Node(
        id="api:fetchData",
        name="fetchData",
        qualified_name="api.fetchData",
        kind="function",
        file_path="src/api.ts",
        start_line=1,
        end_line=3,
        signature="(): Promise<any>",
    )
    db.upsert_node(node_target)

    # Caller symbol
    client_file = tmp_path / "src" / "client.ts"
    client_file.write_text(
        "import { fetchData } from './api';\nfunction load() {\n    const d = fetchData();\n}\n",
        encoding="utf-8",
    )

    node_caller = Node(
        id="client:load",
        name="load",
        qualified_name="client.load",
        kind="function",
        file_path="src/client.ts",
        start_line=2,
        end_line=4,
    )
    db.upsert_node(node_caller)
    db.upsert_edge(
        Edge(
            from_node_id="client:load",
            to_node_id="api:fetchData",
            relation_type="calls",
            call_line=3,
        )
    )

    return db, tmp_path


def test_web_ui_caller_guard_contract(
    web_ui_db: tuple[IndexerDB, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify CallerGuardBadge data contracts against backend API."""
    db, root = web_ui_db
    monkeypatch.setattr("vibetrace.server_api.db", db)
    monkeypatch.setenv("VIBETRACE_PROJECT_ROOT", str(root))

    client = TestClient(app)
    res = client.get("/api/vibe/guard?symbol=fetchData&file_path=src/api.ts")
    assert res.status_code == 200

    data = res.json()
    assert data["found"] is True
    assert data["symbol"] == "fetchData"
    assert data["file_path"] == "src/api.ts"
    assert data["total_callers"] == 1
    assert len(data["callers"]) == 1
    caller = data["callers"][0]
    assert caller["caller_name"] == "load"
    assert caller["caller_file"] == "src/client.ts"
    assert caller["call_line"] == 3
    assert "fetchData" in caller["call_snippet"]
    assert "GUARD ALERT" in data["safety_advisory"]


def test_web_ui_vibe_bundle_contract(
    web_ui_db: tuple[IndexerDB, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify VibeBundleButton data contracts against backend API."""
    db, root = web_ui_db
    monkeypatch.setattr("vibetrace.server_api.db", db)
    monkeypatch.setenv("VIBETRACE_PROJECT_ROOT", str(root))

    client = TestClient(app)
    res = client.get("/api/vibe/bundle?intent=fetchData&symbol=fetchData&budget=1500")
    assert res.status_code == 200

    data = res.json()
    assert data["intent"] == "fetchData"
    assert data["target_symbol"] == "fetchData"
    assert data["token_budget"] == 1500
    assert data["estimated_tokens"] > 0
    assert "formatted_prompt" in data
    assert "# Vibe Bundle: fetchData" in data["formatted_prompt"]
    assert "```typescript" in data["formatted_prompt"]


def test_parse_git_diff_line_ranges() -> None:
    diff_sample = """
diff --git a/src/billing.py b/src/billing.py
index abc..def 100644
--- a/src/billing.py
+++ b/src/billing.py
@@ -10,3 +10,5 @@
+def new_logic():
+    pass
@@ -45 +47,2 @@
-old_call()
+new_call()
diff --git a/src/utils.ts b/src/utils.ts
--- a/src/utils.ts
+++ b/src/utils.ts
@@ -100,5 +100,8 @@
"""
    ranges = parse_git_diff_line_ranges(diff_sample)
    assert "src/billing.py" in ranges
    assert (10, 14) in ranges["src/billing.py"]
    assert (47, 48) in ranges["src/billing.py"]
    assert "src/utils.ts" in ranges
    assert (100, 107) in ranges["src/utils.ts"]


@pytest.fixture
def staged_db(tmp_path: Path) -> tuple[IndexerDB, Path]:
    db_file = tmp_path / ".vibetrace" / "vibetrace.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(db_path=str(db_file))

    # Node 1: calculate_tax in src/billing.py (L10-L20), has 1 caller
    billing_file = tmp_path / "src" / "billing.py"
    billing_file.parent.mkdir(parents=True, exist_ok=True)
    billing_file.write_text(
        "# Header\n" * 9 + "def calculate_tax(amount: float) -> float:\n    return amount * 0.2\n",
        encoding="utf-8",
    )
    node_tax = Node(
        id="billing:calculate_tax",
        name="calculate_tax",
        qualified_name="calculate_tax",
        kind="function",
        file_path="src/billing.py",
        start_line=10,
        end_line=20,
    )
    db.upsert_node(node_tax)

    # Node 2: safe_helper in src/billing.py (L40-L50), 0 callers
    node_safe = Node(
        id="billing:safe_helper",
        name="safe_helper",
        qualified_name="safe_helper",
        kind="function",
        file_path="src/billing.py",
        start_line=40,
        end_line=50,
    )
    db.upsert_node(node_safe)

    # Caller to calculate_tax
    checkout_file = tmp_path / "src" / "checkout.py"
    checkout_file.write_text("def checkout(): calculate_tax(50)\n", encoding="utf-8")
    node_caller = Node(
        id="checkout:checkout",
        name="checkout",
        qualified_name="checkout",
        kind="function",
        file_path="src/checkout.py",
        start_line=1,
        end_line=1,
    )
    db.upsert_node(node_caller)
    db.upsert_edge(
        Edge(
            from_node_id="checkout:checkout",
            to_node_id="billing:calculate_tax",
            relation_type="calls",
            call_line=1,
        )
    )

    return db, tmp_path


def test_staged_caller_guard_detection(staged_db: tuple[IndexerDB, Path]) -> None:
    db, root = staged_db

    # Diff modifying lines 10-12 in src/billing.py (overlaps with calculate_tax L10-11)
    mock_diff = """
diff --git a/src/billing.py b/src/billing.py
--- a/src/billing.py
+++ b/src/billing.py
@@ -10,2 +10,3 @@
"""
    reports = CallerGuardService.get_staged_caller_guard(db, root, diff_text=mock_diff)
    assert len(reports) == 1
    report = reports[0]
    assert report.symbol == "calculate_tax"
    assert report.total_callers == 1
    assert report.callers[0].caller_name == "checkout"

    md = format_staged_guard_md(reports)
    assert "Pre-Commit Caller Guard Check" in md
    assert "⚠️ `calculate_tax`" in md
    assert "checkout" in md


def test_staged_caller_guard_empty_diff(staged_db: tuple[IndexerDB, Path]) -> None:
    db, root = staged_db
    reports = CallerGuardService.get_staged_caller_guard(db, root, diff_text="")
    assert len(reports) == 0
    md = format_staged_guard_md(reports)
    assert "No modified symbol definitions detected" in md


def test_staged_caller_guard_cli(
    staged_db: tuple[IndexerDB, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, root = staged_db

    mock_diff = """
diff --git a/src/billing.py b/src/billing.py
--- a/src/billing.py
+++ b/src/billing.py
@@ -10,2 +10,3 @@
"""
    orig_fn = CallerGuardService.get_staged_caller_guard
    monkeypatch.setattr(
        "vibetrace.services.caller_guard.CallerGuardService.get_staged_caller_guard",
        lambda _db, _root, **_kwargs: orig_fn(_db, _root, diff_text=mock_diff),
    )

    # 1. Advisory mode (exit 0 even if callers exist)
    code = main(["guard", "--staged", "--no-sync", "--root", str(root)])
    assert code == 0
    out, _ = capsys.readouterr()
    assert "Pre-Commit Caller Guard Check" in out
    assert "calculate_tax" in out
    assert "checkout" in out

    # 2. JSON mode
    code_json = main(["guard", "--staged", "--json", "--no-sync", "--root", str(root)])
    assert code_json == 0
    out_json, _ = capsys.readouterr()
    data = json.loads(out_json)
    assert len(data) == 1
    assert data[0]["symbol"] == "calculate_tax"
    assert data[0]["total_callers"] == 1

    # 3. Strict mode (exit 1 if callers affected)
    code_strict = main(["guard", "--staged", "--strict", "--no-sync", "--root", str(root)])
    assert code_strict == 1


@pytest.fixture
def test_workspace(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    # Add caller and callee nodes
    caller = Node(
        id="mod_caller",
        name="caller_fn",
        qualified_name="caller_fn",
        kind="function",
        file_path="src/caller.py",
        start_line=10,
        end_line=20,
    )
    target = Node(
        id="mod_target",
        name="target_fn",
        qualified_name="target_fn",
        kind="function",
        file_path="src/target.py",
        start_line=30,
        end_line=40,
    )
    container = Node(
        id="mod_container",
        name="src.target",
        qualified_name="src.target",
        kind="module",
        file_path="src/target.py",
        start_line=1,
        end_line=50,
    )
    db.upsert_node(caller)
    db.upsert_node(target)
    db.upsert_node(container)

    db.upsert_edge(
        Edge(
            from_node_id="mod_caller",
            to_node_id="mod_target",
            relation_type="calls",
            call_line=15,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="mod_container",
            to_node_id="mod_target",
            relation_type="defines",
            call_line=None,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="mod_caller",
            to_node_id="mod_target",
            relation_type="imports",
            call_line=1,
        )
    )
    return db, tmp_path


@pytest.mark.asyncio
async def test_mcp_caller_guard_staged(test_workspace: tuple[IndexerDB, Path]):
    """Verify MCP caller guard supports staged git inspection."""
    db, root = test_workspace
    with patch("vibetrace.mcp.server.get_context_for_path", return_value=(db, root)):
        # Empty staged diff
        res_empty = await vibetrace_check_caller_guard(
            CallerGuardInput(staged=True, response_format=ResponseFormat.MARKDOWN)
        )
        assert "No modified symbol definitions detected" in res_empty

        # JSON response format with empty diff
        res_json = await vibetrace_check_caller_guard(
            CallerGuardInput(staged=True, response_format=ResponseFormat.JSON)
        )
        assert res_json == "[]"


@pytest.mark.asyncio
async def test_mcp_call_stack_and_dependencies(test_workspace: tuple[IndexerDB, Path]):
    """Verify MCP call-stack all_relations and dependencies reverse filter."""
    db, root = test_workspace
    with patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, root)):
        # 1. Call stack execution-only by default
        stack_default = await vibetrace_get_call_stack(
            CallStackInput(node_name="target_fn", all_relations=False)
        )
        assert "caller_fn" in stack_default
        assert "(defines)" not in stack_default

        # 2. Call stack with all_relations=True
        stack_all = await vibetrace_get_call_stack(
            CallStackInput(node_name="target_fn", all_relations=True)
        )
        assert "(defines)" in stack_all

        # 3. Dependencies with reverse=True
        deps_rev = await vibetrace_get_dependencies(
            DependenciesInput(file_path="src/target.py", reverse=True)
        )
        assert "Inbound Dependents" in deps_rev
        assert "src/caller.py" in deps_rev


def test_rest_api_guard_staged_and_dependencies(test_workspace: tuple[IndexerDB, Path]):
    """Verify FastAPI endpoints support staged guard and enriched dependency analysis."""
    db, root = test_workspace
    with (
        patch("vibetrace.server_api.db", db),
        patch("vibetrace.server_api.project_root", root),
    ):
        client = TestClient(app)

        # 1. /api/vibe/guard?staged=true
        resp_guard = client.get("/api/vibe/guard?staged=true")
        assert resp_guard.status_code == 200
        assert isinstance(resp_guard.json(), list)

        # 2. /api/dependencies
        resp_deps = client.get("/api/dependencies?file_path=src/target.py")
        assert resp_deps.status_code == 200
        data = resp_deps.json()
        assert "dependents" in data
        assert "circular" in data
        assert data["counts"]["dependents"] >= 1


def test_cmd_index_auto_syncs_doc_catalogs(tmp_path: Path):
    """Verify that vibetrace index automatically synchronizes configured local doc catalogs."""
    proj_root = tmp_path / "my_project"
    proj_root.mkdir()
    docs_dir = proj_root / "docs"
    docs_dir.mkdir()
    src_dir = proj_root / "src"
    src_dir.mkdir()

    # Add code and doc file
    (src_dir / "app.py").write_text("def hello(): pass\n", encoding="utf-8")
    (docs_dir / "guide.md").write_text(
        "# API Guide\n\nDetailed documentation here.\n", encoding="utf-8"
    )

    # Register local catalog
    DocCatalogManager.add_path("core", "docs", project_root=proj_root)

    # 1. Run cmd_index (default: auto-sync docs enabled)
    args = argparse.Namespace(
        path=str(proj_root),
        root=str(proj_root),
        force=True,
        embed=False,
        lsp=False,
        json=True,
        no_docs=False,
    )
    res = cmd_index(args)
    assert res == 0

    # Verify doc chunks were indexed into DocDB
    db_path = proj_root / ".vibetrace" / "vibetrace.db"
    doc_db = DocDB(db_path)
    assert doc_db.get_file_mtime("core", "docs/guide.md") is not None
    hits = doc_db.search_bm25("API Guide", catalog="core")
    assert len(hits) >= 1
    assert "API Guide" in hits[0]["heading_path"]

    # 2. Run with --no-docs (should skip doc indexing)
    (docs_dir / "guide.md").write_text("# Updated Guide\n\nNew info.\n", encoding="utf-8")
    args_no_docs = argparse.Namespace(
        path=str(proj_root),
        root=str(proj_root),
        force=False,
        embed=False,
        lsp=False,
        json=True,
        no_docs=True,
    )
    cmd_index(args_no_docs)
    # Old chunks still present because no_docs skipped reindex
    hits_after = doc_db.search_bm25("API Guide", catalog="core")
    assert len(hits_after) >= 1
    assert "API Guide" in hits_after[0]["heading_path"]


def test_hooks_post_commit_includes_docs_index(tmp_path: Path):
    """Verify that automated hook installers wire both vibetrace index and vibetrace docs index."""
    # Check bash template block
    assert "vibetrace docs index" in HOOK_SCRIPT_BLOCK
    assert "uv run vibetrace docs index" in HOOK_SCRIPT_BLOCK

    # Check lefthook injection
    lh_file = tmp_path / "lefthook.yml"
    lh_file.write_text(
        "pre-commit:\n  commands:\n    lint:\n      run: echo lint\n", encoding="utf-8"
    )
    ok, _ = install_lefthook_config(tmp_path)
    assert ok is True
    content = lh_file.read_text(encoding="utf-8")
    assert "uv run vibetrace index && uv run vibetrace docs index" in content


def test_watcher_doc_change_handling(tmp_path: Path):
    """Verify CodeChangeHandler detects documentation file modifications and synchronizes DocDB."""
    proj_root = tmp_path / "watch_proj"
    proj_root.mkdir()
    docs_dir = proj_root / "docs"
    docs_dir.mkdir()
    (proj_root / ".vibetrace").mkdir()

    DocCatalogManager.add_path("core", "docs", project_root=proj_root)
    db = IndexerDB(str(proj_root / ".vibetrace" / "vibetrace.db"))
    handler = CodeChangeHandler(db, root_path=str(proj_root))

    # Simulate creating/modifying a markdown doc
    doc_file = docs_dir / "spec.md"
    doc_file.write_text("# Feature Specification\n\nLive spec body.\n", encoding="utf-8")

    handled = handler._handle_doc_change(str(doc_file), is_deleted=False)
    assert handled is True

    doc_db = DocDB(db.db_path)
    assert doc_db.get_file_mtime("core", "docs/spec.md") is not None
    hits = doc_db.search_bm25("Feature Specification", catalog="core")
    assert len(hits) >= 1
    assert "Feature Specification" in hits[0]["heading_path"]

    # Simulate deleting the markdown doc
    handled_del = handler._handle_doc_change(str(doc_file), is_deleted=True)
    assert handled_del is True
    assert doc_db.get_file_mtime("core", "docs/spec.md") is None


@pytest.fixture
def test_workspace_round60(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    caller = Node(
        id="mod_caller",
        name="caller_fn",
        qualified_name="caller_fn",
        kind="function",
        file_path="src/caller.py",
        start_line=10,
        end_line=20,
    )
    target = Node(
        id="mod_target",
        name="target_fn",
        qualified_name="target_fn",
        kind="function",
        file_path="src/target.py",
        start_line=30,
        end_line=40,
    )
    container = Node(
        id="mod_container",
        name="src.target",
        qualified_name="src.target",
        kind="module",
        file_path="src/target.py",
        start_line=1,
        end_line=50,
    )
    db.upsert_node(caller)
    db.upsert_node(target)
    db.upsert_node(container)

    db.upsert_edge(
        Edge(
            from_node_id="mod_caller",
            to_node_id="mod_target",
            relation_type="calls",
            call_line=15,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="mod_container",
            to_node_id="mod_target",
            relation_type="defines",
            call_line=None,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="mod_caller",
            to_node_id="mod_target",
            relation_type="imports",
            call_line=1,
        )
    )
    return db, tmp_path


def test_guard_strict_endpoint(test_workspace_round60: tuple[IndexerDB, Path]):
    """Verify /api/vibe/guard?staged=true&strict=true returns structured strict status."""
    db, root = test_workspace_round60
    with (
        patch("vibetrace.server_api.db", db),
        patch("vibetrace.server_api.project_root", root),
    ):
        client = TestClient(app)

        # 1. Staged without strict: returns plain list
        resp = client.get("/api/vibe/guard?staged=true")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

        # 2. Staged with strict=true: returns strict payload with violation status
        resp_strict = client.get("/api/vibe/guard?staged=true&strict=true")
        assert resp_strict.status_code == 200
        data = resp_strict.json()
        assert isinstance(data, dict)
        assert data["staged"] is True
        assert data["strict"] is True
        assert "violation" in data
        assert "reports" in data
        assert isinstance(data["reports"], list)


def test_dependencies_reverse_endpoint(test_workspace_round60: tuple[IndexerDB, Path]):
    """Verify /api/dependencies?file_path=...&reverse=true returns inverted dependents."""
    db, root = test_workspace_round60
    with (
        patch("vibetrace.server_api.db", db),
        patch("vibetrace.server_api.project_root", root),
    ):
        client = TestClient(app)

        # 1. Default forward dependencies
        resp_fwd = client.get("/api/dependencies?file_path=src/target.py")
        assert resp_fwd.status_code == 200
        fwd_data = resp_fwd.json()
        assert "internal" in fwd_data
        assert "counts" in fwd_data

        # 2. Reverse dependencies
        resp_rev = client.get("/api/dependencies?file_path=src/target.py&reverse=true")
        assert resp_rev.status_code == 200
        rev_data = resp_rev.json()
        assert rev_data["reverse"] is True
        assert "dependents" in rev_data
        assert any(d["file_path"] == "src/caller.py" for d in rev_data["dependents"])
        assert "circular" in rev_data


def test_call_stack_all_relations(test_workspace_round60: tuple[IndexerDB, Path]):
    """Verify /api/call-stack accepts all_relations parameter."""
    db, root = test_workspace_round60
    with (
        patch("vibetrace.server_api.db", db),
        patch("vibetrace.server_api.project_root", root),
    ):
        client = TestClient(app)

        resp = client.get("/api/call-stack?symbol=target_fn&all_relations=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "symbol" in data
        assert data["symbol"] == "target_fn"
        assert "call_stack" in data


def test_reindex_with_docs(test_workspace_round60: tuple[IndexerDB, Path]):
    """Verify /api/reindex supports docs synchronization flag."""
    db, root = test_workspace_round60

    mock_catalog = MagicMock()
    mock_catalog.name = "core"
    mock_catalog.scope = "local"
    mock_catalog.paths = ["docs"]

    with (
        patch("vibetrace.server_api.db", db),
        patch("vibetrace.server_api.project_root", root),
        patch("vibetrace.indexer.main.reindex_all"),
        patch(
            "vibetrace.docs.service.DocCatalogManager.list_catalogs", return_value=[mock_catalog]
        ),
        patch(
            "vibetrace.docs.service.DocIndexService.index_catalog",
            return_value={"files": 2, "chunks": 5},
        ) as mock_doc_idx,
    ):
        client = TestClient(app)
        resp = client.post("/api/reindex", json={"force": True, "docs": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "docs" in data
        assert data["docs"]["catalogs"] == 1
        assert data["docs"]["files"] == 2
        assert data["docs"]["chunks"] == 5
        mock_doc_idx.assert_called_once()


def test_skill_endpoints(tmp_path: Path):
    """Verify /api/skill GET and POST endpoints."""
    with patch("vibetrace.server_api.project_root", tmp_path):
        client = TestClient(app)

        # 1. GET /api/skill (initially not installed)
        resp_get = client.get("/api/skill")
        assert resp_get.status_code == 200
        get_data = resp_get.json()
        assert "installed" in get_data
        assert "paths" in get_data
        assert get_data["installed"] is False

        # 2. POST /api/skill (installs into project root)
        resp_post = client.post("/api/skill?global_install=false")
        assert resp_post.status_code == 200
        post_data = resp_post.json()
        assert post_data["status"] == "ok"
        assert len(post_data["installed_paths"]) >= 1

        # 3. GET /api/skill after install
        resp_after = client.get("/api/skill")
        assert resp_after.status_code == 200
        assert resp_after.json()["installed"] is True


def test_saved_traces_router(test_workspace_round60: tuple[IndexerDB, Path]):
    """Verify saved-traces endpoints mounted from modular router."""
    db, root = test_workspace_round60
    with (
        patch("vibetrace.server_api.db", db),
        patch("vibetrace.server_api.project_root", root),
    ):
        client = TestClient(app)

        # 1. List saved traces (empty initially)
        resp_list = client.get("/api/saved-traces")
        assert resp_list.status_code == 200
        assert isinstance(resp_list.json(), list)

        # 2. Create a saved trace
        payload = {
            "name": "TestTrace1",
            "description": "Verification trace",
            "from_symbol": "caller_fn",
            "to_symbol": "target_fn",
            "max_depth": 5,
        }
        resp_create = client.post("/api/saved-traces", json=payload)
        assert resp_create.status_code == 200
        created = resp_create.json()
        assert created["name"] == "TestTrace1"
        trace_id = created["id"]

        # 3. Retrieve saved trace by id
        resp_get = client.get(f"/api/saved-traces/{trace_id}")
        assert resp_get.status_code == 200
        assert resp_get.json()["id"] == trace_id

        # 4. Run saved trace
        resp_run = client.get("/api/saved-traces/TestTrace1/run")
        assert resp_run.status_code == 200
        run_data = resp_run.json()
        assert "saved_trace" in run_data
        assert "trace_result" in run_data

        # 5. Delete trace by id
        resp_del = client.delete(f"/api/saved-traces/{trace_id}")
        assert resp_del.status_code == 200
        assert resp_del.json()["status"] == "deleted"

        # 6. Verify deleted
        resp_list_after = client.get("/api/saved-traces")
        assert len(resp_list_after.json()) == 0


def setup_mock_project(tmp_path: Path):
    """Create a mock multi-repo project workspace with two member repos."""
    project_root = tmp_path / "multi_suite"
    project_root.mkdir()

    repo_front = project_root / "frontend"
    repo_back = project_root / "backend"
    repo_front.mkdir()
    repo_back.mkdir()

    # Create package markers for auto-discovery
    (repo_front / "package.json").write_text('{"name": "frontend"}', encoding="utf-8")
    (repo_back / "pyproject.toml").write_text('[project]\nname = "backend"', encoding="utf-8")

    # Setup frontend database
    front_db_dir = repo_front / ".vibetrace"
    front_db_dir.mkdir()
    front_db = IndexerDB(str(front_db_dir / "vibetrace.db"))

    front_node = Node(
        id="fe_checkout_btn",
        name="onCheckoutClick",
        qualified_name="frontend.components.onCheckoutClick",
        kind="function",
        file_path="src/components/Checkout.tsx",
        start_line=10,
        end_line=30,
    )
    front_db.upsert_node(front_node)

    # Setup backend database
    back_db_dir = repo_back / ".vibetrace"
    back_db_dir.mkdir()
    back_db = IndexerDB(str(back_db_dir / "vibetrace.db"))

    back_node = Node(
        id="be_process_payment",
        name="process_payment",
        qualified_name="backend.services.process_payment",
        kind="function",
        file_path="src/services/payment.py",
        start_line=25,
        end_line=50,
    )
    back_caller = Node(
        id="be_api_route",
        name="post_checkout_order",
        qualified_name="backend.routes.post_checkout_order",
        kind="function",
        file_path="src/routes/order.py",
        start_line=15,
        end_line=40,
    )
    back_db.upsert_node(back_node)
    back_db.upsert_node(back_caller)

    # Add caller in backend calling process_payment
    back_db.upsert_edge(
        Edge(
            from_node_id="be_api_route",
            to_node_id="be_process_payment",
            relation_type="calls",
            call_line=20,
        )
    )

    # Add cross-repo caller in frontend calling process_payment
    front_db.upsert_edge(
        Edge(
            from_node_id="fe_checkout_btn",
            to_node_id="process_payment",
            relation_type="calls",
            call_line=18,
        )
    )

    return project_root, repo_front, repo_back


def test_manifest_lifecycle_and_discovery(tmp_path: Path):
    """Verify manifest creation, serialization, finding root, and member auto-discovery."""
    proj_root, repo_front, _ = setup_mock_project(tmp_path)

    # 1. Auto-discover candidate members
    candidates = auto_discover_members(proj_root)
    assert len(candidates) == 2
    names = {c.name for c in candidates}
    assert "frontend" in names
    assert "backend" in names

    # 2. Save manifest
    manifest = ProjectManifest(
        name="E-Commerce Suite",
        description="Federated microservices",
        settings=ProjectSettings(storage_backend="sqlite"),
        members=candidates,
    )
    save_project_manifest(proj_root, manifest)

    # 3. Find project root from a child directory
    found_root = find_project_root(repo_front / "src")
    assert found_root == proj_root

    # 4. Load manifest and verify contents
    loaded = load_project_manifest(proj_root)
    assert loaded.name == "E-Commerce Suite"
    assert len(loaded.members) == 2
    assert loaded.settings.storage_backend == "sqlite"


def test_project_workspace_member_management(tmp_path: Path):
    """Verify ProjectWorkspace init, member adding, removing, and status."""
    proj_root, _, _ = setup_mock_project(tmp_path)

    # Initialize workspace
    ws = ProjectWorkspace.init(proj_root, name="TestSuite", auto_discover=True)
    assert len(ws.manifest.members) == 2

    # Check member statuses
    statuses = ws.list_members()
    assert len(statuses) == 2
    stat_map = {s.name: s for s in statuses}
    assert stat_map["frontend"].is_indexed is True
    assert stat_map["frontend"].node_count >= 1
    assert stat_map["backend"].is_indexed is True
    assert stat_map["backend"].node_count >= 2

    # Add a third member
    new_dir = proj_root / "shared"
    new_dir.mkdir()
    member = ws.add_member("shared", "shared", description="Shared schemas")
    assert member.name == "shared"
    assert len(ws.manifest.members) == 3

    # Remove the member
    removed = ws.remove_member("shared")
    assert removed is True
    assert len(ws.manifest.members) == 2
    ws.close()


def test_federated_sqlite_search(tmp_path: Path):
    """Verify federated search across all repositories and repository-scoped search."""
    proj_root, _, _ = setup_mock_project(tmp_path)
    ws = ProjectWorkspace.init(proj_root, name="SearchSuite", auto_discover=True)

    # 1. Search across all repos (unscoped)
    all_results = ws.search("payment")
    assert len(all_results) >= 1
    assert all_results[0].repo == "backend"
    assert all_results[0].name == "process_payment"

    # 2. Search scoped to frontend
    fe_results = ws.search("Checkout", repo="frontend")
    assert len(fe_results) >= 1
    assert fe_results[0].repo == "frontend"
    assert fe_results[0].name == "onCheckoutClick"

    # 3. Search scoped to backend for a frontend-only symbol (returns empty)
    no_results = ws.search("onCheckoutClick", repo="backend")
    assert len(no_results) == 0
    ws.close()


def test_cross_repo_caller_guard(tmp_path: Path):
    """Verify cross-repository blast-radius detection across member repositories."""
    proj_root, _, _ = setup_mock_project(tmp_path)
    ws = ProjectWorkspace.init(proj_root, name="GuardSuite", auto_discover=True)

    # Inspect process_payment: defined in backend, called in both backend and frontend
    report = ws.guard("process_payment")
    assert report.target_symbol == "process_payment"
    assert report.target_repo == "backend"
    assert report.total_callers == 2

    # Check callers by repository
    assert "backend" in report.callers_by_repo
    assert "frontend" in report.callers_by_repo

    be_callers = report.callers_by_repo["backend"]
    assert any(c["caller_symbol"] == "post_checkout_order" for c in be_callers)

    fe_callers = report.callers_by_repo["frontend"]
    assert any(c["caller_symbol"] == "onCheckoutClick" for c in fe_callers)

    assert "Cross-repo blast radius: 2 caller(s) across 2 repository(ies)" in report.safety_advisory
    ws.close()


def test_project_cli_commands(tmp_path: Path, capsys):
    """Verify vibetrace project CLI subcommands with JSON output."""
    proj_root, _, _ = setup_mock_project(tmp_path)

    # 1. project init
    code_init = cli_main(
        ["--root", str(proj_root), "--json", "project", "init", "--name", "CliSuite"]
    )
    assert code_init == 0
    captured_init = capsys.readouterr()
    assert '"name": "CliSuite"' in captured_init.out

    # 2. project list
    code_list = cli_main(["--root", str(proj_root), "--json", "project", "list"])
    assert code_list == 0
    captured_list = capsys.readouterr()
    assert '"name": "frontend"' in captured_list.out
    assert '"name": "backend"' in captured_list.out

    # 3. project search
    code_search = cli_main(["--root", str(proj_root), "--json", "project", "search", "payment"])
    assert code_search == 0
    captured_search = capsys.readouterr()
    assert '"repo": "backend"' in captured_search.out
    assert '"name": "process_payment"' in captured_search.out

    # 4. project guard
    code_guard = cli_main(
        ["--root", str(proj_root), "--json", "project", "guard", "process_payment"]
    )
    assert code_guard == 0
    captured_guard = capsys.readouterr()
    assert '"total_callers": 2' in captured_guard.out
    assert '"target_repo": "backend"' in captured_guard.out


def setup_linked_workspace(tmp_path: Path):
    """Create a mock workspace with client and server route contracts."""
    project_root = tmp_path / "linked_suite"
    project_root.mkdir()

    fe_dir = project_root / "frontend"
    be_dir = project_root / "backend"
    fe_dir.mkdir()
    be_dir.mkdir()

    (fe_dir / "package.json").write_text('{"name": "frontend"}', encoding="utf-8")
    (be_dir / "pyproject.toml").write_text('[project]\nname = "backend"', encoding="utf-8")

    # Frontend DB
    fe_db_dir = fe_dir / ".vibetrace"
    fe_db_dir.mkdir()
    fe_db = IndexerDB(str(fe_db_dir / "vibetrace.db"))

    fe_fn = Node(
        id="fe_pay_btn",
        name="onPaymentSubmit",
        qualified_name="frontend.pay.onPaymentSubmit",
        kind="function",
        file_path="src/pay.ts",
        start_line=12,
        end_line=28,
    )
    fe_endpoint = Node(
        id="fe_ep_checkout",
        name="POST /api/v1/checkout",
        qualified_name="endpoint:POST /api/v1/checkout",
        kind="endpoint",
        file_path="src/pay.ts",
        start_line=18,
        end_line=18,
    )
    fe_db.upsert_node(fe_fn)
    fe_db.upsert_node(fe_endpoint)
    fe_db.upsert_edge(
        Edge(
            from_node_id="fe_pay_btn",
            to_node_id="fe_ep_checkout",
            relation_type="calls",
            call_line=18,
        )
    )

    # Backend DB
    be_db_dir = be_dir / ".vibetrace"
    be_db_dir.mkdir()
    be_db = IndexerDB(str(be_db_dir / "vibetrace.db"))

    be_endpoint = Node(
        id="be_ep_checkout",
        name="POST /api/v1/checkout",
        qualified_name="endpoint:POST /api/v1/checkout",
        kind="endpoint",
        file_path="src/routes.py",
        start_line=10,
        end_line=10,
    )
    be_handler = Node(
        id="be_handler_checkout",
        name="handle_checkout",
        qualified_name="backend.routes.handle_checkout",
        kind="function",
        file_path="src/routes.py",
        start_line=11,
        end_line=45,
    )
    be_db.upsert_node(be_endpoint)
    be_db.upsert_node(be_handler)
    be_db.upsert_edge(
        Edge(
            from_node_id="be_ep_checkout",
            to_node_id="be_handler_checkout",
            relation_type="routes",
            call_line=10,
        )
    )

    return project_root, fe_dir, be_dir


def test_cross_repo_contract_discovery(tmp_path: Path):
    """Verify HTTP client calls match server route handlers across repos."""
    project_root, _, _ = setup_linked_workspace(tmp_path)
    ws = ProjectWorkspace.init(project_root, name="LinkedProject", auto_discover=True)

    links = ws.discover_cross_repo_links()
    assert len(links) == 1
    link = links[0]
    assert link.source_repo == "frontend"
    assert link.source_symbol == "onPaymentSubmit"
    assert link.target_repo == "backend"
    assert link.target_symbol == "handle_checkout"
    assert link.contract_key == "POST /api/v1/checkout"
    assert link.protocol == "http"
    ws.close()


def test_cross_repo_guard_with_contract_links(tmp_path: Path):
    """Verify modifying a backend route handler detects client-side callers in frontend."""
    project_root, _, _ = setup_linked_workspace(tmp_path)
    ws = ProjectWorkspace.init(project_root, name="LinkedGuard", auto_discover=True)

    report = ws.guard("handle_checkout")
    assert report.target_symbol == "handle_checkout"
    assert report.target_repo == "backend"
    assert report.total_callers >= 1

    assert "frontend" in report.callers_by_repo
    fe_callers = report.callers_by_repo["frontend"]
    assert any("onPaymentSubmit" in c["caller_symbol"] for c in fe_callers)
    ws.close()


def test_postgres_storage_adapter():
    """Verify PostgreSQL storage adapter DDL generation and parameterized query behavior."""
    assert "CREATE TABLE IF NOT EXISTS vibetrace_projects" in POSTGRES_SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS vibetrace_nodes" in POSTGRES_SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS vibetrace_edges" in POSTGRES_SCHEMA_SQL

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    mock_cursor.fetchone.return_value = ("backend", "node_123", "src/pay.py")
    mock_cursor.fetchall.return_value = [
        ("frontend", 15, "onPayClick", "src/app.tsx", "function"),
    ]

    backend = PostgresStorageBackend(
        postgres_url="postgres://user:pass@localhost:5432/vibe",
        project_id="test_proj",
        members=[RepoMember(name="frontend", path="frontend")],
        connection=mock_conn,
    )

    # 1. Test init schema
    backend.init_schema()
    mock_cursor.execute.assert_called_with(POSTGRES_SCHEMA_SQL)
    mock_conn.commit.assert_called_once()

    # 2. Test guard query
    report = backend.get_cross_repo_guard("process_payment")
    assert report.target_symbol == "process_payment"
    assert report.total_callers == 1
    assert "frontend" in report.callers_by_repo

    # 3. Test search query
    mock_cursor.fetchall.return_value = [
        (
            "backend",
            "node_1",
            "process_payment",
            "backend.pay",
            "function",
            "src/pay.py",
            1,
            10,
            None,
        )
    ]
    results = backend.search("process")
    assert len(results) == 1
    assert results[0].name == "process_payment"


def test_cli_project_links(tmp_path: Path, capsys):
    """Verify `vibetrace project links --json` CLI command."""
    project_root, _, _ = setup_linked_workspace(tmp_path)

    cli_main(["--root", str(project_root), "--json", "project", "init", "--name", "LinksSuite"])
    capsys.readouterr()

    code = cli_main(["--root", str(project_root), "--json", "project", "links"])
    assert code == 0
    captured = capsys.readouterr()
    assert '"source_repo": "frontend"' in captured.out
    assert '"target_repo": "backend"' in captured.out
    assert '"contract_key": "POST /api/v1/checkout"' in captured.out


def setup_federated_environment(tmp_path: Path):
    """Create a mock workspace with client and server route contracts."""
    project_root = tmp_path / "api_suite"
    project_root.mkdir()

    fe_dir = project_root / "frontend"
    be_dir = project_root / "backend"
    fe_dir.mkdir()
    be_dir.mkdir()

    (fe_dir / "package.json").write_text('{"name": "frontend"}', encoding="utf-8")
    (be_dir / "pyproject.toml").write_text('[project]\nname = "backend"', encoding="utf-8")

    # Frontend DB
    fe_db_dir = fe_dir / ".vibetrace"
    fe_db_dir.mkdir()
    fe_db = IndexerDB(str(fe_db_dir / "vibetrace.db"))

    fe_fn = Node(
        id="fe_pay_btn",
        name="onPaymentSubmit",
        qualified_name="frontend.pay.onPaymentSubmit",
        kind="function",
        file_path="src/pay.ts",
        start_line=12,
        end_line=28,
    )
    fe_endpoint = Node(
        id="fe_ep_checkout",
        name="POST /api/v1/checkout",
        qualified_name="endpoint:POST /api/v1/checkout",
        kind="endpoint",
        file_path="src/pay.ts",
        start_line=18,
        end_line=18,
    )
    fe_db.upsert_node(fe_fn)
    fe_db.upsert_node(fe_endpoint)
    fe_db.upsert_edge(
        Edge(
            from_node_id="fe_pay_btn",
            to_node_id="fe_ep_checkout",
            relation_type="calls",
            call_line=18,
        )
    )

    # Backend DB
    be_db_dir = be_dir / ".vibetrace"
    be_db_dir.mkdir()
    be_db = IndexerDB(str(be_db_dir / "vibetrace.db"))

    be_endpoint = Node(
        id="be_ep_checkout",
        name="POST /api/v1/checkout",
        qualified_name="endpoint:POST /api/v1/checkout",
        kind="endpoint",
        file_path="src/routes.py",
        start_line=10,
        end_line=10,
    )
    be_handler = Node(
        id="be_handler_checkout",
        name="handle_checkout",
        qualified_name="backend.routes.handle_checkout",
        kind="function",
        file_path="src/routes.py",
        start_line=11,
        end_line=45,
    )
    be_db.upsert_node(be_endpoint)
    be_db.upsert_node(be_handler)
    be_db.upsert_edge(
        Edge(
            from_node_id="be_ep_checkout",
            to_node_id="be_handler_checkout",
            relation_type="routes",
            call_line=10,
        )
    )

    ProjectWorkspace.init(project_root, name="ApiSuite", auto_discover=True)
    return project_root


def test_rest_api_project_endpoints(tmp_path: Path):
    """Verify REST API /api/project endpoints."""
    project_root = setup_federated_environment(tmp_path)

    with patch.dict("os.environ", {"VIBETRACE_PROJECT_ROOT": str(project_root)}):
        client = TestClient(app)

        # 1. GET /api/project (status)
        resp_status = client.get("/api/project")
        assert resp_status.status_code == 200
        data_status = resp_status.json()
        assert "manifest" in data_status
        assert data_status["manifest"]["name"] == "ApiSuite"
        assert len(data_status["members"]) == 2

        # 2. GET /api/project/search
        resp_search = client.get("/api/project/search?query=checkout")
        assert resp_search.status_code == 200
        data_search = resp_search.json()
        assert len(data_search) >= 1
        assert any(r["name"] == "handle_checkout" for r in data_search)

        # 3. GET /api/project/guard
        resp_guard = client.get("/api/project/guard?symbol=handle_checkout")
        assert resp_guard.status_code == 200
        data_guard = resp_guard.json()
        assert data_guard["target_symbol"] == "handle_checkout"
        assert data_guard["total_callers"] >= 1
        assert "frontend" in data_guard["callers_by_repo"]

        # 4. GET /api/project/links
        resp_links = client.get("/api/project/links")
        assert resp_links.status_code == 200
        data_links = resp_links.json()
        assert len(data_links) >= 1
        assert data_links[0]["contract_key"] == "POST /api/v1/checkout"


def test_mcp_project_tools(tmp_path: Path):
    """Verify FastMCP project federation tools."""
    project_root = setup_federated_environment(tmp_path)

    # 1. Status tool
    res_status = vibetrace_project_status(
        ProjectStatusInput(scope_path=str(project_root), response_format=ResponseFormat.MARKDOWN)
    )
    assert "Federated Project Workspace: `ApiSuite`" in res_status
    assert "frontend" in res_status
    assert "backend" in res_status

    # 2. Search tool
    res_search = vibetrace_project_search(
        ProjectSearchInput(
            query="checkout",
            scope_path=str(project_root),
            response_format=ResponseFormat.MARKDOWN,
        )
    )
    assert "handle_checkout" in res_search
    assert "[backend]" in res_search

    # 3. Guard tool
    res_guard = vibetrace_project_guard(
        ProjectGuardInput(
            symbol="handle_checkout",
            scope_path=str(project_root),
            response_format=ResponseFormat.MARKDOWN,
        )
    )
    assert "Cross-Repository Blast Radius Guard: `handle_checkout`" in res_guard
    assert "[frontend]" in res_guard
    assert "onPaymentSubmit" in res_guard

    # 4. Links tool
    res_links = vibetrace_project_links(
        ProjectLinksInput(scope_path=str(project_root), response_format=ResponseFormat.JSON)
    )
    assert "POST /api/v1/checkout" in res_links
    assert "frontend" in res_links


def test_vscode_extension_manifest_contract():
    """Verify extensions/vscode/package.json registers all necessary commands."""
    pkg_path = Path("extensions/vscode/package.json")
    assert pkg_path.is_file(), "extensions/vscode/package.json must exist"

    with open(pkg_path, encoding="utf-8") as f:
        pkg = json.load(f)

    assert pkg["name"] == "vibetrace-vscode"
    assert "engines" in pkg
    assert "vscode" in pkg["engines"]

    # Verify commands
    commands = {cmd["command"] for cmd in pkg.get("contributes", {}).get("commands", [])}
    expected_commands = {
        "vibetrace.inspectCallers",
        "vibetrace.copySlice",
        "vibetrace.bundleContext",
        "vibetrace.openMapRoom",
        "vibetrace.refreshProject",
        "vibetrace.reindex",
    }
    assert expected_commands.issubset(commands), f"Missing commands: {expected_commands - commands}"

    # Verify views
    views = pkg.get("contributes", {}).get("views", {}).get("vibetrace-explorer", [])
    view_ids = {v["id"] for v in views}
    assert "vibetrace.mapRoomView" in view_ids

    # Verify configuration schema
    props = pkg.get("contributes", {}).get("configuration", {}).get("properties", {})
    assert "vibetrace.daemonUrl" in props
    assert "vibetrace.wsUrl" in props
    assert "vibetrace.enableCodeLens" in props
    assert "vibetrace.enableDiagnostics" in props


def test_vscode_extension_bundle_artifact():
    """Verify extensions/vscode/dist/extension.js exports activate/deactivate if built."""
    dist_path = Path("extensions/vscode/dist/extension.js")
    if not dist_path.is_file():
        pytest.skip("VSCode extension not compiled in environment (dist/extension.js absent)")
    content = dist_path.read_text(encoding="utf-8")
    assert len(content) > 1000, "dist/extension.js bundle must not be empty"
    assert "activate" in content, "Bundle must contain activate function"
    assert "deactivate" in content, "Bundle must contain deactivate function"


def test_vscode_client_api_endpoint_parity():
    """Verify that every endpoint invoked by VibeTraceApiClient is reachable."""
    client = TestClient(app)

    # 1. /api/graph (daemon health & initial topology check)
    resp = client.get("/api/graph")
    assert resp.status_code == 200

    # 2. /api/project (federation status)
    resp = client.get("/api/project")
    # 200 if manifest found or 404 if no project file in current test root
    assert resp.status_code in (200, 404)

    # 3. /api/project/links
    resp = client.get("/api/project/links")
    assert resp.status_code in (200, 404)

    # 4. /api/vibe/guard (validation error if symbol missing)
    resp = client.get("/api/vibe/guard")
    assert resp.status_code == 422
