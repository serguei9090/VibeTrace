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


def test_instance_method_resolution_parameter_and_assignment(tmp_path: Path) -> None:
    """Instance method calls on typed params and constructors must resolve to TargetClass.method."""
    pkg_dir = tmp_path / "app"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")

    db_file = pkg_dir / "db.py"
    db_file.write_text(
        "class IndexerDB:\n    def batch_save_file_index(self, items):\n        pass\n",
        encoding="utf-8",
    )

    worker_file = pkg_dir / "worker.py"
    worker_file.write_text(
        "from .db import IndexerDB\n\n"
        "def process_with_param(db: IndexerDB):\n"
        "    db.batch_save_file_index([1, 2, 3])\n\n"
        "def process_with_ctor():\n"
        "    db = IndexerDB()\n"
        "    db.batch_save_file_index([4, 5, 6])\n\n"
        "class WorkerService:\n"
        "    def __init__(self):\n"
        "        self.db = IndexerDB()\n"
        "    def run(self):\n"
        "        self.db.batch_save_file_index([7, 8, 9])\n\n"
        "class ParamWorkerService:\n"
        "    def __init__(self, db: IndexerDB):\n"
        "        self.db = db\n"
        "    def execute(self):\n"
        "        self.db.batch_save_file_index([10])\n\n"
        "class IsolatedService:\n"
        "    def run(self):\n"
        "        self.db.batch_save_file_index([11])\n\n"
        "def orphan_func():\n"
        "    db.batch_save_file_index([12])\n",
        encoding="utf-8",
    )

    resolver = ModuleResolver(tmp_path)
    db = IndexerDB(str(tmp_path / "test.db"))

    assert index_file(db, str(db_file), project_root=tmp_path, resolver=resolver, verbose=False)
    assert index_file(db, str(worker_file), project_root=tmp_path, resolver=resolver, verbose=False)

    target_method_nodes = [
        n
        for n in db.find_nodes_by_name("batch_save_file_index")
        if n["qualified_name"] == "IndexerDB.batch_save_file_index"
    ]
    assert len(target_method_nodes) == 1
    target_method_id = target_method_nodes[0]["id"]

    # 1. Parameter type resolution: process_with_param -> IndexerDB.batch_save_file_index
    param_func = db.find_nodes_by_name("process_with_param")[0]
    param_callees = [
        c for c in db.get_neighbors(param_func["id"], "down") if c["relation_type"] == "calls"
    ]
    assert any(c["neighbor_id"] == target_method_id for c in param_callees)

    # 2. Local constructor resolution: process_with_ctor -> IndexerDB.batch_save_file_index
    ctor_func = db.find_nodes_by_name("process_with_ctor")[0]
    ctor_callees = [
        c for c in db.get_neighbors(ctor_func["id"], "down") if c["relation_type"] == "calls"
    ]
    assert any(c["neighbor_id"] == target_method_id for c in ctor_callees)

    # 3. Instance self.db resolution: WorkerService.run -> IndexerDB.batch_save_file_index
    run_nodes = [
        n for n in db.find_nodes_by_name("run") if n["qualified_name"] == "WorkerService.run"
    ]
    assert len(run_nodes) == 1
    run_callees = [
        c for c in db.get_neighbors(run_nodes[0]["id"], "down") if c["relation_type"] == "calls"
    ]
    assert any(c["neighbor_id"] == target_method_id for c in run_callees)

    # 4. Constructor param to self assignment:
    # ParamWorkerService.execute -> IndexerDB.batch_save_file_index
    exec_nodes = [
        n
        for n in db.find_nodes_by_name("execute")
        if n["qualified_name"] == "ParamWorkerService.execute"
    ]
    assert len(exec_nodes) == 1
    exec_callees = [
        c for c in db.get_neighbors(exec_nodes[0]["id"], "down") if c["relation_type"] == "calls"
    ]
    assert any(c["neighbor_id"] == target_method_id for c in exec_callees)

    # 5. Scope isolation: IsolatedService.run must NOT leak to IndexerDB.batch_save_file_index
    isolated_nodes = [
        n for n in db.find_nodes_by_name("run") if n["qualified_name"] == "IsolatedService.run"
    ]
    assert len(isolated_nodes) == 1
    isolated_callees = [
        c
        for c in db.get_neighbors(isolated_nodes[0]["id"], "down")
        if c["relation_type"] == "calls"
    ]
    assert not any(c["neighbor_id"] == target_method_id for c in isolated_callees)

    # 6. Scope isolation: orphan_func must NOT leak to IndexerDB.batch_save_file_index
    orphan_nodes = db.find_nodes_by_name("orphan_func")
    assert len(orphan_nodes) == 1
    orphan_callees = [
        c for c in db.get_neighbors(orphan_nodes[0]["id"], "down") if c["relation_type"] == "calls"
    ]
    assert not any(c["neighbor_id"] == target_method_id for c in orphan_callees)


def test_phantom_builtin_filtering(tmp_path: Path) -> None:
    """Bare calls to standard builtins must not emit phantom edges into the SQLite database."""
    pkg_dir = tmp_path / "builtins_test"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")

    src_file = pkg_dir / "sample.py"
    src_file.write_text(
        "def custom_worker():\n"
        "    return 42\n\n"
        "def main_func(items):\n"
        "    # Builtin calls that must NOT emit call edges\n"
        "    a = len(items)\n"
        "    b = range(10)\n"
        "    print(a, b)\n"
        "    f = open('test.txt', 'r')\n"
        "    s = str(a)\n"
        "    i = int('123')\n"
        "    fl = float('1.23')\n"
        "    bo = bool(1)\n"
        "    d = dict()\n"
        "    li = list()\n"
        "    se = set()\n"
        "    tu = tuple()\n"
        "    is_inst = isinstance(items, list)\n"
        "    t = type(items)\n"
        "    en = enumerate(items)\n"
        "    z = zip(items, items)\n"
        "    dyn_attr = getattr(items, 'unknown_' + str(a))\n"
        "    setattr(items, 'key', 'val')\n"
        "    has_k = hasattr(items, 'key')\n"
        "    # Custom local function call that MUST emit a call edge\n"
        "    return custom_worker()\n",
        encoding="utf-8",
    )

    resolver = ModuleResolver(tmp_path)
    db = IndexerDB(str(tmp_path / "test.db"))
    assert index_file(db, str(src_file), project_root=tmp_path, resolver=resolver, verbose=False)

    main_nodes = db.find_nodes_by_name("main_func")
    assert len(main_nodes) == 1
    callees = [
        c for c in db.get_neighbors(main_nodes[0]["id"], "down") if c["relation_type"] == "calls"
    ]

    # Only custom_worker should be a callee! None of the builtins should be present.
    custom_nodes = db.find_nodes_by_name("custom_worker")
    assert len(custom_nodes) == 1
    assert len(callees) == 1
    assert callees[0]["neighbor_id"] == custom_nodes[0]["id"]
