"""
Tests for VibeTrace MCP Server.

Tests the refactored MCP tools with Pydantic input models and async functions.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from vibetrace.cli import main as cli_main
from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.mcp.server import (
    CallStackInput,
    CallTreeInput,
    CodeSliceInput,
    ImpactAnalysisInput,
    ResponseFormat,
    SemanticSearchInput,
    StructuralSummaryInput,
    TraceDirection,
    TracePathInput,
    vibetrace_get_call_stack,
    vibetrace_get_call_tree,
    vibetrace_get_code_slice,
    vibetrace_get_structural_summary,
    vibetrace_impact_analysis,
    vibetrace_semantic_search,
    vibetrace_trace_path,
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
    db_file = tmp_path / "test_vibetrace.db"
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
    import vibetrace.mcp.server

    monkeypatch.setattr(
        vibetrace.mcp.server, "_get_context_for_path", lambda path=None: (db, Path(tmp_path))
    )

    return db


# =============================================================================
# Tests for vibetrace_get_structural_summary
# =============================================================================


@pytest.mark.asyncio
async def test_get_structural_summary(mock_db, tmp_path):
    """Test structural summary returns correct info."""
    params = StructuralSummaryInput(file_path=str(tmp_path / "a.py"))
    summary = await vibetrace_get_structural_summary(params)

    # After path normalization, output contains absolute path
    assert "- [f] **func_a** `(x)` (L1-3)" in summary


@pytest.mark.asyncio
async def test_get_structural_summary_json(mock_db, tmp_path):
    """Test structural summary JSON output."""
    params = StructuralSummaryInput(
        file_path=str(tmp_path / "a.py"),
        response_format=ResponseFormat.JSON,
    )
    summary = await vibetrace_get_structural_summary(params)

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
    summary = await vibetrace_get_structural_summary(params)

    assert "not be indexed" in summary


# =============================================================================
# Tests for vibetrace_get_call_stack
# =============================================================================


@pytest.mark.asyncio
async def test_get_call_stack_up(mock_db, tmp_path):
    """Trace callers of func_a (should be func_b)."""
    params = CallStackInput(
        node_name="func_a", direction=TraceDirection.UP, file_path=str(tmp_path / "a.py")
    )
    trace = await vibetrace_get_call_stack(params)

    assert "Trace for `func_a`" in trace
    assert "Callers (Incoming):" in trace
    assert "func_b" in trace


@pytest.mark.asyncio
async def test_get_call_stack_down(mock_db, tmp_path):
    """Trace callees of func_b (should be func_a)."""
    params = CallStackInput(
        node_name="func_b", direction=TraceDirection.DOWN, file_path=str(tmp_path / "b.py")
    )
    trace = await vibetrace_get_call_stack(params)

    assert "Trace for `func_b`" in trace
    assert "Callees (Outgoing):" in trace
    assert "func_a" in trace


@pytest.mark.asyncio
async def test_get_call_stack_not_found(mock_db):
    """Test call stack for non-existent node."""
    params = CallStackInput(node_name="nonexistent_func")
    trace = await vibetrace_get_call_stack(params)

    assert "not found" in trace


@pytest.mark.asyncio
async def test_get_call_stack_both(mock_db, tmp_path):
    """Test call stack with both directions."""
    params = CallStackInput(
        node_name="func_a", direction=TraceDirection.BOTH, file_path=str(tmp_path / "a.py")
    )
    trace = await vibetrace_get_call_stack(params)

    assert "Callers (Incoming):" in trace
    assert "Callees (Outgoing):" in trace


# =============================================================================
# Tests for vibetrace_impact_analysis
# =============================================================================


@pytest.mark.asyncio
async def test_impact_analysis(mock_db, tmp_path):
    """If we change a.py (func_a), b.py (func_b) should be affected."""
    params = ImpactAnalysisInput(file_path=str(tmp_path / "a.py"))
    impact = await vibetrace_impact_analysis(params)

    assert "Impact Analysis" in impact
    assert "func_a" in impact
    assert "Level 1: Direct Impact" in impact
    assert "func_b" in impact


@pytest.mark.asyncio
async def test_impact_analysis_no_impact(mock_db, tmp_path):
    """If we change b.py, nothing depends on it."""
    params = ImpactAnalysisInput(file_path=str(tmp_path / "b.py"))
    impact = await vibetrace_impact_analysis(params)

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
    """Test vibetrace_semantic_search via hybrid RRF."""
    params = SemanticSearchInput(query="func_a", limit=5)
    res = await vibetrace_semantic_search(params)
    assert "func_a" in res


@pytest.mark.asyncio
async def test_mcp_trace_path(mock_db):
    """Test vibetrace_trace_path finds hop from func_b to func_a."""
    params = TracePathInput(from_symbol="func_b", to_symbol="func_a", max_depth=4)
    res = await vibetrace_trace_path(params)
    assert "func_b" in res
    assert "func_a" in res
    assert ":L3" in res


@pytest.mark.asyncio
async def test_mcp_get_code_slice(mock_db):
    """Test vibetrace_get_code_slice returns source code and call references."""
    params = CodeSliceInput(symbol_name="func_b", include_callees=True)
    res = await vibetrace_get_code_slice(params)
    assert "def func_b():" in res
    assert "func_a" in res


@pytest.mark.asyncio
async def test_mcp_code_slice_and_call_tree_scope_priority(monkeypatch, tmp_path):
    """Verify vibetrace_get_code_slice and vibetrace_get_call_tree prioritize scope_path."""
    captured_paths = []

    def mock_get_context(path=None):
        captured_paths.append(path)
        db = IndexerDB(str(tmp_path / "test.db"))
        return db, tmp_path

    import vibetrace.mcp.server

    monkeypatch.setattr(vibetrace.mcp.server, "_get_context_for_path", mock_get_context)

    params_slice = CodeSliceInput(
        symbol_name="dummy", file_path="relative/file.py", scope_path="/custom/project/root"
    )
    await vibetrace_get_code_slice(params_slice)
    assert captured_paths[-1] == "/custom/project/root"

    params_tree = CallTreeInput(
        symbol_name="dummy", file_path="relative/file.py", scope_path="/custom/project/root"
    )
    await vibetrace_get_call_tree(params_tree)
    assert captured_paths[-1] == "/custom/project/root"


@pytest.mark.asyncio
async def test_mcp_reindex_project_params(monkeypatch, tmp_path):
    """Verify vibetrace_reindex_project forwards force and use_lsp to reindex_all."""
    from vibetrace.mcp.schemas import ReindexInput
    from vibetrace.mcp.server import vibetrace_reindex_project

    calls = []

    def mock_reindex(db, target, verbose=True, force=False, project_root=None, use_lsp=False):
        calls.append({"target": target, "force": force, "use_lsp": use_lsp})

    def mock_get_context(path=None, auto_sync=False):
        db = IndexerDB(str(tmp_path / "test_reindex.db"))
        return db, tmp_path

    import vibetrace.mcp.server

    monkeypatch.setattr(vibetrace.mcp.server, "_get_context_for_path", mock_get_context)
    monkeypatch.setattr(vibetrace.mcp.server, "reindex_all", mock_reindex)

    params = ReindexInput(path=str(tmp_path), force=True, use_lsp=True)
    res = await vibetrace_reindex_project(params)
    assert "Successfully reindexed" in res
    assert len(calls) == 1
    assert calls[0]["force"] is True
    assert calls[0]["use_lsp"] is True


# =============================================================================
# Tests for Unified CLI (vibetrace.cli)
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


def test_polyglot_code_slice_markdown_fences():
    """format_code_slice_md must output correct markdown fence according to language extension."""
    from vibetrace.mcp.formatters import format_code_slice_md
    from vibetrace.services.graph_service import CodeSliceResult

    py_slice = CodeSliceResult(
        found=True,
        name="hello",
        qualified_name="hello",
        file_path="src/main.py",
        source_code="def hello(): pass",
        start_line=1,
        end_line=1,
    )
    assert "```python" in format_code_slice_md(py_slice)

    ts_slice = CodeSliceResult(
        found=True,
        name="handleClick",
        qualified_name="handleClick",
        file_path="src/web/Button.tsx",
        source_code="export const Button = () => <button />;",
        start_line=1,
        end_line=1,
    )
    assert "```tsx" in format_code_slice_md(ts_slice)

    go_slice = CodeSliceResult(
        found=True,
        name="ServeHTTP",
        qualified_name="Server.ServeHTTP",
        file_path="pkg/server.go",
        source_code="func (s *Server) ServeHTTP() {}",
        start_line=1,
        end_line=1,
    )
    assert "```go" in format_code_slice_md(go_slice)

    rs_slice = CodeSliceResult(
        found=True,
        name="process",
        qualified_name="process",
        file_path="src/lib.rs",
        source_code="pub fn process() {}",
        start_line=1,
        end_line=1,
    )
    assert "```rust" in format_code_slice_md(rs_slice)
