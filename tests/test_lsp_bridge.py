"""Unit tests for LSP Hybrid Bridge and dynamic type resolution."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.indexer.lsp_bridge import (
    EXT_TO_LANGUAGE,
    LSP_SERVER_CANDIDATES,
    LSPClient,
    _discover_tsserver_path,
    _resolve_lsp_binary,
    _resolve_lsp_root,
    get_default_lsp_command,
    get_lsp_command_for_language,
    resolve_unresolved_edges,
)
from vibetrace.tools_manager import ToolsManager


def test_get_default_lsp_command() -> None:
    """Verify get_default_lsp_command detects binary or returns None without crashing."""
    cmd = get_default_lsp_command(Path.cwd())
    if cmd is not None:
        assert isinstance(cmd, list)
        assert len(cmd) >= 1


def test_lsp_client_lifecycle_missing_binary(tmp_path: Path) -> None:
    """Verify LSPClient fails gracefully when command is not found."""
    client = LSPClient(["non_existent_lsp_server_binary"], tmp_path)
    assert client.start() is False
    client.stop()


def test_resolve_unresolved_edges_with_mock_lsp(tmp_path: Path) -> None:
    """Verify unresolved call edges are rewired when LSP returns target definition."""
    db_path = str(tmp_path / "test_lsp.db")
    db = IndexerDB(db_path)

    # Node A in caller.py
    node_a = Node(
        id="caller_func_id",
        name="caller_func",
        qualified_name="caller_func",
        kind="function",
        file_path="caller.py",
        start_line=1,
        end_line=10,
    )
    # Node B in service.py (the actual definition)
    node_b = Node(
        id="target_service_id",
        name="actual_worker",
        qualified_name="actual_worker",
        kind="function",
        file_path="service.py",
        start_line=20,
        end_line=30,
    )
    db.upsert_node(node_a)
    db.upsert_node(node_b)

    # Edge from Node A to an unresolved phantom ID (e.g. from dynamic re-export)
    edge = Edge(
        from_node_id=node_a.id,
        to_node_id="phantom_unresolved_id",
        relation_type="calls",
        call_line=5,
    )
    db.upsert_edge(edge)

    # Verify edge currently points to phantom
    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT to_node_id FROM edges WHERE from_node_id = ?", (node_a.id,)
        ).fetchone()
        assert row["to_node_id"] == "phantom_unresolved_id"

    # Create dummy caller file on disk
    caller_file = tmp_path / "caller.py"
    caller_file.write_text("def caller_func():\n    actual_worker()\n")

    # Mock LSP client returning (service.py, line 25)
    mock_client = MagicMock(spec=LSPClient)
    mock_client.root_path = tmp_path
    mock_client.get_definition.return_value = ("service.py", 25)

    rewired = resolve_unresolved_edges(db, tmp_path, mock_client, verbose=False)
    assert rewired == 1

    # Verify edge has been rewired to node_b.id!
    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT to_node_id FROM edges WHERE from_node_id = ?", (node_a.id,)
        ).fetchone()
        assert row["to_node_id"] == node_b.id


def test_lsp_timeout_protection(tmp_path: Path) -> None:
    """Verify send_request returns None when response takes longer than timeout."""
    import time

    client = LSPClient(["mock_lsp"], tmp_path)
    client.process = MagicMock()
    client.process.stdin = MagicMock()
    client.process.stdout = MagicMock()

    def slow_read(req_id: int):
        time.sleep(0.3)
        return {"result": 42}

    client._read_response = slow_read  # type: ignore

    res = client.send_request("slow_method", {}, timeout=0.05)
    assert res is None
    client.stop()


def test_lsp_async_reader_matching(tmp_path: Path) -> None:
    """Verify asynchronous reader thread frames JSON-RPC responses and matches request IDs."""
    import io
    import json

    client = LSPClient(["mock_lsp"], tmp_path)
    mock_proc = MagicMock()
    mock_stdin = MagicMock()
    mock_proc.stdin = mock_stdin
    client.process = mock_proc

    # Prepare serialized response payload
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"key": "val"}}).encode("utf-8")
    frame = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

    # Deliver response when request is written to stdin
    def fake_write(data: bytes) -> int:
        mock_proc.stdout = io.BytesIO(frame)
        client._running = True
        client._reader_thread = threading.Thread(target=client._reader_loop, daemon=True)
        client._reader_thread.start()
        return len(data)

    mock_stdin.write.side_effect = fake_write

    res = client.send_request("test_method", {}, timeout=1.0)
    assert res == {"key": "val"}
    client.stop()


def test_lsp_definition_uri_formats(tmp_path: Path) -> None:
    """Verify get_definition handles both 2-slash and 3-slash file URIs."""
    client = LSPClient(["mock_lsp"], tmp_path)
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()

    # 1. Test 3-slash format
    uri_3 = target.resolve().as_uri()
    client.send_request = MagicMock(  # type: ignore
        return_value=[{"uri": uri_3, "range": {"start": {"line": 14, "character": 4}}}]
    )
    res = client.get_definition(tmp_path / "caller.py", 5)
    assert res is not None
    rel_path, line = res
    assert line == 15
    assert rel_path == "src/service.py"
    assert not rel_path.startswith("/")

    # 2. Test 2-slash format (e.g. file://C:/project/src/service.py where drive becomes netloc)
    if Path.cwd().drive:
        uri_2 = f"file://{target.resolve().as_posix()}"
        client.send_request = MagicMock(  # type: ignore
            return_value=[{"uri": uri_2, "range": {"start": {"line": 29, "character": 0}}}]
        )
        res_2 = client.get_definition(tmp_path / "caller.py", 10)
        assert res_2 is not None
        rel_path_2, line_2 = res_2
        assert line_2 == 30
        assert rel_path_2 == "src/service.py"
        assert not rel_path_2.startswith("/")


def test_lsp_stop_shutdown_order(tmp_path: Path) -> None:
    """Verify stop() sends shutdown request before exit notification per LSP 3.x spec."""
    client = LSPClient(["mock_lsp"], tmp_path)
    client.process = MagicMock()
    client.process.stdin = MagicMock()

    calls: list[str] = []

    def mock_send_req(method: str, params: dict, timeout: float = 1.0):
        calls.append(f"request:{method}")
        return {}

    def mock_send_notif(method: str, params: dict):
        calls.append(f"notification:{method}")

    client.send_request = mock_send_req  # type: ignore
    client.send_notification = mock_send_notif  # type: ignore

    client.stop()
    assert "request:shutdown" in calls
    assert "notification:exit" in calls
    assert calls.index("request:shutdown") < calls.index("notification:exit")


def test_resolve_unresolved_edges_incremental_filter(tmp_path: Path) -> None:
    """Verify modified_files strictly filters LSP queries to modified callers only."""
    db_path = str(tmp_path / "test_lsp_incr.db")
    db = IndexerDB(db_path)

    # 1. Setup Caller A (caller_a.py) and Caller B (caller_b.py)
    node_a = Node(
        id="func_a",
        name="func_a",
        qualified_name="func_a",
        kind="function",
        file_path="caller_a.py",
        start_line=1,
        end_line=5,
    )
    node_b = Node(
        id="func_b",
        name="func_b",
        qualified_name="func_b",
        kind="function",
        file_path="caller_b.py",
        start_line=1,
        end_line=5,
    )
    # Target definition in target.py
    node_target = Node(
        id="real_target",
        name="real_target",
        qualified_name="real_target",
        kind="function",
        file_path="target.py",
        start_line=10,
        end_line=15,
    )
    db.upsert_node(node_a)
    db.upsert_node(node_b)
    db.upsert_node(node_target)

    # 2 Unresolved edges: one from caller_a.py, one from caller_b.py
    edge_a = Edge(
        from_node_id="func_a",
        to_node_id="unknown_target_a",
        relation_type="calls",
        call_line=2,
    )
    edge_b = Edge(
        from_node_id="func_b",
        to_node_id="unknown_target_b",
        relation_type="calls",
        call_line=3,
    )
    db.upsert_edge(edge_a)
    db.upsert_edge(edge_b)

    # Create dummy files
    (tmp_path / "caller_a.py").write_text("def func_a():\n    real_target()\n", encoding="utf-8")
    (tmp_path / "caller_b.py").write_text("def func_b():\n    real_target()\n", encoding="utf-8")
    (tmp_path / "target.py").write_text(
        "# target file\n" * 9 + "def real_target():\n    pass\n", encoding="utf-8"
    )

    client = LSPClient(["mock_lsp"], tmp_path)
    client.get_definition = MagicMock(return_value=("target.py", 11))  # type: ignore

    # Test A: Restrict to only caller_a.py
    rewired = resolve_unresolved_edges(
        db,
        tmp_path,
        client,
        verbose=False,
        modified_files=["caller_a.py"],
    )
    assert rewired == 1
    # Only caller_a.py was queried
    assert client.get_definition.call_count == 1
    call_args = client.get_definition.call_args[0]
    assert call_args[0] == tmp_path / "caller_a.py"

    # Edge from func_a is now rewired to real_target
    with db._get_conn() as conn:
        row_a = conn.execute(
            "SELECT to_node_id FROM edges WHERE from_node_id = 'func_a'"
        ).fetchone()
        row_b = conn.execute(
            "SELECT to_node_id FROM edges WHERE from_node_id = 'func_b'"
        ).fetchone()
        assert row_a["to_node_id"] == "real_target"
        assert row_b["to_node_id"] == "unknown_target_b"  # untouched!


def test_lsp_daemon_manager_lifecycle(tmp_path: Path) -> None:
    """Verify LSPDaemonManager caches warm client and cleans up properly."""
    from vibetrace.indexer.lsp_bridge import LSPDaemonManager

    # Stop any leftover daemons
    LSPDaemonManager.stop_all()

    with (
        patch("vibetrace.indexer.lsp_bridge.get_default_lsp_command", return_value=["mock-lsp"]),
        patch(
            "vibetrace.indexer.lsp_bridge.get_lsp_command_for_language", return_value=["mock-lsp"]
        ),
        patch.object(LSPClient, "start", return_value=True),
    ):
        client1 = LSPDaemonManager.get_client(tmp_path)
        assert client1 is not None
        # Retrieve again: must return the identical cached instance
        client2 = LSPDaemonManager.get_client(tmp_path)
        assert client1 is client2

        # Clean up single client
        LSPDaemonManager.stop_client(tmp_path)
        assert len(LSPDaemonManager._instances) == 0


def test_lsp_client_definitions_batch_pipelined(tmp_path: Path) -> None:
    """Verify get_definitions_batch pipelines queries, handles responses, timeouts and chunking."""
    client = LSPClient(["mock-lsp"], tmp_path)
    mock_proc = MagicMock()
    mock_stdin = MagicMock()
    mock_proc.stdin = mock_stdin
    client.process = mock_proc

    with patch.object(client, "ensure_documents_opened_batch") as mock_open:
        queries = [(tmp_path / f"file_{i}.py", i + 1, 0) for i in range(5)]
        written_batches: list[bytes] = []

        def handle_write(data: bytes) -> int:
            written_batches.append(data)
            with client._lock:
                pending_ids = sorted(client._pending_requests.keys())

            for req_id in pending_ids:
                fut = client._pending_requests.get(req_id)
                if not fut or fut.done():
                    continue
                if req_id == 1:
                    target_file = tmp_path / "target_1.py"
                    loc = [
                        {
                            "uri": target_file.as_uri(),
                            "range": {"start": {"line": 10, "character": 0}},
                        }
                    ]
                    fut.set_result(loc)
                elif req_id == 2:
                    target_file = tmp_path / "target_2.py"
                    loc = [
                        {
                            "uri": target_file.as_uri(),
                            "range": {"start": {"line": 20, "character": 0}},
                        }
                    ]
                    fut.set_result(loc)
                elif req_id == 3:
                    fut.set_result([])  # Empty list: definition not found
                elif req_id == 4:
                    fut.set_result({"invalid": "object"})  # Malformed response
                elif req_id == 5:
                    pass  # Leave unresolved to trigger per_batch_timeout
            return len(data)

        mock_stdin.write.side_effect = handle_write

        # batch_size=2 will partition 5 queries into 3 chunks: [2, 2, 1]
        results = client.get_definitions_batch(queries, batch_size=2, per_batch_timeout=0.1)

        mock_open.assert_called_once()
        assert len(written_batches) == 3
        assert len(results) == 5
        assert results[0] == ("target_1.py", 11)
        assert results[1] == ("target_2.py", 21)
        assert results[2] is None
        assert results[3] is None
        assert results[4] is None

        # Verify no orphan futures leak
        with client._lock:
            assert len(client._pending_requests) == 0


def test_discover_tsserver_path_prefers_workspace_local(tmp_path: Path) -> None:
    """Verify _discover_tsserver_path finds workspace-local node_modules tsserver.js first."""
    tsserver_js = tmp_path / "node_modules" / "typescript" / "lib" / "tsserver.js"
    tsserver_js.parent.mkdir(parents=True)
    tsserver_js.write_text("// stub tsserver\n", encoding="utf-8")

    discovered = _discover_tsserver_path(tmp_path, tmp_path)
    assert discovered == str(tsserver_js)


def test_secondary_languages_registered_in_lsp_candidates() -> None:
    """Verify C, C++, C#, Ruby, and Java LSP servers and extensions are registered."""
    for lang in ("c", "cpp", "csharp", "ruby", "java"):
        candidates = LSP_SERVER_CANDIDATES.get(lang)
        assert candidates, f"Missing LSP candidates for {lang}"
        for binary, cmd in candidates:
            assert binary
            assert cmd
            assert cmd[0] == binary

    expected_ext = {
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".rb": "ruby",
        ".rake": "ruby",
        ".java": "java",
    }
    for ext, lang in expected_ext.items():
        assert EXT_TO_LANGUAGE.get(ext) == lang


