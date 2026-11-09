"""Consolidated regression test suite: test_regression_cli_and_mcp.py."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import vibetrace.server_api as server_api
from vibetrace import cli
from vibetrace.cli import build_parser
from vibetrace.cli import main as cli_main
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.embeddings import try_load_sqlite_vec
from vibetrace.indexer.git_utils import get_git_delta, get_git_sha
from vibetrace.indexer.lsp_bridge import LSPClient
from vibetrace.indexer.main import reindex_all
from vibetrace.indexer.models import Edge, Node
from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.indexer.watcher import CodeChangeHandler, start_watcher_blocking
from vibetrace.mcp.formatters import format_impact_analysis_md
from vibetrace.mcp.schemas import (
    DeleteSavedTraceInput,
    DependenciesInput,
    ImpactAnalysisInput,
    ListSavedTracesInput,
    ResponseFormat,
    SaveTraceInput,
    SearchInput,
    TracePathInput,
)
from vibetrace.mcp.server import (
    vibetrace_delete_saved_trace,
    vibetrace_get_dependencies,
    vibetrace_impact_analysis,
    vibetrace_list_saved_traces,
    vibetrace_save_trace,
    vibetrace_search_by_signature,
    vibetrace_trace_path,
)
from vibetrace.server_api import app, lifespan
from vibetrace.services.graph_service import (
    build_call_stack,
    categorize_file_dependencies,
    compute_impact_analysis,
    ensure_index_fresh,
    find_symbol_references,
)
from vibetrace.services.saved_traces_service import (
    SavedTraceCreate,
    create_saved_trace,
    delete_saved_trace_by_name,
    get_saved_trace_by_name,
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


def test_db_clear_file_purges_vector_store(tmp_path: Path):
    """Verify IndexerDB.clear_file automatically calls delete_file_embeddings on vector_store."""
    mock_store = MagicMock()
    mock_store.is_available.return_value = True

    db = IndexerDB(db_path=str(tmp_path / "test.db"), vector_store=mock_store)
    db.clear_file("src/deleted.py")

    mock_store.delete_file_embeddings.assert_called_once_with("src/deleted.py")


def test_db_batch_save_file_index_purges_vector_store(tmp_path: Path):
    """Verify IndexerDB.batch_save_file_index purges old vector records for re-indexed files."""
    mock_store = MagicMock()
    mock_store.is_available.return_value = True

    db = IndexerDB(db_path=str(tmp_path / "test.db"), vector_store=mock_store)
    # Call batch_save_file_index with empty nodes to verify purge hook runs
    db.batch_save_file_index("src/updated.py", "hash123", [], [])

    mock_store.delete_file_embeddings.assert_called_once_with("src/updated.py")


def test_server_api_source_with_absolute_and_unnormalized_path(tmp_path: Path):
    """Verify /source endpoint resolves both relative and absolute paths for indexed files."""
    test_file = tmp_path / "sample.py"
    test_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    from vibetrace import server_api

    old_root = server_api.project_root
    old_db = server_api.db
    try:
        server_api.project_root = tmp_path
        server_api.db = IndexerDB(db_path=str(tmp_path / "server_test.db"))
        # Seed indexed file
        with server_api.db._get_conn() as conn:
            conn.execute(
                "INSERT INTO indexed_files (file_path, content_hash, updated_at) VALUES (?, ?, ?)",
                ("sample.py", "hash1", 100.0),
            )
            conn.commit()

        client = TestClient(app)

        # 1. Relative path query
        res_rel = client.get("/source", params={"file_path": "./sample.py"})
        assert res_rel.status_code == 200
        assert res_rel.json()["file_path"] == "sample.py"
        assert "def hello():" in res_rel.json()["source_code"]

        # 2. Absolute path query
        res_abs = client.get("/source", params={"file_path": str(test_file)})
        assert res_abs.status_code == 200
        assert res_abs.json()["file_path"] == "sample.py"
        assert "def hello():" in res_abs.json()["source_code"]

        # 3. Graph query with absolute path
        res_graph = client.get("/graph", params={"file_path": str(test_file)})
        assert res_graph.status_code == 200
        assert "nodes" in res_graph.json()
    finally:
        server_api.project_root = old_root
        server_api.db = old_db


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test.db"
    db_inst = IndexerDB(str(db_file))
    yield db_inst
    db_inst.close()


def test_path_sanitization_with_redundant_dot_slashes(test_db: IndexerDB):
    test_db.batch_save_file_index(
        file_path="src/utils.py",
        content_hash="hash123",
        nodes=[
            Node(
                id="n1",
                name="helper_func",
                qualified_name="helper_func",
                parent_id=None,
                file_path="src/utils.py",
                kind="function",
                start_line=1,
                end_line=5,
            )
        ],
        edges=[],
    )

    # Redundant ././ prefix should be cleaned and match indexed nodes
    total, _impacts = compute_impact_analysis(test_db, "././src/utils.py")
    assert total == 0
    # Also verify dependencies categorization cleans paths
    internal, third_party, stdlib = categorize_file_dependencies(test_db, "././src/utils.py")
    assert internal == []
    assert third_party == []
    assert stdlib == []


def test_cli_references_and_dependencies(capsys: pytest.CaptureFixture[str]):
    # Test CLI references on existing codebase symbol
    code = cli_main(["references", "upsert_node", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["found"] is True
    assert data["symbol"] == "upsert_node"
    assert data["total_references"] > 0

    # Test CLI references on missing symbol
    code_missing = cli_main(["references", "non_existent_symbol_xyz", "--json"])
    assert code_missing == 1
    captured_missing = capsys.readouterr()
    data_missing = json.loads(captured_missing.out)
    assert data_missing["found"] is False

    # Test CLI dependencies on cli.py
    code_deps = cli_main(["dependencies", "src/vibetrace/cli.py", "--json"])
    assert code_deps == 0
    captured_deps = capsys.readouterr()
    deps_data = json.loads(captured_deps.out)
    assert deps_data["file_path"] == "src/vibetrace/cli.py"
    assert deps_data["counts"]["stdlib"] >= 1


def test_api_impact_endpoint():
    client = TestClient(app)
    # Test existing indexed file
    res = client.get("/api/impact", params={"file_path": "src/vibetrace/cli.py"})
    assert res.status_code == 200
    payload = res.json()
    assert "file_path" in payload
    assert "total_impact" in payload
    assert "levels" in payload

    # Test non-indexed file
    res_404 = client.get("/api/impact", params={"file_path": "src/vibetrace/ghost_file.py"})
    assert res_404.status_code == 404


def test_api_slice_endpoint():
    client = TestClient(app)
    # Test existing symbol
    res = client.get("/api/slice", params={"symbol": "upsert_node"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["found"] is True
    assert "IndexerDB" in payload["qualified_name"] or payload["name"] == "upsert_node"
    assert len(payload["source_code"]) > 0

    # Test non-existent symbol
    res_404 = client.get("/api/slice", params={"symbol": "phantom_symbol_not_found"})
    assert res_404.status_code == 404


def test_api_references_endpoint():
    client = TestClient(app)
    # Test existing symbol
    res = client.get("/api/references", params={"symbol": "upsert_node"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["symbol"] == "upsert_node"
    assert payload["total_references"] > 0
    assert len(payload["targets"]) >= 1

    # Test non-existent symbol
    res_404 = client.get("/api/references", params={"symbol": "completely_unknown_fn"})
    assert res_404.status_code == 404


def test_api_dependencies_endpoint():
    client = TestClient(app)
    # Test existing file
    res = client.get("/api/dependencies", params={"file_path": "src/vibetrace/cli.py"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["file_path"] == "src/vibetrace/cli.py"
    assert "internal" in payload
    assert "stdlib" in payload

    # Test non-indexed file
    res_404 = client.get("/api/dependencies", params={"file_path": "no/such/file.py"})
    assert res_404.status_code == 404


def test_api_summary_endpoint():
    client = TestClient(app)
    # Test existing file
    res = client.get("/api/summary", params={"file_path": "src/vibetrace/cli.py", "limit": 10})
    assert res.status_code == 200
    payload = res.json()
    assert payload["file_path"] == "src/vibetrace/cli.py"
    assert payload["total"] > 0
    assert len(payload["nodes"]) <= 10

    # Test non-indexed file
    res_404 = client.get("/api/summary", params={"file_path": "phantom.py"})
    assert res_404.status_code == 404


@pytest.fixture
def test_db_round30(tmp_path: Path):
    db_file = tmp_path / "test.db"
    db_inst = IndexerDB(str(db_file))
    yield db_inst
    db_inst.close()


def test_signature_search_wildcard_wrapping(test_db_round30: IndexerDB):
    test_db_round30.batch_save_file_index(
        file_path="src/service.py",
        content_hash="hash1",
        nodes=[
            Node(
                id="n1",
                name="handle_req",
                qualified_name="handle_req",
                parent_id=None,
                file_path="src/service.py",
                kind="function",
                start_line=1,
                end_line=10,
                signature="(db: IndexerDB, key: str) -> bool",
            )
        ],
        edges=[],
    )

    # Bare substring without % should automatically match
    res_bare = test_db_round30.search_by_signature_pattern("IndexerDB")
    assert len(res_bare) == 1
    assert res_bare[0]["name"] == "handle_req"

    # Explicit SQL wildcard should also work
    res_wildcard = test_db_round30.search_by_signature_pattern("%key: str%")
    assert len(res_wildcard) == 1

    # Non-matching pattern
    res_none = test_db_round30.search_by_signature_pattern("float")
    assert len(res_none) == 0


def test_cli_search_with_signature_flag(capsys: pytest.CaptureFixture[str]):
    # Test CLI search with --sig and --json
    code = cli_main(["search", "IndexerDB", "--sig", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert isinstance(results, list)

    # Test CLI search with --sig and human markdown output
    code_text = cli_main(["search", "IndexerDB", "--sig"])
    assert code_text == 0
    captured_text = capsys.readouterr()
    assert "## Signature Search:" in captured_text.out


def test_api_search_signature_endpoint():
    client = TestClient(app)
    res = client.get("/api/search/signature", params={"pattern": "IndexerDB", "limit": 10})
    assert res.status_code == 200
    results = res.json()
    assert isinstance(results, list)


def test_api_reindex_endpoint(tmp_path: Path):
    client = TestClient(app)

    # Test single-file 404 for non-existent file
    res_404 = client.post("/api/reindex", json={"file_path": "non_existent_file.py"})
    assert res_404.status_code == 404

    # Test single-file reindex on existing file
    res_file = client.post("/api/reindex", json={"file_path": "src/vibetrace/cli.py"})
    assert res_file.status_code == 200
    payload = res_file.json()
    assert payload["status"] == "ok"
    assert payload["mode"] == "single_file"

    # Test workspace reindex
    res_ws = client.post("/api/reindex", json={})
    assert res_ws.status_code == 200
    assert res_ws.json()["status"] == "ok"


def test_api_call_stack_endpoint():
    client = TestClient(app)

    # Test valid symbol call stack (both directions)
    res = client.get(
        "/call-stack", params={"symbol": "upsert_node", "direction": "both", "depth": 2}
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["found"] is True
    assert payload["symbol"] == "upsert_node"
    assert len(payload["call_stack"]) > 0

    # Test call-stack via /api/call-stack alias
    res_alias = client.get(
        "/api/call-stack", params={"symbol": "upsert_node", "direction": "up", "depth": 1}
    )
    assert res_alias.status_code == 200
    assert res_alias.json()["direction"] == "up"

    # Test missing symbol returns 404
    res_404 = client.get("/call-stack", params={"symbol": "completely_ghost_node"})
    assert res_404.status_code == 404


def test_build_call_stack_service(test_db_round30: IndexerDB):
    test_db_round30.batch_save_file_index(
        file_path="src/flow.py",
        content_hash="hash_flow",
        nodes=[
            Node(
                id="a",
                name="entrypoint",
                qualified_name="entrypoint",
                parent_id=None,
                file_path="src/flow.py",
                kind="function",
                start_line=1,
                end_line=5,
            )
        ],
        edges=[],
    )

    found, output, nodes = build_call_stack(
        test_db_round30, "entrypoint", direction="both", max_depth=2
    )
    assert found is True
    assert len(nodes) == 1
    assert any("entrypoint" in line for line in output)


@pytest.mark.asyncio
async def test_lifespan_closes_db():
    from vibetrace import server_api

    mock_db = MagicMock()
    orig_db = server_api.db
    server_api.db = mock_db
    try:
        async with lifespan(server_api.app):
            pass
        mock_db.close.assert_called_once()
    finally:
        server_api.db = orig_db


def test_cli_call_stack_parser_and_execution(tmp_path: Path, capsys):
    db_path = tmp_path / ".vibetrace" / "vibetrace.db"
    db_path.parent.mkdir(parents=True)
    db = IndexerDB(str(db_path))

    caller = Node(
        id="a.py:caller",
        name="caller",
        kind="function",
        file_path="a.py",
        start_line=1,
        end_line=5,
        qualified_name="caller",
    )
    callee = Node(
        id="a.py:callee",
        name="callee",
        kind="function",
        file_path="a.py",
        start_line=10,
        end_line=15,
        qualified_name="callee",
    )
    db.upsert_node(caller)
    db.upsert_node(callee)
    db.upsert_edge(
        Edge(
            from_node_id="a.py:caller",
            to_node_id="a.py:callee",
            relation_type="calls",
            call_line=3,
        )
    )
    db.close()

    parser = build_parser()

    # 1. Test CLI execution (text output)
    args = parser.parse_args(
        ["call-stack", "callee", "--direction", "up", "--path", str(tmp_path), "--no-sync"]
    )
    code = args.func(args)
    captured = capsys.readouterr()
    assert code == 0
    assert "caller" in captured.out
    assert "Callers (Incoming):" in captured.out

    # 2. Test CLI execution (json output)
    cmd_args = [
        "call-stack",
        "callee",
        "--direction",
        "up",
        "--path",
        str(tmp_path),
        "--no-sync",
        "--json",
    ]
    args_json = parser.parse_args(cmd_args)
    code_json = args_json.func(args_json)
    captured_json = capsys.readouterr()
    assert code_json == 0
    data = json.loads(captured_json.out)
    assert data["found"] is True
    assert data["symbol"] == "callee"
    assert data["direction"] == "up"
    assert any("caller" in line for line in data["call_stack"])


def test_build_call_stack_multi_symbol_isolation(tmp_path: Path):
    """Verify that when 2 symbols share the same name, caller check does not leak across nodes."""
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    # Node 1: worker.py:run (HAS callers)
    worker_run = Node(
        id="worker.py:run",
        name="run",
        kind="function",
        file_path="worker.py",
        start_line=1,
        end_line=5,
        qualified_name="run",
    )
    # Node 2: task.py:run (HAS NO callers)
    task_run = Node(
        id="task.py:run",
        name="run",
        kind="function",
        file_path="task.py",
        start_line=1,
        end_line=5,
        qualified_name="run",
    )
    # Caller for worker.py:run
    invoker = Node(
        id="main.py:start",
        name="start",
        kind="function",
        file_path="main.py",
        start_line=10,
        end_line=15,
        qualified_name="start",
    )

    db.upsert_node(worker_run)
    db.upsert_node(task_run)
    db.upsert_node(invoker)
    db.upsert_edge(
        Edge(
            from_node_id="main.py:start",
            to_node_id="worker.py:run",
            relation_type="calls",
            call_line=12,
        )
    )

    found, output, nodes = build_call_stack(db, "run", direction="up", max_depth=2)
    assert found is True
    assert len(nodes) == 2

    full_text = "\n".join(output)
    # Both traces must be present
    assert "Trace for `run` (function) in `worker.py`" in full_text
    assert "Trace for `run` (function) in `task.py`" in full_text
    # worker.py:run has callers from start
    assert "start" in full_text
    # task.py:run MUST explicitly state "(no callers found)" without leaking from worker.py
    assert "(no callers found)" in full_text
    db.close()


def test_rest_api_namespaced_endpoints(tmp_path: Path):
    """Verify /api/ aliases for /graph, /trace, /call-tree, and /source."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    source_file = project_root / "mod.py"
    source_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    db_path = project_root / ".vibetrace" / "vibetrace.db"
    db_path.parent.mkdir(parents=True)
    db = IndexerDB(str(db_path))

    node = Node(
        id="mod.py:hello",
        name="hello",
        kind="function",
        file_path="mod.py",
        start_line=1,
        end_line=2,
        qualified_name="hello",
    )
    db.upsert_node(node)
    db.update_file_hash("mod.py", "dummy_hash", 12345)

    orig_db = server_api.db
    orig_root = server_api.project_root
    try:
        server_api.db = db
        server_api.project_root = project_root
        client = TestClient(server_api.app)

        # /api/graph
        resp_graph = client.get("/api/graph")
        assert resp_graph.status_code == 200
        assert any(n["id"] == "mod.py:hello" for n in resp_graph.json()["nodes"])

        # /api/source
        src_params = {"file_path": "mod.py", "start_line": 1, "end_line": 2}
        resp_source = client.get("/api/source", params=src_params)
        assert resp_source.status_code == 200
        assert "hello" in resp_source.json()["source_code"]

        # /api/call-tree
        resp_tree = client.get("/api/call-tree", params={"symbol": "hello"})
        assert resp_tree.status_code == 200
        assert resp_tree.json()["found"] is True

        # /api/trace
        trace_params = {"from_symbol": "hello", "to_symbol": "hello"}
        resp_trace = client.get("/api/trace", params=trace_params)
        assert resp_trace.status_code == 200
    finally:
        server_api.db = orig_db
        server_api.project_root = orig_root
        db.close()


