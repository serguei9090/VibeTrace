"""Tests for Documentation RAG, Multi-Catalog Architecture, CLI, REST, and MCP surfaces."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from vibetrace.cli import main
from vibetrace.docs.chunker import chunk_markdown, chunk_text
from vibetrace.docs.db import DocDB
from vibetrace.docs.service import DocCatalogManager, DocIndexService, DocSearchService
from vibetrace.mcp.schemas import DocCatalogsInput, DocSearchInput, ResponseFormat
from vibetrace.mcp.server import vibetrace_list_doc_catalogs, vibetrace_search_docs
from vibetrace.server_api import app


@pytest.fixture
def doc_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Setup an isolated workspace with project config and global config."""
    proj_root = tmp_path / "project"
    proj_root.mkdir()
    global_dir = tmp_path / "global_user" / ".vibetrace"
    global_dir.mkdir(parents=True)

    # Set global sandbox directory
    monkeypatch.setenv("VIBETRACE_GLOBAL_ROOT", str(global_dir))

    # Create dummy documentation files
    docs_dir = proj_root / "docs"
    docs_dir.mkdir()
    (docs_dir / "architecture.md").write_text(
        "# System Architecture\n\nOverview of the platform.\n\n"
        "## Database Storage\n\nSQLite with WAL mode and BEGIN IMMEDIATE locking.\n\n"
        "### Invariants\n\nTransactions must always be atomic.\n",
        encoding="utf-8",
    )
    (docs_dir / "notes.txt").write_text(
        "Line 1: Project setup guide.\nLine 2: Install dependencies.\nLine 3: Run pytest.\n",
        encoding="utf-8",
    )

    # Create dummy PDF
    pdf_path = docs_dir / "manual.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with open(pdf_path, "wb") as f:
        writer.write(f)

    # Global shared docs
    global_docs_dir = tmp_path / "global_shared_docs"
    global_docs_dir.mkdir()
    (global_docs_dir / "stack_rules.md").write_text(
        "# Senior Dev Guidelines\n\nRule 1: Parsimony ladder.\nRule 2: Never break tests.\n",
        encoding="utf-8",
    )

    return {
        "proj_root": proj_root,
        "docs_dir": docs_dir,
        "global_dir": global_dir,
        "global_docs_dir": global_docs_dir,
        "pdf_path": pdf_path,
    }


def test_chunker_markdown_and_text(doc_workspace):
    """Verify heading-aware section chunking preserves hierarchy and lines."""
    docs_dir = doc_workspace["docs_dir"]
    md_content = (docs_dir / "architecture.md").read_text(encoding="utf-8")
    chunks = chunk_markdown(md_content)

    assert len(chunks) == 3
    assert chunks[0].heading_path == "System Architecture"
    assert chunks[1].heading_path == "System Architecture > Database Storage"
    assert chunks[2].heading_path == "System Architecture > Database Storage > Invariants"
    assert "BEGIN IMMEDIATE" in chunks[1].content

    txt_content = (docs_dir / "notes.txt").read_text(encoding="utf-8")
    txt_chunks = chunk_text(txt_content)
    assert len(txt_chunks) >= 1
    assert "Project setup guide" in txt_chunks[0].content


def test_catalog_management_and_precedence(doc_workspace):
    """Verify adding, listing, and local precedence over global catalogs."""
    proj_root = doc_workspace["proj_root"]
    docs_dir = doc_workspace["docs_dir"]
    global_docs_dir = doc_workspace["global_docs_dir"]

    # 1. Add global catalog
    DocCatalogManager.add_path(
        "master_stack", str(global_docs_dir), global_scope=True, project_root=proj_root
    )

    # 2. Add local catalog
    DocCatalogManager.add_path("core", str(docs_dir), global_scope=False, project_root=proj_root)

    cats = DocCatalogManager.list_catalogs(project_root=proj_root)
    assert len(cats) == 2
    cat_names = {c.name: c.scope for c in cats}
    assert cat_names["core"] == "local"
    assert cat_names["master_stack"] == "global"

    # 3. Local precedence test: override master_stack locally
    DocCatalogManager.add_path(
        "master_stack", str(docs_dir), global_scope=False, project_root=proj_root
    )
    cats_after = DocCatalogManager.list_catalogs(project_root=proj_root)
    # Total count remains 2, but master_stack is now local
    assert len(cats_after) == 2
    cat_after_names = {c.name: c.scope for c in cats_after}
    assert cat_after_names["master_stack"] == "local"


