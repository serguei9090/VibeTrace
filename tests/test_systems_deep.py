"""Test deep AST extraction for Go and Rust systems languages."""

from __future__ import annotations

from pathlib import Path

from vibegraph.indexer.parsers.systems_parser import GoParser, RustParser
from vibegraph.indexer.resolver import ModuleResolver


def test_go_struct_embedding_and_interface_methods() -> None:
    parser = GoParser()
    code = b"""package main

type DB struct {
    host string
}

type Service struct {
    DB
    name string
}

type Reader interface {
    Read(p []byte) (n int, err error)
}

func (s *Service) Query() {}
"""
    nodes, edges = parser.extract("main.go", code)

    node_map = {n.name: n for n in nodes}
    assert "DB" in node_map
    assert "Service" in node_map
    assert "Reader" in node_map
    assert "Service.Query" in [n.qualified_name for n in nodes]
    assert "Reader.Read" in [n.qualified_name for n in nodes]

    # Verify embedded struct creates inherits edge from Service to DB
    inherits_edges = [e for e in edges if e.relation_type == "inherits"]
    assert len(inherits_edges) >= 1
    assert any(
        e.from_node_id == node_map["Service"].id and e.to_node_id == node_map["DB"].id
        for e in inherits_edges
    )

    # Verify interface defines its methods
    defines_edges = [e for e in edges if e.relation_type == "defines"]
    reader_node = node_map["Reader"]
    assert any(e.from_node_id == reader_node.id for e in defines_edges)


def test_rust_trait_implementation_and_inherent_impl() -> None:
    parser = RustParser()
    code = b"""
pub trait Greeter {
    fn greet(&self) -> String;
}

pub struct Bot {
    pub name: String,
}

impl Greeter for Bot {
    fn greet(&self) -> String {
        self.name.clone()
    }
}

impl Bot {
    pub fn new() -> Self {
        Bot { name: "bot".to_string() }
    }
}
"""
    nodes, edges = parser.extract("src/lib.rs", code)

    node_map = {n.name: n for n in nodes}
    assert "Greeter" in node_map
    assert "Bot" in node_map
    assert node_map["Greeter"].kind == "trait"
    assert node_map["Bot"].kind == "struct"

    # Verify Bot.greet and Bot.new are scoped under Bot
    qualified_names = [n.qualified_name for n in nodes]
    assert "Greeter.greet" in qualified_names
    assert "Bot.greet" in qualified_names
    assert "Bot.new" in qualified_names

    # Verify implements edge from Bot to Greeter
    implements_edges = [e for e in edges if e.relation_type == "implements"]
    assert len(implements_edges) >= 1
    bot_node = node_map["Bot"]
    greeter_node = node_map["Greeter"]
    assert any(
        e.from_node_id == bot_node.id and e.to_node_id == greeter_node.id for e in implements_edges
    )


def test_go_generic_receiver_methods() -> None:
    """Go 1.18+ generic type parameter receivers must link to canonical struct name."""
    parser = GoParser()
    code = b"""package main

type Container[T any] struct {
    val T
}

func (c *Container[T]) Get() T {
    return c.val
}
"""
    nodes, edges = parser.extract("generic.go", code)
    node_map = {n.name: n for n in nodes}

    assert "Container" in node_map
    assert "Container.Get" in [n.qualified_name for n in nodes]

    container_node = node_map["Container"]
    defines_edges = [e for e in edges if e.relation_type == "defines"]
    assert any(e.from_node_id == container_node.id for e in defines_edges)


def test_rust_generic_impl_block() -> None:
    """Rust generic impl<T> blocks must link methods to canonical struct name."""
    parser = RustParser()
    code = b"""
pub struct Service<T> {
    pub data: T,
}

impl<T> Service<T> {
    pub fn process(&self) {}
}
"""
    nodes, edges = parser.extract("service.rs", code)
    node_map = {n.name: n for n in nodes}

    assert "Service" in node_map
    assert "Service.process" in [n.qualified_name for n in nodes]

    service_node = node_map["Service"]
    defines_edges = [e for e in edges if e.relation_type == "defines"]
    assert any(e.from_node_id == service_node.id for e in defines_edges)


def test_rust_local_variable_and_self_method_calls() -> None:
    """Rust let_declaration and self calls must resolve to struct methods."""
    parser = RustParser()
    code = b"""
pub struct Worker;

impl Worker {
    pub fn do_work(&self) {}
    pub fn execute(&self) {
        self.do_work();
    }
}

pub fn run_job() {
    let w = Worker::new();
    w.execute();
}
"""
    nodes, edges = parser.extract("worker.rs", code)
    node_map = {n.name: n for n in nodes}

    assert "Worker.do_work" in [n.qualified_name for n in nodes]
    assert "Worker.execute" in [n.qualified_name for n in nodes]
    assert "run_job" in node_map

    do_work_id = next(n.id for n in nodes if n.qualified_name == "Worker.do_work")
    execute_id = next(n.id for n in nodes if n.qualified_name == "Worker.execute")
    run_job_id = node_map["run_job"].id

    # execute() calls self.do_work() -> Worker.do_work
    assert any(
        e.from_node_id == execute_id and e.to_node_id == do_work_id and e.relation_type == "calls"
        for e in edges
    )

    # run_job() calls w.execute() -> Worker.execute
    assert any(
        e.from_node_id == run_job_id and e.to_node_id == execute_id and e.relation_type == "calls"
        for e in edges
    )


def test_go_package_qualified_constructor_call() -> None:
    """Go package-qualified constructors (client.NewClient()) must resolve instance calls."""
    parser = GoParser()
    code = b"""package main

type Client struct {}

func (c *Client) FetchData() {}

func main() {
    cli := client.NewClient()
    cli.FetchData()
}
"""
    nodes, edges = parser.extract("client.go", code)
    node_map = {n.name: n for n in nodes}

    assert "Client" in node_map
    assert "Client.FetchData" in [n.qualified_name for n in nodes]
    assert "main" in node_map

    fetch_id = next(n.id for n in nodes if n.qualified_name == "Client.FetchData")
    main_id = node_map["main"].id

    # main calls cli.FetchData -> Client.FetchData
    assert any(
        e.from_node_id == main_id and e.to_node_id == fetch_id and e.relation_type == "calls"
        for e in edges
    )


def test_go_imports_and_cross_package_call(tmp_path: Path) -> None:
    """Verify GoParser extracts import declarations, emits imports edges, and resolves calls."""
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "user.go").write_text("package models\ntype User struct {}\n")
    resolver = ModuleResolver(tmp_path)
    parser = GoParser(resolver=resolver)

    code = b"""package main
import (
    "fmt"
    "models"
)
func main() {}
"""
    _, edges = parser.extract("main.go", code)
    import_edges = [e for e in edges if e.relation_type == "imports"]
    assert len(import_edges) >= 2


def test_rust_use_declaration_and_imports_edge(tmp_path: Path) -> None:
    """Verify RustParser extracts use declarations and emits imports edges."""
    (tmp_path / "models.rs").write_text("pub struct User;\n")
    resolver = ModuleResolver(tmp_path)
    parser = RustParser(resolver=resolver)

    code = b"""
use crate::models::{User, Account as Acc};
use std::collections::HashMap;

fn run() {}
"""
    _, edges = parser.extract("main.rs", code)
    import_edges = [e for e in edges if e.relation_type == "imports"]
    assert len(import_edges) >= 2