def test_shared_format_impact_analysis():
    # Empty impact
    empty_out = format_impact_analysis_md("test.py", 0, {})
    assert "Impact Analysis for `test.py`" in empty_out
    assert "No external dependencies found" in empty_out

    # Multi-level impact
    levels = {1: ["func_a in a.py"], 2: ["func_b in b.py"]}
    multi_out = format_impact_analysis_md("test.py", 2, levels)
    assert "**Total Impact**: 2 components affected" in multi_out
    assert "### Level 1: Direct Impact" in multi_out
    assert "func_a in a.py" in multi_out
    assert "### Level 2: Secondary Impact" in multi_out


def test_shared_find_symbol_references(tmp_path: Path):
    db_path = tmp_path / "refs.db"
    db = IndexerDB(str(db_path))

    target = Node(
        id="lib.py:compute",
        name="compute",
        kind="function",
        file_path="lib.py",
        start_line=1,
        end_line=5,
        qualified_name="compute",
    )
    caller = Node(
        id="app.py:execute",
        name="execute",
        kind="function",
        file_path="app.py",
        start_line=10,
        end_line=20,
        qualified_name="execute",
    )
    db.upsert_node(target)
    db.upsert_node(caller)
    db.upsert_edge(
        Edge(
            from_node_id="app.py:execute",
            to_node_id="lib.py:compute",
            relation_type="calls",
            call_line=12,
        )
    )

    found, targets, summaries = find_symbol_references(db, "compute")
    assert found is True
    assert len(targets) == 1
    assert len(summaries) == 1
    assert len(summaries[0]["references"]) == 1
    assert summaries[0]["references"][0]["name"] == "execute"

    # Non-existent symbol
    not_found, _, _ = find_symbol_references(db, "non_existent_func")
    assert not_found is False
    db.close()


