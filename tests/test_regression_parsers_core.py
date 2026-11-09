"""Consolidated regression test suite: test_regression_parsers_core.py."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vibetrace import cli, server_api
from vibetrace.cli import build_parser
from vibetrace.cli_commands import (
    cmd_call_tree,
    cmd_hooks,
    cmd_impact,
    cmd_mcp,
    cmd_search,
    cmd_serve,
    cmd_skill,
    cmd_slice,
    cmd_summary,
    cmd_trace,
)
from vibetrace.config import load_config, save_default_config
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.db import Node as DBNode
from vibetrace.indexer.git_utils import get_git_delta, get_git_sha
from vibetrace.indexer.hooks import _clean_lefthook_content, _inject_lefthook_hook
from vibetrace.indexer.lsp_bridge import LSPClient, resolve_unresolved_edges
from vibetrace.indexer.main import main as indexer_main
from vibetrace.indexer.main import reindex_all
from vibetrace.indexer.models import Edge, Node
from vibetrace.indexer.parsers import ParserFactory
from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.indexer.parsers.go_parser import GoParser
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.parsers.python_parser import PythonParser
from vibetrace.indexer.parsers.route_extractor import extract_python_routes
from vibetrace.indexer.parsers.rust_parser import RustParser
from vibetrace.indexer.parsers.ts_parser import TypeScriptParser
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.indexer.watcher import (
    CodeChangeHandler,
    start_observer,
    start_watcher_blocking,
    stop_observer,
)
from vibetrace.mcp.formatters import (
    format_code_slice_md,
    format_semantic_search_md,
    format_trace_path_md,
)
from vibetrace.mcp.schemas import (
    CallStackInput,
    ReferencesInput,
    ReindexInput,
    ResponseFormat,
    SearchInput,
    TraceDirection,
)
from vibetrace.mcp.server import (
    vibetrace_find_references,
    vibetrace_get_call_stack,
    vibetrace_reindex_project,
    vibetrace_search_by_signature,
)
from vibetrace.server_api import ConnectionManager, app
from vibetrace.services.call_tree_service import (
    CallTreeNode,
    CallTreeResult,
    _resolve_root_node,
    build_call_tree,
    format_call_tree_md,
)
from vibetrace.services.graph_service import (
    _LAST_SYNC_CHECK,
    CodeSliceResult,
    TraceHop,
    TraceNodeInfo,
    TracePathResult,
    categorize_file_dependencies,
    compute_impact_analysis,
    ensure_index_fresh,
    get_context_for_path,
    normalize_path,
)
from vibetrace.services.hybrid_search import hybrid_search
from vibetrace.services.saved_traces_service import (
    SavedTraceCreate,
    create_saved_trace,
    get_saved_trace_by_id,
)
from vibetrace.services.trace_service import get_code_slice, read_file_lines, trace_execution_path


def test_nested_method_closure_self_call_resolution():
    """Bug 1: Verify nested function inside a method resolves self.method to Class.method."""
    code = b"""
class Worker:
    def process(self):
        def inner_step():
            self.commit()
        inner_step()

    def commit(self):
        pass
"""
    parser = ParserFactory.get_parser("worker.py")
    assert parser is not None
    nodes, edges = parser.extract("worker.py", code)

    commit_node = next((n for n in nodes if n.name == "commit"), None)
    assert commit_node is not None
    assert commit_node.qualified_name == "Worker.commit"

    # Edge from inner_step -> Worker.commit
    call_edges = [e for e in edges if e.relation_type == "calls"]
    target_ids = {e.to_node_id for e in call_edges}
    msg = "self.commit() in nested closure must resolve to Worker.commit"
    assert commit_node.id in target_ids, msg


def test_python_relative_import_from_init():
    """Bug 2: Verify from . import foo resolves to __init__.py when foo is defined there."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        pkg = root / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("def helper(): pass\n", encoding="utf-8")
        consumer = pkg / "consumer.py"
        consumer.write_text("from . import helper\ndef run(): helper()\n", encoding="utf-8")

        resolver = ModuleResolver(root)
        parser = ParserFactory.get_parser("mypkg/consumer.py", resolver=resolver)
        assert parser is not None
        _, edges = parser.extract("mypkg/consumer.py", consumer.read_bytes())

        call_edges = [e for e in edges if e.relation_type == "calls"]
        assert len(call_edges) == 1
        expected_helper_id = parser._get_id("mypkg/__init__.py", "helper")
        assert call_edges[0].to_node_id == expected_helper_id


def test_cmd_mcp_sets_project_root_env():
    """Bug 3: Verify cmd_mcp sets VIBETRACE_PROJECT_ROOT before invoking server."""
    test_path = Path("/mock/project/path")
    args = Namespace(path=str(test_path))

    with patch.dict(os.environ, {}, clear=False):
        with patch("vibetrace.mcp.server.main") as mock_main:
            cmd_mcp(args)
            assert os.environ.get("VIBETRACE_PROJECT_ROOT") == str(test_path.resolve())
            mock_main.assert_called_once()


def test_get_context_finds_dot_vibetrace():
    """Bug 4: Verify get_context_for_path finds parent .vibetrace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        dot_vibe = root / ".vibetrace"
        dot_vibe.mkdir()
        (dot_vibe / "vibetrace.db").touch()
        subdir = root / "src" / "deep"
        subdir.mkdir(parents=True)

        db, found_root = get_context_for_path(str(subdir), auto_sync=False)
        assert found_root == root.resolve()
        assert Path(db.db_path).resolve() == (dot_vibe / "vibetrace.db").resolve()


def test_read_file_lines_leading_slash_safe():
    """Bug 5: Verify read_file_lines handles leading slashes without path traversal error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        test_file = root / "module.py"
        test_file.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")

        content, s, e = read_file_lines(root, "/module.py", 1, 2)
        assert "line 1" in content
        assert "line 2" in content
        assert s == 1
        assert e == 2


def test_cte_pathfinding_multi_candidates():
    """Bug 6: Verify CTE pathfinding searches across multiple symbol candidates."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        db = IndexerDB(db_path=db_path)

        # Candidate 1: Entry in file_a (isolated, no edges)
        node_entry_a = Node(
            id="entry_a",
            name="entry",
            qualified_name="entry",
            file_path="file_a.py",
            kind="function",
        )
        # Candidate 2: Entry in file_b (has edge to target)
        node_entry_b = Node(
            id="entry_b",
            name="entry",
            qualified_name="entry",
            file_path="file_b.py",
            kind="function",
        )
        node_target = Node(
            id="target_1",
            name="target",
            qualified_name="target",
            file_path="target.py",
            kind="function",
        )
        db.upsert_node(node_entry_a)
        db.upsert_node(node_entry_b)
        db.upsert_node(node_target)

        # Edge from entry_b -> target
        with db._get_conn() as conn:
            conn.execute(
                "INSERT INTO edges (from_node_id, to_node_id, relation_type) VALUES (?, ?, ?)",
                ("entry_b", "target_1", "calls"),
            )

        res = trace_execution_path(db, "entry", "target", max_depth=4, engine="cte")
        assert res.found is True
        assert res.node_ids == ["entry_b", "target_1"]


def test_go_generic_receiver_multi_type_params():
    """Verify generic receiver with multiple type params does not mangle struct name."""
    code = b"""package main

type Server[T any, K comparable] struct {}

func (s *Server[T, K]) Serve() string {
    return "ok"
}

func main() {
    srv := &Server[int, string]{}
    srv.Serve()
}
"""
    parser = GoParser()
    nodes, edges = parser.extract("server.go", code)

    node_map = {n.name: n for n in nodes}
    assert "Serve" in node_map
    serve_node = node_map["Serve"]
    # Qualified name must be Server.Serve, NOT K].Serve
    assert serve_node.qualified_name == "Server.Serve"
    expected_id = parser._get_id("server.go", "Server.Serve")
    assert serve_node.id == expected_id

    call_edges = [e for e in edges if e.relation_type == "calls"]
    target_ids = {e.to_node_id for e in call_edges}
    assert expected_id in target_ids


def test_go_new_factory_type_resolution():
    """Verify idiomatic redis.New() or pkg.New() does not produce trailing dot types."""
    code = b"""package main

import "example.com/redis"

func main() {
    client := redis.New()
    client.Get("key")
}
"""
    resolver = ModuleResolver(Path("."))
    parser = GoParser(resolver=resolver)
    _nodes, edges = parser.extract("main.go", code)

    call_edges = [e for e in edges if e.relation_type == "calls"]
    target_ids = {e.to_node_id for e in call_edges}
    # Should resolve to external Get (never main.go::redis..Get)
    expected_get_id = parser._get_id("external", "Get")
    assert expected_get_id in target_ids


def test_rust_self_associated_function_resolution():
    """Verify Rust Self::method() resolves to enclosing struct name."""
    code = b"""
struct ConnectionPool;

impl ConnectionPool {
    fn new() -> Self {
        ConnectionPool
    }

    fn helper() {}

    fn execute(&self) {
        Self::helper();
    }
}
"""
    parser = RustParser()
    _nodes, edges = parser.extract("src/pool.rs", code)

    call_edges = [e for e in edges if e.relation_type == "calls"]
    target_ids = {e.to_node_id for e in call_edges}
    # Must resolve to ConnectionPool.helper, not Self.helper
    expected_id = parser._get_id("src/pool.rs", "ConnectionPool.helper")
    assert expected_id in target_ids
    phantom_id = parser._get_id("src/pool.rs", "Self.helper")
    assert phantom_id not in target_ids


def test_js_export_default_identifier_visibility():
    """Verify `export default myHelper` marks node as exported and populates defined symbols."""
    code = b"""
const calculateTax = (amount) => {
    return amount * 0.2;
};

