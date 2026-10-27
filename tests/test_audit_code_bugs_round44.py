"""Tests for Polyglot AST Parser Edge Cases (Audit Round 44)."""

from __future__ import annotations

from pathlib import Path

from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.indexer.parsers.python_parser import PythonParser
from vibetrace.indexer.parsers.rust_parser import RustParser
from vibetrace.indexer.parsers.ts_parser import TypeScriptParser


def test_python_match_case_pattern_references() -> None:
    """Verify Python match-case statements extract references to classes and dotted symbols."""
    code = b"""
class Point:
    pass

class Status:
    OK = 200

def evaluate(val):
    match val:
        case Point(x, y):
            return 1
        case Status.OK:
            return 2
        case _:
            return 0
"""
    parser = PythonParser()
    nodes, edges = parser.extract("app.py", code)

    node_names = {n.name: n.id for n in nodes}
    assert "Point" in node_names
    assert "Status" in node_names
    assert "evaluate" in node_names

    eval_id = node_names["evaluate"]
    point_id = node_names["Point"]
    status_ok_id = parser._get_id("app.py", "Status.OK")

    ref_edges = [e for e in edges if e.from_node_id == eval_id and e.relation_type == "references"]
    referenced_ids = {e.to_node_id for e in ref_edges}

    assert point_id in referenced_ids
    assert status_ok_id in referenced_ids


def test_rust_constants_and_impl_consts_extraction() -> None:
    """Verify Rust const_item nodes (top-level and in impl blocks) are extracted as variables."""
    code = b"""
pub const MAX_BUFFER_SIZE: usize = 4096;

pub struct Cache;

impl Cache {
    pub const DEFAULT_TTL: u64 = 300;

    pub fn new() -> Self {
        Cache
    }
}
"""
    parser = RustParser()
    nodes, edges = parser.extract("cache.rs", code)

    names = {n.name: n for n in nodes}
    assert "MAX_BUFFER_SIZE" in names
    assert names["MAX_BUFFER_SIZE"].kind == "variable"

    assert "DEFAULT_TTL" in names
    assert names["DEFAULT_TTL"].kind == "variable"
    assert names["DEFAULT_TTL"].qualified_name == "Cache.DEFAULT_TTL"

    cache_id = names["Cache"].id
    ttl_id = names["DEFAULT_TTL"].id
    assert any(
        e.from_node_id == cache_id and e.to_node_id == ttl_id and e.relation_type == "defines"
        for e in edges
    )


def test_typescript_namespace_and_module_extraction() -> None:
    """Verify TypeScript namespace and module blocks create module nodes with scoped children."""
    code = b"""
export namespace Validation {
    export interface StringValidator {
        isAcceptable(s: string): boolean;
    }

    export function check(): boolean {
        return true;
    }
}
"""
    parser = TypeScriptParser()
    nodes, edges = parser.extract("validator.ts", code)

    names = {n.name: n for n in nodes}
    assert "Validation" in names
    assert names["Validation"].kind == "module"

    assert "StringValidator" in names
    assert names["StringValidator"].qualified_name == "Validation.StringValidator"
    assert names["StringValidator"].kind == "interface"

    assert "check" in names
    assert names["check"].qualified_name == "Validation.check"
    assert names["check"].kind == "function"

    ns_id = names["Validation"].id
    fn_id = names["check"].id
    assert any(
        e.from_node_id == ns_id and e.to_node_id == fn_id and e.relation_type == "defines"
        for e in edges
    )


def test_cpp_constructor_member_initializer_call_edges(tmp_path: Path) -> None:
    """Verify C++ member initialization list calls (createService()) emit call edges."""
    code = b"""
class Service {
public:
    Service() {}
};

Service createService() {
    return Service();
}

class Worker {
public:
    Worker(int x) : id(x), svc(createService()) {
    }
private:
    int id;
    Service svc;
};
"""
    parser = GenericParser("cpp")
    nodes, edges = parser.extract("worker.cpp", code)

    names = {n.name: n for n in nodes}
    assert "Worker" in names
    assert "createService" in names

    worker_ctor_id = names["Worker"].id
    svc_fn_id = names["createService"].id

    assert any(
        e.from_node_id == worker_ctor_id
        and e.to_node_id == svc_fn_id
        and e.relation_type == "calls"
        for e in edges
    )