def test_resolve_lsp_root_glob_and_literal_markers(tmp_path: Path) -> None:
    """Verify _resolve_lsp_root supports wildcard (.sln/.csproj/.gemspec) and nested markers."""
    cs_root = tmp_path / "cs_root"
    cs_root.mkdir()
    (cs_root / "Solution.sln").write_text("", encoding="utf-8")
    assert _resolve_lsp_root(cs_root, "csharp") == cs_root

    cs_nested = tmp_path / "cs_nested"
    (cs_nested / "App").mkdir(parents=True)
    (cs_nested / "App" / "App.csproj").write_text("<Project />", encoding="utf-8")
    assert _resolve_lsp_root(cs_nested, "csharp") == cs_nested / "App"

    rb_nested = tmp_path / "rb_nested"
    (rb_nested / "gem").mkdir(parents=True)
    (rb_nested / "gem" / "demo.gemspec").write_text("", encoding="utf-8")
    assert _resolve_lsp_root(rb_nested, "ruby") == rb_nested / "gem"

    cpp_nested = tmp_path / "cpp_nested"
    (cpp_nested / "native").mkdir(parents=True)
    (cpp_nested / "native" / "CMakeLists.txt").write_text("", encoding="utf-8")
    assert _resolve_lsp_root(cpp_nested, "cpp") == cpp_nested / "native"

    java_root = tmp_path / "java_root"
    java_root.mkdir()
    (java_root / "pom.xml").write_text("<project />", encoding="utf-8")
    assert _resolve_lsp_root(java_root, "java") == java_root


