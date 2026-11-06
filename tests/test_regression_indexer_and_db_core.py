"""Consolidated regression test suite: test_regression_indexer_and_db_core.py."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.git_utils import get_git_sha
from vibetrace.indexer.main import reindex_all
from vibetrace.indexer.models import Edge, Node
from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.mcp.schemas import ReindexInput
from vibetrace.mcp.server import vibetrace_reindex_project
from vibetrace.services.call_tree_service import build_call_tree, format_call_tree_md
from vibetrace.services.hybrid_search import hybrid_search


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


def test_get_git_sha_supports_sha1_and_sha256():
    """Verify get_git_sha validates both 40-char SHA-1 and 64-char SHA-256 commit hashes."""
    sha1 = "a" * 40
    sha256 = "b" * 64
    invalid_short = "c" * 39
    invalid_chars = "g" * 40

    with patch("shutil.which", return_value="/usr/bin/git"):
        # Test valid SHA-1
        mock_proc1 = MagicMock(returncode=0, stdout=f"{sha1}\n")
        with patch("subprocess.run", return_value=mock_proc1):
            assert get_git_sha(Path(".")) == sha1

        # Test valid SHA-256
        mock_proc2 = MagicMock(returncode=0, stdout=f"{sha256}\n")
        with patch("subprocess.run", return_value=mock_proc2):
            assert get_git_sha(Path(".")) == sha256

        # Test invalid short SHA
        mock_proc3 = MagicMock(returncode=0, stdout=f"{invalid_short}\n")
        with patch("subprocess.run", return_value=mock_proc3):
            assert get_git_sha(Path(".")) is None

        # Test invalid non-hex characters
        mock_proc4 = MagicMock(returncode=0, stdout=f"{invalid_chars}\n")
        with patch("subprocess.run", return_value=mock_proc4):
            assert get_git_sha(Path(".")) is None


def test_go_module_self_package_resolution(tmp_path: Path):
    """Verify ModuleResolver detects go.mod and resolves internal package imports."""
    proj = tmp_path / "go_proj"
    proj.mkdir()
    (proj / "go.mod").write_text("module github.com/myorg/myapp\n\ngo 1.22\n", encoding="utf-8")

    pkg_auth = proj / "pkg" / "auth"
    pkg_auth.mkdir(parents=True)
    auth_file = pkg_auth / "auth.go"
    auth_file.write_text("package auth\n", encoding="utf-8")

    cmd_server = proj / "cmd" / "server"
    cmd_server.mkdir(parents=True)
    main_file = cmd_server / "main.go"
    main_file.write_text("package main\n", encoding="utf-8")

    resolver = ModuleResolver(proj)
    assert resolver.go_module == "github.com/myorg/myapp"

    # Internal package import should resolve to local auth.go
    res = resolver.resolve("github.com/myorg/myapp/pkg/auth", current_file="cmd/server/main.go")
    assert res == "pkg/auth/auth.go"

    # External package import should NOT match internal module and return None
    ext = resolver.resolve("github.com/gin-gonic/gin", current_file="cmd/server/main.go")
    assert ext is None


def test_cargo_crate_self_package_resolution(tmp_path: Path):
    """Verify ModuleResolver detects Cargo.toml and resolves crate:: and crate_name imports."""
    proj = tmp_path / "rust_proj"
    proj.mkdir()
    cargo_content = """[package]
