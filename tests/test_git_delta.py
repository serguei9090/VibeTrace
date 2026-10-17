"""Unit tests for Git-SHA incremental delta caching and git_utils."""

from __future__ import annotations

import subprocess
from pathlib import Path

from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.git_utils import get_git_delta, get_git_sha
from vibegraph.indexer.main import reindex_all


def test_get_git_sha_current_repo() -> None:
    """Verify get_git_sha retrieves a 40-char SHA on the real git workspace."""
    sha = get_git_sha(Path.cwd())
    assert sha is not None
    assert len(sha) == 40
    assert all(c in "0123456789abcdefABCDEF" for c in sha)


def test_git_sha_non_git_directory(tmp_path: Path) -> None:
    """Verify get_git_sha returns None for non-git directory."""
    non_git = tmp_path / "empty_dir"
    non_git.mkdir()
    assert get_git_sha(non_git) is None
    assert get_git_delta(non_git, "abcdef1234567890abcdef1234567890abcdef12") is None


def test_git_delta_lifecycle(tmp_path: Path) -> None:
    """Test full Git-SHA delta lifecycle: initial commit, edit, delete, incremental reindex."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    # Initialize a temporary git repo
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)

    # File 1: Python module
    file_a = repo_dir / "module_a.py"
    file_a.write_text("def alpha():\n    return 42\n")
    # File 2: Python module
    file_b = repo_dir / "module_b.py"
    file_b.write_text("def beta():\n    return 'hello'\n")

    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

    initial_sha = get_git_sha(repo_dir)
    assert initial_sha is not None

    db_path = str(tmp_path / "test_delta.db")
    db = IndexerDB(db_path)

    # 1. Initial full reindex
    reindex_all(db, str(repo_dir), verbose=False, project_root=repo_dir)
    assert db.get_meta("last_indexed_git_sha") == initial_sha

    # Verify both functions are indexed
    nodes = {r["name"] for r in db.search_lexical("alpha")}
    assert "alpha" in nodes
    nodes_b = {r["name"] for r in db.search_lexical("beta")}
    assert "beta" in nodes_b

    # 2. Modify module_a.py and add module_c.py in working tree
    file_a.write_text("def alpha_updated():\n    return 99\n")
    file_c = repo_dir / "module_c.py"
    file_c.write_text("def gamma():\n    return 100\n")

    delta = get_git_delta(repo_dir, initial_sha)
    assert delta is not None
    changed, deleted = delta
    assert "module_a.py" in changed
    assert "module_c.py" in changed
    assert len(deleted) == 0

    # 3. Incremental reindex using delta cache
    reindex_all(db, str(repo_dir), verbose=False, project_root=repo_dir)
    res_updated = {r["name"] for r in db.search_lexical("alpha_updated")}
    assert "alpha_updated" in res_updated
    res_gamma = {r["name"] for r in db.search_lexical("gamma")}
    assert "gamma" in res_gamma

    # Commit changes
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "add c and update a"], cwd=repo_dir, check=True)
    second_sha = get_git_sha(repo_dir)
    assert second_sha is not None
    assert second_sha != initial_sha

    # 4. Delete module_b.py
    file_b.unlink()
    subprocess.run(["git", "rm", "module_b.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "delete b"], cwd=repo_dir, check=True)
    third_sha = get_git_sha(repo_dir)
    assert third_sha is not None

    # Reindex after deletion
    reindex_all(db, str(repo_dir), verbose=False, project_root=repo_dir)
    assert db.get_meta("last_indexed_git_sha") == third_sha

    # Verify module_b symbols were cleared
    res_deleted = [r for r in db.search_lexical("beta") if r["file_path"] == "module_b.py"]
    assert len(res_deleted) == 0