export default calculateTax;
"""
    parser = JavaScriptParser()
    nodes, _edges = parser.extract("tax.js", code)

    node_map = {n.name: n for n in nodes}
    assert "calculateTax" in node_map
    calc_node = node_map["calculateTax"]
    assert calc_node.visibility == "exported"


def test_java_new_object_creation_call_edge():
    """Verify Java `new Service()` emits calls/references edge to Service."""
    code = b"""
package com.example;

public class Controller {
    public Service createService() {
        return new Service();
    }
}
"""
    parser = GenericParser(language="java")
    _nodes, edges = parser.extract("Controller.java", code)

    call_edges = [e for e in edges if e.relation_type == "calls"]
    target_ids = {e.to_node_id for e in call_edges}
    expected_id = parser._get_id("Controller.java", "Service")
    assert expected_id in target_ids


def test_rust_crate_directory_module_resolution(tmp_path: Path):
    """Verify Rust `use crate::models::User` in nested file resolves to `src/models/mod.rs`."""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    models_dir = src_dir / "models"
    models_dir.mkdir()
    (models_dir / "mod.rs").write_text("pub struct User;", encoding="utf-8")

    services_dir = src_dir / "services"
    services_dir.mkdir()
    auth_file = services_dir / "auth.rs"
    auth_file.write_text("use crate::models::User;", encoding="utf-8")

    resolver = ModuleResolver(tmp_path)
    res = resolver.resolve("crate/models", "src/services/auth.rs")
    assert res is not None
    assert res == "src/models/mod.rs"


def test_get_context_for_path_respects_env_var(tmp_path: Path):
    """Verify get_context_for_path honors VIBETRACE_PROJECT_ROOT when root_override is None."""
    custom_root = tmp_path / "custom_project"
    custom_root.mkdir()
    (custom_root / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")

    old_env = os.environ.get("VIBETRACE_PROJECT_ROOT")
    try:
        os.environ["VIBETRACE_PROJECT_ROOT"] = str(custom_root)
        _, resolved_root = get_context_for_path(".", auto_sync=False)
        assert resolved_root.resolve() == custom_root.resolve()
    finally:
        if old_env is not None:
            os.environ["VIBETRACE_PROJECT_ROOT"] = old_env
        else:
            os.environ.pop("VIBETRACE_PROJECT_ROOT", None)


def test_cli_top_level_json_flag_precedence():
    """Verify top-level --json flag is retained when followed by subcommands."""
    eff_argv = ["--json", "search", "IndexerDB"]
    top_json = "--json" in eff_argv
    parser = build_parser()
    args = parser.parse_args(eff_argv)
    if top_json:
        args.json = True
    assert args.json is True


def test_formatters_omit_lnone_on_missing_lines():
    """Verify formatters do not output literal :LNone or :LNone-None."""
    hop = TraceHop(
        step_index=1,
        from_node=TraceNodeInfo(
            id="from_id",
            name="main",
            qualified_name="main",
            kind="function",
            file_path="main.py",
            start_line=10,
        ),
        to_node=TraceNodeInfo(
            id="to_id",
            name="os",
            qualified_name="os",
            kind="module",
            file_path="external/os.py",
            start_line=None,  # missing start_line
        ),
        relation_type="imports",
    )
    trace_res = TracePathResult(
        from_symbol="main",
        to_symbol="os",
        found=True,
        hops=[hop],
        paths=[[hop]],
        node_ids=["from_id", "to_id"],
    )
    md = format_trace_path_md(trace_res)
    assert ":LNone" not in md
    assert "(`external/os.py`)" in md

    slice_res = CodeSliceResult(
        found=True,
        node_id="sym1",
        name="mymod",
        qualified_name="mymod",
        kind="module",
        file_path="mymod.py",
        start_line=None,
        end_line=None,
        source_code="# empty module",
    )
    slice_md = format_code_slice_md(slice_res)
    assert ":LNone" not in slice_md
    assert "**Location**: `mymod.py`" in slice_md


def test_flask_tuple_and_multiline_methods():
    """Verify Flask routes with tuple methods and multiline decorators are extracted."""
    decorators = [
        """@app.route(
            "/api/items",
            methods=("GET", "POST")
        )"""
    ]
    handler_node = DBNode(
        id="h1",
        name="handle_items",
        qualified_name="handle_items",
        kind="function",
        file_path="app.py",
        start_line=15,
        end_line=25,
    )
    endpoints, edges = extract_python_routes(
        decorators,
        handler_node,
        "app.py",
        node_id_fn=lambda file, name, kind: f"{file}::{name}",
    )
    endpoint_names = {ep.name for ep in endpoints}
    assert "GET /api/items" in endpoint_names
    assert "POST /api/items" in endpoint_names
    assert len(edges) == 2


def test_js_options_and_head_route_methods():
    """Verify Express/Next options and head route methods are properly extracted."""
    code = b"""
