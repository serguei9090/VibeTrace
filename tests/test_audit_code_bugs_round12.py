"""Audit verification tests for Round 12 code critique fixes.

Covers:
1. /source serves only indexed files; CORS allows loopback origins only.
2. get_git_delta treats files deleted in history but present on disk as changed.
3. A failed file write does not advance last_indexed_git_sha (retried next run).
4. One shared ignore rule: delta skips dist/ and dot-dirs; watcher uses root-relative parts.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import vibetrace.server_api as server_api
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.git_utils import get_git_delta, get_git_sha
from vibetrace.indexer.main import reindex_all
from vibetrace.indexer.watcher import CodeChangeHandler

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git not installed")


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return res.stdout.strip()


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")


def test_source_rejects_unindexed_files_and_foreign_origins(monkeypatch, tmp_path: Path):
    db = IndexerDB(str(tmp_path / "api.db"))
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    db.update_file_hash("app.py", "h", 0)
    monkeypatch.setattr(server_api, "db", db)
    monkeypatch.setattr(server_api, "project_root", tmp_path)
    client = TestClient(server_api.app)

    assert client.get("/source?file_path=.env").status_code == 404
    assert client.get("/source?file_path=app.py").status_code == 200

    evil = client.get("/source?file_path=app.py", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers
    local = client.get("/source?file_path=app.py", headers={"Origin": "http://localhost:5173"})
    assert local.headers.get("access-control-allow-origin") == "http://localhost:5173"


@needs_git
def test_git_delta_recreated_file_is_changed_not_deleted(tmp_path: Path):
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("def f(): pass\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "rm", "-q", "a.py")
    _git(tmp_path, "commit", "-qm", "del")
    (tmp_path / "a.py").write_text("def f(): pass\n", encoding="utf-8")

    delta = get_git_delta(tmp_path, base)
    assert delta is not None
    changed, deleted = delta
    assert "a.py" in changed
    assert "a.py" not in deleted


@needs_git
def test_failed_write_does_not_advance_git_sha(monkeypatch, tmp_path: Path):
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("def f(): pass\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    db = IndexerDB(str(tmp_path / ".vibetrace.db"))
    reindex_all(db, str(tmp_path), verbose=False)
    first_sha = db.get_meta("last_indexed_git_sha")

    (tmp_path / "a.py").write_text("def g(): pass\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "edit")

    real_save = db.batch_save_file_index

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "batch_save_file_index", locked)
    reindex_all(db, str(tmp_path), verbose=False)
    assert db.get_meta("last_indexed_git_sha") == first_sha  # not advanced

    monkeypatch.setattr(db, "batch_save_file_index", real_save)
    reindex_all(db, str(tmp_path), verbose=False)
    assert db.get_meta("last_indexed_git_sha") == get_git_sha(tmp_path)
    assert db.find_nodes_by_name("g")


@needs_git
def test_delta_and_watcher_share_ignore_rules(tmp_path: Path):
    root = tmp_path / "build" / "proj"  # ancestor named `build` must not matter
    root.mkdir(parents=True)
    _init_repo(root)
    (root / "app.py").write_text("def app(): pass\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    db = IndexerDB(str(tmp_path / "w.db"))
    reindex_all(db, str(root), verbose=False)

    for rel in ("dist/bundle.js", ".github/ci.py", "src/new.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("function x() {}\n" if rel.endswith(".js") else "def x(): pass\n")
    reindex_all(db, str(root), verbose=False)  # git delta path (untracked files)
    indexed = db.get_all_indexed_file_paths()
    assert "src/new.py" in indexed
    assert "dist/bundle.js" not in indexed
    assert ".github/ci.py" not in indexed

    handler = CodeChangeHandler(db, root_path=str(root))
    assert handler._should_ignore(str(root / "app.py")) is False
    assert handler._should_ignore(str(root / "dist" / "bundle.js")) is True
    assert handler._should_ignore(str(root / ".github" / "ci.py")) is True
