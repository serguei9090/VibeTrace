import tempfile
from pathlib import Path

import pytest

from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.indexer.parsers import ParserFactory
from vibetrace.services.caller_guard import CallerGuardService
from vibetrace.services.hybrid_search import hybrid_search
from vibetrace.services.trace_service import trace_execution_path


@pytest.fixture
def benchmark_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    db = IndexerDB(db_path=db_path)

    # Populate synthetic nodes and call graph
    for i in range(100):
        db.upsert_node(
            Node(
                id=f"node_{i}",
                name=f"function_{i}",
                qualified_name=f"pkg.mod.function_{i}",
                kind="function",
                file_path=f"src/pkg/mod_{i % 5}.py",
                start_line=i * 10,
                end_line=i * 10 + 8,
                signature=f"(arg_{i}: int) -> bool",
                docstring=f"Performs domain transaction number {i}.",
            )
        )
    for i in range(99):
        db.upsert_edge(
            Edge(
                from_node_id=f"node_{i}",
                to_node_id=f"node_{i + 1}",
                relation_type="calls",
                call_line=i * 10 + 2,
            )
        )
    db.update_centrality_scores()
    yield db
    db.close()
    Path(db_path).unlink(missing_ok=True)


def test_benchmark_parser_extraction(benchmark):
    """Benchmark raw AST parsing and symbol extraction speed."""
    sample_file = Path("tests/fixtures/goldens/python_sample.py")
    code = sample_file.read_bytes()
    parser = ParserFactory.get_parser("python_sample.py")
    assert parser is not None

    def run_parse():
        return parser.extract("python_sample.py", code)

    result = benchmark(run_parse)
    nodes, _edges = result
    assert len(nodes) > 0


def test_benchmark_hybrid_search(benchmark, benchmark_db):
    """Benchmark hybrid search (AST + BM25 + Centrality) response time."""

    def run_search():
        return hybrid_search(benchmark_db, "transaction function_50", limit=10)

    results = benchmark(run_search)
    assert len(results) > 0


def test_benchmark_caller_guard(benchmark, benchmark_db):
    """Benchmark pre-flight caller guard blast radius calculation."""
    root_dir = Path(".").resolve()

    def run_guard():
        return CallerGuardService.get_caller_guard(benchmark_db, root_dir, "function_50")

    guard_report = benchmark(run_guard)
    assert guard_report.found is True


def test_benchmark_trace_path(benchmark, benchmark_db):
    """Benchmark bidirectional BFS execution tracepath discovery."""

    def run_trace():
        return trace_execution_path(benchmark_db, "function_10", "function_15")

    trace_result = benchmark(run_trace)
    assert trace_result.found is True
