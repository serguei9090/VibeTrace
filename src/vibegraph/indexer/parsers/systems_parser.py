
from tree_sitter import Node

from vibegraph.indexer.db import Edge
from vibegraph.indexer.db import Node as DBNode
from vibegraph.indexer.parsers.base import LanguageParser, ParserContext
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
        ctx = ParserContext(file_path, nodes, edges, file_module_id)

        def traverse(node: Node, parent_id: str | None = None) -> None:
            node_id = None

            if node.type in ("function_declaration", "method_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    qualified_name = name
                    if node.type == "method_declaration":
                        recv_node = node.child_by_field_name("receiver")
                        if recv_node:
                            recv_text = (
                                self._get_text(recv_node).strip("()").split()[-1].lstrip("*")
                            )
                            if recv_text:
                                qualified_name = f"{recv_text}.{name}"

                    node_id = self._get_id(file_path, qualified_name)
                    params_node = node.child_by_field_name("parameters")
                    signature = self._get_text(params_node) if params_node else None

                    nodes.append(
                        self._create_node(
                            node_id,
                            name,
                            "function",
                            file_path,
                            node,
                            signature,
                            qualified_name=qualified_name,
                            parent_id=parent_id,
                        )
                    )

                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id, "defines", node=node))

            elif node.type == "type_declaration":
                for child in node.children:
                    if child.type == "type_spec":
                        name_node = child.child_by_field_name("name")
                        type_node = child.child_by_field_name("type")
                        if name_node:
                            name = self._get_text(name_node)
                            node_id = self._get_id(file_path, name)
                            is_iface = type_node and type_node.type == "interface_type"
                            kind = "interface" if is_iface else "struct"

                            nodes.append(
                                self._create_node(
                                    node_id,
                                    name,
                                    kind,
                                    file_path,
                                    child,
                                    qualified_name=name,
                                    parent_id=parent_id,
                                )
                            )

                            if parent_id:
                                edges.append(
                                    self._create_edge(parent_id, node_id, "defines", node=child)
                                )

                            if type_node and type_node.type == "struct_type":
                                for struct_child in type_node.children:
                                    if struct_child.type == "field_declaration_list":
                                        for f in struct_child.children:
                                            if f.type == "field_declaration":
                                                has_field_id = any(
                                                    fc.type == "field_identifier"
                                                    for fc in f.children
                                                )
                                                if not has_field_id:
                                                    emb_type = self._get_text(f).strip("* ")
                                                    if emb_type:
                                                        emb_id = self._resolve_call_target(
                                                            emb_type, ctx
                                                        )
                                                        edges.append(
                                                            self._create_edge(
                                                                node_id,
                                                                emb_id,
                                                                "inherits",
                                                                node=f,
                                                            )
                                                        )
                            elif type_node and type_node.type == "interface_type":
                                for iface_child in type_node.children:
                                    if iface_child.type == "method_spec":
                                        m_name_node = iface_child.child_by_field_name("name")
                                        if m_name_node:
                                            m_name = self._get_text(m_name_node)
                                            m_qual = f"{name}.{m_name}"
                                            m_id = self._get_id(file_path, m_qual)
                                            nodes.append(
                                                self._create_node(
                                                    m_id,
                                                    m_name,
                                                    "function",
                                                    file_path,
                                                    iface_child,
                                                    qualified_name=m_qual,
                                                    parent_id=node_id,
                                                )
                                            )
                                            edges.append(
                                                self._create_edge(
                                                    node_id, m_id, "defines", node=iface_child
                                                )
                                            )

            elif parent_id and node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node:
                    called_name = self._get_text(func_node)
                    target_id = self._resolve_call_target(called_name, ctx)
                    edges.append(self._create_edge(parent_id, target_id, "calls", node=node))

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
        ctx = ParserContext(file_path, nodes, edges, file_module_id)

        def traverse(node: Node, parent_id: str | None = None) -> None:
            node_id = None
            pushed_scope = False

            if node.type in ("function_item", "function_signature_item"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    qualified_name = self._scoped_name(name, ctx.scope_stack)
                    node_id = self._get_id(file_path, qualified_name)
                    params_node = node.child_by_field_name("parameters")
                    signature = self._get_text(params_node) if params_node else None

                    nodes.append(
                        self._create_node(
                            node_id,
                            name,
                            "function",
                            file_path,
                            node,
                            signature,
                            qualified_name=qualified_name,
                            parent_id=parent_id,
                        )
                    )

                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id, "defines", node=node))

            elif node.type == "trait_item":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    qualified_name = self._scoped_name(name, ctx.scope_stack)
                    node_id = self._get_id(file_path, qualified_name)
                    nodes.append(
                        self._create_node(
                            node_id,
                            name,
                            "trait",
                            file_path,
                            node,
                            qualified_name=qualified_name,
                            parent_id=parent_id,
                        )
                    )
                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id, "defines", node=node))
                    ctx.scope_stack.append(name)
                    pushed_scope = True

            elif node.type == "struct_item":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    qualified_name = self._scoped_name(name, ctx.scope_stack)
                    node_id = self._get_id(file_path, qualified_name)
                    nodes.append(
                        self._create_node(
                            node_id,
                            name,
                            "struct",
                            file_path,
                            node,
                            qualified_name=qualified_name,
                            parent_id=parent_id,
                        )
                    )
                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id, "defines", node=node))
                    ctx.scope_stack.append(name)
                    pushed_scope = True

            elif node.type == "impl_item":
                type_node = node.child_by_field_name("type")
                trait_node = node.child_by_field_name("trait")
                if type_node:
                    struct_name = self._get_text(type_node)
                    struct_id = self._get_id(file_path, struct_name)
                    if trait_node:
                        trait_name = self._get_text(trait_node)
                        trait_id = self._resolve_call_target(trait_name, ctx)
                        edges.append(
                            self._create_edge(struct_id, trait_id, "implements", node=node)
                        )
                    ctx.scope_stack.append(struct_name)
                    pushed_scope = True
                    node_id = struct_id

            elif parent_id and node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node:
                    called_name = self._get_text(func_node).replace("::", ".")
                    target_id = self._resolve_call_target(called_name, ctx)
                    edges.append(self._create_edge(parent_id, target_id, "calls", node=node))

            current_scope_id = node_id if node_id else parent_id
            for child in node.children:
                traverse(child, current_scope_id)

            if pushed_scope:
                ctx.scope_stack.pop()

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
        ctx = ParserContext(file_path, nodes, edges, file_module_id)

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

        def traverse(node: Node, parent_id: str | None = None) -> None:
            node_id = None
            pushed_scope = False

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
                    qualified_name = self._scoped_name(name, ctx.scope_stack)
                    node_id = self._get_id(file_path, qualified_name)
                    nodes.append(
                        self._create_node(
                            node_id,
                            name,
                            "function",
                            file_path,
                            node,
                            qualified_name=qualified_name,
                            parent_id=parent_id,
                        )
                    )

                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id, "defines", node=node))

            elif node.type in class_types:
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    qualified_name = self._scoped_name(name, ctx.scope_stack)
                    node_id = self._get_id(file_path, qualified_name)
                    nodes.append(
                        self._create_node(
                            node_id,
                            name,
                            "class",
                            file_path,
                            node,
                            qualified_name=qualified_name,
                            parent_id=parent_id,
                        )
                    )

                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id, "defines", node=node))

                    ctx.scope_stack.append(name)
                    pushed_scope = True

            current_scope_id = node_id if node_id else parent_id
            for child in node.children:
                traverse(child, current_scope_id)

            if pushed_scope:
                ctx.scope_stack.pop()

        traverse(tree.root_node, file_module_id)
        return nodes, edges