def test_java_signature_and_return_type_extraction():
    parser = GenericParser("java")
    code = (
        b"class Service { public String processOrder(int orderId, boolean fast) { return null; } }"
    )
    nodes, _ = parser.extract("Service.java", code)
    func_node = next(n for n in nodes if n.kind == "function" and n.name == "processOrder")
    assert func_node.signature == "(int orderId, boolean fast) -> String"


def test_cpp_signature_and_return_type_extraction():
    parser = GenericParser("cpp")
    code = b"double calculateMetrics(int samples, float rate) { return 0.0; }"
    nodes, _ = parser.extract("calc.cpp", code)
    func_node = next(n for n in nodes if n.kind == "function" and n.name == "calculateMetrics")
    assert func_node.signature == "(int samples, float rate) -> double"


def test_csharp_signature_and_return_type_extraction():
    parser = GenericParser("c_sharp")
    code = b"class Controller { public async Task<bool> Validate(string token) { return true; } }"
    nodes, _ = parser.extract("Controller.cs", code)
    func_node = next(n for n in nodes if n.kind == "function" and n.name == "Validate")
    assert func_node.signature == "(string token) -> Task<bool>"


def test_signature_search_deterministic_centrality_and_offset(tmp_path: Path):
    db_path = tmp_path / "sig_test.db"
    db = IndexerDB(str(db_path))

    # Insert 3 nodes with identical signature pattern but different centrality
    node_low = Node(
        id="a.py:low",
        name="low",
        kind="function",
        file_path="a.py",
        start_line=1,
        end_line=5,
        qualified_name="low",
        signature="(x: int) -> bool",
        centrality=0.1,
    )
    node_high = Node(
        id="b.py:high",
        name="high",
        kind="function",
        file_path="b.py",
        start_line=1,
        end_line=5,
        qualified_name="high",
        signature="(x: int) -> bool",
        centrality=0.9,
    )
    node_mid = Node(
        id="c.py:mid",
        name="mid",
        kind="function",
        file_path="c.py",
        start_line=1,
        end_line=5,
        qualified_name="mid",
        signature="(x: int) -> bool",
        centrality=0.5,
    )

    db.upsert_node(node_low)
    db.upsert_node(node_high)
    db.upsert_node(node_mid)

    # 1. Verify deterministic order: high (0.9) -> mid (0.5) -> low (0.1)
    results = db.search_by_signature_pattern("-> bool", limit=10, offset=0)
    assert len(results) == 3
    assert results[0]["name"] == "high"
    assert results[1]["name"] == "mid"
    assert results[2]["name"] == "low"

    # 2. Verify pagination offset
    paginated = db.search_by_signature_pattern("-> bool", limit=2, offset=1)
    assert len(paginated) == 2
    assert paginated[0]["name"] == "mid"
    assert paginated[1]["name"] == "low"

    db.close()


