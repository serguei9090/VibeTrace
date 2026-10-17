from tree_sitter import Node

from vibegraph.indexer.db import Edge
from vibegraph.indexer.db import Node as DBNode
from vibegraph.indexer.parsers.base import LanguageParser, ParserContext
from vibegraph.indexer.parsers.route_extractor import extract_python_routes
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
    ) -> None:
        # Special handling for decorated definitions
        if node.type == "decorated_definition":
            self._handle_decorated(node, parent_id, ctx)
            return

        node_id = None
        pushed_scope = False

        # 1. Definitions
        if node.type in ("function_definition", "class_definition"):
            node_id, def_name = self._handle_definition(node, parent_id, ctx, decorators)
            if def_name:
                ctx.scope_stack.append(def_name)
                pushed_scope = True

        # 2. Calls
        elif parent_id and node.type == "call":
            self._handle_call(node, parent_id, ctx)

        # 3. Imports
        elif node.type in ("import_statement", "import_from_statement"):
            self._handle_import(node, ctx)

        # 4. Dispatch maps: dictionary with symbol references
        elif parent_id and node.type == "dictionary":
            self._handle_dictionary(node, parent_id, ctx)

        # Recurse
        current_scope_id = node_id if node_id else parent_id
        for child in node.children:
            self._traverse(child, current_scope_id, ctx)

        if pushed_scope:
            ctx.scope_stack.pop()

    def _handle_dictionary(self, node: Node, parent_id: str, ctx: ParserContext) -> None:
        for child in node.children:
            if child.type == "pair":
                val_node = child.child_by_field_name("value")
                if val_node and val_node.type in ("identifier", "attribute"):
                    val_name = self._get_text(val_node)
                    target_id = self._resolve_call_target(val_name, ctx)
                    ctx.edges.append(
                        self._create_edge(parent_id, target_id, "references", node=val_node)
                    )

    def _handle_decorated(self, node: Node, parent_id: str | None, ctx: ParserContext) -> None:
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
    ) -> tuple[str | None, str | None]:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None, None

        name = self._get_text(name_node)
        qualified_name = self._scoped_name(name, ctx.scope_stack)
        node_id = self._get_id(ctx.file_path, qualified_name)
        kind = "function" if node.type == "function_definition" else "class"

        params_node = node.child_by_field_name("parameters")
        signature = self._get_text(params_node) if params_node else None
        docstring = self._extract_docstring(node)

        visibility = "public"
        if name.startswith("_") and not name.startswith("__"):
            visibility = "private"

        new_node = self._create_node(
            node_id,
            name,
            kind,
            ctx.file_path,
            node,
            signature,
            docstring,
            decorators=decorators,
            visibility=visibility,
            qualified_name=qualified_name,
            parent_id=parent_id,
        )
        ctx.nodes.append(new_node)

        if decorators:
            endpoint_nodes, route_edges = extract_python_routes(
                decorators, new_node, ctx.file_path, self._get_id
            )
            ctx.nodes.extend(endpoint_nodes)
            ctx.edges.extend(route_edges)
            if parent_id:
                for ep in endpoint_nodes:
                    ctx.edges.append(self._create_edge(parent_id, ep.id, "defines", node=node))

        if parent_id:
            ctx.edges.append(self._create_edge(parent_id, node_id, "defines", node=node))

        # Extract base classes on class_definition
        if node.type == "class_definition":
            superclasses = node.child_by_field_name("superclasses")
            if superclasses:
                for arg in superclasses.children:
                    if arg.type in ("identifier", "attribute"):
                        base_name = self._get_text(arg)
                        base_id = self._resolve_call_target(base_name, ctx)
                        ctx.edges.append(self._create_edge(node_id, base_id, "inherits", node=arg))

        return node_id, name

    def _handle_call(self, node: Node, parent_id: str, ctx: ParserContext) -> None:
        func_node = node.child_by_field_name("function")
        if not func_node:
            return

        called_name = self._get_text(func_node)

        # Dynamic heuristic 1: getattr(obj, "method")
        if called_name == "getattr":
            args_node = node.child_by_field_name("arguments")
            if args_node:
                args = [c for c in args_node.children if c.type not in ("(", ")", ",")]
                if len(args) >= 2 and args[1].type == "string":
                    attr_name = self._get_text(args[1]).strip("'\"")
                    obj_name = self._get_text(args[0])
                    target_id = self._resolve_call_target(f"{obj_name}.{attr_name}", ctx)
                    ctx.edges.append(self._create_edge(parent_id, target_id, "calls", node=node))
                    return

        # Dynamic heuristic 2: getattr(obj, "method")()
        if func_node.type == "call":
            sub_func = func_node.child_by_field_name("function")
            if sub_func and self._get_text(sub_func) == "getattr":
                sub_args = func_node.child_by_field_name("arguments")
                if sub_args:
                    args = [c for c in sub_args.children if c.type not in ("(", ")", ",")]
                    if len(args) >= 2 and args[1].type == "string":
                        attr_name = self._get_text(args[1]).strip("'\"")
                        obj_name = self._get_text(args[0])
                        target_id = self._resolve_call_target(f"{obj_name}.{attr_name}", ctx)
                        ctx.edges.append(
                            self._create_edge(parent_id, target_id, "calls", node=node)
                        )
                        return

        target_id = self._resolve_call_target(called_name, ctx)
        ctx.edges.append(self._create_edge(parent_id, target_id, "calls", node=node))

    def _handle_import(self, node: Node, ctx: ParserContext) -> None:
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    module_name = self._get_text(child)
                    resolved = self._add_import_edge(module_name, node, ctx)
                    if resolved:
                        ctx.imported_symbols[module_name] = (resolved, "__module__")
                        ctx.imported_symbols[module_name.split(".")[-1]] = (resolved, "__module__")
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    alias_node = child.child_by_field_name("alias")
                    if name_node:
                        module_name = self._get_text(name_node)
                        alias = self._get_text(alias_node) if alias_node else module_name
                        resolved = self._add_import_edge(module_name, node, ctx)
                        if resolved:
                            ctx.imported_symbols[alias] = (resolved, "__module__")

        elif node.type == "import_from_statement":
            module_name_node = node.child_by_field_name("module_name")
            if not module_name_node:
                return
            module_name = self._get_text(module_name_node)
            resolved_mod = self._add_import_edge(module_name, node, ctx)

            # Collect imported symbol names after the 'import' keyword
            seen_import_kw = False
            for child in node.children:
                if child.type == "import":
                    seen_import_kw = True
                    continue
                if not seen_import_kw:
                    continue

                orig_name: str | None = None
                local_name: str | None = None
                if child.type == "dotted_name":
                    orig_name = self._get_text(child)
                    local_name = orig_name
                elif child.type == "aliased_import":
                    n_node = child.child_by_field_name("name")
                    a_node = child.child_by_field_name("alias")
                    if n_node:
                        orig_name = self._get_text(n_node)
                        local_name = self._get_text(a_node) if a_node else orig_name

                if orig_name and local_name:
                    # Check if `from pkg import submodule` points to a submodule file
                    sub_query = (
                        f"{module_name}{orig_name}"
                        if module_name.endswith(".")
                        else f"{module_name}.{orig_name}"
                    )
                    sub_resolved = (
                        self.resolver.resolve(sub_query, ctx.file_path) if self.resolver else None
                    )
                    if sub_resolved:
                        self._add_import_edge(sub_query, node, ctx)
                        ctx.imported_symbols[local_name] = (sub_resolved, "__module__")
                    elif resolved_mod:
                        ctx.imported_symbols[local_name] = (resolved_mod, orig_name)

    def _add_import_edge(
        self, module_name: str, import_node: Node, ctx: ParserContext
    ) -> str | None:
        resolved_path = None
        if self.resolver:
            resolved_path = self.resolver.resolve(module_name, ctx.file_path)

        module_path = resolved_path if resolved_path else "external"
        canonical_mod = module_name
        if resolved_path and self.resolver:
            canonical = self.resolver.get_module_name(resolved_path)
            if canonical:
                canonical_mod = canonical

        module_id = self._get_id(module_path, canonical_mod, kind="module")

        if not any(n.id == module_id for n in ctx.nodes):
            ctx.nodes.append(
                self._create_node(module_id, canonical_mod, "module", module_path, import_node)
            )

        ctx.edges.append(
            self._create_edge(ctx.file_module_id, module_id, "imports", node=import_node)
        )
        return resolved_path
