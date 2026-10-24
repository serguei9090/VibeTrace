"""Round 20 regression tests: C++/Java/C# inheritance, Java implements, sibling scoping."""

from __future__ import annotations

import hashlib
from pathlib import Path

from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.indexer.resolver import ModuleResolver


def test_java_generic_inheritance_resolution():
    """Verify class Dog extends Animal<String> links to Animal without generic suffix."""
    p = GenericParser("java")
    code = b"class Animal<T> {}; class Dog extends Animal<String> {};"
    nodes, edges = p.extract("Dog.java", code)
    nodes_by_name = {n.name: n for n in nodes}

    assert "Animal" in nodes_by_name
    assert "Dog" in nodes_by_name
    animal_id = nodes_by_name["Animal"].id
    dog_id = nodes_by_name["Dog"].id

    inherits_edges = [e for e in edges if e.relation_type == "inherits"]
    assert len(inherits_edges) == 1
    assert inherits_edges[0].from_node_id == dog_id
    assert inherits_edges[0].to_node_id == animal_id


def test_java_super_interfaces_implements():
    """Verify class Dog implements Pet, Serializable creates implements edges for all."""
    p = GenericParser("java")
    code = (
        b"interface Pet {}; interface Serializable {}; class Dog implements Pet, Serializable {};"
    )
    nodes, edges = p.extract("Dog.java", code)
    nodes_by_name = {n.name: n for n in nodes}

    assert "Pet" in nodes_by_name
    assert "Serializable" in nodes_by_name
    assert "Dog" in nodes_by_name

    pet_id = nodes_by_name["Pet"].id
    serial_id = nodes_by_name["Serializable"].id
    dog_id = nodes_by_name["Dog"].id

    implements_edges = [e for e in edges if e.relation_type == "implements"]
    assert len(implements_edges) == 2
    target_ids = {e.to_node_id for e in implements_edges}
    assert pet_id in target_ids
    assert serial_id in target_ids
    assert all(e.from_node_id == dog_id for e in implements_edges)


def test_cpp_base_class_clause_inherits():
    """Verify C++ class Dog : public Animal, public Pet creates inherits edges."""
    p = GenericParser("cpp")
    code = b"class Animal {}; class Pet {}; class Dog : public Animal, public Pet {};"
    nodes, edges = p.extract("dog.cpp", code)
    nodes_by_name = {n.name: n for n in nodes}

    assert "Animal" in nodes_by_name
    assert "Pet" in nodes_by_name
    assert "Dog" in nodes_by_name

    animal_id = nodes_by_name["Animal"].id
    pet_id = nodes_by_name["Pet"].id
    dog_id = nodes_by_name["Dog"].id

    inherits_edges = [e for e in edges if e.relation_type == "inherits"]
    assert len(inherits_edges) == 2
    target_ids = {e.to_node_id for e in inherits_edges}
    assert animal_id in target_ids
    assert pet_id in target_ids
    assert all(e.from_node_id == dog_id for e in inherits_edges)


def test_csharp_base_list_inherits_and_implements():
    """Verify C# base_list links class inheritance and interface implementation."""
    p = GenericParser("c_sharp")
    code = b"class Animal {}; interface IPet {}; class Dog : Animal, IPet {};"
    nodes, edges = p.extract("Dog.cs", code)
    nodes_by_name = {n.name: n for n in nodes}

    animal_id = nodes_by_name["Animal"].id
    ipet_id = nodes_by_name["IPet"].id
    dog_id = nodes_by_name["Dog"].id

    inherits_edges = [e for e in edges if e.relation_type == "inherits"]
    implements_edges = [e for e in edges if e.relation_type == "implements"]

    assert len(inherits_edges) == 1
    assert inherits_edges[0].from_node_id == dog_id
    assert inherits_edges[0].to_node_id == animal_id

    assert len(implements_edges) == 1
    assert implements_edges[0].from_node_id == dog_id
    assert implements_edges[0].to_node_id == ipet_id


def test_root_sibling_and_subpackage_isolation():
    """Verify root files find root siblings and folder files do not pollute with subpackages."""
    r = ModuleResolver(Path("."))
    r.known_files.add("Utils.java")
    r.known_files.add("src/models/Order.java")
    r.known_files.add("src/models/dto/OrderDTO.java")

    p = GenericParser("java", resolver=r)
    # 1. Root file Main.java must find Utils.java
    _, edges_root = p.extract("Main.java", b"class Main { void run() { Utils.helper(); } }")
    call_edges = [e for e in edges_root if e.relation_type == "calls"]
    assert len(call_edges) == 1
    expected_id = hashlib.md5(b"Utils.java::Utils.helper").hexdigest()
    assert call_edges[0].to_node_id == expected_id

    # 2. Package file src/models/User.java must resolve sibling Order, but NOT OrderDTO
    user_code = b"class User { void run() { Order.process(); } }"
    _, edges_pkg = p.extract("src/models/User.java", user_code)
    pkg_calls = [e for e in edges_pkg if e.relation_type == "calls"]
    assert len(pkg_calls) == 1
    expected_order_id = hashlib.md5(b"src/models/Order.java::Order.process").hexdigest()
    assert pkg_calls[0].to_node_id == expected_order_id
