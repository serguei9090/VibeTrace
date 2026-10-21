"""Tree-sitter parser for Go source files."""

from __future__ import annotations

from tree_sitter import Node

from vibetrace.indexer.db import Edge
from vibetrace.indexer.db import Node as DBNode
from vibetrace.indexer.parsers.base import LanguageParser, ParserContext
from vibetrace.indexer.resolver import ModuleResolver


class GoParser(LanguageParser):
    """Parser for Go files with AST extraction and type-directed call resolution."""

    def __init__(self, resolver: ModuleResolver | None = None) -> None:
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

        # Pre-populate defined top-level symbol names to avoid false-positive builtin filtering
        for child in tree.root_node.children:
            if child.type in ("function_declaration", "method_declaration"):
                if n_node := child.child_by_field_name("name"):
                    ctx.defined_symbols.add(self._get_text(n_node))
            elif child.type == "type_declaration":
                for sp in child.children:
                    if sp.type == "type_spec" and (sn := sp.child_by_field_name("name")):
                        ctx.defined_symbols.add(self._get_text(sn))

        def traverse(node: Node, parent_id: str | None = None) -> None:
            node_id = None
            saved_local_types: dict[str, str] | None = None

            if node.type == "import_declaration":
                for child in node.children:
                    specs = child.children if child.type == "import_spec_list" else [child]
                    for spec in specs:
                        if spec.type == "import_spec" and (
                            p_node := spec.child_by_field_name("path")
                        ):
                            raw_p = self._get_text(p_node).strip('"')
                            n_node = spec.child_by_field_name("name")
                            alias = (
                                self._get_text(n_node).strip() if n_node else raw_p.split("/")[-1]
                            )
                            res_path = (
                                self.resolver.resolve(raw_p, file_path) if self.resolver else None
                            )
                            m_path = res_path or "external"
                            ctx.imported_symbols[alias] = (m_path, "__module__")
                            m_id = self._get_id(m_path, alias, kind="module")
                            if not any(n.id == m_id for n in ctx.nodes):
                                ctx.nodes.append(
                                    self._create_node(m_id, alias, "module", m_path, spec)
                                )
                            ctx.edges.append(
                                self._create_edge(file_module_id, m_id, "imports", node=spec)
                            )
                return

            if node.type in ("function_declaration", "method_declaration"):
                saved_local_types = dict(ctx.local_types)
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = self._get_text(name_node)
                    qualified_name = name
                    recv_text = None
                    if node.type == "method_declaration":
                        recv_node = node.child_by_field_name("receiver")
                        if recv_node:
                            recv_raw = self._get_text(recv_node).strip("()")
                            recv_parts = recv_raw.split()
                            if len(recv_parts) >= 2:
                                ctx.local_types[recv_parts[0]] = (
                                    recv_parts[1].lstrip("*").split("[")[0].strip()
                                )
                            recv_text = recv_raw.split()[-1].lstrip("*").split("[")[0].strip()
                            if recv_text:
                                qualified_name = f"{recv_text}.{name}"

                    node_id = self._get_id(file_path, qualified_name)
                    params_node = node.child_by_field_name("parameters")
                    signature = self._get_text(params_node) if params_node else None
                    if params_node:
                        for p in params_node.children:
                            if p.type == "parameter_declaration":
                                pn = p.child_by_field_name("name")
                                pt = p.child_by_field_name("type")
                                if pn and pt:
                                    ctx.local_types[self._get_text(pn)] = (
                                        self._get_text(pt).lstrip("*").strip()
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
                    if recv_text:
                        struct_id = self._get_id(file_path, recv_text)
                        edges.append(self._create_edge(struct_id, node_id, "defines", node=node))

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
                                            if f.type == "field_declaration" and not any(
                                                fc.type == "field_identifier" for fc in f.children
                                            ):
                                                emb_type = self._get_text(f).strip("* ")
                                                if emb_type and (
                                                    emb_id := self._resolve_call_target(
                                                        emb_type, ctx
                                                    )
                                                ):
                                                    edges.append(
                                                        self._create_edge(
                                                            node_id, emb_id, "inherits", node=f
                                                        )
                                                    )
                            elif type_node and type_node.type == "interface_type":
                                for iface_child in type_node.children:
                                    if iface_child.type in ("method_spec", "method_elem") and (
                                        m_node := iface_child.child_by_field_name("name")
                                    ):
                                        m_name = self._get_text(m_node)
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

            elif node.type == "var_declaration":
                for child in node.children:
                    if child.type == "var_spec":
                        name_node = child.child_by_field_name("name")
                        type_node = child.child_by_field_name("type")
                        val_node = child.child_by_field_name("value")
                        if name_node:
                            vname = self._get_text(name_node).strip()
                            if type_node:
                                ctx.local_types[vname] = (
                                    self._get_text(type_node).lstrip("*").split("[")[0].strip()
                                )
                            elif val_node and val_node.type == "call_expression":
                                if func := val_node.child_by_field_name("function"):
                                    f_text = self._get_text(func).strip()
                                    parts = f_text.split(".")
                                    base = parts[-1]
                                    if base.startswith("New"):
                                        ctor = base[3:]
                                        if len(parts) > 1 and parts[0] in ctx.imported_symbols:
                                            ctx.local_types[vname] = f"{parts[0]}.{ctor}"
                                        else:
                                            ctx.local_types[vname] = ctor

            elif node.type == "short_var_declaration":
                left_node = node.child_by_field_name("left")
                right_node = node.child_by_field_name("right")
                if left_node and right_node:
                    var_names = [
                        self._get_text(c).strip()
                        for c in left_node.children
                        if c.type == "identifier"
                    ]
                    exprs = [c for c in right_node.children if c.type not in (",",)]
                    for i, vname in enumerate(var_names):
                        if i < len(exprs):
                            expr = exprs[i]
                            target_expr = expr
                            if target_expr.type == "unary_expression":
                                for uc in target_expr.children:
                                    if uc.type == "composite_literal":
                                        target_expr = uc
                                        break
                            if target_expr.type == "composite_literal":
                                if t_node := target_expr.child_by_field_name("type"):
                                    ctx.local_types[vname] = (
                                        self._get_text(t_node).lstrip("*").split("[")[0].strip()
                                    )
                            elif target_expr.type == "call_expression":
                                if func := target_expr.child_by_field_name("function"):
                                    f_text = self._get_text(func).strip()
                                    parts = f_text.split(".")
                                    base = parts[-1]
                                    if base.startswith("New"):
                                        ctor = base[3:]
                                        if len(parts) > 1 and parts[0] in ctx.imported_symbols:
                                            ctx.local_types[vname] = f"{parts[0]}.{ctor}"
                                        else:
                                            ctx.local_types[vname] = ctor

            elif parent_id and node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node:
                    called_name = self._get_text(func_node)
                    target_id = self._resolve_call_target(called_name, ctx)
                    if target_id:
                        edges.append(self._create_edge(parent_id, target_id, "calls", node=node))

            current_scope_id = node_id if node_id else parent_id
            for child in node.children:
                traverse(child, current_scope_id)

            if saved_local_types is not None:
                ctx.local_types = saved_local_types

        traverse(tree.root_node, file_module_id)
        return nodes, edges