app.options('/api/resource', handleOptions);
app.head('/api/resource', handleHead);
"""
    parser = JavaScriptParser()
    nodes, _ = parser.extract("routes.js", code)
    endpoints = [n.name for n in nodes if n.kind == "endpoint"]
    assert "OPTIONS /api/resource" in endpoints
    assert "HEAD /api/resource" in endpoints
    assert "GET /api/resource" not in endpoints


def test_normalize_path_relative_to_custom_project_root(tmp_path: Path):
    """Bug 1: Verify normalize_path resolves relative paths against project_root."""
    custom_root = tmp_path / "custom_root"
    custom_root.mkdir()
    target_rel = "src/nested/service.py"

    normalized = normalize_path(target_rel, custom_root)
    assert normalized == target_rel


def test_hybrid_search_graceful_fallback_on_embedding_error(tmp_path: Path):
    """Bug 2: Verify hybrid_search gracefully falls back to AST/FTS5 when embedding fails."""
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    # Insert a node to find via AST/FTS5
    from vibetrace.indexer.models import Node as DBNode

    db.upsert_node(
        DBNode(
            id="test::payment_worker",
            name="payment_worker",
            qualified_name="payment_worker",
            kind="function",
            file_path="src/worker.py",
            start_line=10,
            end_line=25,
            signature="def payment_worker(ctx): pass",
            docstring="Processes outbound payments",
        )
    )

    failing_engine = MagicMock()
    failing_engine.search_semantic.side_effect = RuntimeError(
        "Failed to load embedding model: Connection timed out"
    )

    hits = hybrid_search(db, "payment_worker", limit=10, embedding_engine=failing_engine)
    assert len(hits) > 0
    assert hits[0].name == "payment_worker"
    assert "semantic" not in hits[0].match_sources


def test_git_delta_handles_quoted_paths_with_spaces(tmp_path: Path):
    """Bug 3: Verify get_git_delta strips quotes from git diff on files with spaces."""
    file_with_space = tmp_path / "path with space" / "service.py"
    file_with_space.parent.mkdir(parents=True)
    file_with_space.write_text("print('hello')", encoding="utf-8")

    mock_diff = MagicMock()
    mock_diff.returncode = 0
    mock_diff.stdout = 'M\t"path with space/service.py"\n'

    mock_status = MagicMock()
    mock_status.returncode = 0
    mock_status.stdout = ""

    with patch("shutil.which", return_value="git"):
        with patch("subprocess.run", side_effect=[mock_diff, mock_status]):
            delta = get_git_delta(tmp_path, base_sha="a" * 40)
            assert delta is not None
            changed, deleted = delta
            assert "path with space/service.py" in changed
            assert "path with space/service.py" not in deleted


def test_cpp_pointer_method_call_normalization():
    """Bug 4: Verify C/C++ pointer access ptr->method() is normalized to resolve call target."""
    code = b"""
    class Calculator {
    public:
        int add(int a, int b) { return a + b; }
    };

    void process(Calculator* calc) {
        calc->add(1, 2);
    }
    """
    parser = GenericParser("cpp")
    _nodes, edges = parser.extract("src/calc.cpp", code)

    call_edges = [e for e in edges if e.relation_type == "calls"]
    assert len(call_edges) == 1
    expected_target = parser._get_id("src/calc.cpp", "Calculator.add")
    assert call_edges[0].to_node_id == expected_target


def test_go_grouped_parameter_type_resolution():
    """Bug 5a: Verify Go parser tracks all variables in grouped parameters (a, b Service)."""
    code = b"""
    package main

    type Worker struct{}
    func (w *Worker) Execute() {}

    func Handle(a, b *Worker) {
        b.Execute()
    }
    """
    parser = GoParser()
    _nodes, edges = parser.extract("main.go", code)

    call_edges = [e for e in edges if e.relation_type == "calls"]
    assert len(call_edges) == 1
    expected_exec_id = parser._get_id("main.go", "Worker.Execute")
    assert call_edges[0].to_node_id == expected_exec_id


def test_js_namespace_export_reexport(tmp_path: Path):
    """Bug 5b: Verify JS/TS parser extracts export * as ns from './mod' namespace exports."""
    utils_file = tmp_path / "utils.js"
    utils_file.write_text("export function helper() {}\n", encoding="utf-8")
    index_file = tmp_path / "index.js"
    index_file.write_text("export * as utils from './utils';\n", encoding="utf-8")

    resolver = ModuleResolver(tmp_path)
    parser = JavaScriptParser(resolver=resolver)
    nodes, edges = parser.extract("index.js", index_file.read_bytes())

    utils_nodes = [n for n in nodes if n.name == "utils" and n.kind == "variable"]
    assert len(utils_nodes) == 1
    assert utils_nodes[0].visibility == "exported"

    ref_edges = [
        e for e in edges if e.from_node_id == utils_nodes[0].id and e.relation_type == "references"
    ]
    assert len(ref_edges) == 1
    expected_target = parser._get_id("utils.js", "utils", kind="module")
    assert ref_edges[0].to_node_id == expected_target


def test_ts_as_expression_unwrapping_resolves_method_call():
    """Bug 1: Verify TypeScript parser unwraps as_expression to track variable types."""
    code = b"""
    interface IWorker {
        work(): void;
    }
    class ConcreteWorker implements IWorker {
        work(): void {}
    }

    function run() {
        const w1 = new ConcreteWorker() as IWorker;
        w1.work();

        const w2 = new ConcreteWorker() as any;
        w2.work();
    }
    """
    parser = TypeScriptParser("typescript")
    _nodes, edges = parser.extract("src/app.ts", code)

    call_edges = [e for e in edges if e.relation_type == "calls"]
    assert len(call_edges) == 2
    # w1 resolved via IWorker.work, w2 resolved via unwrapped ConcreteWorker.work
    target_ids = [e.to_node_id for e in call_edges]
    assert parser._get_id("src/app.ts", "IWorker.work") in target_ids
    assert parser._get_id("src/app.ts", "ConcreteWorker.work") in target_ids


def test_parser_factory_supplies_resolver_to_ruby_and_php():
    """Bug 2: Verify ParserFactory binds resolver to Ruby and PHP parsers."""
    mock_resolver = MagicMock(spec=ModuleResolver)

    rb_parser = ParserFactory.get_parser("lib/service.rb", resolver=mock_resolver)
    assert rb_parser is not None
    assert rb_parser.resolver == mock_resolver

    php_parser = ParserFactory.get_parser("src/index.php", resolver=mock_resolver)
    assert php_parser is not None
    assert php_parser.resolver == mock_resolver


def test_module_resolver_handles_path_aliases(tmp_path: Path):
    """Bug 3: Verify ModuleResolver resolves @/ and ~/ TypeScript path aliases."""
    btn_file = tmp_path / "src" / "components" / "ui" / "button.tsx"
    btn_file.parent.mkdir(parents=True)
    btn_file.write_text("export function Button() {}\n", encoding="utf-8")

    view_file = tmp_path / "src" / "views" / "home.tsx"
    view_file.parent.mkdir(parents=True)
    view_file.write_text("import { Button } from '@/components/ui/button';\n", encoding="utf-8")

    resolver = ModuleResolver(tmp_path)

    # Test @/ alias
    resolved_at = resolver.resolve("@/components/ui/button", "src/views/home.tsx")
    assert resolved_at == "src/components/ui/button.tsx"

    # Test ~/ alias
    resolved_tilde = resolver.resolve("~/components/ui/button", "src/views/home.tsx")
    assert resolved_tilde == "src/components/ui/button.tsx"


def test_python_walrus_operator_assignment_type_resolution():
    """Bug 4: Verify Python parser tracks local types assigned via walrus operator (:=)."""
    code = b"""
    class Processor:
        def process(self):
            pass

    def execute():
        if (p := Processor()):
            p.process()
    """
    parser = PythonParser()
    _nodes, edges = parser.extract("src/proc.py", code)

    call_edges = [e for e in edges if e.relation_type == "calls"]
    expected_target = parser._get_id("src/proc.py", "Processor.process")
    assert any(e.to_node_id == expected_target for e in call_edges)


def test_indexer_entry_points_resolve_context_db(tmp_path: Path):
    """Bug 5: Verify indexer main and watcher use get_context_for_path to target project DB."""
    project_dir = tmp_path / "my_project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")

    mock_db = MagicMock()
    mock_db.db_path = str(project_dir / ".vibetrace" / "vibetrace.db")

    mock_ctx_patch = patch(
        "vibetrace.services.graph_service.get_context_for_path",
        return_value=(mock_db, project_dir),
    )
    with mock_ctx_patch as mock_ctx:
        with patch("vibetrace.indexer.main.reindex_all") as mock_reindex:
            with patch("sys.argv", ["indexer_main", str(project_dir)]):
                indexer_main()
                mock_ctx.assert_called_once_with(str(project_dir), auto_sync=False)
                mock_reindex.assert_called_once_with(
                    mock_db, str(project_dir), project_root=project_dir
                )

    with mock_ctx_patch as mock_ctx_w:
        with patch("vibetrace.indexer.watcher.start_observer") as mock_obs:
            with patch("time.sleep", side_effect=KeyboardInterrupt):
                start_watcher_blocking(str(project_dir))
                mock_ctx_w.assert_called_once_with(str(project_dir), auto_sync=False)
                mock_obs.assert_called_once_with(str(project_dir), mock_db)


def test_polyglot_stdlib_categorization() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        db = IndexerDB(db_path=db_path)

        # 1. Go file with fmt (stdlib) and gin (third_party)
        go_file = "server.go"
        go_nodes = [
            Node(
                id="g_main",
                name="main",
                file_path=go_file,
                kind="function",
                start_line=1,
                end_line=10,
            ),
            Node(
                id="g_fmt",
                name="fmt",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
            Node(
                id="g_gin",
                name="github.com/gin-gonic/gin",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
        ]
        go_edges = [
            Edge(from_node_id="g_main", to_node_id="g_fmt", relation_type="imports"),
            Edge(from_node_id="g_main", to_node_id="g_gin", relation_type="imports"),
        ]
        db.batch_save_file_index(go_file, "h_go", go_nodes, go_edges)

        _, go_third, go_std = categorize_file_dependencies(db, go_file)
        assert [d["name"] for d in go_std] == ["fmt"]
        assert [d["name"] for d in go_third] == ["github.com/gin-gonic/gin"]

        # 2. Node/JS file with node:fs (stdlib) and express (third_party)
        js_file = "index.js"
        js_nodes = [
            Node(
                id="j_app",
                name="app",
                file_path=js_file,
                kind="function",
                start_line=1,
                end_line=10,
            ),
            Node(
                id="j_fs",
                name="node:fs",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
            Node(
                id="j_exp",
                name="express",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
        ]
        js_edges = [
            Edge(from_node_id="j_app", to_node_id="j_fs", relation_type="imports"),
            Edge(from_node_id="j_app", to_node_id="j_exp", relation_type="imports"),
        ]
        db.batch_save_file_index(js_file, "h_js", js_nodes, js_edges)

        _, js_third, js_std = categorize_file_dependencies(db, js_file)
        assert [d["name"] for d in js_std] == ["node:fs"]
        assert [d["name"] for d in js_third] == ["express"]

        # 3. Ruby file with json (stdlib) and rails (third_party)
        rb_file = "app.rb"
        rb_nodes = [
            Node(
                id="r_app", name="App", file_path=rb_file, kind="class", start_line=1, end_line=10
            ),
            Node(
                id="r_json",
                name="json",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
            Node(
                id="r_rails",
                name="rails",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
        ]
        rb_edges = [
            Edge(from_node_id="r_app", to_node_id="r_json", relation_type="imports"),
            Edge(from_node_id="r_app", to_node_id="r_rails", relation_type="imports"),
        ]
        db.batch_save_file_index(rb_file, "h_rb", rb_nodes, rb_edges)

        _, rb_third, rb_std = categorize_file_dependencies(db, rb_file)
        assert [d["name"] for d in rb_std] == ["json"]
        assert [d["name"] for d in rb_third] == ["rails"]

    finally:
        Path(db_path).unlink(missing_ok=True)


def test_windows_backslash_db_queries() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        db = IndexerDB(db_path=db_path)
        canonical_path = "src/utils/db.py"
        windows_path = r"src\utils\db.py"

        nodes = [
            Node(
                id="n_query",
                name="execute_query",
                file_path=canonical_path,
                kind="function",
                start_line=10,
                end_line=25,
            ),
            Node(
                id="n_ext",
                name="sqlite3",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
        ]
        edges = [
            Edge(from_node_id="n_query", to_node_id="n_ext", relation_type="imports"),
        ]
        db.batch_save_file_index(canonical_path, "sha256_dummy", nodes, edges)

        # 1. get_file_structure with Windows backslash
        total, symbols = db.get_file_structure(windows_path)
        assert total == 1
        assert symbols[0]["name"] == "execute_query"

        # 2. get_nodes_in_file with Windows backslash
        file_nodes = db.get_nodes_in_file(windows_path)
        assert len(file_nodes) == 1
        assert file_nodes[0]["name"] == "execute_query"

        # 3. get_file_imports with Windows backslash
        imports = db.get_file_imports(windows_path)
        assert len(imports) == 1
        assert imports[0]["name"] == "sqlite3"

        # 4. find_nodes_by_name scoped with Windows backslash
        matched = db.find_nodes_by_name("execute_query", file_path=windows_path)
        assert len(matched) == 1
        assert matched[0]["id"] == "n_query"

        # 5. get_file_hash with Windows backslash
        h = db.get_file_hash(windows_path)
        assert h == "sha256_dummy"

        # 6. get_graph_data with Windows backslash
        g_data = db.get_graph_data(windows_path)
        assert len(g_data["nodes"]) == 1

        # 7. clear_file with Windows backslash
        db.clear_file(windows_path)
        assert len(db.get_nodes_in_file(canonical_path)) == 0

    finally:
        Path(db_path).unlink(missing_ok=True)


def test_commonjs_export_extraction() -> None:
    parser = ParserFactory.get_parser("lib/service.js")
    assert parser is not None

    source = b"""
    const internalHelper = (x) => x * 2;
    const processData = (items) => items.map(internalHelper);

    exports.calculate = (a, b) => a + b;
    module.exports.multiply = function(a, b) {
        return a * b;
    };

    module.exports = {
        processData,
        aliasHelper: internalHelper,
    };
    """

    nodes, _ = parser.extract("lib/service.js", source)
    nodes_by_name = {n.name: n for n in nodes}

    # calculate exported directly via exports.calculate
    assert "calculate" in nodes_by_name
    assert nodes_by_name["calculate"].visibility == "exported"

    # multiply exported directly via module.exports.multiply
    assert "multiply" in nodes_by_name
    assert nodes_by_name["multiply"].visibility == "exported"

    # processData and internalHelper promoted to exported via module.exports object
    assert "processData" in nodes_by_name
    assert nodes_by_name["processData"].visibility == "exported"
    assert "internalHelper" in nodes_by_name
    assert nodes_by_name["internalHelper"].visibility == "exported"


def test_ruby_ast_node_types_extraction() -> None:
    parser = ParserFactory.get_parser("app/models/user.rb")
    assert parser is not None

    source = b"""
    module Authentication
      class User < BaseRecord
        def initialize(name)
          @name = name
        end

        def self.find_by_name(name)
          User.new(name)
        end
      end
    end
    """

    nodes, edges = parser.extract("app/models/user.rb", source)
    names = {n.name for n in nodes}
    kinds = {n.name: n.kind for n in nodes}

    assert "Authentication" in names
    assert kinds["Authentication"] == "class"

    assert "User" in names
    assert kinds["User"] == "class"

    assert "initialize" in names
    assert kinds["initialize"] == "function"

    assert "find_by_name" in names
    assert kinds["find_by_name"] == "function"

    # User inherits BaseRecord
    inherits_edges = [e for e in edges if e.relation_type == "inherits"]
    assert len(inherits_edges) >= 1


def test_ruby_and_php_import_and_heritage() -> None:
    # 1. Ruby require & require_relative
    rb_parser = ParserFactory.get_parser("app.rb")
    assert rb_parser is not None
    rb_code = b"""
    require 'json'
    require_relative './helpers/auth'

    def run
      Auth.verify
    end
    """
    rb_nodes, rb_edges = rb_parser.extract("app.rb", rb_code)
    rb_imports = [n.name for n in rb_nodes if n.kind == "module" and n.name != "app.rb"]
    assert "json" in rb_imports
    assert "auth" in rb_imports
    assert any(e.relation_type == "imports" for e in rb_edges)

    # 2. PHP use and class heritage
    php_parser = ParserFactory.get_parser("UserController.php")
    assert php_parser is not None
    php_code = b"""
    <?php
    namespace App\\Controllers;

    use App\\Services\\PaymentService;
    use App\\Interfaces\\AuthInterface;

    class UserController extends BaseController implements AuthInterface {
        public function handlePayment() {
            PaymentService::charge(100);
        }
    }
    """
    php_nodes, php_edges = php_parser.extract("UserController.php", php_code)
    php_names = {n.name for n in php_nodes}
    assert "PaymentService" in php_names
    assert "UserController" in php_names
    assert "handlePayment" in php_names

    # Check inherits and implements
    relations = {(e.relation_type) for e in php_edges}
    assert "imports" in relations
    assert "inherits" in relations
    assert "implements" in relations


@pytest.fixture
def temp_project(tmp_path: Path):
    """Sets up a temporary indexed project for query testing."""
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    src_file = tmp_path / "app.py"
    src_file.write_text(
        "def helper():\n    return 42\n\ndef main():\n    return helper()\n",
        encoding="utf-8",
    )

    n_main = Node(
        id="app.py::main",
        name="main",
        qualified_name="app.main",
        kind="function",
        file_path="app.py",
        start_line=4,
        end_line=5,
    )
    n_helper = Node(
        id="app.py::helper",
        name="helper",
        qualified_name="app.helper",
        kind="function",
        file_path="app.py",
        start_line=1,
        end_line=2,
    )
    db.batch_save_file_index(
        "app.py",
        "hash123",
        [n_main, n_helper],
        [
            Edge(
                from_node_id="app.py::main",
                to_node_id="app.py::helper",
                relation_type="calls",
                call_line=5,
            )
        ],
    )
    return db, tmp_path


def test_symbol_query_with_trailing_parens_and_spaces(temp_project):
    """Verify tracepath, slice, and call-tree resolve symbols with trailing '()'."""
    db, root = temp_project

    # Tracepath resolution
    res_bfs = trace_execution_path(db, "main() ", "helper()", engine="bfs")
    assert res_bfs.found is True
    assert len(res_bfs.hops) == 1

    res_cte = trace_execution_path(db, "main()", " helper() ", engine="cte")
    assert res_cte.found is True

    # Code slice resolution
    slice_res = get_code_slice(db, root, "helper()")
    assert slice_res.found is True
    assert slice_res.name == "helper"
    assert "return 42" in slice_res.source_code

    # Call tree resolution
    tree_res = build_call_tree(db, "main()")
    assert tree_res.found is True
    assert tree_res.tree is not None
    assert tree_res.tree.name == "main"
    assert len(tree_res.tree.children) == 1
    assert tree_res.tree.children[0].name == "helper"


@pytest.mark.asyncio
async def test_mcp_symbol_parens_resilience(temp_project, monkeypatch):
    """Verify MCP tools handle symbol names with trailing '()'."""
    db, root = temp_project
    monkeypatch.setattr(
        "vibetrace.mcp.server._get_context_for_path",
        lambda _: (db, root),
    )

    # find_references
    ref_out = await vibetrace_find_references(
        ReferencesInput(symbol_name="helper()", scope_path=str(root))
    )
    assert "References to `helper()`" in ref_out
    assert "Used by `main` in `app.py`" in ref_out

    # get_call_stack
    stack_out = await vibetrace_get_call_stack(
        CallStackInput(node_name="main()", file_path="app.py", depth=2)
    )
    assert "Trace for `main`" in stack_out
    assert "helper" in stack_out


def test_go_var_declaration_unwrapping_and_multivar(tmp_path: Path):
    """Verify Go var_spec expressions and multi-var definitions unwrap properly."""
    go_code = """
