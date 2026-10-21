"""Tests for Round 7 code bug audit findings.

Verifies:
1. Polyglot standard library categorization in graph_service.
2. Windows backslash path normalization in SQLite DB queries.
3. CommonJS exports (exports.func, module.exports = { ... }) in JS parser.
4. Ruby AST node types (class, module, method) in GenericParser.
5. PHP and Ruby import extraction and class inheritance in GenericParser.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.indexer.parsers import ParserFactory
from vibetrace.services.graph_service import categorize_file_dependencies


def test_polyglot_stdlib_categorization() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        db = IndexerDB(db_path=db_path)

        # 1. Go file with fmt (stdlib) and gin (third_party)
        go_file = "server.go"
        go_nodes = [
            Node(
                id="g_main",
                name="main",
                file_path=go_file,
                kind="function",
                start_line=1,
                end_line=10,
            ),
            Node(
                id="g_fmt",
                name="fmt",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
            Node(
                id="g_gin",
                name="github.com/gin-gonic/gin",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
        ]
        go_edges = [
            Edge(from_node_id="g_main", to_node_id="g_fmt", relation_type="imports"),
            Edge(from_node_id="g_main", to_node_id="g_gin", relation_type="imports"),
        ]
        db.batch_save_file_index(go_file, "h_go", go_nodes, go_edges)

        _, go_third, go_std = categorize_file_dependencies(db, go_file)
        assert [d["name"] for d in go_std] == ["fmt"]
        assert [d["name"] for d in go_third] == ["github.com/gin-gonic/gin"]

        # 2. Node/JS file with node:fs (stdlib) and express (third_party)
        js_file = "index.js"
        js_nodes = [
            Node(
                id="j_app",
                name="app",
                file_path=js_file,
                kind="function",
                start_line=1,
                end_line=10,
            ),
            Node(
                id="j_fs",
                name="node:fs",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
            Node(
                id="j_exp",
                name="express",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
        ]
        js_edges = [
            Edge(from_node_id="j_app", to_node_id="j_fs", relation_type="imports"),
            Edge(from_node_id="j_app", to_node_id="j_exp", relation_type="imports"),
        ]
        db.batch_save_file_index(js_file, "h_js", js_nodes, js_edges)

        _, js_third, js_std = categorize_file_dependencies(db, js_file)
        assert [d["name"] for d in js_std] == ["node:fs"]
        assert [d["name"] for d in js_third] == ["express"]

        # 3. Ruby file with json (stdlib) and rails (third_party)
        rb_file = "app.rb"
        rb_nodes = [
            Node(
                id="r_app", name="App", file_path=rb_file, kind="class", start_line=1, end_line=10
            ),
            Node(
                id="r_json",
                name="json",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
            Node(
                id="r_rails",
                name="rails",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
        ]
        rb_edges = [
            Edge(from_node_id="r_app", to_node_id="r_json", relation_type="imports"),
            Edge(from_node_id="r_app", to_node_id="r_rails", relation_type="imports"),
        ]
        db.batch_save_file_index(rb_file, "h_rb", rb_nodes, rb_edges)

        _, rb_third, rb_std = categorize_file_dependencies(db, rb_file)
        assert [d["name"] for d in rb_std] == ["json"]
        assert [d["name"] for d in rb_third] == ["rails"]

    finally:
        Path(db_path).unlink(missing_ok=True)


def test_windows_backslash_db_queries() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        db = IndexerDB(db_path=db_path)
        canonical_path = "src/utils/db.py"
        windows_path = r"src\utils\db.py"

        nodes = [
            Node(
                id="n_query",
                name="execute_query",
                file_path=canonical_path,
                kind="function",
                start_line=10,
                end_line=25,
            ),
            Node(
                id="n_ext",
                name="sqlite3",
                file_path="external",
                kind="module",
                start_line=0,
                end_line=0,
            ),
        ]
        edges = [
            Edge(from_node_id="n_query", to_node_id="n_ext", relation_type="imports"),
        ]
        db.batch_save_file_index(canonical_path, "sha256_dummy", nodes, edges)

        # 1. get_file_structure with Windows backslash
        total, symbols = db.get_file_structure(windows_path)
        assert total == 1
        assert symbols[0]["name"] == "execute_query"

        # 2. get_nodes_in_file with Windows backslash
        file_nodes = db.get_nodes_in_file(windows_path)
        assert len(file_nodes) == 1
        assert file_nodes[0]["name"] == "execute_query"

        # 3. get_file_imports with Windows backslash
        imports = db.get_file_imports(windows_path)
        assert len(imports) == 1
        assert imports[0]["name"] == "sqlite3"

        # 4. find_nodes_by_name scoped with Windows backslash
        matched = db.find_nodes_by_name("execute_query", file_path=windows_path)
        assert len(matched) == 1
        assert matched[0]["id"] == "n_query"

        # 5. get_file_hash with Windows backslash
        h = db.get_file_hash(windows_path)
        assert h == "sha256_dummy"

        # 6. get_graph_data with Windows backslash
        g_data = db.get_graph_data(windows_path)
        assert len(g_data["nodes"]) == 1

        # 7. clear_file with Windows backslash
        db.clear_file(windows_path)
        assert len(db.get_nodes_in_file(canonical_path)) == 0

    finally:
        Path(db_path).unlink(missing_ok=True)


def test_commonjs_export_extraction() -> None:
    parser = ParserFactory.get_parser("lib/service.js")
    assert parser is not None

    source = b"""
    const internalHelper = (x) => x * 2;
    const processData = (items) => items.map(internalHelper);

    exports.calculate = (a, b) => a + b;
    module.exports.multiply = function(a, b) {
        return a * b;
    };

    module.exports = {
        processData,
        aliasHelper: internalHelper,
    };
    """

    nodes, _ = parser.extract("lib/service.js", source)
    nodes_by_name = {n.name: n for n in nodes}

    # calculate exported directly via exports.calculate
    assert "calculate" in nodes_by_name
    assert nodes_by_name["calculate"].visibility == "exported"

    # multiply exported directly via module.exports.multiply
    assert "multiply" in nodes_by_name
    assert nodes_by_name["multiply"].visibility == "exported"

    # processData and internalHelper promoted to exported via module.exports object
    assert "processData" in nodes_by_name
    assert nodes_by_name["processData"].visibility == "exported"
    assert "internalHelper" in nodes_by_name
    assert nodes_by_name["internalHelper"].visibility == "exported"


def test_ruby_ast_node_types_extraction() -> None:
    parser = ParserFactory.get_parser("app/models/user.rb")
    assert parser is not None

    source = b"""
    module Authentication
      class User < BaseRecord
        def initialize(name)
          @name = name
        end

        def self.find_by_name(name)
          User.new(name)
        end
      end
    end
    """

    nodes, edges = parser.extract("app/models/user.rb", source)
    names = {n.name for n in nodes}
    kinds = {n.name: n.kind for n in nodes}

    assert "Authentication" in names
    assert kinds["Authentication"] == "class"

    assert "User" in names
    assert kinds["User"] == "class"

    assert "initialize" in names
    assert kinds["initialize"] == "function"

    assert "find_by_name" in names
    assert kinds["find_by_name"] == "function"

    # User inherits BaseRecord
    inherits_edges = [e for e in edges if e.relation_type == "inherits"]
    assert len(inherits_edges) >= 1


def test_ruby_and_php_import_and_heritage() -> None:
    # 1. Ruby require & require_relative
    rb_parser = ParserFactory.get_parser("app.rb")
    assert rb_parser is not None
    rb_code = b"""
    require 'json'
    require_relative './helpers/auth'

    def run
      Auth.verify
    end
    """
    rb_nodes, rb_edges = rb_parser.extract("app.rb", rb_code)
    rb_imports = [n.name for n in rb_nodes if n.kind == "module" and n.name != "app.rb"]
    assert "json" in rb_imports
    assert "auth" in rb_imports
    assert any(e.relation_type == "imports" for e in rb_edges)

    # 2. PHP use and class heritage
    php_parser = ParserFactory.get_parser("UserController.php")
    assert php_parser is not None
    php_code = b"""
    <?php
    namespace App\\Controllers;

    use App\\Services\\PaymentService;
    use App\\Interfaces\\AuthInterface;

    class UserController extends BaseController implements AuthInterface {
        public function handlePayment() {
            PaymentService::charge(100);
        }
    }
    """
    php_nodes, php_edges = php_parser.extract("UserController.php", php_code)
    php_names = {n.name for n in php_nodes}
    assert "PaymentService" in php_names
    assert "UserController" in php_names
    assert "handlePayment" in php_names

    # Check inherits and implements
    relations = {(e.relation_type) for e in php_edges}
    assert "imports" in relations
    assert "inherits" in relations
    assert "implements" in relations
