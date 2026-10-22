"""Audit verification tests for Round 11 code critique fixes.

Covers:
1. Global flags (--root, --no-sync, --json) accepted before and after subcommands.
2. Trailing "()" in search queries still hits the AST exact-match stream.
3. Python `cls.method()` resolves to the enclosing class method.
4. ConnectionManager.disconnect is idempotent under repeated/concurrent calls.
"""

from __future__ import annotations

from pathlib import Path

from vibetrace.cli import build_parser
from vibetrace.indexer.db import IndexerDB, Node
from vibetrace.indexer.parsers import ParserFactory
from vibetrace.server_api import ConnectionManager


def test_global_flags_after_subcommand():
    p = build_parser()
    a = p.parse_args(["index", ".", "--root", "repo", "--no-sync"])
    assert a.root == "repo"
    assert a.no_sync is True
    a = p.parse_args(["serve", "--root", "repo", "--json"])
    assert a.root == "repo"
    assert a.json is True
    # Root-level placement must not be clobbered by subparser defaults.
    a = p.parse_args(["--root", "top", "--no-sync", "search", "q"])
    assert a.root == "top"
    assert a.no_sync is True
    assert p.parse_args(["search", "q"]).root is None


def test_search_ast_strips_trailing_parens(tmp_path: Path):
    db = IndexerDB(str(tmp_path / "s.db"))
    db.upsert_node(
        Node(
            id="a.py::calculate",
            name="calculate",
            qualified_name="calculate",
            kind="function",
            file_path="a.py",
            start_line=1,
            end_line=2,
        )
    )
    hits = db.search_ast_exact_or_prefix("calculate()")
    assert hits
    assert hits[0]["name"] == "calculate"
    assert hits[0]["match_tier"] == 0


def test_python_cls_method_resolution():
    code = b"""
class Factory:
    @classmethod
    def create(cls):
        return cls.build()

    @classmethod
    def build(cls):
        pass
"""
    parser = ParserFactory.get_parser("f.py")
    assert parser is not None
    nodes, edges = parser.extract("f.py", code)
    ids = {n.qualified_name: n.id for n in nodes}
    assert (ids["Factory.create"], ids["Factory.build"]) in {
        (e.from_node_id, e.to_node_id) for e in edges
    }


def test_connection_manager_disconnect_idempotent():
    mgr = ConnectionManager()
    ws = object()
    mgr.active_connections.add(ws)  # type: ignore[arg-type]
    mgr.disconnect(ws)  # type: ignore[arg-type]
    mgr.disconnect(ws)  # type: ignore[arg-type]
    assert not mgr.active_connections