package main

type Calculator struct{}

func (c *Calculator) Compute() int {
    return 42
}

func NewCalculator() *Calculator {
    return &Calculator{}
}

func Run() {
    var calc = NewCalculator()
    calc.Compute()

    var a, b Calculator
    a.Compute()
    b.Compute()

    var ptr = &Calculator{}
    ptr.Compute()
}
"""
    parser = GoParser(ModuleResolver(tmp_path))
    nodes, edges = parser.extract("main.go", go_code.encode("utf-8"))

    compute_node = next(n for n in nodes if n.name == "Compute")
    compute_calls = [
        e for e in edges if e.relation_type == "calls" and e.to_node_id == compute_node.id
    ]
    # All 4 calls (calc, a, b, ptr) should resolve to Calculator.Compute
    assert len(compute_calls) == 4


def test_module_resolver_get_module_name_fallbacks(tmp_path: Path):
    """Verify canonical module name resolution handles ./ prefixes and fallbacks."""
    resolver = ModuleResolver(tmp_path)

    # Normalization of ./ prefix
    assert resolver.get_module_name("./src/pkg/__init__.py") == "pkg"
    assert resolver.get_module_name("pkg/__init__.py") == "pkg"

    # Index files
    assert resolver.get_module_name("src/components/index.ts") == "components"
    assert resolver.get_module_name("rust_crate/src/mod.rs") == "rust_crate"

    # Regular files
    assert resolver.get_module_name("./src/utils/calc.py") == "utils.calc"
    assert resolver.get_module_name("lib/network/http.go") == "network.http"


def test_fastapi_saved_trace_by_id_endpoint(tmp_path: Path, monkeypatch):
    """Verify GET /api/saved-traces/{trace_id} retrieves or returns 404."""
    db_path = tmp_path / "saved.db"
    db = IndexerDB(str(db_path))
    monkeypatch.setattr("vibetrace.server_api.db", db)

    saved = create_saved_trace(
        db,
        SavedTraceCreate(
            name="auth_flow",
            description="Trace from login to token verification",
            from_symbol="login",
            to_symbol="verify_token",
            max_depth=5,
        ),
    )

    # Service layer check
    fetched = get_saved_trace_by_id(db, saved.id)
    assert fetched is not None
    assert fetched.name == "auth_flow"
    assert fetched.from_symbol == "login"
    assert get_saved_trace_by_id(db, 99999) is None

    # HTTP endpoint check
    client = TestClient(app)
    resp = client.get(f"/api/saved-traces/{saved.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == saved.id
    assert data["name"] == "auth_flow"
    assert data["from_symbol"] == "login"

    # Non-existent ID -> 404
    resp_404 = client.get("/api/saved-traces/99999")
    assert resp_404.status_code == 404


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


def test_global_flags_after_subcommand():
    p = build_parser()
    a = p.parse_args(["index", ".", "--root", "repo", "--no-sync"])
    assert a.root == "repo"
    assert a.no_sync is True
    a = p.parse_args(["serve", "--root", "repo", "--json"])
    assert a.root == "repo"
    assert a.json is True
    # Root-level placement must not be clobbered by subparser defaults.
    a = p.parse_args(["--root", "top", "--no-sync", "search", "q"])
    assert a.root == "top"
    assert a.no_sync is True
    assert p.parse_args(["search", "q"]).root is None


def test_search_ast_strips_trailing_parens(tmp_path: Path):
    db = IndexerDB(str(tmp_path / "s.db"))
    db.upsert_node(
        Node(
            id="a.py::calculate",
            name="calculate",
            qualified_name="calculate",
            kind="function",
            file_path="a.py",
            start_line=1,
            end_line=2,
        )
    )
    hits = db.search_ast_exact_or_prefix("calculate()")
    assert hits
    assert hits[0]["name"] == "calculate"
    assert hits[0]["match_tier"] == 0


def test_python_cls_method_resolution():
    code = b"""
class Factory:
    @classmethod
    def create(cls):
        return cls.build()

    @classmethod
    def build(cls):
        pass
