"""Audit verification tests for Round 14 code critique fixes.

Covers:
1. CLI --no-sync flag is respected by all command handlers (cmd_search, cmd_trace, etc.).
2. normalize_path() correctly handles relative file paths when invoked from subdirectories.
3. Non-git workspace registers 'non-git' SHA in meta and avoids reindexing storms.
4. Python super().method() calls resolve to Base.method instead of phantom symbol nodes.
"""

from __future__ import annotations

import os
from pathlib import Path

from vibetrace.cli import build_parser
from vibetrace.cli_commands import (
    cmd_call_tree,
    cmd_impact,
    cmd_search,
    cmd_slice,
    cmd_summary,
    cmd_trace,
)
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.parsers.python_parser import PythonParser
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.services.graph_service import (
    _LAST_SYNC_CHECK,
    ensure_index_fresh,
    normalize_path,
)


def test_cli_no_sync_flag_is_respected_by_command_handlers(monkeypatch, tmp_path: Path):
    """Ensure --no-sync flag propagates auto_sync=False to get_context_for_path."""
    sync_calls: list[bool] = []

    def mock_ensure_fresh(db, root, debounce_seconds=2.0, verbose=False):
        sync_calls.append(True)
        return False

    monkeypatch.setattr("vibetrace.services.graph_service.ensure_index_fresh", mock_ensure_fresh)

    dummy_file = tmp_path / "app.py"
    dummy_file.write_text("def run(): pass\n", encoding="utf-8")
    parser = build_parser()

    # 1. With --no-sync, ensure_index_fresh should never be called
    sync_calls.clear()
    args_no_sync = parser.parse_args(
        ["--no-sync", "search", "run", "--path", str(tmp_path), "--json"]
    )
    cmd_search(args_no_sync)
    assert not sync_calls, "cmd_search called ensure_index_fresh despite --no-sync"

    args_trace = parser.parse_args(
        ["--no-sync", "trace", "run", "other", "--path", str(tmp_path), "--json"]
    )
    cmd_trace(args_trace)
    assert not sync_calls, "cmd_trace called ensure_index_fresh despite --no-sync"

    args_slice = parser.parse_args(
        ["--no-sync", "slice", "run", "--file", str(dummy_file), "--json"]
    )
    cmd_slice(args_slice)
    assert not sync_calls, "cmd_slice called ensure_index_fresh despite --no-sync"

    args_tree = parser.parse_args(
        ["--no-sync", "call-tree", "run", "--path", str(tmp_path), "--json"]
    )
    cmd_call_tree(args_tree)
    assert not sync_calls, "cmd_call_tree called ensure_index_fresh despite --no-sync"

    args_impact = parser.parse_args(["--no-sync", "impact", str(dummy_file), "--json"])
    cmd_impact(args_impact)
    assert not sync_calls, "cmd_impact called ensure_index_fresh despite --no-sync"

    args_summary = parser.parse_args(["--no-sync", "summary", str(dummy_file), "--json"])
    cmd_summary(args_summary)
    assert not sync_calls, "cmd_summary called ensure_index_fresh despite --no-sync"

    # 2. Without --no-sync, auto_sync defaults to True and triggers sync
    sync_calls.clear()
    _LAST_SYNC_CHECK.clear()
    args_sync = parser.parse_args(["search", "run", "--path", str(tmp_path), "--json"])
    cmd_search(args_sync)
    assert len(sync_calls) == 1, (
        "cmd_search failed to call ensure_index_fresh when auto_sync was enabled"
    )


def test_normalize_path_in_subdirectories(tmp_path: Path):
    """Ensure normalize_path correctly computes relative paths when run in a subdirectory."""
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    f = sub / "mod.py"
    f.write_text("x = 1\n", encoding="utf-8")

    orig_cwd = os.getcwd()
    try:
        os.chdir(sub)
        norm = normalize_path("mod.py", tmp_path)
        assert norm == "src/pkg/mod.py", f"Expected 'src/pkg/mod.py', got '{norm}'"
    finally:
        os.chdir(orig_cwd)

    # Calling from project root should also produce 'src/pkg/mod.py'
    norm_from_root = normalize_path("src/pkg/mod.py", tmp_path)
    assert norm_from_root == "src/pkg/mod.py"


def test_non_git_workspace_sets_meta_and_avoids_repeated_reindexing(tmp_path: Path):
    """Ensure non-git directory indexes once, stores 'non-git' in meta, and skips repeated walks."""
    (tmp_path / "service.py").write_text("def ping(): return 'pong'\n", encoding="utf-8")
    db = IndexerDB(db_path=str(tmp_path / "test.db"))

    _LAST_SYNC_CHECK.clear()
    # Initial check: must perform full index
    indexed_first = ensure_index_fresh(db, tmp_path, debounce_seconds=0.0)
    assert indexed_first is True
    assert db.get_meta("last_indexed_git_sha") == "non-git"

    # Subsequent check: must skip reindex
    _LAST_SYNC_CHECK.clear()
    indexed_second = ensure_index_fresh(db, tmp_path, debounce_seconds=0.0)
    assert indexed_second is False


def test_python_super_method_calls_resolve_to_base_class_method():
    """Ensure super().method() calls resolve to Base.method instead of phantom node IDs."""
    parser = PythonParser()
    code = b"""
class Base:
    def run(self):
        return 1

class Child(Base):
    def run(self):
        return super().run()
"""
    nodes, edges = parser.extract("worker.py", code)
    node_map = {n.id: n.qualified_name for n in nodes}

    # Verify Base.run and Child.run nodes exist
    assert any(n.qualified_name == "Base.run" for n in nodes)
    assert any(n.qualified_name == "Child.run" for n in nodes)

    # Find calls edges originating from Child.run
    child_run_node = next(n for n in nodes if n.qualified_name == "Child.run")
    base_run_node = next(n for n in nodes if n.qualified_name == "Base.run")

    call_edges = [
        e for e in edges if e.from_node_id == child_run_node.id and e.relation_type == "calls"
    ]
    assert call_edges, "No calls edges found for Child.run"
    assert call_edges[0].to_node_id == base_run_node.id, (
        f"Expected call to {base_run_node.id} ({node_map.get(base_run_node.id)}), "
        f"got {call_edges[0].to_node_id} ({node_map.get(call_edges[0].to_node_id)})"
    )


def test_python_super_cross_module_method_resolution(tmp_path: Path):
    """Ensure super().method() resolves cross-file when base class is imported."""
    (tmp_path / "base.py").write_text("class Base:\n    def start(self): pass\n", encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    child_code = b"""
from base import Base

class Child(Base):
    def start(self):
        super().start()
"""
    parser = PythonParser(resolver=resolver)
    nodes, edges = parser.extract("child.py", child_code)

    child_start = next(n for n in nodes if n.qualified_name == "Child.start")
    calls = [e for e in edges if e.from_node_id == child_start.id and e.relation_type == "calls"]
    assert calls, "Missing call edge from Child.start"
    # Target should be base.py::Base.start
    expected_target_id = parser._get_id("base.py", "Base.start")
    assert calls[0].to_node_id == expected_target_id
