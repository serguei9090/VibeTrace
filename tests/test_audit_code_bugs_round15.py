"""Audit verification tests for Round 15 code critique fixes.

Covers:
1. JS/TS class & interface heritage (inherits & implements) and super.method() call resolution.
2. Go interface embedding (type_elem) emitting inherits edges.
3. db.get_symbol_references() includes inherits and implements alongside calls and references.
4. CLI subcommands (hooks, mcp, skill) respecting the --root path override.
"""

from __future__ import annotations

import os
from pathlib import Path

from vibetrace.cli import build_parser
from vibetrace.cli_commands import cmd_hooks, cmd_mcp, cmd_skill
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.parsers.go_parser import GoParser
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.parsers.python_parser import PythonParser


def test_jsts_class_and_interface_heritage_and_super_calls():
    """Ensure JS/TS classes & interfaces emit inherits/implements edges and super calls resolve."""
    parser = JavaScriptParser(language="typescript")
    code = b"""
class BaseService {
    start(): void {}
}

interface IRunnable {
    run(): void;
}

class WorkerService extends BaseService implements IRunnable {
    start(): void {
        super.start();
    }
    run(): void {}
}

interface ISuperWorker extends IRunnable {
    stop(): void;
}
"""
    nodes, edges = parser.extract("services.ts", code)
    node_map = {n.id: n.qualified_name for n in nodes}

    # Verify nodes exist
    assert any(n.qualified_name == "BaseService" for n in nodes)
    assert any(n.qualified_name == "WorkerService" for n in nodes)
    assert any(n.qualified_name == "IRunnable" for n in nodes)
    assert any(n.qualified_name == "ISuperWorker" for n in nodes)

    worker_node = next(n for n in nodes if n.qualified_name == "WorkerService")
    base_node = next(n for n in nodes if n.qualified_name == "BaseService")
    runnable_node = next(n for n in nodes if n.qualified_name == "IRunnable")
    super_worker_node = next(n for n in nodes if n.qualified_name == "ISuperWorker")
    base_start_node = next(n for n in nodes if n.qualified_name == "BaseService.start")
    worker_start_node = next(n for n in nodes if n.qualified_name == "WorkerService.start")

    # 1. WorkerService inherits BaseService
    inherits_edges = [
        e for e in edges if e.from_node_id == worker_node.id and e.relation_type == "inherits"
    ]
    assert inherits_edges, "Missing inherits edge for WorkerService -> BaseService"
    assert inherits_edges[0].to_node_id == base_node.id

    # 2. WorkerService implements IRunnable
    implements_edges = [
        e for e in edges if e.from_node_id == worker_node.id and e.relation_type == "implements"
    ]
    assert implements_edges, "Missing implements edge for WorkerService -> IRunnable"
    assert implements_edges[0].to_node_id == runnable_node.id

    # 3. ISuperWorker extends_type_clause inherits IRunnable
    iface_inherits = [
        e for e in edges if e.from_node_id == super_worker_node.id and e.relation_type == "inherits"
    ]
    assert iface_inherits, "Missing inherits edge for ISuperWorker -> IRunnable"
    assert iface_inherits[0].to_node_id == runnable_node.id

    # 4. WorkerService.start calls BaseService.start (not phantom super.start)
    super_calls = [
        e for e in edges if e.from_node_id == worker_start_node.id and e.relation_type == "calls"
    ]
    assert super_calls, "Missing calls edge from WorkerService.start"
    assert super_calls[0].to_node_id == base_start_node.id, (
        f"Expected call to {base_start_node.id} ({node_map.get(base_start_node.id)}), "
        f"got {super_calls[0].to_node_id} ({node_map.get(super_calls[0].to_node_id)})"
    )


def test_go_interface_embedding_emits_inherits():
    """Ensure Go interfaces embedding other interfaces emit inherits edges."""
    parser = GoParser()
    code = b"""package stream

type Reader interface {
    Read(p []byte) (n int, err error)
}

type ReadCloser interface {
    Reader
    Close() error
}
"""
    nodes, edges = parser.extract("stream.go", code)
    reader_node = next(n for n in nodes if n.qualified_name == "Reader")
    read_closer_node = next(n for n in nodes if n.qualified_name == "ReadCloser")

    inherits_edges = [
        e for e in edges if e.from_node_id == read_closer_node.id and e.relation_type == "inherits"
    ]
    assert inherits_edges, "Missing inherits edge for ReadCloser -> Reader"
    assert inherits_edges[0].to_node_id == reader_node.id


def test_get_symbol_references_includes_inherits_and_implements(tmp_path: Path):
    """Ensure get_symbol_references finds derived classes and interface implementations."""
    db = IndexerDB(db_path=str(tmp_path / "test.db"))
    code = b"""
class BaseHandler:
    def handle(self):
        pass

class CustomHandler(BaseHandler):
    def handle(self):
        super().handle()
"""
    parser = PythonParser()
    nodes, edges = parser.extract("handler.py", code)
    db.batch_save_file_index("handler.py", "hash1", nodes, edges)

    base_class_node = next(n for n in nodes if n.qualified_name == "BaseHandler")
    refs = db.get_symbol_references(base_class_node.id)

    # BaseHandler should be referenced by CustomHandler via inherits
    assert any(r["name"] == "CustomHandler" and r["relation_type"] == "inherits" for r in refs), (
        f"Expected inherits reference in {refs}"
    )

    base_method_node = next(n for n in nodes if n.qualified_name == "BaseHandler.handle")
    method_refs = db.get_symbol_references(base_method_node.id)
    assert any(r["name"] == "handle" and r["relation_type"] == "calls" for r in method_refs), (
        f"Expected calls reference in {method_refs}"
    )


def test_cli_subcommands_respect_root_override(monkeypatch, tmp_path: Path):
    """Ensure cmd_hooks, cmd_mcp, and cmd_skill respect the --root path override."""
    target_root = tmp_path / "custom_project"
    target_root.mkdir()
    (target_root / "src").mkdir()
    (target_root / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")

    parser = build_parser()

    # 1. cmd_hooks with --root
    args_hooks = parser.parse_args(["hooks", "status", "--root", str(target_root), "--json"])
    assert cmd_hooks(args_hooks) == 0

    # 2. cmd_skill with --root
    args_skill = parser.parse_args(["skill", "install", "--root", str(target_root), "--json"])
    assert cmd_skill(args_skill) == 0
    assert (target_root / ".agents" / "skills" / "vibetrace-navigator").is_dir()

    # 3. cmd_mcp with --root sets VIBETRACE_PROJECT_ROOT environment variable
    monkeypatch.setattr("vibetrace.mcp.server.main", lambda: None)
    args_mcp = parser.parse_args(["mcp", "--root", str(target_root)])
    cmd_mcp(args_mcp)
    assert os.environ.get("VIBETRACE_PROJECT_ROOT") == str(target_root.resolve())
