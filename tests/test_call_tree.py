"""Tests for top-down recursive call-tree generation and ASCII formatting."""

from pathlib import Path

from vibegraph.cli import main
from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.main import reindex_all
from vibegraph.services.call_tree_service import (
    CallTreeNode,
    build_call_tree,
    format_call_tree_md,
)


def _create_sample_tree_project(tmp_path: Path) -> IndexerDB:
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    # Leaf nodes
    (pkg / "db_ops.py").write_text(
        "def execute_sql(query: str) -> None:\n"
        "    pass\n\n"
        "def audit_log(event: str) -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )

    # Middle layer
    (pkg / "service.py").write_text(
        "from .db_ops import execute_sql, audit_log\n\n"
        "def handle_payment(amount: int) -> bool:\n"
        "    execute_sql('INSERT ...')\n"
        "    audit_log('paid')\n"
        "    return True\n",
        encoding="utf-8",
    )

    # Top-level entry
    (pkg / "entry.py").write_text(
        "from .service import handle_payment\n\n"
        "def main_entry() -> None:\n"
        "    handle_payment(100)\n",
        encoding="utf-8",
    )

    ctx_dir = tmp_path / "vibegraph_context"
    ctx_dir.mkdir()
    db = IndexerDB(str(ctx_dir / "vibegraph.db"))
    reindex_all(db, str(tmp_path), verbose=False)
    return db


def _create_cyclic_project(tmp_path: Path) -> IndexerDB:
    pkg = tmp_path / "cyclic_app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    (pkg / "recursion.py").write_text(
        "def ping(n: int) -> None:\n"
        "    if n > 0:\n"
        "        pong(n - 1)\n\n"
        "def pong(n: int) -> None:\n"
        "    if n > 0:\n"
        "        ping(n - 1)\n",
        encoding="utf-8",
    )

    ctx_dir = tmp_path / "vibegraph_context"
    ctx_dir.mkdir()
    db = IndexerDB(str(ctx_dir / "vibegraph.db"))
    reindex_all(db, str(tmp_path), verbose=False)
    return db


def test_build_call_tree_basic(tmp_path: Path) -> None:
    """build_call_tree builds full top-down tree from entry point to leaf functions."""
    db = _create_sample_tree_project(tmp_path)

    res = build_call_tree(db, "main_entry", max_depth=5)
    assert res.root_symbol == "main_entry"
    assert res.tree is not None
    assert res.tree.name == "main_entry"
    assert res.total_nodes >= 4

    # Level 1 child: handle_payment
    children = res.tree.children
    child_names = [c.name for c in children]
    assert "handle_payment" in child_names

    payment_node = next(c for c in children if c.name == "handle_payment")
    assert payment_node.relation_type == "calls"
    assert payment_node.depth == 1

    # Level 2 children: execute_sql and audit_log
    grandchild_names = [gc.name for gc in payment_node.children]
    assert "execute_sql" in grandchild_names
    assert "audit_log" in grandchild_names


def test_build_call_tree_depth_limit(tmp_path: Path) -> None:
    """build_call_tree respects max_depth."""
    db = _create_sample_tree_project(tmp_path)

    # Depth 1 should only return direct children
    res = build_call_tree(db, "main_entry", max_depth=1)
    assert res.tree is not None
    assert len(res.tree.children) >= 1
    for child in res.tree.children:
        assert len(child.children) == 0


def test_build_call_tree_cycle_detection(tmp_path: Path) -> None:
    """build_call_tree terminates without infinite loop on mutually recursive functions."""
    db = _create_cyclic_project(tmp_path)

    res = build_call_tree(db, "ping", max_depth=10)
    assert res.root_symbol == "ping"
    assert res.tree is not None

    # Should find cycle on ping -> pong -> ping (cycle)
    def find_cycle(node: CallTreeNode) -> bool:
        if node.is_cycle:
            return True
        return any(find_cycle(c) for c in node.children)

    assert find_cycle(res.tree) is True


def test_format_call_tree_md(tmp_path: Path) -> None:
    """format_call_tree_md produces readable ASCII tree with connectors."""
    db = _create_sample_tree_project(tmp_path)
    res = build_call_tree(db, "main_entry", max_depth=3)
    md = format_call_tree_md(res)

    assert "Top-Down Call Tree: `main_entry`" in md
    assert "main_entry" in md
    assert "handle_payment" in md
    assert "```text" in md
    # ASCII connectors present
    assert "└──" in md or "├──" in md or "\\--" in md

    # Explicit ASCII-only fallback check
    md_ascii = format_call_tree_md(res, ascii_only=True)
    assert "\\--" in md_ascii or "|--" in md_ascii


def test_cli_call_tree(tmp_path: Path, capsys) -> None:
    """Test CLI call-tree command invocation."""
    _create_sample_tree_project(tmp_path)

    exit_code = main(["--root", str(tmp_path), "call-tree", "main_entry", "--depth", "3"])
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "Top-Down Call Tree: `main_entry`" in captured.out
    assert "handle_payment" in captured.out


def test_call_tree_max_nodes_ceiling(tmp_path: Path) -> None:
    """build_call_tree must respect max_nodes limit on dense graphs."""
    db = _create_sample_tree_project(tmp_path)
    res = build_call_tree(db, "main_entry", max_depth=3, max_nodes=2)
    assert res.found is True
    assert res.total_nodes <= 2
