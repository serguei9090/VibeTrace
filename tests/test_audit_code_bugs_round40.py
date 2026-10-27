from __future__ import annotations

import hashlib
from pathlib import Path

from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.indexer.parsers.go_parser import GoParser
from vibetrace.indexer.resolver import ModuleResolver


def test_csharp_using_directive_imports_registered(tmp_path: Path):
    """Verify C# using directives create module nodes and import edges in GenericParser."""
    cs_file = tmp_path / "Controller.cs"
    code = """
    using System;
    using System.Collections.Generic;
    using Proj = MyProject.Services;

    namespace MyApp {
        public class Controller {
            public void Index() {}
        }
    }
    """
    cs_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = GenericParser(resolver=resolver, language="c_sharp")
    nodes, edges = parser.extract(str(cs_file), code.encode("utf-8"))

    module_nodes = {n.name: n for n in nodes if n.kind == "module"}
    assert "System" in module_nodes
    assert module_nodes["System"].file_path == "external"

    assert "Generic" in module_nodes
    assert module_nodes["Generic"].file_path == "external"

    assert "Proj" in module_nodes
    assert module_nodes["Proj"].file_path == "external"

    import_edges = [e for e in edges if e.relation_type == "imports"]
    assert len(import_edges) >= 3


def test_cpp_standard_include_registered(tmp_path: Path):
    """Verify C++ standard library includes (<vector>, <iostream>) create external module nodes."""
    cpp_file = tmp_path / "main.cpp"
    code = """
    #include <vector>
    #include <iostream>

    int main() {
        return 0;
    }
    """
    cpp_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = GenericParser(resolver=resolver, language="cpp")
    nodes, edges = parser.extract(str(cpp_file), code.encode("utf-8"))

    module_nodes = {n.name: n for n in nodes if n.kind == "module"}
    assert "vector" in module_nodes
    assert module_nodes["vector"].file_path == "external"

    assert "iostream" in module_nodes
    assert module_nodes["iostream"].file_path == "external"

    import_edges = [e for e in edges if e.relation_type == "imports"]
    assert len(import_edges) >= 2


def test_go_dot_import_wildcard_call_resolution(tmp_path: Path):
    """Verify Go dot-import (import . "fmt") enables bare call resolution to external module."""
    go_file = tmp_path / "main.go"
    code = """
    package main

    import . "fmt"

    func main() {
        Println("hello world")
    }
    """
    go_file.write_text(code, encoding="utf-8")
    resolver = ModuleResolver(tmp_path)
    parser = GoParser(resolver=resolver)
    _, edges = parser.extract(str(go_file), code.encode("utf-8"))

    call_edges = [e for e in edges if e.relation_type == "calls"]
    expected_println = hashlib.md5(b"external::Println").hexdigest()

    targets = {e.to_node_id for e in call_edges}
    assert expected_println in targets


def test_find_nodes_by_name_deterministic_order(tmp_path: Path):
    """Verify find_nodes_by_name returns nodes sorted deterministically by file_path and line."""
    db = IndexerDB(str(tmp_path / "test.db"))

    # Upsert nodes in reverse order
    node_b = Node(
        id=hashlib.md5(b"src/b.py::execute").hexdigest(),
        name="execute",
        qualified_name="execute",
        kind="function",
        file_path="src/b.py",
        start_line=50,
        end_line=60,
    )
    node_a2 = Node(
        id=hashlib.md5(b"src/a.py::execute:L80").hexdigest(),
        name="execute",
        qualified_name="execute",
        kind="function",
        file_path="src/a.py",
        start_line=80,
        end_line=90,
    )
    node_a1 = Node(
        id=hashlib.md5(b"src/a.py::execute:L20").hexdigest(),
        name="execute",
        qualified_name="execute",
        kind="function",
        file_path="src/a.py",
        start_line=20,
        end_line=30,
    )

    db.upsert_node(node_b)
    db.upsert_node(node_a2)
    db.upsert_node(node_a1)

    results = db.find_nodes_by_name("execute")
    assert len(results) == 3
    # Must be ordered by file_path ASC, start_line ASC: a.py:L20, a.py:L80, b.py:L50
    assert results[0]["file_path"] == "src/a.py"
    assert results[0]["start_line"] == 20
    assert results[1]["file_path"] == "src/a.py"
    assert results[1]["start_line"] == 80
    assert results[2]["file_path"] == "src/b.py"
    assert results[2]["start_line"] == 50


def test_get_symbol_references_deterministic_order(tmp_path: Path):
    """Verify get_symbol_references returns references deterministically ordered."""
    db = IndexerDB(str(tmp_path / "test_refs.db"))

    target_id = hashlib.md5(b"src/core.py::target").hexdigest()
    target_node = Node(
        id=target_id,
        name="target",
        qualified_name="target",
        kind="function",
        file_path="src/core.py",
        start_line=10,
        end_line=20,
    )
    caller_b = Node(
        id=hashlib.md5(b"src/z.py::caller_b").hexdigest(),
        name="caller_b",
        qualified_name="caller_b",
        kind="function",
        file_path="src/z.py",
        start_line=15,
        end_line=25,
    )
    caller_a = Node(
        id=hashlib.md5(b"src/a.py::caller_a").hexdigest(),
        name="caller_a",
        qualified_name="caller_a",
        kind="function",
        file_path="src/a.py",
        start_line=40,
        end_line=50,
    )

    db.upsert_node(target_node)
    db.upsert_node(caller_b)
    db.upsert_node(caller_a)

    # Insert edges in reverse alphabetical order
    db.upsert_edge(
        Edge(
            from_node_id=caller_b.id,
            to_node_id=target_id,
            relation_type="calls",
            call_line=20,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id=caller_a.id,
            to_node_id=target_id,
            relation_type="calls",
            call_line=45,
        )
    )

    refs = db.get_symbol_references(target_id)
    assert len(refs) == 2
    # Must be ordered by file_path ASC, call_line ASC: src/a.py first, then src/z.py
    assert refs[0]["file_path"] == "src/a.py"
    assert refs[1]["file_path"] == "src/z.py"


def test_get_neighbors_deterministic_order(tmp_path: Path):
    """Verify get_neighbors returns upward/downward neighbors in deterministic file/line order."""
    db = IndexerDB(str(tmp_path / "test_neighbors.db"))

    hub_id = hashlib.md5(b"src/hub.py::hub").hexdigest()
    hub = Node(
        id=hub_id,
        name="hub",
        qualified_name="hub",
        kind="function",
        file_path="src/hub.py",
        start_line=10,
        end_line=20,
    )
    n2 = Node(
        id=hashlib.md5(b"src/y.py::n2").hexdigest(),
        name="n2",
        qualified_name="n2",
        kind="function",
        file_path="src/y.py",
        start_line=30,
        end_line=40,
    )
    n1 = Node(
        id=hashlib.md5(b"src/x.py::n1").hexdigest(),
        name="n1",
        qualified_name="n1",
        kind="function",
        file_path="src/x.py",
        start_line=15,
        end_line=25,
    )

    db.upsert_node(hub)
    db.upsert_node(n2)
    db.upsert_node(n1)

    db.upsert_edge(Edge(from_node_id=hub_id, to_node_id=n2.id, relation_type="calls", call_line=12))
    db.upsert_edge(Edge(from_node_id=hub_id, to_node_id=n1.id, relation_type="calls", call_line=14))

    down = db.get_neighbors(hub_id, direction="down")
    assert len(down) == 2
    # Must be sorted by file_path ASC: src/x.py then src/y.py
    assert down[0]["file_path"] == "src/x.py"
    assert down[1]["file_path"] == "src/y.py"
