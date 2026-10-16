from tree_sitter import Node

from vibegraph.indexer.db import Edge
from vibegraph.indexer.db import Node as DBNode
from vibegraph.indexer.parsers.base import LanguageParser, ParserContext
from vibegraph.indexer.resolver import ModuleResolver


class PythonParser(LanguageParser):
    """Parser for Python files."""

    def __init__(self, resolver: ModuleResolver | None = None):
        super().__init__("python", resolver)

    def _extract_docstring(self, node: Node) -> str | None:
        body_node = node.child_by_field_name("body")
        if not body_node:
            return None

        for child in body_node.children:
            if child.type == "expression_statement":
                first_child = child.children[0]
                if first_child.type == "string":
                    return self._get_text(first_child).strip("\"'")
            elif child.type == "comment":
                continue
            else:
                break
        return None

    def extract(self, file_path: str, source_code: bytes) -> tuple[list[DBNode], list[Edge]]:
        if not source_code.strip():
            return [], []

        tree = self.parse(source_code)
        nodes: list[DBNode] = []
        edges: list[Edge] = []

        # Create a node for the file itself (module)
        module_name = file_path.split("/")[-1].replace(".py", "")
        if self.resolver:
            canonical = self.resolver.get_module_name(file_path)
            if canonical:
                module_name = canonical

        file_module_id = self._get_id(file_path, module_name, kind="module")
        nodes.append(
            self._create_node(file_module_id, module_name, "module", file_path, tree.root_node)
        )

        ctx = ParserContext(file_path, nodes, edges, file_module_id)
        self._traverse(tree.root_node, file_module_id, ctx)

        return nodes, edges

    def _traverse(
        self,
        node: Node,
        parent_id: str | None,
        ctx: ParserContext,
        decorators: list[str] | None = None,
    ):
        # Special handling for decorated definitions
        if node.type == "decorated_definition":
            self._handle_decorated(node, parent_id, ctx)
            return

        node_id = None

        # 1. Definitions
        if node.type in ("function_definition", "class_definition"):
            node_id = self._handle_definition(node, parent_id, ctx, decorators)

        # 2. Calls
        elif parent_id and node.type == "call":
            self._handle_call(node, parent_id, ctx)

        # 3. Imports
        elif node.type in ("import_statement", "import_from_statement"):
            self._handle_import(node, ctx)

        # Recurse
        current_scope_id = node_id if node_id else parent_id
        for child in node.children:
            self._traverse(child, current_scope_id, ctx)

    def _handle_decorated(self, node: Node, parent_id: str | None, ctx: ParserContext):
        current_decorators = []
        def_node = None
        for child in node.children:
            if child.type == "decorator":
                current_decorators.append(self._get_text(child))
            elif child.type in ("function_definition", "class_definition"):
                def_node = child

        if def_node:
            self._traverse(def_node, parent_id, ctx, current_decorators)

    def _handle_definition(
        self, node: Node, parent_id: str | None, ctx: ParserContext, decorators: list[str] | None
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None

        name = self._get_text(name_node)
        node_id = self._get_id(ctx.file_path, name)
        kind = "function" if node.type == "function_definition" else "class"

        params_node = node.child_by_field_name("parameters")
        signature = self._get_text(params_node) if params_node else None
        docstring = self._extract_docstring(node)

        # Determine visibility
        visibility = "public"
        if name.startswith("_") and not name.startswith("__"):
            visibility = "private"

        ctx.nodes.append(
            self._create_node(
                node_id,
                name,
                kind,
                ctx.file_path,
                node,
                signature,
                docstring,
                decorators=decorators,
                visibility=visibility,
            )
        )

        if parent_id:
            ctx.edges.append(
                Edge(from_node_id=parent_id, to_node_id=node_id, relation_type="defines")
            )
        return node_id

    def _handle_call(self, node: Node, parent_id: str, ctx: ParserContext):
        func_node = node.child_by_field_name("function")
        if func_node:
            called_name = self._get_text(func_node)
            target_id = self._get_id(ctx.file_path, called_name)
            ctx.edges.append(
                Edge(from_node_id=parent_id, to_node_id=target_id, relation_type="calls")
            )

    def _handle_import(self, node: Node, ctx: ParserContext):
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    module_name = self._get_text(child)
                    self._add_import_edge(module_name, node, ctx)
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        module_name = self._get_text(name_node)
                        self._add_import_edge(module_name, node, ctx)

        elif node.type == "import_from_statement":
            module_name_node = node.child_by_field_name("module_name")
            if module_name_node:
                module_name = self._get_text(module_name_node)
                self._add_import_edge(module_name, node, ctx)

    def _add_import_edge(self, module_name: str, import_node: Node, ctx: ParserContext):
        resolved_path = None
        if self.resolver:
            resolved_path = self.resolver.resolve(module_name, ctx.file_path)

        module_path = resolved_path if resolved_path else "external"
        module_id = self._get_id(module_path, module_name, kind="module")

        if not any(n.id == module_id for n in ctx.nodes):
            ctx.nodes.append(
                self._create_node(module_id, module_name, "module", module_path, import_node)
            )

        ctx.edges.append(self._create_edge(ctx.file_module_id, module_id, "imports"))
