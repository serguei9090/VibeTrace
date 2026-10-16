from pathlib import Path

from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.main import index_file
from vibegraph.indexer.parser import PythonParser
from vibegraph.indexer.resolver import ModuleResolver


def test_python_extraction():
    parser = PythonParser()
    source_code = b"""
def hello_world(name):
    \"\"\"This is a docstring.\"\"\"
    print(name)

class MyClass:
    def method(self):
        pass
"""
    file_path = "/tmp/test.py"
    nodes, edges = parser.extract(file_path, source_code)

    # Check Nodes
    assert len(nodes) == 4

    # Node 0: test.py (module)
    module_node = next(n for n in nodes if n.kind == "module")
    assert module_node.name == "test"

    # Node 1: hello_world
    func_node = next(n for n in nodes if n.name == "hello_world")
    assert func_node.kind == "function"
    assert func_node.qualified_name == "hello_world"
    assert func_node.docstring == "This is a docstring."
    assert func_node.signature is not None
    assert "(name)" in func_node.signature

    # Node 2: MyClass
    class_node = next(n for n in nodes if n.name == "MyClass")
    assert class_node.kind == "class"
    assert class_node.qualified_name == "MyClass"

    # Node 3: method
    method_node = next(n for n in nodes if n.name == "method")
    assert method_node.kind == "function"
    assert method_node.qualified_name == "MyClass.method"
    assert method_node.parent_id == class_node.id

    # Check Edges (File defines hello_world and MyClass, MyClass defines method)
    assert len(edges) >= 3

    def has_edge(f, t):
        return any(e.from_node_id == f and e.to_node_id == t for e in edges)

    assert has_edge(module_node.id, func_node.id)
    assert has_edge(module_node.id, class_node.id)
    assert has_edge(class_node.id, method_node.id)


def test_scoped_method_ids_prevent_collision():
    """Two classes with __init__ and run in the same file must have distinct IDs."""
    parser = PythonParser()
    source_code = b"""
class Alpha:
    def __init__(self):
        self.run()

    def run(self):
        pass

class Beta(Alpha):
    def __init__(self):
        pass

    def run(self):
        pass
"""
    nodes, edges = parser.extract("models.py", source_code)
    qnames = {n.qualified_name: n for n in nodes}

    assert "Alpha.__init__" in qnames
    assert "Beta.__init__" in qnames
    assert "Alpha.run" in qnames
    assert "Beta.run" in qnames
    assert qnames["Alpha.__init__"].id != qnames["Beta.__init__"].id
    assert qnames["Alpha.run"].id != qnames["Beta.run"].id

    # Verify self.run() inside Alpha.__init__ resolves to Alpha.run with exact call_line (line 4)
    call_edges = [
        e
        for e in edges
        if e.from_node_id == qnames["Alpha.__init__"].id
        and e.to_node_id == qnames["Alpha.run"].id
        and e.relation_type == "calls"
    ]
    assert len(call_edges) == 1
    assert call_edges[0].call_line == 4

    # Verify Beta(Alpha) emits an inherits edge with call_line (line 9)
    inherit_edges = [
        e
        for e in edges
        if e.from_node_id == qnames["Beta"].id
        and e.to_node_id == qnames["Alpha"].id
        and e.relation_type == "inherits"
    ]
    assert len(inherit_edges) == 1
    assert inherit_edges[0].call_line == 9


def test_cross_file_call_resolution_and_incremental_hash(tmp_path: Path):
    """Cross-file imports should link calls in file A directly to target node IDs in file B."""
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")

    file_b = pkg_dir / "service.py"
    file_b.write_text(
        "def compute_total(items):\n    return sum(items)\n",
        encoding="utf-8",
    )

    file_a = pkg_dir / "handler.py"
    file_a.write_text(
        "from .service import compute_total\n\n"
        "def handle_request(req):\n"
        "    return compute_total(req)\n",
        encoding="utf-8",
    )

    resolver = ModuleResolver(tmp_path)
    db = IndexerDB(str(tmp_path / "vibe.db"))

    # First index should return True
    assert index_file(db, str(file_b), project_root=tmp_path, resolver=resolver, verbose=False)
    assert index_file(db, str(file_a), project_root=tmp_path, resolver=resolver, verbose=False)

    # Second index without changes should skip (return False)
    assert not index_file(db, str(file_a), project_root=tmp_path, resolver=resolver, verbose=False)
    # Forced index should re-run (return True)
    assert index_file(
        db, str(file_a), project_root=tmp_path, resolver=resolver, verbose=False, force=True
    )

    handle_nodes = db.find_nodes_by_name("handle_request")
    compute_nodes = db.find_nodes_by_name("compute_total")
    assert len(handle_nodes) == 1
    assert len(compute_nodes) == 1

    callees = db.get_neighbors(handle_nodes[0]["id"], "down")
    call_targets = [c for c in callees if c["relation_type"] == "calls"]
    assert len(call_targets) == 1
    assert call_targets[0]["neighbor_id"] == compute_nodes[0]["id"]
    assert call_targets[0]["file_path"] == "pkg/service.py"
    assert call_targets[0]["call_line"] == 4
