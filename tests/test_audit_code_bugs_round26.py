"""Tests verifying fixes for audit findings in Round 26."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibetrace.cli_commands import cmd_trace
from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.indexer.lsp_bridge import LSPClient, resolve_unresolved_edges
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.indexer.watcher import CodeChangeHandler
from vibetrace.services.saved_traces_service import SavedTraceCreate, create_saved_trace


def test_lsp_bridge_decoupled_resolution_and_clean_path(tmp_path: Path):
    """Verify LSP rewires unresolved edges using clean paths without transaction lock."""
    db = IndexerDB(str(tmp_path / "test.db"))
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True)

    caller_file = project_root / "src" / "caller.py"
    caller_file.parent.mkdir(parents=True)
    caller_file.write_text("from helper import do_work\ndo_work()\n", encoding="utf-8")

    target_file = project_root / "src" / "helper.py"
    target_file.write_text("def do_work():\n    return 42\n", encoding="utf-8")

    caller_node = Node(
        id="caller_mod",
        name="caller",
        qualified_name="caller",
        kind="module",
        file_path="src/caller.py",
        start_line=1,
        end_line=2,
    )
    target_node = Node(
        id="helper_do_work",
        name="do_work",
        qualified_name="helper.do_work",
        kind="function",
        file_path="src/helper.py",
        start_line=1,
        end_line=2,
    )
    db.upsert_node(caller_node)
    db.upsert_node(target_node)

    # Edge pointing to missing/unresolved symbol
    unresolved_edge = Edge(
        from_node_id="caller_mod",
        to_node_id="missing_symbol",
        relation_type="calls",
        call_line=2,
    )
    db.upsert_edge(unresolved_edge)

    # Mock LSP client returning un-normalized path with leading ./ and backslash
    mock_lsp = MagicMock(spec=LSPClient)
    mock_lsp.get_definition.return_value = (".\\src\\helper.py", 1)

    rewired = resolve_unresolved_edges(db, project_root, mock_lsp, verbose=False)
    assert rewired == 1

    # Verify edge target was rewired to helper_do_work
    with db._get_conn() as conn:
        q = "SELECT to_node_id FROM edges WHERE from_node_id = 'caller_mod'"
        row = conn.execute(q).fetchone()
        assert row is not None
        assert row["to_node_id"] == "helper_do_work"


def test_watcher_ignores_unsupported_files_and_cleans_timers(tmp_path: Path):
    """Verify watcher does not schedule post-sync for non-code files and stop() cancels timer."""
    db = IndexerDB(str(tmp_path / "test.db"))
    sync_called = []

    handler = CodeChangeHandler(
        db,
        on_change=lambda: sync_called.append(True),
        root_path=str(tmp_path),
        debounce_seconds=0.05,
    )

    # Mock non-code file event
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("hello notes", encoding="utf-8")

    event = MagicMock()
    event.is_directory = False
    event.src_path = str(txt_file)

    handler.on_modified(event)
    assert handler._debounce_timer is None
    assert len(sync_called) == 0

    handler.on_created(event)
    assert handler._debounce_timer is None
    assert "notes.txt" not in handler.resolver.known_files

    handler.on_deleted(event)
    assert handler._debounce_timer is None

    # Test stop() cancels active timer
    handler._debounce_timer = MagicMock()
    handler.stop()
    assert handler._debounce_timer is None


def test_cli_trace_depth_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify CLI --depth explicitly overrides saved trace max_depth."""
    db = IndexerDB(str(tmp_path / "test.db"))
    create_saved_trace(
        db,
        SavedTraceCreate(
            name="auth_flow",
            from_symbol="login",
            to_symbol="verify_jwt",
            max_depth=4,
        ),
    )

    captured_depth = []

    def mock_trace_exec(db_arg, from_s, to_s, max_depth=6, engine="bfs"):
        captured_depth.append(max_depth)
        res = MagicMock()
        res.found = False
        res.model_dump_json.return_value = "{}"
        return res

    monkeypatch.setattr("vibetrace.cli_commands.trace_execution_path", mock_trace_exec)
    monkeypatch.setattr(
        "vibetrace.cli_commands.get_context_for_path", lambda *a, **kw: (db, tmp_path)
    )

    # Case 1: --saved without --depth uses saved.max_depth (4)
    args1 = argparse.Namespace(
        path=str(tmp_path),
        from_symbol=None,
        to_symbol=None,
        saved="auth_flow",
        save_as=None,
        depth=None,
        list_saved=False,
        engine="bfs",
        json=True,
    )
    cmd_trace(args1)
    assert captured_depth[-1] == 4

    # Case 2: --saved with explicit --depth 15 respects user override (15)
    args2 = argparse.Namespace(
        path=str(tmp_path),
        from_symbol=None,
        to_symbol=None,
        saved="auth_flow",
        save_as=None,
        depth=15,
        list_saved=False,
        engine="bfs",
        json=True,
    )
    cmd_trace(args2)
    assert captured_depth[-1] == 15


def test_js_parser_line_ceiling_and_endpoint_extraction(tmp_path: Path):
    """Verify js_parser.py adheres to < 500 LOC and extracts Express endpoint routes."""
    parser_path = Path("src/vibetrace/indexer/parsers/js_parser.py")
    line_count = len(parser_path.read_text(encoding="utf-8").splitlines())
    assert line_count < 500, f"js_parser.py has {line_count} lines; must be < 500"

    code = b"""
    const express = require('express');
    const app = express();

    function getUser(req, res) {
        res.send("user");
    }

    app.get('/api/users', getUser);
    """
    resolver = ModuleResolver(tmp_path)
    parser = JavaScriptParser(resolver=resolver)
    nodes, edges = parser.extract("server.js", code)

    endpoint_nodes = [n for n in nodes if n.kind == "endpoint"]
    assert any(n.name == "GET /api/users" for n in endpoint_nodes)

    route_edges = [e for e in edges if e.relation_type == "routes"]
    assert len(route_edges) >= 1