name = "hyper_service"
version = "0.1.0"
edition = "2021"
"""
    (proj / "Cargo.toml").write_text(cargo_content, encoding="utf-8")

    src = proj / "src"
    src.mkdir()
    (src / "lib.rs").write_text("pub mod config;\n", encoding="utf-8")
    (src / "config.rs").write_text("pub struct Config;\n", encoding="utf-8")

    resolver = ModuleResolver(proj)
    assert resolver.cargo_package == "hyper_service"

    # Test resolving hyper_service::config
    res_pkg = resolver.resolve("hyper_service::config", current_file="src/lib.rs")
    assert res_pkg == "src/config.rs"

    # Test resolving crate/config
    res_crate = resolver.resolve("crate/config", current_file="src/lib.rs")
    assert res_crate == "src/config.rs"


def test_call_tree_traverses_routes_relation(tmp_path: Path):
    """Verify call tree automatically traverses 'routes' relation from endpoint to handler."""
    db = IndexerDB(str(tmp_path / "test.db"))

    endpoint_node = Node(
        id="endpoint_users",
        name="GET /api/users",
        qualified_name="GET /api/users",
        kind="endpoint",
        file_path="src/routes.py",
        start_line=10,
        end_line=10,
    )
    handler_node = Node(
        id="func_get_users",
        name="get_users",
        qualified_name="routes.get_users",
        kind="function",
        file_path="src/routes.py",
        start_line=12,
        end_line=15,
    )
    db.upsert_node(endpoint_node)
    db.upsert_node(handler_node)

    # Web endpoint route connection
    db.upsert_edge(
        Edge(
            from_node_id="endpoint_users",
            to_node_id="func_get_users",
            relation_type="routes",
            call_line=10,
        )
    )

    res = build_call_tree(db, "GET /api/users", max_depth=2)
    assert res.found is True
    assert res.tree is not None
    assert len(res.tree.children) == 1
    assert res.tree.children[0].name == "get_users"
    assert res.tree.children[0].relation_type == "routes"


def test_format_call_tree_md_external_root(tmp_path: Path):
    """Verify format_call_tree_md formats external root symbols cleanly without :L1."""
    db = IndexerDB(str(tmp_path / "test.db"))
    ext_node = Node(
        id="ext_pkg",
        name="external_lib",
        qualified_name="external_lib",
        kind="module",
        file_path="external",
        start_line=1,
        end_line=1,
    )
    db.upsert_node(ext_node)

    res = build_call_tree(db, "external_lib", max_depth=1)
    md = format_call_tree_md(res)
    assert "(external)" in md
    assert "(external:L1)" not in md


@pytest.mark.asyncio
async def test_mcp_reindex_resolves_relative_path_against_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify vibetrace_reindex_project resolves relative path against root not cwd."""
    project_root = tmp_path / "my_project"
    project_root.mkdir(parents=True)
    target_file = project_root / "src" / "worker.py"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("def work(): pass\n", encoding="utf-8")

    db = IndexerDB(str(project_root / "vibetrace.db"))

    captured_target = []

    def mock_reindex(db_arg, target_path_str, **kwargs):
        captured_target.append(target_path_str)

    monkeypatch.setattr("vibetrace.mcp.server.reindex_all", mock_reindex)
    monkeypatch.setattr(
        "vibetrace.mcp.server._get_context_for_path",
        lambda p, auto_sync=False: (db, project_root),
    )

    # Pass relative path "src/worker.py"
    input_params = ReindexInput(path="src/worker.py")
    res = await vibetrace_reindex_project(input_params)
    assert "Successfully reindexed" in res
    assert len(captured_target) == 1
    assert captured_target[0] == str(target_file.resolve())


def test_single_file_reindex_skips_centrality_on_skip_or_failure(tmp_path: Path):
    """Verify reindex_all skips centrality recalculation when single file is skipped."""
    db = IndexerDB(str(tmp_path / "test.db"))
    db.update_centrality_scores = MagicMock()

    unsupported_file = tmp_path / "data.csv"
    unsupported_file.write_text("a,b,c\n1,2,3\n", encoding="utf-8")

    reindex_all(db, str(unsupported_file), verbose=False, project_root=tmp_path)
    db.update_centrality_scores.assert_not_called()


def test_in_memory_db_path_guard(tmp_path: Path):
    """Verify in-memory SQLite instances skip disk path resolution."""
    db = IndexerDB(":memory:")
    # Should not raise exception or resolve ":memory:" on Windows
    db._resolve_vector_store()
    assert db.vector_store is None

    # Hybrid search on in-memory DB should run without error
    results = hybrid_search(db, "test_query")
    assert results == []
