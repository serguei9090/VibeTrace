from tree_sitter import Node

from vibegraph.indexer.db import Edge
from vibegraph.indexer.db import Node as DBNode
from vibegraph.indexer.parsers.base import LanguageParser
from vibegraph.indexer.resolver import ModuleResolver


class JavaScriptParser(LanguageParser):
    """Parser for JavaScript and JSX files."""

    def __init__(self, resolver: ModuleResolver | None = None):
        super().__init__("javascript", resolver)

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

        def traverse(node: Node, parent_id: str | None = None, is_exported: bool = False):
            if node.type == "export_statement":
                for child in node.children:
                    traverse(child, parent_id, is_exported=True)
                return

            node_id = None
            visibility = "exported" if is_exported else "internal"

            if node.type in (
                "function_declaration",
                "arrow_function",
                "function",
                "method_definition",
            ):
                name_node = node.child_by_field_name("name")
                name = self._get_text(name_node) if name_node else "<anonymous>"
                node_id = self._get_id(file_path, name)
                kind = "function"
                params_node = node.child_by_field_name("parameters")
                signature = self._get_text(params_node) if params_node else None

                nodes.append(
                    self._create_node(
                        node_id, name, kind, file_path, node, signature, visibility=visibility
                    )
                )

                if parent_id:
                    edges.append(self._create_edge(parent_id, node_id))

            elif node.type == "class_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    node_id = self._get_id(file_path, name)

                    nodes.append(
                        self._create_node(
                            node_id, name, "class", file_path, node, visibility=visibility
                        )
                    )

                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id))

                    class_heritage = node.child_by_field_name("class_heritage")
                    if class_heritage:
                        extends_clause = class_heritage.children[0]
                        if extends_clause.type == "extends_clause":
                            base_class_node = extends_clause.children[1]
                            base_class_name = self._get_text(base_class_node)
                            base_id = self._get_id(file_path, base_class_name)
                            edges.append(
                                Edge(
                                    from_node_id=node_id,
                                    to_node_id=base_id,
                                    relation_type="inherits",
                                )
                            )

            if parent_id and node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node:
                    called_name = self._get_text(func_node)
                    target_id = self._get_id(file_path, called_name)
                    edges.append(
                        Edge(from_node_id=parent_id, to_node_id=target_id, relation_type="calls")
                    )

            current_scope_id = node_id if node_id else parent_id
            for child in node.children:
                traverse(child, current_scope_id, is_exported=is_exported)

        traverse(tree.root_node, file_module_id)
        return nodes, edges


class TypeScriptParser(LanguageParser):
    """Parser for TypeScript and TSX files."""

    def __init__(self, language="typescript", resolver: ModuleResolver | None = None):
        super().__init__(language, resolver)

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

        def traverse(
            node: Node,
            parent_id: str | None = None,
            is_exported: bool = False,
        ):
            if node.type == "export_statement":
                for child in node.children:
                    traverse(child, parent_id, is_exported=True)
                return

            node_id = None
            visibility = "exported" if is_exported else "internal"

            if node.type in (
                "function_declaration",
                "arrow_function",
                "function",
                "method_definition",
                "method_signature",
            ):
                name_node = node.child_by_field_name("name")
                name = self._get_text(name_node) if name_node else "<anonymous>"
                node_id = self._get_id(file_path, name)
                kind = "function"
                params_node = node.child_by_field_name("parameters")
                signature = self._get_text(params_node) if params_node else None

                nodes.append(
                    self._create_node(
                        node_id, name, kind, file_path, node, signature, visibility=visibility
                    )
                )

                if parent_id:
                    edges.append(self._create_edge(parent_id, node_id))

            elif node.type in ("class_declaration", "interface_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    node_id = self._get_id(file_path, name)
                    kind = "interface" if node.type == "interface_declaration" else "class"

                    nodes.append(
                        self._create_node(
                            node_id, name, kind, file_path, node, visibility=visibility
                        )
                    )

                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id))

                    class_heritage = node.child_by_field_name("class_heritage")
                    if class_heritage:
                        for child in class_heritage.children:
                            if child.type == "extends_clause":
                                for type_node in child.children:
                                    if type_node.type == "type_identifier":
                                        base_name = self._get_text(type_node)
                                        base_id = self._get_id(file_path, base_name)
                                        edges.append(
                                            Edge(
                                                from_node_id=node_id,
                                                to_node_id=base_id,
                                                relation_type="inherits",
                                            )
                                        )
                            elif child.type == "implements_clause":
                                for type_node in child.children:
                                    if type_node.type == "type_identifier":
                                        interface_name = self._get_text(type_node)
                                        interface_id = self._get_id(file_path, interface_name)
                                        edges.append(
                                            Edge(
                                                from_node_id=node_id,
                                                to_node_id=interface_id,
                                                relation_type="implements",
                                            )
                                        )

            if parent_id and node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node:
                    called_name = self._get_text(func_node)
                    if "." in called_name:
                        called_name = called_name.split(".")[-1]

                    target_id = self._get_id(file_path, called_name)
                    edges.append(
                        Edge(from_node_id=parent_id, to_node_id=target_id, relation_type="calls")
                    )

            current_scope_id = node_id if node_id else parent_id
            for child in node.children:
                traverse(child, current_scope_id)

        traverse(tree.root_node, file_module_id)
        return nodes, edges
