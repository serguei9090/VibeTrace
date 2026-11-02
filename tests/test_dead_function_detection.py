"""Unit tests for Call-Graph Dead Function Detection (RSPEC-1144)."""

from __future__ import annotations

from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.quality.service import QualityService


def test_dead_private_function_detected_by_call_graph(tmp_path: Path) -> None:
    db = IndexerDB(db_path=":memory:")

    # Insert a called private function and an uncalled dead private function
    called_node = Node(
        id="node:src/app.py:_helper_called",
        name="_helper_called",
        qualified_name="_helper_called",
        file_path="src/app.py",
        kind="function",
        start_line=10,
        end_line=15,
        signature="def _helper_called():",
    )
    dead_node = Node(
        id="node:src/app.py:_helper_dead",
        name="_helper_dead",
        qualified_name="_helper_dead",
        file_path="src/app.py",
        kind="function",
        start_line=20,
        end_line=25,
        signature="def _helper_dead():",
    )
    caller_node = Node(
        id="node:src/app.py:main",
        name="main",
        qualified_name="main",
        file_path="src/app.py",
        kind="function",
        start_line=1,
        end_line=8,
        signature="def main():",
    )

    # Edge from main to _helper_called
    call_edge = Edge(
        from_node_id=caller_node.id,
        to_node_id=called_node.id,
        relation_type="calls",
        call_line=4,
    )

    db.batch_save_file_index(
        file_path="src/app.py",
        content_hash="mock_hash_123",
        nodes=[called_node, dead_node, caller_node],
        edges=[call_edge],
    )

    dead_funcs = db.get_uncalled_private_functions()
    assert len(dead_funcs) == 1
    assert dead_funcs[0]["name"] == "_helper_dead"

    # Verify QualityService.audit_project surfaces it as python:S1144
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    app_file = src_dir / "app.py"
    app_file.write_text("def main(): pass\n", encoding="utf-8")

    report = QualityService.audit_project(
        db=db,
        project_root=tmp_path,
        complexity_threshold=15,
        include_tests=False,
    )

    dead_findings = [f for f in report.findings if f.rule_id == "python:S1144"]
    assert len(dead_findings) == 1
    assert dead_findings[0].symbol_name == "_helper_dead"
    assert "Unused private function '_helper_dead'" in dead_findings[0].title
