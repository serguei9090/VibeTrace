"""Tests for Round 59: Automated Documentation Indexing on Post-Commit and Live Auto-Sync.

1. cmd_index auto-syncs local doc catalogs unless --no-docs is provided.
2. Hook installer configures post-commit & post-merge with both index and docs index.
3. Watcher detects live documentation file changes and synchronizes DocDB.
"""

import argparse
from pathlib import Path

from vibetrace.cli_commands import cmd_index
from vibetrace.docs.db import DocDB
from vibetrace.docs.service import DocCatalogManager
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.hooks import HOOK_SCRIPT_BLOCK, install_lefthook_config
from vibetrace.indexer.watcher import CodeChangeHandler


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
