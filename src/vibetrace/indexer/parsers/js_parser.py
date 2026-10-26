from tree_sitter import Node

from vibetrace.indexer.db import Edge
from vibetrace.indexer.db import Node as DBNode
from vibetrace.indexer.parsers.base import LanguageParser, ParserContext
from vibetrace.indexer.parsers.route_extractor import extract_js_endpoint_route
from vibetrace.indexer.resolver import ModuleResolver

_FUNC_TYPES = {
    "function_declaration",
    "function_expression",
    "arrow_function",
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

    def _handle_require(self, node: Node, args: list[Node], ctx: ParserContext) -> None:
        if not args or args[0].type not in ("string", "template_string"):
            return
        res_p = self._register_imported_module(self._get_text(args[0]).strip("'\"`"), ctx, node)
        m_path = res_p or "external"
        if not (
            node.parent
            and node.parent.type == "variable_declarator"
            and (vn := node.parent.child_by_field_name("name"))
        ):
            return
        if vn.type == "identifier":
            v_name = self._get_text(vn)
            ctx.imported_symbols[v_name] = (m_path, "*")
            ctx.defined_symbols.add(v_name)
        elif vn.type == "object_pattern":
            for c in vn.children:
                if c.type == "shorthand_property_identifier_pattern":
                    p = self._get_text(c)
                    ctx.imported_symbols[p] = (m_path, p)
                    ctx.defined_symbols.add(p)
                elif c.type == "pair_pattern":
                    k, v = c.child_by_field_name("key"), c.child_by_field_name("value")
                    if k and v:
                        ctx.imported_symbols[self._get_text(v)] = (m_path, self._get_text(k))
                        ctx.defined_symbols.add(self._get_text(v))

    def _handle_es_import(self, node: Node, ctx: ParserContext) -> None:
        if not (source_node := node.child_by_field_name("source")):
            return
        mod_spec = self._get_text(source_node).strip("\"'`")
        resolved_path = self._register_imported_module(mod_spec, ctx, node)
        m_path = resolved_path or "external"

        def visit_clause(n: Node) -> None:
            if n.type == "import_specifier":
                name_node = n.child_by_field_name("name")
                alias_node = n.child_by_field_name("alias")
                if name_node:
                    orig = self._get_text(name_node)
                    local = self._get_text(alias_node) if alias_node else orig
                    ctx.imported_symbols[local] = (m_path, orig)
                return
            if n.type == "namespace_import":
                for c in n.children:
                    if c.type == "identifier":
                        ctx.imported_symbols[self._get_text(c)] = (m_path, "__module__")
                return
            if n.type == "identifier" and n.parent and n.parent.type == "import_clause":
                ident = self._get_text(n)
                ctx.imported_symbols[ident] = (m_path, ident)
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
                        if spec.type == "export_specifier" and (
                            sn := spec.child_by_field_name("name")
                        ):
                            ctx.defined_symbols.add(self._get_text(sn))
            return

        mod_spec = self._get_text(source_node).strip("\"'`")
        resolved_path = self._register_imported_module(mod_spec, ctx, node)
        module_path = resolved_path if resolved_path else "external"

        is_wildcard = any(
            c.type == "*" or (c.type == "identifier" and self._get_text(c) == "*")
            for c in node.children
        )
        if is_wildcard:
            ctx.imported_symbols["*"] = (resolved_path or module_path, "__module__")
            return

        def _add_reexport(sym: str, orig: str, spec_node: Node, is_ns: bool = False) -> None:
            exp_id = self._get_id(ctx.file_path, sym)
            if not any(n.id == exp_id for n in ctx.nodes):
                self._add_node(
                    ctx,
                    exp_id,
                    sym,
                    "variable",
                    spec_node,
                    parent_id=ctx.file_module_id,
                    qualified_name=sym,
                    visibility="exported",
                )
            if resolved_path:
                t_id = (
                    self._get_id(resolved_path, orig, kind="module")
                    if is_ns
                    else self._get_id(resolved_path, orig)
                )
                ctx.edges.append(self._create_edge(exp_id, t_id, "references", node=spec_node))
                ctx.imported_symbols[sym] = (resolved_path, "__module__" if is_ns else orig)

        for child in node.children:
            if child.type == "namespace_export":
                if id_n := next((c for c in child.children if c.type == "identifier"), None):
                    ns = self._get_text(id_n)
                    _add_reexport(ns, ns, child, is_ns=True)
            elif child.type == "export_clause":
                for spec in child.children:
                    if spec.type == "export_specifier" and (nn := spec.child_by_field_name("name")):
                        orig = self._get_text(nn)
                        an = spec.child_by_field_name("alias")
                        local_exp = self._get_text(an) if an else orig
                        _add_reexport(local_exp, orig, spec, is_ns=False)

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
                    if c.type in _SYM_TYPES:
                        target = c
                        break
                    if c.type == "export_clause":
                        for spec in c.children:
                            if spec.type == "export_specifier" and (
                                sn := spec.child_by_field_name("name")
                            ):
                                ctx.defined_symbols.add(self._get_text(sn))
                    elif c.type == "identifier":
                        ctx.defined_symbols.add(self._get_text(c))
            if target.type in _SYM_TYPES and (name_node := target.child_by_field_name("name")):
                ctx.defined_symbols.add(self._get_text(name_node))

        def traverse(node: Node, parent_id: str | None = None, is_exported: bool = False) -> None:
            if node.type == "export_statement":
                source_node = node.child_by_field_name("source")
                if source_node:
                    self._handle_es_export(node, ctx)
                    return
                for child in node.children:
                    if child.type == "identifier":
                        exp_name = self._get_text(child)
                        ctx.defined_symbols.add(exp_name)
                        for n in ctx.nodes:
                            if n.name == exp_name and n.file_path == file_path:
                                n.visibility = "exported"
                    else:
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
                # Support `const divide = (a, b) => a / b` variable declarators or CommonJS exports
                if name == "<anonymous>" and node.parent:
                    if node.parent.type == "variable_declarator":
                        if decl_name := node.parent.child_by_field_name("name"):
                            name = self._get_text(decl_name)
                    elif node.parent.type == "assignment_expression":
                        if ln := node.parent.child_by_field_name("left"):
                            ln_t = self._get_text(ln).strip()
                            if ln_t.startswith(("exports.", "module.exports.")):
                                name = ln_t.split(".")[-1]
                                visibility = "exported"
                    elif node.parent.type == "export_statement":
                        stem = file_path.rsplit(".", 1)[0].split("/")[-1]
                        if stem in ("index", "mod") and "/" in file_path:
                            parts = file_path.split("/")
                            stem = parts[-2] if len(parts) >= 2 else stem
                        name = stem or "<anonymous>"
                        visibility = "exported"

                qualified_name = self._scoped_name(name, ctx.scope_stack)
                node_id = self._get_id(file_path, qualified_name)
                kind = "function"
                signature = None
                if params_node := node.child_by_field_name("parameters"):
                    signature = self._get_text(params_node)
                    if ret_node := node.child_by_field_name("return_type"):
                        ret_text = self._get_text(ret_node).strip()
                        if ret_text:
                            signature = (
                                f"{signature}{ret_text}"
                                if ret_text.startswith(":")
                                else f"{signature}: {ret_text}"
                            )
                    for p in params_node.children:
                        if p.type in ("required_parameter", "optional_parameter"):
                            pn = next(
                                (self._get_text(c) for c in p.children if c.type == "identifier"),
                                None,
                            )
                            pt = next(
                                (
                                    self._get_text(c).lstrip(": ").strip()
                                    for c in p.children
                                    if c.type == "type_annotation"
                                ),
                                None,
                            )
                            if pn and pt:
                                ctx.local_types[pn] = self._clean_ts_type(pt)
                elif single_param := node.child_by_field_name("parameter"):
                    p_name = self._get_text(single_param).strip()
                    signature = f"({p_name})"
                    if single_param.type == "identifier":
                        ctx.local_types[p_name] = "any"

                self._add_node(
                    ctx,
                    node_id,
                    name,
                    kind,
                    node,
                    parent_id=parent_id,
                    qualified_name=qualified_name,
                    visibility=visibility,
                    signature=signature,
                )

                if name != "<anonymous>":
                    ctx.scope_stack.append(name)
                    pushed_scope = True

            elif node.type in ("public_field_definition", "field_definition"):
                f_name_node = node.child_by_field_name("name")
                f_type_node = node.child_by_field_name("type")
                f_val_node = node.child_by_field_name("value")
                if f_name_node:
                    f_name = f"this.{self._get_text(f_name_node)}"
                    if f_type_node and (f_type := self._clean_ts_type(self._get_text(f_type_node))):
                        ctx.local_types[f_name] = f_type
                    elif f_val_node:
                        self._record_ts_val_type(f_name, f_val_node, ctx)

            elif node.type == "assignment_expression":
                if (ln := node.child_by_field_name("left")) and (
                    rn := node.child_by_field_name("right")
                ):
                    ln_t = self._get_text(ln).strip()
                    self._record_ts_val_type(ln_t, rn, ctx)
                    if ln_t in ("module.exports", "exports") and rn.type == "object":
                        for c in rn.children:
                            sym = (
                                self._get_text(c)
                                if c.type
                                in (
                                    "shorthand_property_identifier",
                                    "shorthand_property_identifier_pattern",
                                )
                                else (
                                    self._get_text(v)
                                    if c.type == "pair"
                                    and (v := c.child_by_field_name("value"))
                                    and v.type == "identifier"
                                    else None
                                )
                            )
                            if sym:
                                for ex_n in nodes:
                                    if ex_n.name == sym:
                                        ex_n.visibility = "exported"
                    elif (
                        ln_t.startswith(("exports.", "module.exports.")) and rn.type == "identifier"
                    ):
                        rn_sym = self._get_text(rn).strip()
                        for ex_n in nodes:
                            if ex_n.name == rn_sym:
                                ex_n.visibility = "exported"

            elif node.type == "variable_declarator":
                v_name_node = node.child_by_field_name("name")
                val_node = node.child_by_field_name("value")
                if v_name_node:
                    v_name = self._get_text(v_name_node)
                    if is_exported and (not val_node or val_node.type != "arrow_function"):
                        v_qid = self._scoped_name(v_name, ctx.scope_stack)
                        v_id = self._get_id(file_path, v_qid)
                        if not any(n.id == v_id for n in nodes):
                            self._add_node(
                                ctx,
                                v_id,
                                v_name,
                                "variable",
                                node,
                                parent_id=parent_id,
                                qualified_name=v_qid,
                                visibility="exported",
                            )
                    if (tn := node.child_by_field_name("type")) and (
                        v_type := self._clean_ts_type(self._get_text(tn))
                    ):
                        ctx.local_types[v_name] = v_type
                    elif val_node:
                        self._record_ts_val_type(v_name, val_node, ctx)

            elif node.type in (
                "class",
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "type_alias_declaration",
            ):
                name_node = node.child_by_field_name("name")
                name = self._get_text(name_node) if name_node else None
                if not name and node.parent:
                    if node.parent.type == "variable_declarator":
                        if decl_name := node.parent.child_by_field_name("name"):
                            name = self._get_text(decl_name)
                    elif node.parent.type == "export_statement":
                        stem = file_path.rsplit(".", 1)[0].split("/")[-1]
                        if stem in ("index", "mod") and "/" in file_path:
                            parts = file_path.split("/")
                            stem = parts[-2] if len(parts) >= 2 else stem
                        name = stem
                if name:
                    qualified_name = self._scoped_name(name, ctx.scope_stack)
                    node_id = self._get_id(file_path, qualified_name)
                    kind = (
                        "interface"
                        if node.type in ("interface_declaration", "type_alias_declaration")
                        else ("enum" if node.type == "enum_declaration" else "class")
                    )

                    self._add_node(
                        ctx,
                        node_id,
                        name,
                        kind,
                        node,
                        parent_id=parent_id,
                        qualified_name=qualified_name,
                        visibility=visibility,
                    )

                    h_types = ("class_heritage", "extends_type_clause")
                    for hn in [c for c in node.children if c.type in h_types]:
                        for cl in hn.children if hn.type == "class_heritage" else [hn]:
                            if cl.type in (
                                "extends_clause",
                                "implements_clause",
                                "extends_type_clause",
                            ):
                                rel = "implements" if cl.type == "implements_clause" else "inherits"
                                for tn in cl.children:
                                    if tn.type in ("identifier", "type_identifier"):
                                        t_name = self._get_text(tn)
                                        if rel == "inherits" and t_name not in ("Object", "any"):
                                            ctx.local_types["super"] = t_name
                                        if t_id := self._resolve_call_target(t_name, ctx):
                                            edges.append(
                                                self._create_edge(node_id, t_id, rel, node=tn)
                                            )

                    ctx.scope_stack.append(name)
                    pushed_scope = True

            if parent_id and node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                args_node = node.child_by_field_name("arguments")
                args = (
                    [c for c in args_node.children if c.type not in ("(", ")", ",")]
                    if args_node
                    else []
                )

                # Dynamic import: import("./module")
                is_dyn_import = any(c.type == "import" for c in node.children) or (
                    func_node and self._get_text(func_node) == "import"
                )
                if is_dyn_import and args and args[0].type in ("string", "template_string"):
                    self._register_imported_module(self._get_text(args[0]).strip("'\"`"), ctx, node)

                # CommonJS require: const { x } = require("./module")
                elif func_node and self._get_text(func_node) == "require":
                    self._handle_require(node, args, ctx)

                elif func_node:
                    called_name = self._get_text(func_node)
                    if target_id := self._resolve_call_target(called_name, ctx):
                        edges.append(self._create_edge(parent_id, target_id, "calls", node=node))

                    extract_js_endpoint_route(
                        node,
                        called_name,
                        args,
                        parent_id,
                        file_path,
                        file_module_id,
                        self._get_text,
                        self._get_id,
                        self._create_node,
                        self._create_edge,
                        self._resolve_call_target,
                        ctx,
                        nodes,
                        edges,
                    )

            elif parent_id and node.type in ("jsx_self_closing_element", "jsx_opening_element"):
                if (nn := node.child_by_field_name("name")) and (tag := self._get_text(nn)):
                    if tag[0].isupper() and (target_id := self._resolve_call_target(tag, ctx)):
                        edges.append(self._create_edge(parent_id, target_id, "calls", node=node))

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