def test_resolve_lsp_binary_discovers_dotnet_and_ruby_tool_dirs(tmp_path: Path) -> None:
    """Verify _resolve_lsp_binary finds csharp-ls and ruby-lsp in tool dirs when mode='global'."""
    proj = tmp_path / "proj"
    proj.mkdir(parents=True)
    (proj / ".vibetrace.toml").write_text(
        '[lsp.servers.csharp]\nmode = "global"\npath = ""\n\n'
        '[lsp.servers.ruby]\nmode = "global"\npath = ""\n',
        encoding="utf-8",
    )

    fake_home = tmp_path / "home"
    dotnet_tools = fake_home / ".dotnet" / "tools"
    dotnet_tools.mkdir(parents=True)
    csharp_name = "csharp-ls.exe" if os.name == "nt" else "csharp-ls"
    csharp_bin = dotnet_tools / csharp_name
    csharp_bin.write_text("", encoding="utf-8")

    ruby_dir = tmp_path / "ruby" / "bin"
    ruby_dir.mkdir(parents=True)
    ruby_exe = ruby_dir / ("ruby.exe" if os.name == "nt" else "ruby")
    ruby_exe.write_text("", encoding="utf-8")
    ruby_lsp_name = "ruby-lsp.bat" if os.name == "nt" else "ruby-lsp"
    ruby_lsp_bin = ruby_dir / ruby_lsp_name
    ruby_lsp_bin.write_text("", encoding="utf-8")

    def fake_which(name: str) -> str | None:
        if name == "ruby":
            return str(ruby_exe)
        return None

    with (
        patch("vibetrace.indexer.lsp_bridge.Path.home", return_value=fake_home),
        patch("vibetrace.indexer.lsp_bridge.shutil.which", side_effect=fake_which),
    ):
        assert _resolve_lsp_binary("csharp-ls", project_root=proj) == str(csharp_bin)
        assert get_lsp_command_for_language("csharp", project_root=proj) == [str(csharp_bin)]
        assert _resolve_lsp_binary("ruby-lsp", project_root=proj) == str(ruby_lsp_bin)
        assert get_lsp_command_for_language("ruby", project_root=proj) == [str(ruby_lsp_bin)]