"""
    parser = ParserFactory.get_parser("f.py")
    assert parser is not None
    nodes, edges = parser.extract("f.py", code)
    ids = {n.qualified_name: n.id for n in nodes}
    assert (ids["Factory.create"], ids["Factory.build"]) in {
        (e.from_node_id, e.to_node_id) for e in edges
    }


def test_connection_manager_disconnect_idempotent():
    mgr = ConnectionManager()
    ws = object()
    mgr.active_connections.add(ws)  # type: ignore[arg-type]
    mgr.disconnect(ws)  # type: ignore[arg-type]
    mgr.disconnect(ws)  # type: ignore[arg-type]
    assert not mgr.active_connections


def test_whitespace_only_docstring_does_not_crash_search_formatter(tmp_path: Path):
    db = IndexerDB(str(tmp_path / "doc.db"))
    parser = ParserFactory.get_parser("w.py")
    assert parser is not None
    nodes, edges = parser.extract("w.py", b'def blank():\n    """   \n  """\n    pass\n')
    db.batch_save_file_index("w.py", "h", nodes, edges)

    hits = hybrid_search(db, "blank")
    assert hits
    out = format_semantic_search_md("blank", hits)
    assert "blank" in out


async def test_mcp_reindex_project_dot_uses_project_root_not_cwd(monkeypatch, tmp_path: Path):
    proj_a = tmp_path / "A"
    proj_b = tmp_path / "B"
    proj_a.mkdir()
    proj_b.mkdir()
    (proj_a / "a_mod.py").write_text("def a(): pass\n", encoding="utf-8")
    (proj_b / "b_mod.py").write_text("def b(): pass\n", encoding="utf-8")

    monkeypatch.setenv("VIBETRACE_PROJECT_ROOT", str(proj_a))
    monkeypatch.chdir(proj_b)

    await vibetrace_reindex_project(ReindexInput(path=".", force=True))
    db = IndexerDB(str(proj_a / "vibetrace_context" / "vibetrace.db"))
    indexed = db.get_all_indexed_file_paths()
    assert "a_mod.py" in indexed
    assert not any("b_mod.py" in p for p in indexed)


async def test_call_stack_clears_visited_between_start_nodes(monkeypatch, tmp_path: Path):
    db = IndexerDB(str(tmp_path / "cs.db"))

    def mk(nid: str, name: str, fp: str) -> Node:
        return Node(
            id=nid,
            name=name,
            qualified_name=name,
            kind="function",
            file_path=fp,
            start_line=1,
            end_line=2,
        )

    for n in (
        mk("r1", "run", "a.py"),
        mk("r2", "run", "b.py"),
        mk("h", "helper", "h.py"),
        mk("deep", "deep", "h.py"),
    ):
        db.upsert_node(n)
    for src, dst in (("r1", "h"), ("r2", "h"), ("h", "deep")):
        db.upsert_edge(Edge(from_node_id=src, to_node_id=dst, relation_type="calls", call_line=1))

    monkeypatch.setattr(
        "vibetrace.mcp.server._get_context_for_path",
        lambda *_a, **_k: (db, tmp_path),
    )
    out = await vibetrace_get_call_stack(
        CallStackInput(node_name="run", direction=TraceDirection.DOWN, depth=3)
    )
    assert out.count("run > helper > deep") == 2
    assert "already traversed in another branch" not in out


def test_compute_impact_analysis_preserves_dot_directory_paths(tmp_path: Path):
    db = IndexerDB(str(tmp_path / "imp.db"))
    db.upsert_node(
        Node(
            id="g1",
            name="ci_helper",
            qualified_name="ci_helper",
            kind="function",
            file_path=".github/workflows/ci.py",
            start_line=1,
            end_line=2,
        )
    )
    db.upsert_node(
        Node(
            id="c1",
            name="caller",
            qualified_name="caller",
            kind="function",
            file_path="src/runner.py",
            start_line=1,
            end_line=2,
        )
    )
    db.upsert_edge(Edge(from_node_id="c1", to_node_id="g1", relation_type="calls", call_line=1))

    total, levels = compute_impact_analysis(db, ".github/workflows/ci.py")
    assert total == 1
    assert any("caller" in item for item in levels[1])


def test_cli_no_sync_flag_is_respected_by_command_handlers(monkeypatch, tmp_path: Path):
    """Ensure --no-sync flag propagates auto_sync=False to get_context_for_path."""
    sync_calls: list[bool] = []

    def mock_ensure_fresh(db, root, debounce_seconds=2.0, verbose=False):
        sync_calls.append(True)
        return False

    monkeypatch.setattr("vibetrace.services.graph_service.ensure_index_fresh", mock_ensure_fresh)

    dummy_file = tmp_path / "app.py"
    dummy_file.write_text("def run(): pass\n", encoding="utf-8")
    parser = build_parser()

    # 1. With --no-sync, ensure_index_fresh should never be called
    sync_calls.clear()
    args_no_sync = parser.parse_args(
        ["--no-sync", "search", "run", "--path", str(tmp_path), "--json"]
    )
    cmd_search(args_no_sync)
    assert not sync_calls, "cmd_search called ensure_index_fresh despite --no-sync"

    args_trace = parser.parse_args(
        ["--no-sync", "trace", "run", "other", "--path", str(tmp_path), "--json"]
    )
    cmd_trace(args_trace)
    assert not sync_calls, "cmd_trace called ensure_index_fresh despite --no-sync"

    args_slice = parser.parse_args(
        ["--no-sync", "slice", "run", "--file", str(dummy_file), "--json"]
    )
    cmd_slice(args_slice)
    assert not sync_calls, "cmd_slice called ensure_index_fresh despite --no-sync"

    args_tree = parser.parse_args(
        ["--no-sync", "call-tree", "run", "--path", str(tmp_path), "--json"]
    )
    cmd_call_tree(args_tree)
    assert not sync_calls, "cmd_call_tree called ensure_index_fresh despite --no-sync"

    args_impact = parser.parse_args(["--no-sync", "impact", str(dummy_file), "--json"])
    cmd_impact(args_impact)
    assert not sync_calls, "cmd_impact called ensure_index_fresh despite --no-sync"

    args_summary = parser.parse_args(["--no-sync", "summary", str(dummy_file), "--json"])
    cmd_summary(args_summary)
    assert not sync_calls, "cmd_summary called ensure_index_fresh despite --no-sync"

    # 2. Without --no-sync, auto_sync defaults to True and triggers sync
    sync_calls.clear()
    _LAST_SYNC_CHECK.clear()
    args_sync = parser.parse_args(["search", "run", "--path", str(tmp_path), "--json"])
    cmd_search(args_sync)
    assert len(sync_calls) == 1, (
        "cmd_search failed to call ensure_index_fresh when auto_sync was enabled"
    )


def test_normalize_path_in_subdirectories(tmp_path: Path):
    """Ensure normalize_path correctly computes relative paths when run in a subdirectory."""
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    f = sub / "mod.py"
    f.write_text("x = 1\n", encoding="utf-8")

    orig_cwd = os.getcwd()
    try:
        os.chdir(sub)
        norm = normalize_path("mod.py", tmp_path)
        assert norm == "src/pkg/mod.py", f"Expected 'src/pkg/mod.py', got '{norm}'"
    finally:
        os.chdir(orig_cwd)

    # Calling from project root should also produce 'src/pkg/mod.py'
    norm_from_root = normalize_path("src/pkg/mod.py", tmp_path)
    assert norm_from_root == "src/pkg/mod.py"


def test_non_git_workspace_sets_meta_and_avoids_repeated_reindexing(tmp_path: Path):
    """Ensure non-git directory indexes once, stores 'non-git' in meta, and skips repeated walks."""
    (tmp_path / "service.py").write_text("def ping(): return 'pong'\n", encoding="utf-8")
    db = IndexerDB(db_path=str(tmp_path / "test.db"))

    _LAST_SYNC_CHECK.clear()
    # Initial check: must perform full index
    indexed_first = ensure_index_fresh(db, tmp_path, debounce_seconds=0.0)
    assert indexed_first is True
    assert db.get_meta("last_indexed_git_sha") == "non-git"

    # Subsequent check: must skip reindex
    _LAST_SYNC_CHECK.clear()
    indexed_second = ensure_index_fresh(db, tmp_path, debounce_seconds=0.0)
    assert indexed_second is False


def test_python_super_method_calls_resolve_to_base_class_method():
    """Ensure super().method() calls resolve to Base.method instead of phantom node IDs."""
    parser = PythonParser()
    code = b"""
class Base:
    def run(self):
        return 1

class Child(Base):
    def run(self):
        return super().run()
"""
    nodes, edges = parser.extract("worker.py", code)
    node_map = {n.id: n.qualified_name for n in nodes}

    # Verify Base.run and Child.run nodes exist
    assert any(n.qualified_name == "Base.run" for n in nodes)
    assert any(n.qualified_name == "Child.run" for n in nodes)

    # Find calls edges originating from Child.run
    child_run_node = next(n for n in nodes if n.qualified_name == "Child.run")
    base_run_node = next(n for n in nodes if n.qualified_name == "Base.run")

    call_edges = [
        e for e in edges if e.from_node_id == child_run_node.id and e.relation_type == "calls"
    ]
    assert call_edges, "No calls edges found for Child.run"
    assert call_edges[0].to_node_id == base_run_node.id, (
        f"Expected call to {base_run_node.id} ({node_map.get(base_run_node.id)}), "
        f"got {call_edges[0].to_node_id} ({node_map.get(call_edges[0].to_node_id)})"
    )


def test_python_super_cross_module_method_resolution(tmp_path: Path):
    """Ensure super().method() resolves cross-file when base class is imported."""
    (tmp_path / "base.py").write_text("class Base:\n    def start(self): pass\n", encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    child_code = b"""
from base import Base

class Child(Base):
    def start(self):
        super().start()
