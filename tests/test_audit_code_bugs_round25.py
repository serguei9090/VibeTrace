"""Round 25 Audit regression tests.

Verifies:
1. DB path sanitization with redundant './' prefixes in DB operations.
2. Call tree location rendering without :L1 for external targets and scoped prefix fallback.
3. FastAPI generic @router.api_route multi-method extraction.
"""

from pathlib import Path

from vibetrace.indexer.db import IndexerDB, Node
from vibetrace.indexer.parsers.python_parser import PythonParser
from vibetrace.indexer.parsers.route_extractor import extract_python_routes
from vibetrace.services.call_tree_service import (
    CallTreeNode,
    CallTreeResult,
    _resolve_root_node,
    format_call_tree_md,
)


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