def test_tools_manager_install_lsp_secondary_ecosystems(tmp_path: Path) -> None:
    """Verify project-local pinned csharp-ls/ruby-lsp installs and global hints."""
    cs_rb = tmp_path / "cs_rb"
    cs_rb.mkdir()
    (cs_rb / "Demo.csproj").write_text("<Project />", encoding="utf-8")
    (cs_rb / "Gemfile").write_text('source "https://rubygems.org"\n', encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        calls.append(cmd)
        return MagicMock(returncode=0)

    def fake_which(name: str) -> str | None:
        if name in ("dotnet", "gem"):
            return f"/usr/bin/{name}"
        return None

    with (
        patch("vibetrace.indexer.lsp_bridge._resolve_lsp_binary", return_value=None),
        patch("vibetrace.tools_manager.shutil.which", side_effect=fake_which),
        patch("vibetrace.tools_manager.subprocess.run", side_effect=fake_run),
    ):
        ok, msg = ToolsManager.install("lsp", cs_rb)

    local_bin = str(cs_rb / ".vibetrace" / "bin")
    ruby_dir = str(cs_rb / ".vibetrace" / "tools" / "ruby")
    assert ok is True
    assert "csharp-ls@0.16.0" in msg
    assert "ruby-lsp@0.26.11" in msg
    assert [
        "/usr/bin/dotnet",
        "tool",
        "install",
        "csharp-ls",
        "--version",
        "0.16.0",
        "--tool-path",
        local_bin,
    ] in calls
    assert [
        "/usr/bin/gem",
        "install",
        "--no-document",
        "--install-dir",
        ruby_dir,
        "--bindir",
        local_bin,
        "ruby-lsp",
        "-v",
        "0.26.11",
    ] in calls
    assert all("-g" not in cmd for cmd in calls)

    cpp_only = tmp_path / "cpp_only"
    cpp_only.mkdir()
    (cpp_only / "CMakeLists.txt").write_text("", encoding="utf-8")
    with (
        patch("vibetrace.indexer.lsp_bridge._resolve_lsp_binary", return_value=None),
        patch("vibetrace.tools_manager.shutil.which", return_value=None),
    ):
        ok_cpp, msg_cpp = ToolsManager.install("lsp", cpp_only)

    assert ok_cpp is False
    assert "clangd" in msg_cpp


def test_resolve_lsp_binary_hybrid_local_and_global_modes(tmp_path: Path) -> None:
    """Verify local mode uses .vibetrace/bin while global mode supports PATH or explicit path."""
    proj = tmp_path / "hybrid"
    proj.mkdir()
    local_bin = proj / ".vibetrace" / "bin"
    local_bin.mkdir(parents=True)
    bin_name = "basedpyright-langserver.exe" if os.name == "nt" else "basedpyright-langserver"
    local_exe = local_bin / bin_name
    local_exe.write_text("", encoding="utf-8")

    global_exe = tmp_path / "global_tools" / bin_name
    global_exe.parent.mkdir(parents=True)
    global_exe.write_text("", encoding="utf-8")

    custom_exe = tmp_path / "custom" / "my-pyright.exe"
    custom_exe.parent.mkdir(parents=True)
    custom_exe.write_text("", encoding="utf-8")

    def fake_which(name: str) -> str | None:
        if name == "basedpyright-langserver":
            return str(global_exe)
        if name == "gopls":
            return str(tmp_path / "global_tools" / ("gopls.exe" if os.name == "nt" else "gopls"))
        return None

    # 1. Default mode="local": finds .vibetrace/bin binary, ignores PATH-only binary
    with patch("vibetrace.indexer.lsp_bridge.shutil.which", side_effect=fake_which):
        assert _resolve_lsp_binary("basedpyright-langserver", project_root=proj) == str(local_exe)
        assert _resolve_lsp_binary("gopls", project_root=proj, language="go") is None

    # 2. Explicit mode="global" + path="" resolves via PATH
    (proj / ".vibetrace.toml").write_text(
        '[lsp.servers.python]\nmode = "global"\npath = ""\n\n'
        '[lsp.servers.go]\nmode = "global"\npath = ""\n',
        encoding="utf-8",
    )
    with patch("vibetrace.indexer.lsp_bridge.shutil.which", side_effect=fake_which):
        assert _resolve_lsp_binary("basedpyright-langserver", project_root=proj) == str(global_exe)
        assert _resolve_lsp_binary("gopls", project_root=proj, language="go") is not None

    # 3. Explicit mode="global" + explicit path resolves strictly that binary
    (proj / ".vibetrace.toml").write_text(
        f'[lsp.servers.python]\nmode = "global"\npath = "{custom_exe.as_posix()}"\n',
        encoding="utf-8",
    )
    with patch("vibetrace.indexer.lsp_bridge.shutil.which", side_effect=fake_which):
        assert _resolve_lsp_binary("basedpyright-langserver", project_root=proj) == str(custom_exe)

    # 4. Explicit mode="global" + missing custom path returns None (does not silently fall back)
    missing_path = (tmp_path / "missing" / "pyright").as_posix()
    (proj / ".vibetrace.toml").write_text(
        f'[lsp.servers.python]\nmode = "global"\npath = "{missing_path}"\n',
        encoding="utf-8",
    )
    with patch("vibetrace.indexer.lsp_bridge.shutil.which", side_effect=fake_which):
        assert _resolve_lsp_binary("basedpyright-langserver", project_root=proj) is None


def test_tools_manager_install_lsp_primary_ecosystems_local_pinned(tmp_path: Path) -> None:
    """Verify Python/TS/Go/PHP LSP installs are project-local and version-pinned."""
    proj = tmp_path / "poly"
    proj.mkdir()
    (proj / "app.py").write_text("x = 1\n", encoding="utf-8")
    (proj / "app.ts").write_text("export const x = 1;\n", encoding="utf-8")
    (proj / "main.go").write_text("package main\n", encoding="utf-8")
    (proj / "index.php").write_text("<?php\n", encoding="utf-8")

    recorded: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(cmd: list[str], env: dict[str, str] | None = None, **_kwargs: object) -> MagicMock:
        recorded.append((cmd, env))
        return MagicMock(returncode=0)

    def fake_which(name: str) -> str | None:
        if name in ("uv", "npm", "go"):
            return f"/usr/bin/{name}"
        return None

    with (
        patch("vibetrace.indexer.lsp_bridge._resolve_lsp_binary", return_value=None),
        patch("vibetrace.tools_manager.shutil.which", side_effect=fake_which),
        patch("vibetrace.tools_manager.subprocess.run", side_effect=fake_run),
    ):
        ok, _msg = ToolsManager.install("lsp", proj)

    assert ok is True
    cmds = [c for c, _ in recorded]
    assert all("-g" not in c for c in cmds)

    uv_calls = [(c, e) for c, e in recorded if c[0] == "/usr/bin/uv"]
    assert len(uv_calls) == 1
    assert uv_calls[0][0] == ["/usr/bin/uv", "tool", "install", "basedpyright==1.40.2"]
    assert uv_calls[0][1] is not None
    assert uv_calls[0][1]["UV_TOOL_BIN_DIR"] == str(proj / ".vibetrace" / "bin")

    npm_cmds = [c for c, _ in recorded if c[0] == "/usr/bin/npm"]
    assert any(
        "typescript-language-server@6.1.0" in c and "typescript@5.9.3" in c for c in npm_cmds
    )
    assert any("intelephense@1.18.5" in c for c in npm_cmds)
    assert any(str(proj / ".vibetrace" / "tools" / "node") in c for c in npm_cmds)

    go_calls = [(c, e) for c, e in recorded if c[0] == "/usr/bin/go"]
    assert len(go_calls) == 1
    assert go_calls[0][0] == ["/usr/bin/go", "install", "golang.org/x/tools/gopls@v0.24.0"]
    assert go_calls[0][1] is not None
    assert go_calls[0][1]["GOBIN"] == str(proj / ".vibetrace" / "bin")


def test_tools_manager_install_lsp_skips_when_already_local_and_init_go_uses_root(
    tmp_path: Path,
) -> None:
    """Verify already-local LSPs skip reinstall and cmd_init Go check uses root path."""
    import argparse

    from vibetrace.cli_commands import cmd_init

    proj = tmp_path / "cached_poly"
    proj.mkdir()
    (proj / "app.py").write_text("x = 1\n", encoding="utf-8")
    (proj / "app.ts").write_text("export const x = 1;\n", encoding="utf-8")
    (proj / "main.go").write_text("package main\n", encoding="utf-8")
    (proj / "index.php").write_text("<?php\n", encoding="utf-8")

    local_bin = proj / ".vibetrace" / "bin"
    node_bin = proj / ".vibetrace" / "tools" / "node" / "node_modules" / ".bin"
    local_bin.mkdir(parents=True)
    node_bin.mkdir(parents=True)

    exe_ext = ".exe" if os.name == "nt" else ""
    cmd_ext = ".cmd" if os.name == "nt" else ""
    (local_bin / f"basedpyright-langserver{exe_ext}").write_text("", encoding="utf-8")
    (local_bin / f"gopls{exe_ext}").write_text("", encoding="utf-8")
    (node_bin / f"typescript-language-server{cmd_ext}").write_text("", encoding="utf-8")
    (node_bin / f"intelephense{cmd_ext}").write_text("", encoding="utf-8")

    with patch("vibetrace.tools_manager.subprocess.run") as spy_run:
        ok, msg = ToolsManager.install("lsp", proj)

    assert ok is True
    assert spy_run.call_count == 0
    assert "basedpyright/pyright" in msg
    assert "typescript-language-server" in msg
    assert "gopls" in msg
    assert "intelephense" in msg

    go_proj = tmp_path / "go_only"
    go_proj.mkdir()
    (go_proj / "go.mod").write_text("module demo\n", encoding="utf-8")
    captured_ns: list[argparse.Namespace] = []

    with (
        patch("vibetrace.cli_tools.cmd_tools", side_effect=lambda ns: captured_ns.append(ns) or 0),
        patch("vibetrace.indexer.lsp_bridge.get_lsp_command_for_language", return_value=None),
    ):
        ret = cmd_init(
            argparse.Namespace(
                path=str(go_proj),
                no_index=True,
                install_lsp=True,
                json=False,
            )
        )

    assert ret == 0
    assert len(captured_ns) == 1
    assert captured_ns[0].action == "install-lsp"
    assert Path(captured_ns[0].path).resolve() == go_proj.resolve()
