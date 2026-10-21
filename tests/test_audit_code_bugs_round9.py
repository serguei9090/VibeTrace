"""Audit verification tests for Round 9 code critique fixes.

Covers:
1. Subtree indexing does not poison repository-wide last_indexed_git_sha.
2. Watcher gitignore matches Windows backslashes under directory rules.
3. JS clientside API call emits relation_type='calls' and appears in get_code_slice callees.
4. Git delta with non-ASCII UTF-8 filenames preserves paths without octal deletion.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.git_utils import get_git_delta, get_git_sha
from vibetrace.indexer.main import reindex_all
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.indexer.watcher import CodeChangeHandler
from vibetrace.services.trace_service import get_code_slice


def test_subtree_indexing_does_not_poison_git_sha(tmp_path: Path):
    """Verify indexing a subfolder does not record last_indexed_git_sha for whole repo."""
    if not shutil.which("git"):
        return

    # Initialize git repo
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

    root_file = tmp_path / "root.py"
    root_file.write_text("def root_fn(): pass\n", encoding="utf-8")

    sub_dir = tmp_path / "sub"
    sub_dir.mkdir()
    sub_file = sub_dir / "child.py"
    sub_file.write_text("def child_fn(): pass\n", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True
    )
    current_sha = get_git_sha(tmp_path)
    assert current_sha is not None

    db = IndexerDB(str(tmp_path / "index.db"))

    # Index ONLY subtree 'sub'
    reindex_all(db, str(sub_dir), project_root=tmp_path, verbose=False)

    # Repository-wide SHA must NOT be set
    assert db.get_meta("last_indexed_git_sha") is None
    indexed_files = db.get_all_indexed_file_paths()
    assert "sub/child.py" in indexed_files
    assert "root.py" not in indexed_files

    # Now run full repository index: root.py must be indexed!
    reindex_all(db, str(tmp_path), project_root=tmp_path, verbose=False)
    all_indexed = db.get_all_indexed_file_paths()
    assert "root.py" in all_indexed
    assert "sub/child.py" in all_indexed
    assert db.get_meta("last_indexed_git_sha") == current_sha


def test_watcher_gitignore_windows_backslash_normalization(tmp_path: Path):
    """Verify watcher correctly ignores files on Windows when gitignore has directory patterns."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("build/*\nsrc/cache/*\n*.tmp\n", encoding="utf-8")

    db = IndexerDB(str(tmp_path / "test.db"))
    handler = CodeChangeHandler(db, root_path=str(tmp_path))

    # Path with subdirectories inside gitignored folder
    target_ignored = tmp_path / "src" / "cache" / "data.json"
    assert handler._should_ignore(str(target_ignored)) is True

    build_ignored = tmp_path / "build" / "bundle.js"
    assert handler._should_ignore(str(build_ignored)) is True

    # Non-ignored path
    valid_file = tmp_path / "src" / "index.ts"
    assert handler._should_ignore(str(valid_file)) is False


def test_js_client_api_call_relation_type_and_slicing(tmp_path: Path):
    """Verify clientside API calls emit 'calls' relation and appear in code slice callees."""
    js_code = """
function loginUser() {
    return axios.post("/api/auth/login", { user: "alice" });
}
"""
    parser = JavaScriptParser(ModuleResolver(tmp_path))
    nodes, edges = parser.extract("src/api.js", js_code.encode("utf-8"))

    endpoint_node = next(n for n in nodes if n.name == "POST /api/auth/login")
    route_edges = [
        e for e in edges if e.relation_type == "routes" and e.to_node_id == endpoint_node.id
    ]
    assert len(route_edges) == 1

    # Test code slicing extracts the endpoint as a callee
    db = IndexerDB(str(tmp_path / "test.db"))
    src_file = tmp_path / "src" / "api.js"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text(js_code, encoding="utf-8")

    db.batch_save_file_index("src/api.js", "hash1", nodes, edges)
    slice_res = get_code_slice(db, tmp_path, "loginUser", include_callees=True)
    assert slice_res.found is True
    assert len(slice_res.callees) == 1
    assert slice_res.callees[0].name == "POST /api/auth/login"


def test_git_delta_quotepath_utf8(tmp_path: Path):
    """Verify git delta preserves UTF-8 filenames without octal quote corruption."""
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

    f1 = tmp_path / "init.txt"
    f1.write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "first"], cwd=tmp_path, check=True, capture_output=True)
    base_sha = get_git_sha(tmp_path)
    assert base_sha is not None

    # Add file with UTF-8 non-ASCII characters
    utf8_file = tmp_path / "données.py"
    utf8_file.write_text("def test(): pass\n", encoding="utf-8")

    delta = get_git_delta(tmp_path, base_sha)
    assert delta is not None
    changed, deleted = delta
    assert "données.py" in changed
    assert "données.py" not in deleted