"""
    parser = PythonParser(resolver=resolver)
    nodes, edges = parser.extract("child.py", child_code)

    child_start = next(n for n in nodes if n.qualified_name == "Child.start")
    calls = [e for e in edges if e.from_node_id == child_start.id and e.relation_type == "calls"]
    assert calls, "Missing call edge from Child.start"
    # Target should be base.py::Base.start
    expected_target_id = parser._get_id("base.py", "Base.start")
    assert calls[0].to_node_id == expected_target_id


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
    orig_env = os.environ.get("VIBETRACE_PROJECT_ROOT")
    try:
        monkeypatch.setattr("vibetrace.mcp.server.main", lambda: None)
        args_mcp = parser.parse_args(["mcp", "--root", str(target_root)])
        cmd_mcp(args_mcp)
        assert os.environ.get("VIBETRACE_PROJECT_ROOT") == str(target_root.resolve())
    finally:
        if orig_env is not None:
            os.environ["VIBETRACE_PROJECT_ROOT"] = orig_env
        else:
            os.environ.pop("VIBETRACE_PROJECT_ROOT", None)


def test_git_diff_copy_and_status_copy(tmp_path: Path):
    """Verify git diff and git status copy statuses properly detect new copy destinations."""
    # Create fake files on disk so verification passes
    (tmp_path / "orig.py").write_text("orig = 1\n", encoding="utf-8")
    (tmp_path / "copy.py").write_text("orig = 1\n", encoding="utf-8")
    (tmp_path / "new_rename.py").write_text("new = 2\n", encoding="utf-8")

    # Mock diff output with a copy C100, and status with copy and rename
    diff_stdout = "C100\torig.py\tcopy.py\n"
    status_stdout = "C  orig.py -> copy.py\nR  old_name.py -> new_rename.py\n"

    def mock_run(cmd, **kwargs):
        mock_res = MagicMock()
        mock_res.returncode = 0
        if "diff" in cmd:
            mock_res.stdout = diff_stdout
        elif "status" in cmd:
            mock_res.stdout = status_stdout
        return mock_res

    with (
        patch("shutil.which", return_value="git"),
        patch("subprocess.run", side_effect=mock_run),
    ):
        delta = get_git_delta(tmp_path, base_sha="a" * 40)
        assert delta is not None
        changed, deleted = delta

        # copy.py and new_rename.py are on disk, so they must be in changed
        assert "copy.py" in changed
        assert "new_rename.py" in changed
        # orig.py is still on disk, so it should NOT be in deleted
        assert "orig.py" not in deleted
        # old_name.py does not exist on disk, so it should be in deleted
        assert "old_name.py" in deleted


def test_call_tree_line_zero_sorting():
    """Verify callee calls at line 0 sort before lines 1..N and are not treated as line 999999."""
    mock_db = MagicMock()
    mock_db.find_nodes_by_name.return_value = [
        {
            "id": "root_id",
            "name": "main",
            "qualified_name": "main",
            "kind": "function",
            "file_path": "main.py",
            "start_line": 1,
        }
    ]
    # Return two callees: one at line 0, one at line 10
    mock_db.get_neighbors.return_value = [
        {
            "neighbor_id": "late_id",
            "relation_type": "calls",
            "call_line": 10,
            "name": "later_fn",
            "qualified_name": "later_fn",
            "file_path": "main.py",
            "start_line": 20,
        },
        {
            "neighbor_id": "early_id",
            "relation_type": "calls",
            "call_line": 0,
            "name": "early_fn",
            "qualified_name": "early_fn",
            "file_path": "main.py",
            "start_line": 5,
        },
    ]

    res = build_call_tree(mock_db, "main")
    assert res.found
    assert res.tree is not None
    assert len(res.tree.children) == 2
    # early_fn with call_line=0 must be sorted FIRST
    assert res.tree.children[0].name == "early_fn"
    assert res.tree.children[0].call_line == 0
    assert res.tree.children[1].name == "later_fn"
    assert res.tree.children[1].call_line == 10


def test_js_export_default_anonymous_function_named_from_stem():
    """Verify export default function in components/Card.tsx is named Card."""
    parser = JavaScriptParser()
    nodes, _ = parser.extract(
        "components/Card.tsx",
        b"export default function() { return 1; }",
    )
    names = {n.name: n for n in nodes}
    assert "Card" in names
    card_node = names["Card"]
    assert card_node.kind == "function"
    assert card_node.visibility == "exported"


def test_js_export_default_anonymous_arrow_named_from_stem():
    """Verify export default in Header/index.tsx resolves stem from parent folder."""
    parser = JavaScriptParser()
    nodes, _ = parser.extract(
        "views/Header/index.tsx",
        b"export default () => 42;",
    )
    names = {n.name: n for n in nodes}
    assert "Header" in names
    header_node = names["Header"]
    assert header_node.kind == "function"
    assert header_node.visibility == "exported"


def test_js_export_default_anonymous_class_and_backend_route_defines():
    """Verify export default class creates class node and backend route creates defines edge."""
    parser = JavaScriptParser()
    code = (
        b"export default class {\n"
        b"    render() { return 1; }\n"
        b"}\n"
        b"function setup(app) {\n"
        b'    app.get("/api/items", handleItems);\n'
        b"}\n"
        b"function handleItems() {}\n"
    )
    nodes, edges = parser.extract("components/App.tsx", code)
    names = {n.name: n for n in nodes}

    assert "App" in names
    assert names["App"].kind == "class"
    assert names["App"].visibility == "exported"

    # Verify route edge from endpoint to handleItems AND defines edge from setup to endpoint
    route_edges = [e for e in edges if e.relation_type == "routes"]
    defines_edges = [e for e in edges if e.relation_type == "defines"]
    assert len(route_edges) >= 1
    # One defines edge should connect setup function to the endpoint
    endpoint_nodes = [n for n in nodes if n.kind == "endpoint"]
    assert len(endpoint_nodes) == 1
    ep_id = endpoint_nodes[0].id
    assert any(e.to_node_id == ep_id and e.relation_type == "defines" for e in defines_edges)


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


def test_db_path_sanitization_leading_dot_slash(tmp_path: Path):
    """Verify IndexerDB normalizes ./ prefixes for clear_file, get_file_hash, etc."""
    db_path = str(tmp_path / "test.db")
    db = IndexerDB(db_path)

    node = Node(
        id="n1",
        name="calculate",
        qualified_name="calculate",
        kind="function",
        file_path="src/math.py",
        start_line=1,
        end_line=5,
    )
    # Save with normalized path
    db.batch_save_file_index("src/math.py", "hash123", [node], [])

    # get_file_hash with ./ should match
    assert db.get_file_hash("./src/math.py") == "hash123"

    # find_nodes_by_name with ./ should match
    nodes = db.find_nodes_by_name("calculate", file_path="./src/math.py")
    assert len(nodes) == 1
    assert nodes[0]["name"] == "calculate"

    # clear_file with ./ should successfully delete nodes and hash
    db.clear_file("./src/math.py")
    assert db.get_file_hash("src/math.py") is None
    assert len(db.find_nodes_by_name("calculate")) == 0


def test_call_tree_external_node_formatting():
    """Verify format_call_tree_md formats external nodes as (external) without :L1."""
    root_node = CallTreeNode(
        id="r1",
        name="main",
        qualified_name="app.main",
        kind="function",
        file_path="src/main.py",
        start_line=10,
        end_line=20,
    )
    ext_child = CallTreeNode(
        id="c1",
        name="external_lib_func",
        qualified_name="external_lib_func",
        kind="function",
        file_path="external",
        start_line=None,
        relation_type="calls",
        call_line=15,
        depth=1,
    )
    root_node.children.append(ext_child)

    res = CallTreeResult(
        root_symbol="main",
        found=True,
        max_depth=2,
        tree=root_node,
        total_nodes=2,
        node_ids=["r1", "c1"],
    )
    output = format_call_tree_md(res)
    assert "(external)" in output
    assert "(external:L1)" not in output


def test_call_tree_resolve_root_scopes_prefix_by_file(tmp_path: Path):
    """Verify _resolve_root_node prioritizes prefix matches from specified file_path."""
    db_path = str(tmp_path / "test.db")
    db = IndexerDB(db_path)

    node_a = Node(
        id="a1",
        name="compute_engine",
        qualified_name="pkg.compute_engine",
        kind="function",
        file_path="src/pkg/engine.py",
    )
    node_b = Node(
        id="b1",
        name="compute_helper",
        qualified_name="test.compute_helper",
        kind="function",
        file_path="tests/test_compute.py",
    )
    db.batch_save_file_index("src/pkg/engine.py", "h1", [node_a], [])
    db.batch_save_file_index("tests/test_compute.py", "h2", [node_b], [])

    # Resolving prefix "compute" scoped to tests/test_compute.py should pick node_b
    resolved = _resolve_root_node(db, "compute", file_path="./tests/test_compute.py")
    assert resolved is not None
    assert resolved["id"] == "b1"


def test_fastapi_generic_api_route_decorator_extraction():
    """Verify @router.api_route multi-method routes are parsed into Endpoint nodes."""
    handler = Node(
        id="h1",
        name="manage_items",
        qualified_name="manage_items",
        kind="function",
        file_path="src/api.py",
        start_line=25,
        end_line=35,
    )
    decorators = ['@router.api_route("/items", methods=["GET", "POST"])']
    endpoints, edges = extract_python_routes(
        decorators, handler, "src/api.py", lambda f, n, kind="endpoint": f"{kind}::{n}"
    )

    assert len(endpoints) == 2
    names = {e.name for e in endpoints}
    assert "GET /items" in names
    assert "POST /items" in names

    assert len(edges) == 2
    assert all(e.relation_type == "routes" for e in edges)
    assert all(e.to_node_id == "h1" for e in edges)


def test_python_parser_e2e_api_route():
    """Verify PythonParser extracts routes from @router.api_route decorators end-to-end."""
    code = b"""
from fastapi import APIRouter

router = APIRouter(prefix="/v1")

@router.api_route("/data", methods=["GET", "DELETE"])
def handle_data():
    pass
