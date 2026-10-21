"""Tests verifying fixes for Round 2 code bug critique audit."""

from pathlib import Path

from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.indexer.parsers.go_parser import GoParser
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.parsers.rust_parser import RustParser
from vibetrace.indexer.resolver import ModuleResolver


def test_go_generic_receiver_multi_type_params():
    """Verify generic receiver with multiple type params does not mangle struct name."""
    code = b"""package main

type Server[T any, K comparable] struct {}

func (s *Server[T, K]) Serve() string {
    return "ok"
}

func main() {
    srv := &Server[int, string]{}
    srv.Serve()
}
"""
    parser = GoParser()
    nodes, edges = parser.extract("server.go", code)

    node_map = {n.name: n for n in nodes}
    assert "Serve" in node_map
    serve_node = node_map["Serve"]
    # Qualified name must be Server.Serve, NOT K].Serve
    assert serve_node.qualified_name == "Server.Serve"
    expected_id = parser._get_id("server.go", "Server.Serve")
    assert serve_node.id == expected_id

    call_edges = [e for e in edges if e.relation_type == "calls"]
    target_ids = {e.to_node_id for e in call_edges}
    assert expected_id in target_ids


def test_go_new_factory_type_resolution():
    """Verify idiomatic redis.New() or pkg.New() does not produce trailing dot types."""
    code = b"""package main

import "example.com/redis"

func main() {
    client := redis.New()
    client.Get("key")
}
"""
    resolver = ModuleResolver(Path("."))
    parser = GoParser(resolver=resolver)
    _nodes, edges = parser.extract("main.go", code)

    call_edges = [e for e in edges if e.relation_type == "calls"]
    target_ids = {e.to_node_id for e in call_edges}
    # Should resolve to external Get (never main.go::redis..Get)
    expected_get_id = parser._get_id("external", "Get")
    assert expected_get_id in target_ids


def test_rust_self_associated_function_resolution():
    """Verify Rust Self::method() resolves to enclosing struct name."""
    code = b"""
struct ConnectionPool;

impl ConnectionPool {
    fn new() -> Self {
        ConnectionPool
    }

    fn helper() {}

    fn execute(&self) {
        Self::helper();
    }
}
"""
    parser = RustParser()
    _nodes, edges = parser.extract("src/pool.rs", code)

    call_edges = [e for e in edges if e.relation_type == "calls"]
    target_ids = {e.to_node_id for e in call_edges}
    # Must resolve to ConnectionPool.helper, not Self.helper
    expected_id = parser._get_id("src/pool.rs", "ConnectionPool.helper")
    assert expected_id in target_ids
    phantom_id = parser._get_id("src/pool.rs", "Self.helper")
    assert phantom_id not in target_ids


def test_js_export_default_identifier_visibility():
    """Verify `export default myHelper` marks node as exported and populates defined symbols."""
    code = b"""
const calculateTax = (amount) => {
    return amount * 0.2;
};

export default calculateTax;
"""
    parser = JavaScriptParser()
    nodes, _edges = parser.extract("tax.js", code)

    node_map = {n.name: n for n in nodes}
    assert "calculateTax" in node_map
    calc_node = node_map["calculateTax"]
    assert calc_node.visibility == "exported"


def test_java_new_object_creation_call_edge():
    """Verify Java `new Service()` emits calls/references edge to Service."""
    code = b"""
package com.example;

public class Controller {
    public Service createService() {
        return new Service();
    }
}
"""
    parser = GenericParser(language="java")
    _nodes, edges = parser.extract("Controller.java", code)

    call_edges = [e for e in edges if e.relation_type == "calls"]
    target_ids = {e.to_node_id for e in call_edges}
    expected_id = parser._get_id("Controller.java", "Service")
    assert expected_id in target_ids


def test_rust_crate_directory_module_resolution(tmp_path: Path):
    """Verify Rust `use crate::models::User` in nested file resolves to `src/models/mod.rs`."""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    models_dir = src_dir / "models"
    models_dir.mkdir()
    (models_dir / "mod.rs").write_text("pub struct User;", encoding="utf-8")

    services_dir = src_dir / "services"
    services_dir.mkdir()
    auth_file = services_dir / "auth.rs"
    auth_file.write_text("use crate::models::User;", encoding="utf-8")

    resolver = ModuleResolver(tmp_path)
    res = resolver.resolve("crate/models", "src/services/auth.rs")
    assert res is not None
    assert res == "src/models/mod.rs"
