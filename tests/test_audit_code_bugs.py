import os
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from vibetrace.cli_commands import cmd_mcp
from vibetrace.indexer.db import IndexerDB, Node
from vibetrace.indexer.parsers import ParserFactory
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.services.graph_service import get_context_for_path
from vibetrace.services.trace_service import read_file_lines, trace_execution_path


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