"""
    parser = PythonParser()
    nodes, _edges = parser.extract("src/routes.py", code)

    endpoint_names = {n.name for n in nodes if n.kind == "endpoint"}
    assert "GET /v1/data" in endpoint_names
    assert "DELETE /v1/data" in endpoint_names


def test_lsp_bridge_decoupled_resolution_and_clean_path(tmp_path: Path):
    """Verify LSP rewires unresolved edges using clean paths without transaction lock."""
    db = IndexerDB(str(tmp_path / "test.db"))
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True)

    caller_file = project_root / "src" / "caller.py"
    caller_file.parent.mkdir(parents=True)
    caller_file.write_text("from helper import do_work\ndo_work()\n", encoding="utf-8")

    target_file = project_root / "src" / "helper.py"
    target_file.write_text("def do_work():\n    return 42\n", encoding="utf-8")

    caller_node = Node(
        id="caller_mod",
        name="caller",
        qualified_name="caller",
        kind="module",
        file_path="src/caller.py",
        start_line=1,
        end_line=2,
    )
    target_node = Node(
        id="helper_do_work",
        name="do_work",
        qualified_name="helper.do_work",
        kind="function",
        file_path="src/helper.py",
        start_line=1,
        end_line=2,
    )
    db.upsert_node(caller_node)
    db.upsert_node(target_node)

    # Edge pointing to missing/unresolved symbol
    unresolved_edge = Edge(
        from_node_id="caller_mod",
        to_node_id="missing_symbol",
        relation_type="calls",
        call_line=2,
    )
    db.upsert_edge(unresolved_edge)

    # Mock LSP client returning un-normalized path with leading ./ and backslash
    mock_lsp = MagicMock(spec=LSPClient)
    mock_lsp.get_definition.return_value = (".\\src\\helper.py", 1)

    rewired = resolve_unresolved_edges(db, project_root, mock_lsp, verbose=False)
    assert rewired == 1

    # Verify edge target was rewired to helper_do_work
    with db._get_conn() as conn:
        q = "SELECT to_node_id FROM edges WHERE from_node_id = 'caller_mod'"
        row = conn.execute(q).fetchone()
        assert row is not None
        assert row["to_node_id"] == "helper_do_work"


def test_watcher_ignores_unsupported_files_and_cleans_timers(tmp_path: Path):
    """Verify watcher does not schedule post-sync for non-code files and stop() cancels timer."""
    db = IndexerDB(str(tmp_path / "test.db"))
    sync_called = []

    handler = CodeChangeHandler(
        db,
        on_change=lambda: sync_called.append(True),
        root_path=str(tmp_path),
        debounce_seconds=0.05,
    )

    # Mock non-code file event
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("hello notes", encoding="utf-8")

    event = MagicMock()
    event.is_directory = False
    event.src_path = str(txt_file)

    handler.on_modified(event)
    assert handler._debounce_timer is None
    assert len(sync_called) == 0

    handler.on_created(event)
    assert handler._debounce_timer is None
    assert "notes.txt" not in handler.resolver.known_files

    handler.on_deleted(event)
    assert handler._debounce_timer is None

    # Test stop() cancels active timer
    handler._debounce_timer = MagicMock()
    handler.stop()
    assert handler._debounce_timer is None


def test_cli_trace_depth_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify CLI --depth explicitly overrides saved trace max_depth."""
    db = IndexerDB(str(tmp_path / "test.db"))
    create_saved_trace(
        db,
        SavedTraceCreate(
            name="auth_flow",
            from_symbol="login",
            to_symbol="verify_jwt",
            max_depth=4,
        ),
    )

    captured_depth = []

    def mock_trace_exec(db_arg, from_s, to_s, max_depth=6, engine="bfs"):
        captured_depth.append(max_depth)
        res = MagicMock()
        res.found = False
        res.model_dump_json.return_value = "{}"
        return res

    monkeypatch.setattr("vibetrace.cli_commands.trace_execution_path", mock_trace_exec)
    monkeypatch.setattr(
        "vibetrace.cli_commands.get_context_for_path", lambda *a, **kw: (db, tmp_path)
    )

    # Case 1: --saved without --depth uses saved.max_depth (4)
    args1 = argparse.Namespace(
        path=str(tmp_path),
        from_symbol=None,
        to_symbol=None,
        saved="auth_flow",
        save_as=None,
        depth=None,
        list_saved=False,
        engine="bfs",
        json=True,
    )
    cmd_trace(args1)
    assert captured_depth[-1] == 4

    # Case 2: --saved with explicit --depth 15 respects user override (15)
    args2 = argparse.Namespace(
        path=str(tmp_path),
        from_symbol=None,
        to_symbol=None,
        saved="auth_flow",
        save_as=None,
        depth=15,
        list_saved=False,
        engine="bfs",
        json=True,
    )
    cmd_trace(args2)
    assert captured_depth[-1] == 15


def test_js_parser_line_ceiling_and_endpoint_extraction(tmp_path: Path):
    """Verify js_parser.py adheres to <= 600 LOC buffer ceiling and extracts routes."""
    parser_path = Path("src/vibetrace/indexer/parsers/js_parser.py")
    line_count = len(parser_path.read_text(encoding="utf-8").splitlines())
    assert line_count <= 600, f"js_parser.py has {line_count} lines; must be <= 600"

    code = b"""
    const express = require('express');
    const app = express();

    function getUser(req, res) {
        res.send("user");
    }

    app.get('/api/users', getUser);
    """
    resolver = ModuleResolver(tmp_path)
    parser = JavaScriptParser(resolver=resolver)
    nodes, edges = parser.extract("server.js", code)

    endpoint_nodes = [n for n in nodes if n.kind == "endpoint"]
    assert any(n.name == "GET /api/users" for n in endpoint_nodes)

    route_edges = [e for e in edges if e.relation_type == "routes"]
    assert len(route_edges) >= 1


def test_stop_observer_cancels_debounce_timer(tmp_path: Path):
    """Verify stop_observer cancels in-flight debounce timer and stops observer."""
    db = IndexerDB(str(tmp_path / "test.db"))
    observer = start_observer(str(tmp_path), db)
    assert hasattr(observer, "_event_handler")

    # Simulate pending timer
    mock_timer = MagicMock()
    observer._event_handler._debounce_timer = mock_timer

    stop_observer(observer)
    mock_timer.cancel.assert_called_once()
    assert observer._event_handler._debounce_timer is None


def test_server_api_call_tree_normalizes_file_path(tmp_path: Path, monkeypatch):
    """Verify /call-tree endpoint normalizes file_path parameter before building tree."""
    from vibetrace import server_api

    captured_file_path = []

    def mock_build_call_tree(db, symbol, max_depth=3, relations=None, file_path=None):
        captured_file_path.append(file_path)
        res = MagicMock()
        res.model_dump.return_value = {"root_symbol": symbol, "found": True}
        return res

    monkeypatch.setattr(server_api, "build_call_tree", mock_build_call_tree)
    monkeypatch.setattr(server_api, "project_root", tmp_path)

    client = TestClient(app)
    # Pass Windows-style relative path with leading ./ and backslash
    res = client.get("/call-tree?symbol=worker&file_path=.\\src\\worker.py")
    assert res.status_code == 200
    assert len(captured_file_path) == 1
    assert captured_file_path[0] == "src/worker.py"


def test_go_parser_embedded_struct_with_tags(tmp_path: Path):
    """Verify Go struct embedding with struct tags correctly creates inherits edge."""
    code = b"""package main

type BaseService struct {
    ID string
}

type UserService struct {
    *BaseService `json:"-"`
    Name string
}
"""
    resolver = ModuleResolver(tmp_path)
    parser = GoParser(resolver=resolver)
    nodes, edges = parser.extract("main.go", code)

    user_node = next((n for n in nodes if n.name == "UserService"), None)
    base_node = next((n for n in nodes if n.name == "BaseService"), None)
    assert user_node is not None
    assert base_node is not None

    inherits_edges = [
        e for e in edges if e.from_node_id == user_node.id and e.relation_type == "inherits"
    ]
    assert len(inherits_edges) == 1
    assert inherits_edges[0].to_node_id == base_node.id


def test_config_discovery_file_path_defense(tmp_path: Path):
    """Verify load_config and save_default_config handle file paths safely."""
    dummy_file = tmp_path / "src" / "main.py"
    dummy_file.parent.mkdir(parents=True)
    dummy_file.write_text("print('hello')", encoding="utf-8")

    # load_config with a file path should use parent directory
    cfg = load_config(dummy_file)
    assert cfg.project.name == "src"

    # save_default_config with a file path should create .vibetrace.toml in parent
    saved = save_default_config(dummy_file, project_name="my_service")
    assert saved == tmp_path / "src" / ".vibetrace.toml"
    assert saved.is_file()


def test_lefthook_injection_and_clean_keys():
    """Verify lefthook injection and cleaning avoids duplicate keys."""
    raw_content = """pre-commit:
  commands:
    lint:
      run: ruff check .

post-commit:
  commands:
"""
    injected = _inject_lefthook_hook(raw_content, "post-commit")
    assert "vibetrace-sync:" in injected
    assert injected.count("post-commit:") == 1

    cleaned = _clean_lefthook_content(injected)
    assert "vibetrace-sync" not in cleaned


def test_python_parser_preserves_return_type():
    code = b"def calculate(total: int) -> float:\n    return float(total)\n"
    nodes, _ = PythonParser().extract("calc.py", code)
    func_node = next(n for n in nodes if n.kind == "function")
    assert func_node.signature == "(total: int) -> float"


def test_typescript_parser_preserves_return_type():
    code = b"function fetchData(id: string): Promise<string> { return Promise.resolve(id); }"
    nodes, _ = JavaScriptParser(language="typescript").extract("fetch.ts", code)
    func_node = next(n for n in nodes if n.kind == "function")
    assert func_node.signature == "(id: string): Promise<string>"


