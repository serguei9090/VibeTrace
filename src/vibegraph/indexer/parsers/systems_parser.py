from typing import Any

from tree_sitter import Node

from vibegraph.indexer.db import Edge
from vibegraph.indexer.db import Node as DBNode
from vibegraph.indexer.parsers.base import LanguageParser
from vibegraph.indexer.resolver import ModuleResolver


class GoParser(LanguageParser):
    """Parser for Go files."""

    def __init__(self, resolver: ModuleResolver | None = None):
        super().__init__("go", resolver)

    def extract(self, file_path: str, source_code: bytes) -> tuple[list[DBNode], list[Edge]]:
        if not source_code.strip():
            return [], []

        tree = self.parse(source_code)
        nodes: list[DBNode] = []
        edges: list[Edge] = []

        module_name = file_path.split("/")[-1]
        if self.resolver:
            canonical = self.resolver.get_module_name(file_path)
            if canonical:
                module_name = canonical

        file_module_id = self._get_id(file_path, module_name, kind="module")
        nodes.append(
            self._create_node(file_module_id, module_name, "module", file_path, tree.root_node)
        )

        def traverse(node: Node, parent_id: str | None = None):
            node_id = None

            if node.type in ("function_declaration", "method_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    node_id = self._get_id(file_path, name)
                    params_node = node.child_by_field_name("parameters")
                    signature = self._get_text(params_node) if params_node else None

                    nodes.append(
                        self._create_node(node_id, name, "function", file_path, node, signature)
                    )

                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id))

            elif node.type == "type_declaration":
                type_spec = node.child_by_field_name("type")
                if type_spec:
                    name_node = type_spec.child_by_field_name("name")
                    if name_node:
                        name = self._get_text(name_node)
                        node_id = self._get_id(file_path, name)
                        kind = "interface" if type_spec.type == "interface_type" else "struct"

                        nodes.append(self._create_node(node_id, name, kind, file_path, node))

                        if parent_id:
                            edges.append(self._create_edge(parent_id, node_id))

            current_scope_id = node_id if node_id else parent_id
            for child in node.children:
                traverse(child, current_scope_id)

        traverse(tree.root_node, file_module_id)
        return nodes, edges


class RustParser(LanguageParser):
    """Parser for Rust files."""

    def __init__(self, resolver: ModuleResolver | None = None):
        super().__init__("rust", resolver)

    def extract(self, file_path: str, source_code: bytes) -> tuple[list[DBNode], list[Edge]]:
        if not source_code.strip():
            return [], []

        tree = self.parse(source_code)
        nodes: list[DBNode] = []
        edges: list[Edge] = []

        module_name = file_path.split("/")[-1]
        if self.resolver:
            canonical = self.resolver.get_module_name(file_path)
            if canonical:
                module_name = canonical

        file_module_id = self._get_id(file_path, module_name, kind="module")
        nodes.append(
            self._create_node(file_module_id, module_name, "module", file_path, tree.root_node)
        )

        def traverse(node: Node, parent_id: str | None = None):
            node_id = None

            if node.type == "function_item":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    node_id = self._get_id(file_path, name)
                    params_node = node.child_by_field_name("parameters")
                    signature = self._get_text(params_node) if params_node else None

                    nodes.append(
                        self._create_node(node_id, name, "function", file_path, node, signature)
                    )

                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id))

            elif node.type in ("struct_item", "trait_item", "impl_item"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    node_id = self._get_id(file_path, name)
                    kind: Any = "class"
                    if node.type == "trait_item":
                        kind = "trait"
                    elif node.type == "struct_item":
                        kind = "struct"
                    elif node.type == "impl_item":
                        kind = "impl"

                    nodes.append(self._create_node(node_id, name, kind, file_path, node))

                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id))

            current_scope_id = node_id if node_id else parent_id
            for child in node.children:
                traverse(child, current_scope_id)

        traverse(tree.root_node, file_module_id)
        return nodes, edges


class GenericParser(LanguageParser):
    """Generic parser for languages with basic function/class extraction."""

    def __init__(self, language: str, resolver: ModuleResolver | None = None):
        super().__init__(language, resolver)
        self.language_name = language

    def extract(self, file_path: str, source_code: bytes) -> tuple[list[DBNode], list[Edge]]:
        if not source_code.strip():
            return [], []

        tree = self.parse(source_code)
        nodes: list[DBNode] = []
        edges: list[Edge] = []

        module_name = file_path.split("/")[-1]
        if self.resolver:
            canonical = self.resolver.get_module_name(file_path)
            if canonical:
                module_name = canonical

        file_module_id = self._get_id(file_path, module_name, kind="module")
        nodes.append(
            self._create_node(file_module_id, module_name, "module", file_path, tree.root_node)
        )

        function_types = {
            "function_definition",
            "function_declaration",
            "method_declaration",
            "function_item",
            "method_definition",
        }
        class_types = {
            "class_definition",
            "class_declaration",
            "struct_declaration",
            "interface_declaration",
            "trait_item",
        }

        def traverse(node: Node, parent_id: str | None = None):
            node_id = None

            if node.type in function_types:
                name_node = node.child_by_field_name("name") or node.child_by_field_name(
                    "declarator"
                )
                if name_node:
                    name_text = self._get_text(name_node)
                    name = (
                        name_text.split("(")[0].strip().split()[-1]
                        if "(" in name_text
                        else name_text
                    )
                    node_id = self._get_id(file_path, name)
                    nodes.append(self._create_node(node_id, name, "function", file_path, node))

                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id))

            elif node.type in class_types:
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    node_id = self._get_id(file_path, name)
                    nodes.append(self._create_node(node_id, name, "class", file_path, node))

                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id))

            current_scope_id = node_id if node_id else parent_id
            for child in node.children:
                traverse(child, current_scope_id)

        traverse(tree.root_node, file_module_id)
        return nodes, edges
