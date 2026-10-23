"""Round 19 regression tests: git copy delta, call-tree line sorting, and JS/TS default exports."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from vibetrace.indexer.git_utils import get_git_delta
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.services.call_tree_service import build_call_tree


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
