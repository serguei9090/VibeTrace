"""Tests for Audit Round 51: Unified Code + Docs Semantic Cross-Query."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibetrace.cli import main
from vibetrace.docs.db import DocDB
from vibetrace.docs.service import DocIndexService
from vibetrace.indexer.db import IndexerDB
from vibetrace.mcp.schemas import ResponseFormat, SemanticSearchInput
from vibetrace.mcp.server import vibetrace_semantic_search
from vibetrace.server_api import app


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