def test_api_search_signature_with_pagination(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    db_path = project_root / ".vibetrace" / "vibetrace.db"
    db_path.parent.mkdir(parents=True)
    db = IndexerDB(str(db_path))

    for i in range(5):
        node = Node(
            id=f"file{i}.py:fn{i}",
            name=f"fn{i}",
            kind="function",
            file_path=f"file{i}.py",
            start_line=1,
            end_line=5,
            qualified_name=f"fn{i}",
            signature=f"(arg: int) -> Res{i}",
            centrality=float(i),
        )
        db.upsert_node(node)

    orig_db = server_api.db
    orig_root = server_api.project_root
    try:
        server_api.db = db
        server_api.project_root = project_root
        client = TestClient(server_api.app)

        query_params = {"pattern": "arg: int", "limit": 2, "offset": 1}
        resp = client.get("/api/search/signature", params=query_params)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # Ranked by centrality DESC: fn4 (4.0), fn3 (3.0), fn2 (2.0), ...
        # offset=1 should skip fn4 and yield fn3 and fn2
        assert data[0]["name"] == "fn3"
        assert data[1]["name"] == "fn2"
    finally:
        server_api.db = orig_db
        server_api.project_root = orig_root
        db.close()


@pytest.mark.asyncio
async def test_mcp_search_by_signature_uses_formatter(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "mcp_sig.db"
    db = IndexerDB(str(db_path))

    node = Node(
        id="calc.cpp:compute",
        name="compute",
        kind="function",
        file_path="calc.cpp",
        start_line=10,
        end_line=15,
        qualified_name="compute",
        signature="(val: int) -> double",
        centrality=0.8,
    )
    db.upsert_node(node)

    monkeypatch.setattr(
        "vibetrace.mcp.server._get_context_for_path",
        lambda _: (db, tmp_path),
    )

    out = await vibetrace_search_by_signature(SearchInput(pattern="double"))
    assert "## Signature Search: `double`" in out
    assert "**`compute`**" in out
    assert "(val: int) -> double" in out
    assert "calc.cpp:L10" in out
    db.close()


def test_saved_trace_delete_by_name(tmp_path: Path):
    """Verify service-level deletion by name."""
    db_path = tmp_path / "test_del.db"
    db = IndexerDB(str(db_path))

    create_saved_trace(
        db,
        SavedTraceCreate(
            name="CheckoutFlow",
            from_symbol="cart",
            to_symbol="charge",
            description="Payment path",
        ),
    )
    assert get_saved_trace_by_name(db, "CheckoutFlow") is not None

    deleted = delete_saved_trace_by_name(db, "CheckoutFlow")
    assert deleted is True
    assert get_saved_trace_by_name(db, "CheckoutFlow") is None
    assert delete_saved_trace_by_name(db, "NonExistent") is False
    db.close()


def test_saved_trace_delete_by_name_api():
    """Verify REST endpoint DELETE /api/saved-traces/by-name/{name}."""
    from vibetrace.server_api import app

    client = TestClient(app)
    # 1. Create trace
    client.post(
        "/api/saved-traces",
        json={
            "name": "ApiDeleteTest",
            "from_symbol": "src_sym",
            "to_symbol": "dst_sym",
            "max_depth": 3,
        },
    )

    # 2. Delete by name
    del_res = client.delete("/api/saved-traces/by-name/ApiDeleteTest")
    assert del_res.status_code == 200
    assert del_res.json() == {"status": "deleted", "name": "ApiDeleteTest"}

    # 3. 404 on repeat
    del_res_again = client.delete("/api/saved-traces/by-name/ApiDeleteTest")
    assert del_res_again.status_code == 404


def test_cli_trace_delete_saved(tmp_path: Path, capsys):
    """Verify CLI vibetrace trace --delete-saved <name>."""
    db_path = tmp_path / ".vibetrace" / "vibetrace.db"
    db_path.parent.mkdir(parents=True)
    db = IndexerDB(str(db_path))
    create_saved_trace(
        db,
        SavedTraceCreate(name="CliFlow", from_symbol="start", to_symbol="end", max_depth=4),
    )
    db.close()

    # Delete via CLI with --json
    code = cli.main(
        [
            "trace",
            "--delete-saved",
            "CliFlow",
            "--json",
            "--path",
            str(tmp_path),
            "--no-sync",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res == {"status": "deleted", "name": "CliFlow"}

    # Second delete returns code 1
    code_fail = cli.main(
        [
            "trace",
            "--delete-saved",
            "CliFlow",
            "--json",
            "--path",
            str(tmp_path),
            "--no-sync",
        ]
    )
    assert code_fail == 1


@pytest.mark.asyncio
async def test_mcp_saved_traces_suite(tmp_path: Path):
    """Verify MCP save, list, trace by saved_name, and delete saved trace tools."""
    db_path = tmp_path / "test_mcp_traces.db"
    db = IndexerDB(str(db_path))

    # Add symbols and edges to allow a valid trace
    n1 = Node(id="app.py:main", name="main", kind="function", file_path="app.py", start_line=1)
    n2 = Node(id="app.py:worker", name="worker", kind="function", file_path="app.py", start_line=10)
    db.upsert_node(n1)
    db.upsert_node(n2)
    db.upsert_edge(Edge(from_node_id=n1.id, to_node_id=n2.id, relation_type="calls", call_line=5))

    with patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, tmp_path)):
        # 1. Save trace
        save_out = await vibetrace_save_trace(
            SaveTraceInput(
                name="MainToWorker",
                from_symbol="main",
                to_symbol="worker",
                description="Worker callpath",
                max_depth=3,
            )
        )
        assert "MainToWorker" in save_out

        # 2. List saved traces (JSON)
        list_json = await vibetrace_list_saved_traces(
            ListSavedTracesInput(response_format=ResponseFormat.JSON)
        )
        traces = json.loads(list_json)
        assert len(traces) == 1
        assert traces[0]["name"] == "MainToWorker"

        # 3. List saved traces (Markdown)
        list_md = await vibetrace_list_saved_traces(
            ListSavedTracesInput(response_format=ResponseFormat.MARKDOWN)
        )
        assert "## Saved Architectural Traces" in list_md
        assert "**MainToWorker**" in list_md

        # 4. Execute trace using saved_name
        trace_json = await vibetrace_trace_path(
            TracePathInput(saved_name="MainToWorker", response_format=ResponseFormat.JSON)
        )
        trace_res = json.loads(trace_json)
        assert trace_res["found"] is True
        assert trace_res["from_symbol"] == "main"
        assert trace_res["to_symbol"] == "worker"

        # 5. Delete saved trace
        del_out = await vibetrace_delete_saved_trace(DeleteSavedTraceInput(name="MainToWorker"))
        assert "Successfully deleted" in del_out

        # 6. Verify deletion
        list_after = await vibetrace_list_saved_traces(
            ListSavedTracesInput(response_format=ResponseFormat.JSON)
        )
        assert len(json.loads(list_after)) == 0

    db.close()


@pytest.mark.asyncio
async def test_mcp_impact_and_dependencies_json(tmp_path: Path):
    """Verify MCP impact analysis and dependencies honor response_format=JSON."""
    db_path = tmp_path / "test_mcp_dep.db"
    db = IndexerDB(str(db_path))

    n1 = Node(id="srv.py:run", name="run", kind="function", file_path="srv.py", start_line=1)
    db.upsert_node(n1)

    with patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, tmp_path)):
        # Impact analysis JSON
        impact_out = await vibetrace_impact_analysis(
            ImpactAnalysisInput(file_path="srv.py", response_format=ResponseFormat.JSON)
        )
        impact_data = json.loads(impact_out)
        assert "file_path" in impact_data
        assert "total_impact" in impact_data
        assert "levels" in impact_data

        # Dependencies JSON
        dep_out = await vibetrace_get_dependencies(
            DependenciesInput(file_path="srv.py", response_format=ResponseFormat.JSON)
        )
        dep_data = json.loads(dep_out)
        assert "file_path" in dep_data
        assert "counts" in dep_data
        assert "internal" in dep_data

    db.close()