def test_go_parser_preserves_return_type():
    code = b"package main\nfunc Execute(ctx Context) (string, error) { return ctx, nil }"
    nodes, _ = GoParser().extract("main.go", code)
    func_node = next(n for n in nodes if n.kind == "function")
    assert func_node.signature == "(ctx Context) (string, error)"


def test_rust_parser_preserves_return_type():
    code = b"fn process(val: u32) -> Result<String, ()> { Ok(val.to_string()) }"
    nodes, _ = RustParser().extract("lib.rs", code)
    func_node = next(n for n in nodes if n.kind == "function")
    assert func_node.signature == "(val: u32) -> Result<String, ()>"


def test_cte_trace_deterministic_edge_prioritization(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    # Insert two nodes: Caller and Callee
    caller = Node(
        id="file.py:caller",
        name="caller",
        kind="function",
        file_path="file.py",
        start_line=1,
        end_line=5,
        qualified_name="caller",
    )
    callee = Node(
        id="file.py:callee",
        name="callee",
        kind="function",
        file_path="file.py",
        start_line=10,
        end_line=15,
        qualified_name="callee",
    )
    db.upsert_node(caller)
    db.upsert_node(callee)

    # Insert multiple edges between caller and callee in order: references first, then calls
    with db._get_conn() as conn:
        conn.execute(
            "INSERT INTO edges (from_node_id, to_node_id, relation_type, call_line) "
            "VALUES ('file.py:caller', 'file.py:callee', 'references', 2)"
        )
        conn.execute(
            "INSERT INTO edges (from_node_id, to_node_id, relation_type, call_line) "
            "VALUES ('file.py:caller', 'file.py:callee', 'calls', 3)"
        )
        conn.commit()

    # Trace execution path using CTE engine
    result = trace_execution_path(db, from_symbol="caller", to_symbol="callee", engine="cte")
    assert result.found is True
    assert len(result.hops) == 1
    # 'calls' must take precedence over 'references' regardless of insertion order
    assert result.hops[0].relation_type == "calls"
    assert result.hops[0].call_line == 3
    db.close()


def test_api_reindex_rejects_path_traversal(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    secret_file = secret_dir / "keys.py"
    secret_file.write_text("API_KEY = 'secret'", encoding="utf-8")

    db_path = project_root / ".vibetrace" / "vibetrace.db"
    db_path.parent.mkdir(parents=True)
    db = IndexerDB(str(db_path))

    orig_db = server_api.db
    orig_root = server_api.project_root
    try:
        server_api.db = db
        server_api.project_root = project_root
        client = TestClient(server_api.app)

        # Attempt directory traversal outside project root
        resp = client.post("/api/reindex", json={"file_path": "../../secret/keys.py"})
        assert resp.status_code == 403
        assert "Access denied" in resp.json()["detail"]
    finally:
        server_api.db = orig_db
        server_api.project_root = orig_root
        db.close()


def test_signature_search_matches_return_types(tmp_path: Path):
    db_path = tmp_path / "test_sig.db"
    db = IndexerDB(str(db_path))

    node = Node(
        id="svc.py:run",
        name="run",
        kind="function",
        file_path="svc.py",
        start_line=1,
        end_line=10,
        qualified_name="run",
        signature="(timeout: int) -> dict[str, Any]",
    )
    db.upsert_node(node)

    # Search by return type keyword
    results = db.search_by_signature_pattern("dict[str, Any]")
    assert len(results) == 1
    assert results[0]["name"] == "run"

    # Search with arrow
    results_arrow = db.search_by_signature_pattern("-> dict")
    assert len(results_arrow) == 1
    assert results_arrow[0]["name"] == "run"
    db.close()


def test_generic_parser_cpp_pointer_declarator():
    """Verify C++ pointer/reference return types extract clean names and signatures."""
    parser = GenericParser("cpp")
    source = b"""
    int* get_data(int id) {
        return nullptr;
    }
    double &get_ref(float val) {
        return x;
    }
    """
    nodes, _ = parser.extract("src/storage.cpp", source)
    fn_nodes = [n for n in nodes if n.kind == "function"]
    assert len(fn_nodes) == 2

    data_fn = next(n for n in fn_nodes if n.name == "get_data")
    assert data_fn.name == "get_data"
    assert not data_fn.name.startswith("*")
    assert data_fn.signature is not None
    assert "(int id)" in data_fn.signature

    ref_fn = next(n for n in fn_nodes if n.name == "get_ref")
    assert ref_fn.name == "get_ref"
    assert not ref_fn.name.startswith("&")
    assert ref_fn.signature is not None
    assert "(float val)" in ref_fn.signature


def test_js_parser_single_param_arrow_function():
    """Verify JS/TS single-parameter arrow functions extract correct (x) signature."""
    parser = JavaScriptParser()
    source = b"""
    const doubleVal = x => x * 2;
    const multi = (a, b) => a * b;
    """
    nodes, _ = parser.extract("src/math.js", source)
    fn_nodes = [n for n in nodes if n.kind == "function"]
    assert len(fn_nodes) == 2

    double_fn = next(n for n in fn_nodes if n.name == "doubleVal")
    assert double_fn.signature == "(x)"

    multi_fn = next(n for n in fn_nodes if n.name == "multi")
    assert multi_fn.signature == "(a, b)"


@pytest.mark.asyncio
async def test_mcp_search_by_signature_pagination_and_json(tmp_path: Path):
    """Verify vibetrace_search_by_signature honors limit, offset, and JSON format."""
    db_path = tmp_path / "test_sig_mcp.db"
    db = IndexerDB(str(db_path))

    for i in range(4):
        node = Node(
            id=f"f{i}.py:fn{i}",
            name=f"fn{i}",
            kind="function",
            file_path=f"f{i}.py",
            start_line=1,
            end_line=5,
            signature="(val: int) -> int",
            centrality=float(i),
        )
        db.upsert_node(node)

    with patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, tmp_path)):
        # Ranking by centrality DESC: fn3 (3.0), fn2 (2.0), fn1 (1.0), fn0 (0.0)
        # offset=1, limit=2 -> fn2, fn1
        out = await vibetrace_search_by_signature(
            SearchInput(
                pattern="val: int",
                limit=2,
                offset=1,
                response_format=ResponseFormat.JSON,
            )
        )
        data = json.loads(out)
        assert len(data) == 2
        assert data[0]["name"] == "fn2"
        assert data[1]["name"] == "fn1"

    db.close()


@pytest.mark.asyncio
async def test_mcp_find_references_kind_and_json(tmp_path: Path):
    """Verify vibetrace_find_references supports kind filtering and JSON response format."""
    db_path = tmp_path / "test_refs_mcp.db"
    db = IndexerDB(str(db_path))

    target = Node(
        id="core.py:service",
        name="service",
        kind="function",
        file_path="core.py",
        start_line=10,
        end_line=20,
    )
    caller = Node(
        id="app.py:main",
        name="main",
        kind="function",
        file_path="app.py",
        start_line=1,
        end_line=5,
    )
    db.upsert_node(target)
    db.upsert_node(caller)

    from vibetrace.indexer.db import Edge

    db.upsert_edge(
        Edge(from_node_id=caller.id, to_node_id=target.id, relation_type="calls", call_line=3)
    )

    with patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, tmp_path)):
        out = await vibetrace_find_references(
            ReferencesInput(
                symbol_name="service",
                kind="calls",
                response_format=ResponseFormat.JSON,
            )
        )
        data = json.loads(out)
        assert data["found"] is True
        assert data["symbol"] == "service"
        assert data["total_references"] == 1
        assert data["targets"][0]["references"][0]["relation_type"] == "calls"

    db.close()


@pytest.mark.asyncio
async def test_mcp_call_stack_json(tmp_path: Path):
    """Verify vibetrace_get_call_stack supports response_format=JSON."""
    db_path = tmp_path / "test_stack_mcp.db"
    db = IndexerDB(str(db_path))

    n1 = Node(
        id="mod.py:entry",
        name="entry",
        kind="function",
        file_path="mod.py",
        start_line=1,
        end_line=5,
    )
    db.upsert_node(n1)

    with patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, tmp_path)):
        out = await vibetrace_get_call_stack(
            CallStackInput(
                node_name="entry",
                file_path="mod.py",
                direction=TraceDirection.BOTH,
                depth=2,
                response_format=ResponseFormat.JSON,
            )
        )
        data = json.loads(out)
        assert data["found"] is True
        assert data["symbol"] == "entry"
        assert data["direction"] == "both"
        assert len(data["nodes"]) == 1

    db.close()


def test_cli_search_sig_offset(tmp_path: Path, capsys):
    """Verify CLI search --sig respects --offset and --limit with --json."""
    db_path = tmp_path / ".vibetrace" / "vibetrace.db"
    db_path.parent.mkdir(parents=True)
    db = IndexerDB(str(db_path))

    for i in range(4):
        node = Node(
            id=f"cli{i}.py:run{i}",
            name=f"run{i}",
            kind="function",
            file_path=f"cli{i}.py",
            start_line=1,
            end_line=5,
            signature="() -> TaskResult",
            centrality=float(i),
        )
        db.upsert_node(node)
    db.close()

    ret = cli.main(
        [
            "search",
            "TaskResult",
            "--sig",
            "--limit",
            "2",
            "--offset",
            "1",
            "--json",
            "--path",
            str(tmp_path),
            "--no-sync",
        ]
    )
    assert ret == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert len(data) == 2
    # Centrality DESC: run3 (3.0), run2 (2.0), run1 (1.0), run0 (0.0)
    # offset=1, limit=2 -> run2, run1
    assert data[0]["name"] == "run2"
    assert data[1]["name"] == "run1"
