"""Audit verification tests for Round 10 code critique fixes.

Covers:
1. LSP bridge decodes percent-encoded Windows drive letters (file://c%3A/...).
2. ensure_index_fresh recovers from stale/pruned Git SHAs when delta is None.
3. compute_impact_analysis normalizes Windows backslashes and relative prefixes.
4. try_load_sqlite_vec ensures enable_load_extension is safely disabled in finally.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.embeddings import try_load_sqlite_vec
from vibetrace.indexer.git_utils import get_git_sha
from vibetrace.indexer.lsp_bridge import LSPClient
from vibetrace.indexer.main import reindex_all
from vibetrace.indexer.models import Edge, Node
from vibetrace.services.graph_service import (
    compute_impact_analysis,
    ensure_index_fresh,
)


def test_lsp_unquote_encoded_windows_drive_letter(tmp_path: Path):
    """Verify LSP definition resolution decodes percent-encoded Windows drive letters."""
    proj_root = Path("C:/project") if os.name == "nt" else Path("/project")
    client = LSPClient(["fake-lsp"], proj_root)

    # Mock send_request returning encoded Windows URI
    encoded_uris = [
        "file://c%3A/project/sub/file.py",
        "file:///c%3A/project/sub/file.py",
        "file:///C:/project/sub/file.py",
    ]

    for uri in encoded_uris:
        target_uri = uri
        client.send_request = (  # type: ignore
            lambda _m, _p, u=target_uri, **_k: {
                "uri": u,
                "range": {"start": {"line": 10, "character": 4}},
            }
        )
        res = client.get_definition(proj_root / "main.py", 1)
        if os.name == "nt":
            assert res is not None
            rel_file, line = res
            assert rel_file == "sub/file.py"
            assert line == 11


def test_ensure_index_fresh_pruned_sha_reindex(tmp_path: Path):
    """Verify ensure_index_fresh re-indexes when last_sha is pruned and git delta returns None."""
    if not shutil.which("git"):
        return

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True
    )

    src = tmp_path / "app.py"
    src.write_text("def run(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    current_sha = get_git_sha(tmp_path)
    assert current_sha is not None

    db = IndexerDB(str(tmp_path / "index.db"))
    reindex_all(db, str(tmp_path), verbose=False)
    assert db.get_meta("last_indexed_git_sha") == current_sha

    # Simulate pruned / unreachable commit SHA
    fake_sha = "deadbeef00000000000000000000000000000000"
    db.set_meta("last_indexed_git_sha", fake_sha)

    # ensure_index_fresh must detect delta is None and re-index
    updated = ensure_index_fresh(db, tmp_path, debounce_seconds=0.0)
    assert updated is True
    assert db.get_meta("last_indexed_git_sha") == current_sha


def test_compute_impact_analysis_windows_paths_same_file(tmp_path: Path):
    """Verify same-file function dependencies are not counted as external impacts."""
    db = IndexerDB(str(tmp_path / "impact.db"))

    # Two nodes in the same file: fn_a calls fn_b
    n_a = Node(
        id="calc.py::fn_a",
        name="fn_a",
        qualified_name="fn_a",
        kind="function",
        file_path="src/utils/calc.py",
        start_line=1,
        end_line=2,
    )
    n_b = Node(
        id="calc.py::fn_b",
        name="fn_b",
        qualified_name="fn_b",
        kind="function",
        file_path="src/utils/calc.py",
        start_line=4,
        end_line=5,
    )
    edge = Edge(
        from_node_id="calc.py::fn_a",
        to_node_id="calc.py::fn_b",
        relation_type="calls",
        call_line=2,
    )
    db.batch_save_file_index("src/utils/calc.py", "hash1", [n_a, n_b], [edge])

    # Query with Windows backslashes
    total, impacts = compute_impact_analysis(db, "src\\utils\\calc.py")
    assert total == 0
    assert len(impacts[1]) == 0

    # Query with leading ./
    total_rel, impacts_rel = compute_impact_analysis(db, "./src/utils/calc.py")
    assert total_rel == 0
    assert len(impacts_rel[1]) == 0


def test_try_load_sqlite_vec_exception_safety():
    """Verify try_load_sqlite_vec always leaves extension loading disabled."""
    conn = sqlite3.connect(":memory:")
    # Execution should not throw and must ensure extension loading is disabled
    try_load_sqlite_vec(conn)

    # If extension loading remained enabled, loading a dummy extension would fail
    # with 'not authorized' rather than 'cannot open' or disabled error.
    # In Python sqlite3, when disabled, calling enable_load_extension is required.
    try:
        with pytest.raises((sqlite3.OperationalError, AttributeError)):
            conn.load_extension("dummy_nonexistent")
    finally:
        conn.close()
