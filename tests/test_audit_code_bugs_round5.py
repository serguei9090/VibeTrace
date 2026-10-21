"""Tests verifying fixes for Round 5 strict code bug critique audit."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.git_utils import get_git_delta
from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.indexer.parsers.go_parser import GoParser
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.services.graph_service import normalize_path
from vibetrace.services.hybrid_search import hybrid_search


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
