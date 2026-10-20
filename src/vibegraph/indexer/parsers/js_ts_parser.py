from tree_sitter import Node

from vibegraph.indexer.db import Edge
from vibegraph.indexer.db import Node as DBNode
from vibegraph.indexer.parsers.base import LanguageParser, ParserContext
from vibegraph.indexer.resolver import ModuleResolver

_FUNC_TYPES = {
    "function_declaration",
    "arrow_function",
    "function",
    "method_definition",
    "method_signature",
}
_SYM_TYPES = {
    "function_declaration",
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "type_alias_declaration",
}


class JavaScriptParser(LanguageParser):
    """Parser for JavaScript and JSX files."""

    def __init__(self, resolver: ModuleResolver | None = None, language: str = "javascript"):
        super().__init__(language, resolver)

    def _record_ts_val_type(self, target_name: str, val_node: Node, ctx: ParserContext) -> None:
        if val_node.type == "new_expression":
            if ctor := val_node.child_by_field_name("constructor"):
                ctx.local_types[target_name] = self._get_text(ctor).strip()
        elif val_node.type == "identifier":
            r_name = self._get_text(val_node).strip()
            if r_name in ctx.local_types:
                ctx.local_types[target_name] = ctx.local_types[r_name]

    def _handle_es_import(self, node: Node, ctx: ParserContext) -> None:
        if not (source_node := node.child_by_field_name("source")):
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

    def _handle_es_export(self, node: Node, ctx: ParserContext) -> None:
        source_node = node.child_by_field_name("source")
        if not source_node:
            for child in node.children:
                if child.type == "export_clause":
                    for spec in child.children:
                        if spec.type == "export_specifier":
                            name_node = spec.child_by_field_name("name")
                            if name_node:
                                ctx.defined_symbols.add(self._get_text(name_node))
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

        # Check wildcard: export * from './mod'
        is_wildcard = any(
            c.type == "*" or (c.type == "identifier" and self._get_text(c) == "*")
            for c in node.children
        )
        if is_wildcard:
            ctx.imported_symbols["*"] = (resolved_path or module_path, "__module__")
            return

        for child in node.children:
            if child.type == "export_clause":
                for spec in child.children:
                    if spec.type == "export_specifier":
                        name_node = spec.child_by_field_name("name")
                        alias_node = spec.child_by_field_name("alias")
                        if name_node:
                            orig = self._get_text(name_node)
                            local_exported = self._get_text(alias_node) if alias_node else orig
                            export_node_id = self._get_id(ctx.file_path, local_exported)
                            if not any(n.id == export_node_id for n in ctx.nodes):
                                exp_node = self._create_node(
                                    export_node_id,
                                    local_exported,
                                    "variable",
                                    ctx.file_path,
                                    spec,
                                    visibility="exported",
                                    qualified_name=local_exported,
                                    parent_id=ctx.file_module_id,
                                )
                                ctx.nodes.append(exp_node)
                                ctx.edges.append(
                                    self._create_edge(
                                        ctx.file_module_id, export_node_id, "defines", node=spec
                                    )
                                )
                            if resolved_path:
                                target_id = self._get_id(resolved_path, orig)
                                ctx.edges.append(
                                    self._create_edge(
                                        export_node_id, target_id, "references", node=spec
                                    )
                                )
                                ctx.imported_symbols[local_exported] = (resolved_path, orig)

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

        # Pre-populate defined top-level symbol names to avoid false-positive builtin filtering
        for child in tree.root_node.children:
            target = child
            if child.type == "export_statement":
                for c in child.children:
                    if c.type in (
                        "function_declaration",
                        "class_declaration",
                        "interface_declaration",
                    ):
                        target = c
                        break
                    if c.type == "export_clause":
                        for spec in c.children:
                            if spec.type == "export_specifier" and (
                                sn := spec.child_by_field_name("name")
                            ):
                                ctx.defined_symbols.add(self._get_text(sn))
            if target.type in _SYM_TYPES and (name_node := target.child_by_field_name("name")):
                ctx.defined_symbols.add(self._get_text(name_node))

        def traverse(node: Node, parent_id: str | None = None, is_exported: bool = False) -> None:
            if node.type == "export_statement":
                source_node = node.child_by_field_name("source")
                if source_node:
                    self._handle_es_export(node, ctx)
                    return
                for child in node.children:
                    traverse(child, parent_id, is_exported=True)
                return

            if node.type == "import_statement":
                self._handle_es_import(node, ctx)
                return

            node_id = None
            pushed_scope = False
            saved_local_types: dict[str, str] | None = None
            visibility = "exported" if is_exported else "internal"

            if node.type in _FUNC_TYPES:
                saved_local_types = dict(ctx.local_types)
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
                if params_node:
                    for p in params_node.children:
                        if p.type in ("required_parameter", "optional_parameter"):
                            p_name = next(
                                (self._get_text(c) for c in p.children if c.type == "identifier"),
                                None,
                            )
                            p_type = next(
                                (
                                    self._get_text(c).lstrip(": ").strip()
                                    for c in p.children
                                    if c.type == "type_annotation"
                                ),
                                None,
                            )
                            if p_name and p_type:
                                ctx.local_types[p_name] = p_type

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

            elif node.type in ("public_field_definition", "field_definition"):
                f_name_node = node.child_by_field_name("name")
                f_type_node = node.child_by_field_name("type")
                f_val_node = node.child_by_field_name("value")
                if f_name_node:
                    f_name = f"this.{self._get_text(f_name_node)}"
                    if f_type_node and (f_type := self._get_text(f_type_node).lstrip(": ").strip()):
                        ctx.local_types[f_name] = f_type
                    elif f_val_node:
                        self._record_ts_val_type(f_name, f_val_node, ctx)

            elif node.type == "assignment_expression":
                left_node = node.child_by_field_name("left")
                right_node = node.child_by_field_name("right")
                if left_node and right_node:
                    self._record_ts_val_type(self._get_text(left_node).strip(), right_node, ctx)

            elif node.type == "variable_declarator":
                v_name_node = node.child_by_field_name("name")
                type_node = node.child_by_field_name("type")
                val_node = node.child_by_field_name("value")
                if v_name_node:
                    v_name = self._get_text(v_name_node)
                    # Exported constant/variable (excluding arrow functions)
                    if is_exported and (not val_node or val_node.type != "arrow_function"):
                        v_qid = self._scoped_name(v_name, ctx.scope_stack)
                        v_id = self._get_id(file_path, v_qid)
                        if not any(n.id == v_id for n in nodes):
                            nodes.append(
                                self._create_node(
                                    v_id,
                                    v_name,
                                    "variable",
                                    file_path,
                                    node,
                                    visibility="exported",
                                    qualified_name=v_qid,
                                    parent_id=parent_id,
                                )
                            )
                            if parent_id:
                                edges.append(
                                    self._create_edge(parent_id, v_id, "defines", node=node)
                                )
                    if type_node and (v_type := self._get_text(type_node).lstrip(": ").strip()):
                        ctx.local_types[v_name] = v_type
                    elif val_node:
                        self._record_ts_val_type(v_name, val_node, ctx)

            elif node.type in (
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "type_alias_declaration",
            ):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    qualified_name = self._scoped_name(name, ctx.scope_stack)
                    node_id = self._get_id(file_path, qualified_name)
                    kind = (
                        "interface"
                        if node.type in ("interface_declaration", "type_alias_declaration")
                        else "enum"
                        if node.type == "enum_declaration"
                        else "class"
                    )

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
                            if child.type in ("extends_clause", "implements_clause"):
                                rel = "inherits" if child.type == "extends_clause" else "implements"
                                for type_node in child.children:
                                    if type_node.type in ("identifier", "type_identifier"):
                                        t_name = self._get_text(type_node)
                                        t_id = self._resolve_call_target(t_name, ctx)
                                        if t_id:
                                            edge = self._create_edge(
                                                node_id, t_id, rel, node=type_node
                                            )
                                            edges.append(edge)

                    ctx.scope_stack.append(name)
                    pushed_scope = True

            if parent_id and node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node:
                    called_name = self._get_text(func_node)
                    if target_id := self._resolve_call_target(called_name, ctx):
                        edges.append(self._create_edge(parent_id, target_id, "calls", node=node))

                    # Check for clientside or backend API requests/routes
                    if args_node := node.child_by_field_name("arguments"):
                        args = [c for c in args_node.children if c.type not in ("(", ")", ",")]
                        if args and args[0].type in ("string", "template_string"):
                            raw_url = self._get_text(args[0]).strip("'\"`")
                            if raw_url.startswith("/") or "/api/" in raw_url:
                                method = "GET"
                                lower_func = called_name.lower()
                                for m in ("post", "put", "delete", "patch"):
                                    if f".{m}" in lower_func:
                                        method = m.upper()
                                        break

                                endpoint_name = f"{method} {raw_url}"
                                endpoint_id = self._get_id(
                                    "__endpoint__", endpoint_name, kind="endpoint"
                                )
                                if not any(n.id == endpoint_id for n in nodes):
                                    nodes.append(
                                        self._create_node(
                                            endpoint_id,
                                            endpoint_name,
                                            "endpoint",
                                            file_path,
                                            node,
                                            qualified_name=endpoint_name,
                                            parent_id=file_module_id,
                                        )
                                    )
                                # Check backend route definition: app.get("/users", handler)
                                is_backend_route = (
                                    any(
                                        called_name.startswith(pfx)
                                        for pfx in ("app.", "router.", "server.", "api.")
                                    )
                                    and len(args) >= 2
                                )
                                if is_backend_route:
                                    handler_arg = args[-1]
                                    if handler_arg.type == "identifier":
                                        h_name = self._get_text(handler_arg)
                                        h_target_id = self._resolve_call_target(h_name, ctx)
                                        if h_target_id:
                                            edges.append(
                                                self._create_edge(
                                                    endpoint_id,
                                                    h_target_id,
                                                    "routes",
                                                    node=handler_arg,
                                                )
                                            )
                                else:
                                    # Clientside call: caller -> endpoint
                                    edges.append(
                                        self._create_edge(
                                            parent_id, endpoint_id, "routes", node=node
                                        )
                                    )

            elif parent_id and node.type in ("jsx_self_closing_element", "jsx_opening_element"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    tag_name = self._get_text(name_node)
                    if tag_name and tag_name[0].isupper():
                        target_id = self._resolve_call_target(tag_name, ctx)
                        if target_id:
                            edges.append(
                                self._create_edge(parent_id, target_id, "calls", node=node)
                            )

            current_scope_id = node_id if node_id else parent_id
            for child in node.children:
                traverse(child, current_scope_id, is_exported=is_exported)

            if pushed_scope:
                ctx.scope_stack.pop()

            if saved_local_types is not None:
                if node.type in _FUNC_TYPES and ctx.scope_stack:
                    for k, v in ctx.local_types.items():
                        if k.startswith("this."):
                            saved_local_types[k] = v
                ctx.local_types = saved_local_types

        traverse(tree.root_node, file_module_id)
        return nodes, edges


class TypeScriptParser(JavaScriptParser):
    """Parser for TypeScript and TSX files."""

    def __init__(self, language: str = "typescript", resolver: ModuleResolver | None = None):
        super().__init__(resolver=resolver, language=language)