def test_indexing_and_hybrid_search(doc_workspace):
    """Verify document chunking, FTS5 storage, and hybrid RRF search."""
    proj_root = doc_workspace["proj_root"]
    docs_dir = doc_workspace["docs_dir"]
    global_docs_dir = doc_workspace["global_docs_dir"]

    DocCatalogManager.add_path("core", str(docs_dir), global_scope=False, project_root=proj_root)
    DocCatalogManager.add_path(
        "master_stack", str(global_docs_dir), global_scope=True, project_root=proj_root
    )

    # Index local
    local_db = doc_workspace["proj_root"] / ".vibetrace" / "vibetrace.db"
    res_local = DocIndexService.index_catalog("core", [str(docs_dir)], DocDB(local_db), proj_root)
    assert res_local["files"] >= 1
    assert res_local["chunks"] >= 3

    # Index global
    global_db = doc_workspace["global_dir"] / "docs.db"
    res_global = DocIndexService.index_catalog(
        "master_stack", [str(global_docs_dir)], DocDB(global_db), Path.home()
    )
    assert res_global["files"] >= 1

    # Search local by default (scope="local")
    items = DocSearchService.search(
        query="BEGIN IMMEDIATE SQLite",
        project_root=proj_root,
        limit=5,
    )
    assert len(items) >= 1
    top = items[0]
    assert "Database Storage" in top.heading_path
    assert top.catalog == "core"
    assert top.scope == "local"

    # Default scope="local" isolates search: global "Parsimony ladder" is NOT returned
    local_only_items = DocSearchService.search(
        query="Parsimony ladder",
        project_root=proj_root,
        limit=5,
    )
    assert len(local_only_items) == 0

    # Search global explicitly (scope="global")
    global_items = DocSearchService.search(
        query="Parsimony ladder",
        project_root=proj_root,
        scope="global",
        limit=5,
    )
    assert len(global_items) >= 1
    g_top = global_items[0]
    assert g_top.catalog == "master_stack"
    assert g_top.scope == "global"


def test_cli_docs_commands(doc_workspace, capsys):
    """Verify CLI commands for docs catalog, index, and search."""
    proj_root = doc_workspace["proj_root"]
    docs_dir = doc_workspace["docs_dir"]

    # 1. catalog add
    code = main(
        ["docs", "catalog", "add", "spec", str(docs_dir), "--root", str(proj_root), "--json"]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert '"status": "added"' in captured.out

    # 2. catalog list
    code = main(["docs", "catalog", "list", "--root", str(proj_root), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    assert '"spec"' in captured.out

    # 3. docs index
    code = main(["docs", "index", "--root", str(proj_root), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    assert '"status": "ok"' in captured.out

    # 4. docs search
    code = main(["docs", "search", "SQLite", "--root", str(proj_root), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert len(res) >= 1
    assert "Database Storage" in res[0]["heading_path"]


def test_rest_api_docs_endpoints(doc_workspace, monkeypatch):
    """Verify REST API endpoints for docs catalogs, indexing, and search."""
    proj_root = doc_workspace["proj_root"]
    docs_dir = doc_workspace["docs_dir"]
    monkeypatch.setenv("VIBETRACE_PROJECT_ROOT", str(proj_root))

    client = TestClient(app)

    # 1. POST /api/docs/catalogs
    res = client.post("/api/docs/catalogs", json={"name": "apidocs", "path": str(docs_dir)})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 2. GET /api/docs/catalogs
    res = client.get("/api/docs/catalogs")
    assert res.status_code == 200
    assert any(c["name"] == "apidocs" for c in res.json())

    # 3. POST /api/docs/index
    res = client.post("/api/docs/index", json={"catalog": "apidocs"})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 4. GET /api/docs/search
    res = client.get("/api/docs/search?q=Architecture")
    assert res.status_code == 200
    assert len(res.json()) >= 1


@pytest.mark.asyncio
async def test_mcp_docs_tools(doc_workspace):
    """Verify MCP tools for docs catalog listing and search."""
    proj_root = doc_workspace["proj_root"]
    docs_dir = doc_workspace["docs_dir"]

    DocCatalogManager.add_path("mcp_cat", str(docs_dir), global_scope=False, project_root=proj_root)
    local_db = proj_root / ".vibetrace" / "vibetrace.db"
    DocIndexService.index_catalog("mcp_cat", [str(docs_dir)], DocDB(local_db), proj_root)

    # 1. List catalogs via MCP
    cat_res = await vibetrace_list_doc_catalogs(
        DocCatalogsInput(scope_path=str(proj_root), response_format=ResponseFormat.JSON)
    )
    cats_data = json.loads(cat_res)
    assert any(c["name"] == "mcp_cat" for c in cats_data)

    # 2. Search docs via MCP (JSON format)
    search_res = await vibetrace_search_docs(
        DocSearchInput(
            query="Architecture",
            scope_path=str(proj_root),
            response_format=ResponseFormat.JSON,
        )
    )
    search_data = json.loads(search_res)
    assert len(search_data) >= 1
    assert "System Architecture" in search_data[0]["heading_path"]

    # 3. Search docs via MCP (Markdown format with tag)
    md_res = await vibetrace_search_docs(
        DocSearchInput(
            query="Architecture",
            scope_path=str(proj_root),
            response_format=ResponseFormat.MARKDOWN,
        )
    )
    assert "[LOCAL:mcp_cat]" in md_res
    assert "**Tag:** `[LOCAL]`" in md_res
