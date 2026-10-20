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
                                    if iface_child.type == "method_spec" and (
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
                                    base = f_text.split(".")[-1]
                                    if base.startswith("New"):
                                        ctx.local_types[vname] = base[3:]

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

        # Pre-populate defined top-level symbol names to avoid false-positive builtin filtering
        for child in tree.root_node.children:
            if child.type in ("function_item", "struct_item", "trait_item", "enum_item"):
                if name_node := child.child_by_field_name("name"):
                    ctx.defined_symbols.add(self._get_text(name_node))

        def traverse(node: Node, parent_id: str | None = None) -> None:
            node_id = None
            pushed_scope = False
            saved_local_types: dict[str, str] | None = None

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
                    ctx.scope_stack.append(name)
                    pushed_scope = True

            elif node.type == "impl_item":
                type_node = node.child_by_field_name("type")
                trait_node = node.child_by_field_name("trait")
                if type_node:
                    struct_name = self._get_text(type_node).split("<")[0].strip()
                    ctx.local_types["self"] = struct_name
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

        for child in tree.root_node.children:
            if child.type in function_types or child.type in class_types:
                n_node = child.child_by_field_name("name") or child.child_by_field_name(
                    "declarator"
                )
                if n_node:
                    n_text = self._get_text(n_node)
                    ctx.defined_symbols.add(
                        n_text.split("(")[0].strip().split()[-1] if "(" in n_text else n_text
                    )

        def traverse(node: Node, parent_id: str | None = None) -> None:
            node_id = None
            pushed_scope = False

            if node.type in function_types or node.type in class_types:
                is_func = node.type in function_types
                name_node = node.child_by_field_name("name") or (
                    node.child_by_field_name("declarator") if is_func else None
                )
                if name_node:
                    n_text = self._get_text(name_node)
                    name = (
                        n_text.split("(")[0].strip().split()[-1]
                        if ("(" in n_text and is_func)
                        else n_text
                    )
                    qualified_name = self._scoped_name(name, ctx.scope_stack)
                    node_id = self._get_id(file_path, qualified_name)
                    nodes.append(
                        self._create_node(
                            node_id,
                            name,
                            "function" if is_func else "class",
                            file_path,
                            node,
                            qualified_name=qualified_name,
                            parent_id=parent_id,
                        )
                    )
                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id, "defines", node=node))
                    if not is_func:
                        ctx.scope_stack.append(name)
                        pushed_scope = True

            elif parent_id and node.type in ("call_expression", "method_invocation"):
                func_node = node.child_by_field_name("function") or node.child_by_field_name("name")
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

        traverse(tree.root_node, file_module_id)
        return nodes, edges
