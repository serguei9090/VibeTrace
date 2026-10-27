"""Tests for strict code audit and parity fixes (Round 41).

Covers:
1. JS/TS aliased named exports (`export { local as alias }`) and local exported visibility.
2. Clientside `fetch()` HTTP method option extraction (`{ method: 'POST' }`, `DELETE`).
3. Rust `enum_item` variants and `type_item` alias definitions in AST.
4. Java, C#, and C/C++ enum declarations in `GenericParser`.
5. Deterministic sorting in `get_nodes_in_file`, `get_incoming_dependents`, `get_file_imports`.
6. Deterministic tie-breaking stability in `hybrid_search` RRF score ranking.
"""

from __future__ import annotations

from pathlib import Path

from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.parsers.rust_parser import RustParser
from vibetrace.services.hybrid_search import hybrid_search


def test_js_aliased_named_exports(tmp_path: Path) -> None:
    """Verify that export { local as alias } creates an exported alias node and references edge."""
    code = b"""
function computeTotal(x) {
    return x * 2;
}

function internalHelper() {
    return 1;
}

export { computeTotal as calcTotal, internalHelper };
"""
    parser = JavaScriptParser()
    nodes, edges = parser.extract("src/math.js", code)

    # 1. Verify calcTotal alias node exists with exported visibility
    alias_node = next((n for n in nodes if n.name == "calcTotal"), None)
    assert alias_node is not None
    assert alias_node.visibility == "exported"
    assert alias_node.kind == "variable"

    # 2. Verify references edge connects calcTotal to computeTotal
    ref_edge = next((e for e in edges if e.from_node_id == alias_node.id), None)
    assert ref_edge is not None
    assert ref_edge.relation_type == "references"
    compute_node = next((n for n in nodes if n.name == "computeTotal"), None)
    assert compute_node is not None
    assert ref_edge.to_node_id == compute_node.id

    # 3. Verify internalHelper visibility was upgraded to exported
    helper_node = next((n for n in nodes if n.name == "internalHelper"), None)
    assert helper_node is not None
    assert helper_node.visibility == "exported"


def test_fetch_method_options_extraction() -> None:
    """Verify that fetch('/api/...', { method: 'POST' }) captures the non-GET HTTP verb."""
    code = b"""
async function submitData(payload) {
    return await fetch('/api/data', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
}

async function deleteData(id) {
    return await fetch('/api/data/' + id, {
        method: "DELETE"
    });
}
"""
    parser = JavaScriptParser()
    nodes, _ = parser.extract("src/client.js", code)

    endpoints = [n for n in nodes if n.kind == "endpoint"]
    endpoint_names = [e.name for e in endpoints]

    assert "POST /api/data" in endpoint_names
    assert "GET /api/data" not in endpoint_names
    assert "DELETE /api/data/" in endpoint_names


def test_rust_enum_and_type_item_definitions() -> None:
    """Verify that RustParser extracts enum_item, its variants, and type_item aliases."""
    code = b"""
pub enum Status {
    Pending,
    Active,
    Completed(i32),
}

pub type MyResult<T> = Result<T, String>;
"""
    parser = RustParser()
    nodes, edges = parser.extract("src/models.rs", code)

    # 1. Verify Status enum node
    status_node = next((n for n in nodes if n.name == "Status"), None)
    assert status_node is not None
    assert status_node.kind == "enum"

    # 2. Verify enum variants
    variant_names = [n.name for n in nodes if (n.qualified_name or "").startswith("Status.")]
    assert "Pending" in variant_names
    assert "Active" in variant_names
    assert "Completed" in variant_names

    # 3. Verify defines edge from Status to variants
    variant_node = next((n for n in nodes if n.name == "Pending"), None)
    assert variant_node is not None
    def_edge = next(
        (e for e in edges if e.from_node_id == status_node.id and e.to_node_id == variant_node.id),
        None,
    )
    assert def_edge is not None
    assert def_edge.relation_type == "defines"

    # 4. Verify type alias
    type_node = next((n for n in nodes if n.name == "MyResult"), None)
    assert type_node is not None
    assert type_node.kind == "struct"


def test_generic_parser_enum_declarations() -> None:
    """Verify that GenericParser extracts enums across Java, C#, and C++."""
    # Java
    java_code = b"""
public enum OrderState {
    CREATED,
    PAID,
    CANCELLED
}
"""
    j_nodes, _ = GenericParser("java").extract("src/OrderState.java", java_code)
    j_enum = next((n for n in j_nodes if n.name == "OrderState"), None)
    assert j_enum is not None
    assert j_enum.kind == "enum"

    # C#
    csharp_code = b"""
public enum ConnectionStatus {
    Disconnected,
    Connecting,
    Connected
}
"""
    cs_nodes, _ = GenericParser("c_sharp").extract("src/Status.cs", csharp_code)
    cs_enum = next((n for n in cs_nodes if n.name == "ConnectionStatus"), None)
    assert cs_enum is not None
    assert cs_enum.kind == "enum"

    # C++
    cpp_code = b"""
enum class UserRole {
    Admin,
    Member,
    Guest
};
"""
    cpp_nodes, _ = GenericParser("cpp").extract("src/roles.hpp", cpp_code)
    cpp_enum = next((n for n in cpp_nodes if n.name == "UserRole"), None)
    assert cpp_enum is not None
    assert cpp_enum.kind == "enum"


