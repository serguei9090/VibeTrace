from tree_sitter import Node

from vibegraph.indexer.db import Edge
from vibegraph.indexer.db import Node as DBNode
from vibegraph.indexer.parsers.base import LanguageParser, ParserContext
from vibegraph.indexer.resolver import ModuleResolver


class JavaScriptParser(LanguageParser):
    """Parser for JavaScript and JSX files."""

    def __init__(self, resolver: ModuleResolver | None = None, language: str = "javascript"):
        super().__init__(language, resolver)

    def _handle_es_import(self, node: Node, ctx: ParserContext) -> None:
        source_node = node.child_by_field_name("source")
        if not source_node:
            return
        mod_spec = self._get_text(source_node).strip("\"'`")
        resolved_path = self.resolver.resolve(mod_spec, ctx.file_path) if self.resolver else None
        module_path = resolved_path if resolved_path else "external"
        canonical_mod = mod_spec
        if resolved_path and self.resolver:
            canonical = self.resolver.get_module_name(resolved_path)
            if canonical:
                canonical_mod = canonical

        module_id = self._get_id(module_path, canonical_mod, kind="module")
        if not any(n.id == module_id for n in ctx.nodes):
            ctx.nodes.append(
                self._create_node(module_id, canonical_mod, "module", module_path, node)
            )
        ctx.edges.append(self._create_edge(ctx.file_module_id, module_id, "imports", node=node))

        if not resolved_path:
            return

        # Extract imported specifiers
        def visit_clause(n: Node) -> None:
            if n.type == "import_specifier":
                name_node = n.child_by_field_name("name")
                alias_node = n.child_by_field_name("alias")
                if name_node:
                    orig = self._get_text(name_node)
                    local = self._get_text(alias_node) if alias_node else orig
                    ctx.imported_symbols[local] = (resolved_path, orig)
                return
            if n.type == "namespace_import":
                for c in n.children:
                    if c.type == "identifier":
                        ctx.imported_symbols[self._get_text(c)] = (resolved_path, "__module__")
                return
            if n.type == "identifier" and n.parent and n.parent.type == "import_clause":
                ident = self._get_text(n)
                ctx.imported_symbols[ident] = (resolved_path, ident)
                return
            for c in n.children:
                visit_clause(c)

        visit_clause(node)

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

        def traverse(node: Node, parent_id: str | None = None, is_exported: bool = False) -> None:
            if node.type == "export_statement":
                for child in node.children:
                    traverse(child, parent_id, is_exported=True)
                return

            if node.type == "import_statement":
                self._handle_es_import(node, ctx)
                return

            node_id = None
            pushed_scope = False
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
                # Support `const divide = (a, b) => a / b` variable declarator names
                if (
                    name == "<anonymous>"
                    and node.parent
                    and node.parent.type == "variable_declarator"
                ):
                    decl_name = node.parent.child_by_field_name("name")
                    if decl_name:
                        name = self._get_text(decl_name)

                qualified_name = self._scoped_name(name, ctx.scope_stack)
                node_id = self._get_id(file_path, qualified_name)
                kind = "function"
                params_node = node.child_by_field_name("parameters")
                signature = self._get_text(params_node) if params_node else None

                nodes.append(
                    self._create_node(
                        node_id,
                        name,
                        kind,
                        file_path,
                        node,
                        signature,
                        visibility=visibility,
                        qualified_name=qualified_name,
                        parent_id=parent_id,
                    )
                )

                if parent_id:
                    edges.append(self._create_edge(parent_id, node_id, "defines", node=node))

                if name != "<anonymous>":
                    ctx.scope_stack.append(name)
                    pushed_scope = True

            elif node.type in ("class_declaration", "interface_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    qualified_name = self._scoped_name(name, ctx.scope_stack)
                    node_id = self._get_id(file_path, qualified_name)
                    kind = "interface" if node.type == "interface_declaration" else "class"

                    nodes.append(
                        self._create_node(
                            node_id,
                            name,
                            kind,
                            file_path,
                            node,
                            visibility=visibility,
                            qualified_name=qualified_name,
                            parent_id=parent_id,
                        )
                    )

                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id, "defines", node=node))

                    class_heritage = node.child_by_field_name("class_heritage")
                    if class_heritage:
                        for child in class_heritage.children:
                            if child.type == "extends_clause":
                                for type_node in child.children:
                                    if type_node.type in ("identifier", "type_identifier"):
                                        base_name = self._get_text(type_node)
                                        base_id = self._resolve_call_target(base_name, ctx)
                                        edges.append(
                                            self._create_edge(
                                                node_id, base_id, "inherits", node=type_node
                                            )
                                        )
                            elif child.type == "implements_clause":
                                for type_node in child.children:
                                    if type_node.type in ("identifier", "type_identifier"):
                                        iface_name = self._get_text(type_node)
                                        iface_id = self._resolve_call_target(iface_name, ctx)
                                        edges.append(
                                            self._create_edge(
                                                node_id, iface_id, "implements", node=type_node
                                            )
                                        )

                    ctx.scope_stack.append(name)
                    pushed_scope = True

            if parent_id and node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node:
                    called_name = self._get_text(func_node)
                    target_id = self._resolve_call_target(called_name, ctx)
                    edges.append(self._create_edge(parent_id, target_id, "calls", node=node))

            elif parent_id and node.type in ("jsx_self_closing_element", "jsx_opening_element"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    tag_name = self._get_text(name_node)
                    if tag_name and tag_name[0].isupper():
                        target_id = self._resolve_call_target(tag_name, ctx)
                        edges.append(self._create_edge(parent_id, target_id, "calls", node=node))

            current_scope_id = node_id if node_id else parent_id
            for child in node.children:
                traverse(child, current_scope_id, is_exported=is_exported)

            if pushed_scope:
                ctx.scope_stack.pop()

        traverse(tree.root_node, file_module_id)
        return nodes, edges


class TypeScriptParser(JavaScriptParser):
    """Parser for TypeScript and TSX files."""

    def __init__(self, language: str = "typescript", resolver: ModuleResolver | None = None):
        super().__init__(resolver=resolver, language=language)
