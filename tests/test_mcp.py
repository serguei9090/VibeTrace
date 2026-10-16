"""
Tests for VibeGraph MCP Server.

Tests the refactored MCP tools with Pydantic input models and async functions.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from vibegraph.cli import main as cli_main
from vibegraph.indexer.db import Edge, IndexerDB, Node
from vibegraph.mcp.server import (
    CallStackInput,
    CodeSliceInput,
    ImpactAnalysisInput,
    ResponseFormat,
    SemanticSearchInput,
    StructuralSummaryInput,
    TraceDirection,
    TracePathInput,
    vibegraph_get_call_stack,
    vibegraph_get_code_slice,
    vibegraph_get_structural_summary,
    vibegraph_impact_analysis,
    vibegraph_semantic_search,
    vibegraph_trace_path,
)


# Mock Database Setup
@pytest.fixture
def mock_db(monkeypatch, tmp_path):
    """Overrides the _get_context_for_path function in server.py to use a temp file DB."""

    # Create dummy source files for slicing
    (tmp_path / "a.py").write_text(
        'def func_a(x):\n    """Doc A"""\n    return x + 1\n\n# end\n',
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        'def func_b():\n    """Doc B"""\n    return func_a(42)\n\n# end\n',
        encoding="utf-8",
    )

    # Create a temp DB file
    db_file = tmp_path / "test_vibegraph.db"
    db = IndexerDB(str(db_file))

    # Use relative paths for consistency with the new normalization
    a_py_path = "a.py"
    b_py_path = "b.py"

    # Add Nodes
    # File A: Defines func_a
    db.upsert_node(
        Node(
            id="a",
            name="func_a",
            qualified_name="func_a",
            kind="function",
            file_path=a_py_path,
            start_line=1,
            end_line=3,
            signature="(x)",
            docstring="Doc A",
        )
    )
    # File B: Defines func_b, calls func_a
    db.upsert_node(
        Node(
            id="b",
            name="func_b",
            qualified_name="func_b",
            kind="function",
            file_path=b_py_path,
            start_line=1,
            end_line=3,
            signature="()",
            docstring="Doc B",
        )
    )

    # Add Edges
    # func_b calls func_a
    db.upsert_edge(Edge(from_node_id="b", to_node_id="a", relation_type="calls", call_line=3))

    # Monkeypatch - mocked root is tmp_path
    import vibegraph.mcp.server

    monkeypatch.setattr(
        vibegraph.mcp.server, "_get_context_for_path", lambda path=None: (db, Path(tmp_path))
    )

    return db


# =============================================================================
# Tests for vibegraph_get_structural_summary
# =============================================================================


@pytest.mark.asyncio
async def test_get_structural_summary(mock_db, tmp_path):
    """Test structural summary returns correct info."""
    params = StructuralSummaryInput(file_path=str(tmp_path / "a.py"))
    summary = await vibegraph_get_structural_summary(params)

    # After path normalization, output contains absolute path
    assert "- [f] **func_a** `(x)` (L1-3)" in summary


@pytest.mark.asyncio
async def test_get_structural_summary_json(mock_db, tmp_path):
    """Test structural summary JSON output."""
    params = StructuralSummaryInput(
        file_path=str(tmp_path / "a.py"),
        response_format=ResponseFormat.JSON,
    )
    summary = await vibegraph_get_structural_summary(params)

    # Should be valid JSON with expected fields
    import json

    data = json.loads(summary)
    assert data["total"] == 1
    assert data["nodes"][0]["name"] == "func_a"
    assert data["nodes"][0]["kind"] == "function"


@pytest.mark.asyncio
async def test_get_structural_summary_not_found(mock_db, tmp_path):
    """Test structural summary for non-indexed file."""
    params = StructuralSummaryInput(file_path=str(tmp_path / "nonexistent.py"))
    summary = await vibegraph_get_structural_summary(params)

    assert "not be indexed" in summary


# =============================================================================
# Tests for vibegraph_get_call_stack
# =============================================================================


@pytest.mark.asyncio
async def test_get_call_stack_up(mock_db, tmp_path):
    """Trace callers of func_a (should be func_b)."""
    params = CallStackInput(
        node_name="func_a", direction=TraceDirection.UP, file_path=str(tmp_path / "a.py")
    )
    trace = await vibegraph_get_call_stack(params)

    assert "Trace for `func_a`" in trace
    assert "Callers (Incoming):" in trace
    assert "func_b" in trace


@pytest.mark.asyncio
async def test_get_call_stack_down(mock_db, tmp_path):
    """Trace callees of func_b (should be func_a)."""
    params = CallStackInput(
        node_name="func_b", direction=TraceDirection.DOWN, file_path=str(tmp_path / "b.py")
    )
    trace = await vibegraph_get_call_stack(params)

    assert "Trace for `func_b`" in trace
    assert "Callees (Outgoing):" in trace
    assert "func_a" in trace


@pytest.mark.asyncio
async def test_get_call_stack_not_found(mock_db):
    """Test call stack for non-existent node."""
    params = CallStackInput(node_name="nonexistent_func")
    trace = await vibegraph_get_call_stack(params)

    assert "not found" in trace


@pytest.mark.asyncio
async def test_get_call_stack_both(mock_db, tmp_path):
    """Test call stack with both directions."""
    params = CallStackInput(
        node_name="func_a", direction=TraceDirection.BOTH, file_path=str(tmp_path / "a.py")
    )
    trace = await vibegraph_get_call_stack(params)

    assert "Callers (Incoming):" in trace
    assert "Callees (Outgoing):" in trace


# =============================================================================
# Tests for vibegraph_impact_analysis
# =============================================================================


@pytest.mark.asyncio
async def test_impact_analysis(mock_db, tmp_path):
    """If we change a.py (func_a), b.py (func_b) should be affected."""
    params = ImpactAnalysisInput(file_path=str(tmp_path / "a.py"))
    impact = await vibegraph_impact_analysis(params)

    assert "Impact Analysis" in impact
    assert "func_a" in impact
    assert "Level 1: Direct Impact" in impact
    assert "func_b" in impact


@pytest.mark.asyncio
async def test_impact_analysis_no_impact(mock_db, tmp_path):
    """If we change b.py, nothing depends on it."""
    params = ImpactAnalysisInput(file_path=str(tmp_path / "b.py"))
    impact = await vibegraph_impact_analysis(params)

    assert "Impact Analysis" in impact
    assert (
        "[OK] No external dependencies found" in impact
        or "✅ No external dependencies found" in impact
    )


# =============================================================================
# Tests for New MCP Tools: semantic_search, trace_path, get_code_slice
# =============================================================================


@pytest.mark.asyncio
async def test_mcp_semantic_search(mock_db):
    """Test vibegraph_semantic_search via hybrid RRF."""
    params = SemanticSearchInput(query="func_a", limit=5)
    res = await vibegraph_semantic_search(params)
    assert "func_a" in res


@pytest.mark.asyncio
async def test_mcp_trace_path(mock_db):
    """Test vibegraph_trace_path finds hop from func_b to func_a."""
    params = TracePathInput(from_symbol="func_b", to_symbol="func_a", max_depth=4)
    res = await vibegraph_trace_path(params)
    assert "func_b" in res
    assert "func_a" in res
    assert ":L3" in res


@pytest.mark.asyncio
async def test_mcp_get_code_slice(mock_db):
    """Test vibegraph_get_code_slice returns source code and call references."""
    params = CodeSliceInput(symbol_name="func_b", include_callees=True)
    res = await vibegraph_get_code_slice(params)
    assert "def func_b():" in res
    assert "func_a" in res


# =============================================================================
# Tests for Unified CLI (vibegraph.cli)
# =============================================================================


def test_cli_index_search_trace_slice(tmp_path, capsys):
    """Test unified CLI subcommands against a temporary project directory."""
    proj = tmp_path / "cli_proj"
    proj.mkdir()
    (proj / "mod.py").write_text(
        'def helper(v: int) -> int:\n    """Helper doc"""\n    return v * 2\n\n'
        'def entry() -> int:\n    """Entry doc"""\n    return helper(5)\n',
        encoding="utf-8",
    )

    assert cli_main(["--root", str(proj), "index", str(proj)]) == 0
    out_index = capsys.readouterr().out
    assert "Extracted" in out_index

    assert cli_main(["--root", str(proj), "--json", "search", "helper"]) == 0
    out_search = capsys.readouterr().out
    assert "helper" in out_search

    assert cli_main(["--root", str(proj), "trace", "entry", "helper"]) == 0
    out_trace = capsys.readouterr().out
    assert "entry" in out_trace
    assert "helper" in out_trace

    assert cli_main(["--root", str(proj), "slice", "helper"]) == 0
    out_slice = capsys.readouterr().out
    assert "def helper" in out_slice


# =============================================================================
# Tests for Pydantic Validation
# =============================================================================


def test_structural_summary_input_validation():
    """Test input validation for structural summary."""
    params = StructuralSummaryInput(file_path="test.py", limit=50, offset=10)
    assert params.file_path == "test.py"
    assert params.limit == 50
    assert params.offset == 10

    with pytest.raises(ValidationError):
        StructuralSummaryInput(file_path="test.py", limit=1000)

    with pytest.raises(ValidationError):
        StructuralSummaryInput(file_path="test.py", offset=-1)


def test_call_stack_input_validation():
    """Test input validation for call stack."""
    params = CallStackInput(node_name="my_func", depth=5)
    assert params.node_name == "my_func"
    assert params.depth == 5

    with pytest.raises(ValidationError):
        CallStackInput(node_name="my_func", depth=20)

    with pytest.raises(ValidationError):
        CallStackInput(node_name="")
