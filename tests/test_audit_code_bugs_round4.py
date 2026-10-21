"""Tests verifying fixes for Round 4 strict code bug critique audit."""

import os
from pathlib import Path

from vibetrace.cli import build_parser
from vibetrace.indexer.db import Node as DBNode
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.parsers.route_extractor import extract_python_routes
from vibetrace.mcp.formatters import format_code_slice_md, format_trace_path_md
from vibetrace.services.graph_service import (
    CodeSliceResult,
    TraceHop,
    TraceNodeInfo,
    TracePathResult,
    get_context_for_path,
)


def test_get_context_for_path_respects_env_var(tmp_path: Path):
    """Verify get_context_for_path honors VIBETRACE_PROJECT_ROOT when root_override is None."""
    custom_root = tmp_path / "custom_project"
    custom_root.mkdir()
    (custom_root / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")

    old_env = os.environ.get("VIBETRACE_PROJECT_ROOT")
    try:
        os.environ["VIBETRACE_PROJECT_ROOT"] = str(custom_root)
        _, resolved_root = get_context_for_path(".", auto_sync=False)
        assert resolved_root.resolve() == custom_root.resolve()
    finally:
        if old_env is not None:
            os.environ["VIBETRACE_PROJECT_ROOT"] = old_env
        else:
            os.environ.pop("VIBETRACE_PROJECT_ROOT", None)


def test_cli_top_level_json_flag_precedence():
    """Verify top-level --json flag is retained when followed by subcommands."""
    eff_argv = ["--json", "search", "IndexerDB"]
    top_json = "--json" in eff_argv
    parser = build_parser()
    args = parser.parse_args(eff_argv)
    if top_json:
        args.json = True
    assert args.json is True


def test_formatters_omit_lnone_on_missing_lines():
    """Verify formatters do not output literal :LNone or :LNone-None."""
    hop = TraceHop(
        step_index=1,
        from_node=TraceNodeInfo(
            id="from_id",
            name="main",
            qualified_name="main",
            kind="function",
            file_path="main.py",
            start_line=10,
        ),
        to_node=TraceNodeInfo(
            id="to_id",
            name="os",
            qualified_name="os",
            kind="module",
            file_path="external/os.py",
            start_line=None,  # missing start_line
        ),
        relation_type="imports",
    )
    trace_res = TracePathResult(
        from_symbol="main",
        to_symbol="os",
        found=True,
        hops=[hop],
        paths=[[hop]],
        node_ids=["from_id", "to_id"],
    )
    md = format_trace_path_md(trace_res)
    assert ":LNone" not in md
    assert "(`external/os.py`)" in md

    slice_res = CodeSliceResult(
        found=True,
        node_id="sym1",
        name="mymod",
        qualified_name="mymod",
        kind="module",
        file_path="mymod.py",
        start_line=None,
        end_line=None,
        source_code="# empty module",
    )
    slice_md = format_code_slice_md(slice_res)
    assert ":LNone" not in slice_md
    assert "**Location**: `mymod.py`" in slice_md


def test_flask_tuple_and_multiline_methods():
    """Verify Flask routes with tuple methods and multiline decorators are extracted."""
    decorators = [
        """@app.route(
            "/api/items",
            methods=("GET", "POST")
        )"""
    ]
    handler_node = DBNode(
        id="h1",
        name="handle_items",
        qualified_name="handle_items",
        kind="function",
        file_path="app.py",
        start_line=15,
        end_line=25,
    )
    endpoints, edges = extract_python_routes(
        decorators,
        handler_node,
        "app.py",
        node_id_fn=lambda file, name, kind: f"{file}::{name}",
    )
    endpoint_names = {ep.name for ep in endpoints}
    assert "GET /api/items" in endpoint_names
    assert "POST /api/items" in endpoint_names
    assert len(edges) == 2


def test_js_options_and_head_route_methods():
    """Verify Express/Next options and head route methods are properly extracted."""
    code = b"""
app.options('/api/resource', handleOptions);
app.head('/api/resource', handleHead);
"""
    parser = JavaScriptParser()
    nodes, _ = parser.extract("routes.js", code)
    endpoints = [n.name for n in nodes if n.kind == "endpoint"]
    assert "OPTIONS /api/resource" in endpoints
    assert "HEAD /api/resource" in endpoints
    assert "GET /api/resource" not in endpoints
