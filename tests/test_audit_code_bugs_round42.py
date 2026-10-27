"""Test suite for Round 42 audit fixes.

Verifies:
1. Python 3.12 PEP 695 type_alias_statement extraction into interface nodes.
2. Python Enum/IntEnum/StrEnum detection with member variant extraction as variable nodes.
3. Go const declarations and package-level var declarations extracted as variable nodes.
4. JS/TS route extraction for custom router variable names (e.g. authRouter, userRoutes).
5. SQLite get_nodes_missing_embeddings query deterministic ordering.
6. File line count constraints within the 500-600 buffer ceiling.
"""

from pathlib import Path

from vibetrace.indexer.db import IndexerDB, Node
from vibetrace.indexer.parsers.go_parser import GoParser
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.parsers.python_parser import PythonParser


def test_python_pep695_type_alias():
    parser = PythonParser()
    code = b"""
type Point = tuple[float, float]
type ListOrSet[T] = list[T] | set[T]
"""
    nodes, edges = parser.extract("geometry.py", code)
    node_map = {n.name: n for n in nodes}

    assert "Point" in node_map
    assert node_map["Point"].kind == "interface"
    assert "Point" in (node_map["Point"].signature or "")

    assert "ListOrSet" in node_map
    assert node_map["ListOrSet"].kind == "interface"

    defines_edges = [
        e for e in edges if e.relation_type == "defines" and e.to_node_id == node_map["Point"].id
    ]
    assert len(defines_edges) == 1
    assert defines_edges[0].from_node_id == node_map["geometry"].id


def test_python_enum_detection_and_variants():
    parser = PythonParser()
    code = b"""
from enum import Enum, IntEnum

class Color(Enum):
    RED = 'red'
    GREEN = 'green'

class Priority(IntEnum):
    HIGH = 1
    LOW = 2

def get_color():
    return Color.RED
"""
    nodes, edges = parser.extract("colors.py", code)
    node_map = {n.name: n for n in nodes}

    assert "Color" in node_map
    assert node_map["Color"].kind == "enum"

    assert "Priority" in node_map
    assert node_map["Priority"].kind == "enum"

    assert "RED" in node_map
    assert node_map["RED"].kind == "variable"
    assert node_map["RED"].qualified_name == "Color.RED"
    assert node_map["RED"].parent_id == node_map["Color"].id

    assert "HIGH" in node_map
    assert node_map["HIGH"].kind == "variable"
    assert node_map["HIGH"].qualified_name == "Priority.HIGH"
    assert node_map["HIGH"].parent_id == node_map["Priority"].id

    defines_red = [
        e
        for e in edges
        if e.from_node_id == node_map["Color"].id
        and e.to_node_id == node_map["RED"].id
        and e.relation_type == "defines"
    ]
    assert len(defines_red) == 1

    # Verify intra-file attribute references Color.RED
    color_refs = [
        e
        for e in edges
        if e.from_node_id == node_map["get_color"].id
        and e.to_node_id == node_map["RED"].id
        and e.relation_type == "references"
    ]
    assert len(color_refs) == 1


def test_go_const_and_top_level_var_extraction():
    parser = GoParser()
    code = b"""
package main

const (
    StatusOK = 200
    statusNotFound = 404
)
const MaxRetries = 3

var ErrNotFound = 1
var internalCounter = 0

func Worker() {
    localVar := 5
}
"""
    nodes, edges = parser.extract("server.go", code)
    node_map = {n.name: n for n in nodes}

    assert "StatusOK" in node_map
    assert node_map["StatusOK"].kind == "variable"
    assert node_map["StatusOK"].visibility == "public"

    assert "statusNotFound" in node_map
    assert node_map["statusNotFound"].kind == "variable"
    assert node_map["statusNotFound"].visibility == "private"

    assert "MaxRetries" in node_map
    assert node_map["MaxRetries"].kind == "variable"
    assert node_map["MaxRetries"].visibility == "public"

    assert "ErrNotFound" in node_map
    assert node_map["ErrNotFound"].kind == "variable"
    assert node_map["ErrNotFound"].visibility == "public"

    assert "internalCounter" in node_map
    assert node_map["internalCounter"].kind == "variable"
    assert node_map["internalCounter"].visibility == "private"

    # Ensure local variable inside function is not emitted as a package-level DBNode
    assert "localVar" not in node_map

    # Ensure defines edges from module to top-level constants and vars
    module_id = node_map["server.go"].id
    defines_status = [
        e
        for e in edges
        if e.from_node_id == module_id
        and e.to_node_id == node_map["StatusOK"].id
        and e.relation_type == "defines"
    ]
    assert len(defines_status) == 1


def test_js_custom_router_variable_route_linking():
    parser = JavaScriptParser()
    code = b"""
function handleAuth(req, res) {}
function getUser(req, res) {}
function clientAction() {
    fetch('/api/auth/token');
}

const authRouter = express.Router();
authRouter.post('/api/auth/login', handleAuth);

const userRoutes = express.Router();
userRoutes.get('/api/users/me', getUser);
"""
    nodes, edges = parser.extract("routes.js", code)
    node_map = {n.name: n for n in nodes}

    assert "POST /api/auth/login" in node_map
    assert "GET /api/users/me" in node_map

    # Backend routes link to their respective handler functions
    auth_routes = [
        e
        for e in edges
        if e.from_node_id == node_map["POST /api/auth/login"].id
        and e.to_node_id == node_map["handleAuth"].id
        and e.relation_type == "routes"
    ]
    assert len(auth_routes) == 1

    user_routes = [
        e
        for e in edges
        if e.from_node_id == node_map["GET /api/users/me"].id
        and e.to_node_id == node_map["getUser"].id
        and e.relation_type == "routes"
    ]
    assert len(user_routes) == 1

    # Client fetch links from caller to endpoint
    client_calls = [
        e
        for e in edges
        if e.from_node_id == node_map["clientAction"].id
        and e.to_node_id == node_map["GET /api/auth/token"].id
        and e.relation_type == "routes"
    ]
    assert len(client_calls) == 1


def test_db_queries_get_nodes_missing_embeddings_deterministic_order(tmp_path: Path):
    db_file = tmp_path / "test_missing.db"
    db = IndexerDB(str(db_file))

    # Insert out of order
    n3 = Node(id="id-3", name="n3", kind="function", file_path="b.py", start_line=50, end_line=60)
    n1 = Node(id="id-1", name="n1", kind="function", file_path="a.py", start_line=10, end_line=20)
    n2 = Node(id="id-2", name="n2", kind="function", file_path="a.py", start_line=30, end_line=40)

    for n in [n3, n1, n2]:
        db.upsert_node(n)

    missing = db.get_nodes_missing_embeddings("model-1")
    returned_ids = [m["id"] for m in missing]
    assert returned_ids == ["id-1", "id-2", "id-3"]
    db.close()


def test_modified_file_line_counts_round42():
    root = Path(__file__).parent.parent
    files_to_check = [
        root / "src" / "vibetrace" / "indexer" / "parsers" / "python_parser.py",
        root / "src" / "vibetrace" / "indexer" / "parsers" / "go_parser.py",
        root / "src" / "vibetrace" / "indexer" / "parsers" / "route_extractor.py",
        root / "src" / "vibetrace" / "indexer" / "db_queries.py",
    ]
    for path in files_to_check:
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 600, f"{path.name} has {lines} lines; must be <= 600"