def test_db_queries_ordering_determinism(tmp_path: Path) -> None:
    """Verify that file, incoming dependent, and import queries have deterministic order."""
    db = IndexerDB(str(tmp_path / "test.db"))

    # Insert nodes out of line order
    node_c = Node(
        id="f::c",
        name="c",
        qualified_name="c",
        kind="function",
        file_path="src/file.py",
        start_line=30,
        end_line=40,
    )
    node_a = Node(
        id="f::a",
        name="a",
        qualified_name="a",
        kind="function",
        file_path="src/file.py",
        start_line=10,
        end_line=20,
    )
    node_b = Node(
        id="f::b",
        name="b",
        qualified_name="b",
        kind="function",
        file_path="src/file.py",
        start_line=20,
        end_line=25,
    )
    for n in (node_c, node_a, node_b):
        db.upsert_node(n)

    # 1. get_nodes_in_file sorted by start_line ASC
    file_nodes = db.get_nodes_in_file("src/file.py")
    assert [n["name"] for n in file_nodes] == ["a", "b", "c"]

    # 2. get_incoming_dependents sorted deterministically
    caller_z = Node(
        id="z::caller",
        name="zCaller",
        qualified_name="zCaller",
        kind="function",
        file_path="src/z.py",
        start_line=5,
        end_line=10,
    )
    caller_m = Node(
        id="m::caller",
        name="mCaller",
        qualified_name="mCaller",
        kind="function",
        file_path="src/m.py",
        start_line=5,
        end_line=10,
    )
    for c in (caller_z, caller_m):
        db.upsert_node(c)
    db.upsert_edge(
        Edge(from_node_id="z::caller", to_node_id="f::a", relation_type="calls", call_line=6)
    )
    db.upsert_edge(
        Edge(from_node_id="m::caller", to_node_id="f::a", relation_type="calls", call_line=6)
    )

    dependents = db.get_incoming_dependents("f::a")
    assert dependents[0]["file_path"] == "src/m.py"
    assert dependents[1]["file_path"] == "src/z.py"

    # 3. get_file_imports sorted deterministically
    imp_b = Node(
        id="imp::b",
        name="beta",
        qualified_name="beta",
        kind="module",
        file_path="src/beta.py",
        start_line=1,
        end_line=1,
    )
    imp_a = Node(
        id="imp::a",
        name="alpha",
        qualified_name="alpha",
        kind="module",
        file_path="src/alpha.py",
        start_line=1,
        end_line=1,
    )
    for imp in (imp_b, imp_a):
        db.upsert_node(imp)
    db.upsert_edge(
        Edge(from_node_id="f::a", to_node_id="imp::b", relation_type="imports", call_line=1)
    )
    db.upsert_edge(
        Edge(from_node_id="f::a", to_node_id="imp::a", relation_type="imports", call_line=2)
    )

    imports = db.get_file_imports("src/file.py")
    assert [imp["name"] for imp in imports] == ["alpha", "beta"]


def test_hybrid_search_rrf_tie_breaking_stability(tmp_path: Path) -> None:
    """Verify that hybrid search with equal RRF scores produces stable, non-drifting pagination."""
    db = IndexerDB(str(tmp_path / "test.db"))

    # Four distinct nodes matching lexical query 'service'
    nodes = [
        Node(
            id=f"svc::{ch}",
            name=f"service_{ch}",
            qualified_name=f"service_{ch}",
            kind="function",
            file_path=f"src/{ch}.py",
            start_line=10,
            end_line=20,
            docstring="Shared service handler for events",
        )
        for ch in ("d", "c", "b", "a")
    ]
    for n in nodes:
        db.upsert_node(n)

    page1 = hybrid_search(db, "service", limit=2, offset=0)
    page2 = hybrid_search(db, "service", limit=2, offset=2)

    assert len(page1) == 2
    assert len(page2) == 2

    # Verify no item appears on both page 1 and page 2
    page1_ids = {r.node_id for r in page1}
    page2_ids = {r.node_id for r in page2}
    assert page1_ids.isdisjoint(page2_ids)

    # Verify deterministic tie-breaking sorted by file_path/name
    assert page1[0].file_path == "src/d.py"
    assert page1[1].file_path == "src/c.py"
    assert page2[0].file_path == "src/b.py"
    assert page2[1].file_path == "src/a.py"