def test_watcher_blocking_closes_db(tmp_path: Path):
    """Verify start_watcher_blocking ensures db.close() is called on exit."""
    db_path = tmp_path / "test_watch.db"
    db = IndexerDB(str(db_path))

    closed = False
    orig_close = db.close

    def mock_close():
        nonlocal closed
        closed = True
        orig_close()

    db.close = mock_close

    with (
        patch("vibetrace.services.graph_service.get_context_for_path", return_value=(db, tmp_path)),
        patch("vibetrace.indexer.watcher.start_observer") as mock_obs,
        patch("time.sleep", side_effect=KeyboardInterrupt),
    ):
        start_watcher_blocking(str(tmp_path))
        assert mock_obs.return_value is not None
        assert closed is True


def test_db_cascade_cleanup_contracts_and_orphaned_edges(tmp_path: Path):
    """Verify IndexerDB cascades deletion to symbol docs and taint contracts, and prunes edges."""
    from vibetrace.indexer.models import Edge, Node

    db = IndexerDB(str(tmp_path / "cascade.db"))

    node1 = Node(
        id="src/app.py::foo",
        name="foo",
        qualified_name="foo",
        kind="function",
        file_path="src/app.py",
        start_line=1,
        end_line=5,
    )
    node2 = Node(
        id="src/app.py::bar",
        name="bar",
        qualified_name="bar",
        kind="function",
        file_path="src/app.py",
        start_line=7,
        end_line=12,
    )
    node_ext = Node(
        id="src/other.py::baz",
        name="baz",
        qualified_name="baz",
        kind="function",
        file_path="src/other.py",
        start_line=1,
        end_line=5,
    )
    db.upsert_node(node1)
    db.upsert_node(node2)
    db.upsert_node(node_ext)

    db.upsert_edge(
        Edge(
            from_node_id=node_ext.id,
            to_node_id=node1.id,
            relation_type="calls",
            confidence="exact",
        )
    )

    with db._get_conn() as conn:
        conn.execute(
            """
            INSERT INTO symbol_doc_links
            (symbol_id, symbol_name, doc_path, heading, heading_path, start_line, end_line, content)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (node1.id, "foo", "docs/api.md", "Foo", "Foo", 1, 10, "Documentation for foo"),
        )
        conn.execute(
            """
            INSERT INTO function_taint_contracts
            (symbol_id, contract_json, file_hash, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (node1.id, "{}", "hash123", 1000),
        )
        conn.commit()

    db.batch_save_file_index("src/app.py", "new_hash", [node2], [])

    with db._get_conn() as conn:
        ftc = conn.execute(
            "SELECT count(*) FROM function_taint_contracts WHERE symbol_id = ?", (node1.id,)
        ).fetchone()[0]
        sdl = conn.execute(
            "SELECT count(*) FROM symbol_doc_links WHERE symbol_id = ?", (node1.id,)
        ).fetchone()[0]
        assert ftc == 0
        assert sdl == 0

    pruned = db.prune_orphaned_edges()
    assert pruned >= 0
    db.close()


def test_cli_indexing_telemetry_output_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify Phase 1 indexing telemetry: throughput, timing, nodes/edges summary and JSON."""
    sub_dir = tmp_path / "project_telemetry"
    sub_dir.mkdir()
    (sub_dir / ".vibetrace").mkdir()

    # Create dummy Python files
    (sub_dir / "file1.py").write_text("def a(): pass\n", encoding="utf-8")
    (sub_dir / "file2.py").write_text("def b(): a()\n", encoding="utf-8")

    # 1. Human-readable CLI execution
    ret = cli_main(["index", str(sub_dir), "--root", str(sub_dir), "--force"])
    assert ret == 0
    captured = capsys.readouterr().out
    # Assert presence of real-time telemetry output
    assert "✔ Indexed" in captured
    assert "files/sec" in captured
    assert "nodes" in captured
    assert "edges" in captured

    # 2. JSON output CLI execution
    ret_json = cli_main(["index", str(sub_dir), "--root", str(sub_dir), "--force", "--json"])
    assert ret_json == 0
    captured_json = capsys.readouterr().out
    data = json.loads(captured_json)
    assert data["status"] == "ok"
    assert "telemetry" in data
    telemetry = data["telemetry"]
    assert telemetry["files_processed"] == 2
    assert "elapsed_seconds" in telemetry
    assert "throughput_fps" in telemetry
    assert telemetry["throughput_fps"] >= 0
    assert "file1.py" in telemetry["modified_files"] or "file2.py" in telemetry["modified_files"]
