"""Tree-sitter parser for Rust source files."""

from __future__ import annotations

from tree_sitter import Node

from vibegraph.indexer.db import Edge
from vibegraph.indexer.db import Node as DBNode
from vibegraph.indexer.parsers.base import LanguageParser, ParserContext
from vibegraph.indexer.resolver import ModuleResolver


class RustParser(LanguageParser):
    """Parser for Rust files with AST extraction and type-directed call resolution."""

    def __init__(self, resolver: ModuleResolver | None = None) -> None:
        super().__init__("rust", resolver)
        self.builtins = {
            "Some",
            "None",
            "Ok",
            "Err",
            "drop",
            "assert",
            "assert_eq",
            "assert_ne",
            "panic",
            "vec",
            "format",
            "println",
            "eprintln",
        }

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
            if child.type in ("function_item", "struct_item", "trait_item", "enum_item"):
                if name_node := child.child_by_field_name("name"):
                    ctx.defined_symbols.add(self._get_text(name_node))

        # Track struct fields across the file to hydrate impl scopes
        struct_fields: dict[str, dict[str, str]] = {}

        def traverse(node: Node, parent_id: str | None = None) -> None:
            node_id = None
            pushed_scope = False
            saved_local_types: dict[str, str] | None = None

            if node.type == "mod_item":
                if n_node := node.child_by_field_name("name"):
                    mod_name = self._get_text(n_node).strip()
                    if not node.child_by_field_name("body"):
                        res = self.resolver.resolve(mod_name, file_path) if self.resolver else None
                        m_path = res or "external"
                        ctx.imported_symbols[mod_name] = (m_path, "__module__")
                        m_id = self._get_id(m_path, mod_name, kind="module")
                        if not any(x.id == m_id for x in ctx.nodes):
                            ctx.nodes.append(
                                self._create_node(m_id, mod_name, "module", m_path, node)
                            )
                        ctx.edges.append(
                            self._create_edge(file_module_id, m_id, "imports", node=node)
                        )
                        return
                    ctx.scope_stack.append(mod_name)
                    pushed_scope = True

            if node.type == "use_declaration":
                if arg := node.child_by_field_name("argument"):
                    raw = self._get_text(arg).strip("; ")
                    items: list[tuple[str, str, str]] = []
                    if "::{" in raw:
                        base, grp = raw.split("::{", 1)
                        for it in grp.rstrip("}").split(","):
                            if s := it.strip():
                                sym = s.split(" as ")[-1].strip()
                                orig = s.split(" as ")[0].strip()
                                items.append((sym, orig, f"{base}::{orig}".replace("::", "/")))
                    elif "::" in raw:
                        parts = raw.split("::")
                        items.append((parts[-1], parts[-1], "/".join(parts[:-1])))
                    for sym, orig, mod in items:
                        res = self.resolver.resolve(mod, file_path) if self.resolver else None
                        m_path = res or "external"
                        ctx.imported_symbols[sym] = (m_path, orig)
                        m_id = self._get_id(m_path, sym, kind="module")
                        if not any(x.id == m_id for x in ctx.nodes):
                            ctx.nodes.append(self._create_node(m_id, sym, "module", m_path, node))
                        ctx.edges.append(
                            self._create_edge(file_module_id, m_id, "imports", node=node)
                        )
                return

            if node.type in ("function_item", "function_signature_item"):
                saved_local_types = dict(ctx.local_types)
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    qualified_name = self._scoped_name(name, ctx.scope_stack)
                    node_id = self._get_id(file_path, qualified_name)
                    params_node = node.child_by_field_name("parameters")
                    signature = self._get_text(params_node) if params_node else None
                    if params_node:
                        for p in params_node.children:
                            if p.type == "parameter":
                                pn = p.child_by_field_name("pattern")
                                pt = p.child_by_field_name("type")
                                if pn and pt:
                                    ctx.local_types[self._get_text(pn).strip()] = (
                                        self._get_text(pt).lstrip("&* ").split("<")[0].strip()
                                    )

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

            elif node.type in ("trait_item", "struct_item"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    qualified_name = self._scoped_name(name, ctx.scope_stack)
                    node_id = self._get_id(file_path, qualified_name)
                    kind = "trait" if node.type == "trait_item" else "struct"
                    nodes.append(
                        self._create_node(
                            node_id,
                            name,
                            kind,
                            file_path,
                            node,
                            qualified_name=qualified_name,
                            parent_id=parent_id,
                        )
                    )
                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id, "defines", node=node))

                    if node.type == "struct_item":
                        fields_dict: dict[str, str] = {}
                        for c in node.children:
                            if c.type == "field_declaration_list":
                                for fc in c.children:
                                    if fc.type == "field_declaration":
                                        fn = fc.child_by_field_name("name")
                                        ft = fc.child_by_field_name("type")
                                        if fn and ft:
                                            fname = self._get_text(fn).strip()
                                            ftype = (
                                                self._get_text(ft)
                                                .lstrip("&* ")
                                                .split("<")[0]
                                                .strip()
                                            )
                                            fields_dict[fname] = ftype
                        struct_fields[name] = fields_dict

                    ctx.scope_stack.append(name)
                    pushed_scope = True

            elif node.type == "impl_item":
                type_node = node.child_by_field_name("type")
                trait_node = node.child_by_field_name("trait")
                if type_node:
                    struct_name = self._get_text(type_node).split("<")[0].strip()
                    saved_local_types = dict(ctx.local_types)
                    ctx.local_types["self"] = struct_name
                    # Hydrate struct fields into local type environment for self.field calls
                    if struct_name in struct_fields:
                        for fname, ftype in struct_fields[struct_name].items():
                            ctx.local_types[fname] = ftype
                            ctx.local_types[f"self.{fname}"] = ftype

                    resolved_struct = self._resolve_call_target(struct_name, ctx)
                    struct_id = (
                        resolved_struct if resolved_struct else self._get_id(file_path, struct_name)
                    )
                    if trait_node:
                        trait_name = self._get_text(trait_node).split("<")[0].strip()
                        trait_id = self._resolve_call_target(trait_name, ctx)
                        if trait_id:
                            edges.append(
                                self._create_edge(struct_id, trait_id, "implements", node=node)
                            )
                    ctx.scope_stack.append(struct_name)
                    pushed_scope = True
                    node_id = struct_id

            elif node.type == "let_declaration":
                p_node = node.child_by_field_name("pattern")
                t_node = node.child_by_field_name("type")
                v_node = node.child_by_field_name("value")
                if p_node:
                    vname = self._get_text(p_node).strip()
                    if t_node:
                        ctx.local_types[vname] = (
                            self._get_text(t_node).lstrip("&* ").split("<")[0].strip()
                        )
                    elif v_node and v_node.type == "call_expression":
                        if fn := v_node.child_by_field_name("function"):
                            fn_text = self._get_text(fn).replace("::", ".").strip()
                            parts = fn_text.split(".")
                            if len(parts) >= 2 and parts[-1] in (
                                "new",
                                "default",
                                "create",
                                "build",
                                "init",
                            ):
                                ctx.local_types[vname] = parts[-2]
                    elif v_node and v_node.type == "struct_expression":
                        if sn := v_node.child_by_field_name("name"):
                            ctx.local_types[vname] = self._get_text(sn).split("<")[0].strip()

            elif parent_id and node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node:
                    called_name = self._get_text(func_node).replace("::", ".")
                    target_id = self._resolve_call_target(called_name, ctx)
                    if target_id:
                        edges.append(self._create_edge(parent_id, target_id, "calls", node=node))

            current_scope_id = node_id if node_id else parent_id
            for child in node.children:
                traverse(child, current_scope_id)

            if pushed_scope:
                ctx.scope_stack.pop()

            if saved_local_types is not None:
                ctx.local_types = saved_local_types

        traverse(tree.root_node, file_module_id)
        return nodes, edges
