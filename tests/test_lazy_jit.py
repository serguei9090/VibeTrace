"""Unit tests for Lazy JIT (Just-In-Time) query synchronization."""

from __future__ import annotations

import subprocess
from pathlib import Path

from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.git_utils import get_git_sha
from vibegraph.indexer.main import reindex_all
from vibegraph.services.graph_service import ensure_index_fresh


def test_lazy_jit_uncommitted_changes(tmp_path: Path) -> None:
    """Verify dirty/uncommitted file edits are auto-indexed JIT before query execution."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    # Git init
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "jit@test.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "JIT User"], cwd=repo_dir, check=True)

    calc_file = repo_dir / "calc.py"
    calc_file.write_text("def add(a, b):\n    return a + b\n")

    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init calc"], cwd=repo_dir, check=True)

    context_dir = repo_dir / "vibegraph_context"
    context_dir.mkdir(exist_ok=True)
    db = IndexerDB(str(context_dir / "vibegraph.db"))

    # Initial index
    reindex_all(db, str(repo_dir), verbose=False, project_root=repo_dir)
    assert get_git_sha(repo_dir) == db.get_meta("last_indexed_git_sha")

    # Initial query confirms add exists, subtract does not
    res_add = db.search_lexical("add")
    assert any(r["name"] == "add" for r in res_add)
    assert not any(r["name"] == "subtract" for r in db.search_lexical("subtract"))

    # Developer or AI agent edits calc.py WITHOUT committing
    calc_file.write_text(
        "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n"
    )

    # Query via get_context_for_path (default auto_sync=True, debounce=0 for test)
    ensure_index_fresh(db, repo_dir, debounce_seconds=0.0)

    # Now subtract is automatically indexed on the fly!
    res_sub = db.search_lexical("subtract")
    assert any(r["name"] == "subtract" for r in res_sub)
