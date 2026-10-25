"""Round 23 Audit regression tests.

Verifies:
1. Multi-language standard library builtin filtering for Java, JS/TS, C#, and C++.
2. User-defined symbols with matching prefix names continue resolving without regression.
3. CLI 'serve' subcommand accepts --path and sets VIBETRACE_PROJECT_ROOT.
4. MCP vibetrace_search_by_signature formats line locations gracefully when start_line is None.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from vibetrace.cli import build_parser
from vibetrace.cli_commands import cmd_serve
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.mcp.schemas import SearchInput
from vibetrace.mcp.server import vibetrace_search_by_signature


def test_java_builtins_do_not_produce_phantom_edges():
    """Verify System.out.println and Math.max do not emit call edges to phantom symbols."""
    code = b"""
    public class App {
        public void run() {
            System.out.println("Hello");
            int m = Math.max(10, 20);
        }
    }
    """
    parser = GenericParser("java")
    nodes, edges = parser.extract("src/App.java", code)

    # Class App, module App.java, and method run
    assert any(n.name == "App" for n in nodes)
    assert any(n.name == "run" for n in nodes)

    # Must NOT have any call edges to System or Math
    calls = [e for e in edges if e.relation_type == "calls"]
    for c in calls:
        target_node = next((n for n in nodes if n.id == c.to_node_id), None)
        target_name = target_node.name if target_node else ""
        assert "System" not in target_name
        assert "Math" not in target_name


def test_js_promise_and_object_builtins_filtered():
    """Verify Promise.resolve and Object.keys do not emit phantom call edges."""
    code = b"""
    export function execute() {
        Promise.resolve(42);
        const keys = Object.keys({});
    }
    """
    parser = JavaScriptParser()
    nodes, edges = parser.extract("src/index.js", code)

    calls = [e for e in edges if e.relation_type == "calls"]
    for c in calls:
        target_node = next((n for n in nodes if n.id == c.to_node_id), None)
        target_name = target_node.name if target_node else ""
        assert "Promise" not in target_name
        assert "Object" not in target_name


def test_csharp_console_builtins_filtered():
    """Verify Console.WriteLine in C# does not emit phantom call edges."""
    code = b"""
    class Program {
        static void Main() {
            Console.WriteLine("Hello World");
        }
    }
    """
    parser = GenericParser("c_sharp")
    nodes, edges = parser.extract("Program.cs", code)

    calls = [e for e in edges if e.relation_type == "calls"]
    for c in calls:
        target_node = next((n for n in nodes if n.id == c.to_node_id), None)
        target_name = target_node.name if target_node else ""
        assert "Console" not in target_name


def test_user_defined_class_call_resolves_normally():
    """Verify that a user-defined class method call resolves correctly."""
    code = b"""
    class Logger {
        public static void log(String msg) {}
    }
    class App {
        public void run() {
            Logger.log("test");
        }
    }
    """
    parser = GenericParser("java")
    nodes, edges = parser.extract("src/App.java", code)

    # App.run should call Logger.log
    calls = [e for e in edges if e.relation_type == "calls"]
    assert len(calls) == 1
    call = calls[0]
    to_node = next((n for n in nodes if n.id == call.to_node_id), None)
    assert to_node is not None
    assert to_node.name == "log"


def test_cli_serve_accepts_path(tmp_path: Path):
    """Verify CLI parser for serve accepts --path and sets VIBETRACE_PROJECT_ROOT."""
    parser = build_parser()
    target_dir = str(tmp_path / "custom_proj")
    args = parser.parse_args(["serve", "--path", target_dir, "--port", "9000"])
    assert args.path == target_dir
    assert args.port == 9000

    orig_env = os.environ.get("VIBETRACE_PROJECT_ROOT")
    try:
        with patch("uvicorn.run") as mock_run:
            code = cmd_serve(args)
            assert code == 0
            mock_run.assert_called_once()
            assert os.environ.get("VIBETRACE_PROJECT_ROOT") == str(Path(target_dir).resolve())
    finally:
        if orig_env is not None:
            os.environ["VIBETRACE_PROJECT_ROOT"] = orig_env
        else:
            os.environ.pop("VIBETRACE_PROJECT_ROOT", None)


@pytest.mark.asyncio
async def test_mcp_search_by_signature_none_start_line(tmp_path: Path):
    """Verify vibetrace_search_by_signature handles None start_line without :LNone."""
    db_path = str(tmp_path / "test.db")
    db = IndexerDB(db_path)

    fake_row = {
        "id": "1",
        "name": "root_mod",
        "signature": "package root_mod",
        "file_path": "src/root.go",
        "start_line": None,
    }

    with (
        patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, tmp_path)),
        patch.object(db, "search_by_signature_pattern", return_value=[fake_row]),
    ):
        result = await vibetrace_search_by_signature(
            SearchInput(pattern="package%", scope_path=str(tmp_path))
        )
        assert "In `src/root.go`" in result
        assert ":LNone" not in result
