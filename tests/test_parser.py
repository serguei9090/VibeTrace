from vibegraph.indexer.parser import PythonParser


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
    assert func_node.docstring == "This is a docstring."
    assert func_node.signature is not None
    assert "(name)" in func_node.signature

    # Node 2: MyClass
    class_node = next(n for n in nodes if n.name == "MyClass")
    assert class_node.kind == "class"

    # Node 3: method
    method_node = next(n for n in nodes if n.name == "method")
    assert method_node.kind == "function"

    # Check Edges (File defines hello_world and MyClass, MyClass defines method)
    assert len(edges) >= 3

    # helper to find edges
    def has_edge(f, t):
        return any(e.from_node_id == f and e.to_node_id == t for e in edges)

    assert has_edge(module_node.id, func_node.id)
    assert has_edge(module_node.id, class_node.id)
    assert has_edge(class_node.id, method_node.id)
