"""Tree-sitter generic parser for C++, Java, C#, and other system languages."""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node

from vibetrace.indexer.db import Edge
from vibetrace.indexer.db import Node as DBNode
from vibetrace.indexer.parsers.base import LanguageParser, ParserContext
from vibetrace.indexer.parsers.builtins import (
    CPP_BUILTINS,
    CSHARP_BUILTINS,
    JAVA_BUILTINS,
    PHP_BUILTINS,
    RUBY_BUILTINS,
)
from vibetrace.indexer.resolver import ModuleResolver


class GenericParser(LanguageParser):
    """Generic parser for languages with function/class extraction and call resolution."""

    def __init__(self, language: str, resolver: ModuleResolver | None = None) -> None:
        super().__init__(language, resolver)
        self.language_name = language
        if language == "java":
            self.builtins = JAVA_BUILTINS
        elif language in ("cpp", "c"):
            self.builtins = CPP_BUILTINS
        elif language == "c_sharp":
            self.builtins = CSHARP_BUILTINS
        elif language == "ruby":
            self.builtins = RUBY_BUILTINS
        elif language == "php":
            self.builtins = PHP_BUILTINS

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

        # Pre-resolve sibling package files in the same directory (standard for Java/C++ packages)
        if self.resolver:
            curr_parent = str(Path(file_path).parent).replace("\\", "/")
            for kf in self.resolver.known_files:
                if kf != file_path and str(Path(kf).parent).replace("\\", "/") == curr_parent:
                    sibling_base = Path(kf).stem
                    ctx.imported_symbols[sibling_base] = (kf, sibling_base)

        function_types = {
            "function_definition",
            "function_declaration",
            "method_declaration",
            "constructor_declaration",
            "function_item",
            "method_definition",
            "method",
            "singleton_method",
        }
        class_types = {
            "class_definition",
            "class_declaration",
            "class_specifier",
            "struct_declaration",
            "struct_specifier",
            "interface_declaration",
            "trait_item",
            "class",
            "module",
            "enum_declaration",
            "enum_specifier",
        }

        def collect_defined_symbols(node: Node) -> None:
            if node.type in function_types or node.type in class_types:
                n_node = node.child_by_field_name("name") or node.child_by_field_name("declarator")
                if n_node:
                    n_text = self._get_text(n_node).replace("::", ".")
                    ctx.defined_symbols.add(
                        n_text.split("(")[0].strip().split()[-1] if "(" in n_text else n_text
                    )
            for ch in node.children:
                collect_defined_symbols(ch)

        collect_defined_symbols(tree.root_node)

        def _link_import(sym: str, target: str, n: Node) -> None:
            ctx.imported_symbols[sym] = (target, sym)
            m_id = self._get_id(target, sym, kind="module")
            if not any(x.id == m_id for x in ctx.nodes):
                ctx.nodes.append(self._create_node(m_id, sym, "module", target, n))
            ctx.edges.append(self._create_edge(file_module_id, m_id, "imports", node=n))

        def traverse(node: Node, parent_id: str | None = None) -> None:
            node_id = None
            pushed_scope = False
            saved_local_types: dict[str, str] | None = None

            # Java import extraction: import com.example.service.PaymentGateway;
            if node.type == "import_declaration":
                raw_imp = (
                    self._get_text(node).replace("import", "").replace("static", "").strip("; \r\n")
                )
                if raw_imp:
                    sym_name = raw_imp.split(".")[-1]
                    res_path = self.resolver.resolve(raw_imp, file_path) if self.resolver else None
                    if not res_path and self.resolver:
                        res_path = self.resolver.resolve(sym_name, file_path)
                    _link_import(sym_name, res_path or "external", node)
                return

            # C# using directive: using System.Collections.Generic; using Proj = MyProject;
            if node.type == "using_directive":
                raw_u = (
                    self._get_text(node).replace("using", "").replace("static", "").strip("; \r\n")
                )
                if raw_u:
                    alias, orig = None, raw_u
                    if "=" in raw_u:
                        parts = raw_u.split("=", 1)
                        alias, orig = parts[0].strip(), parts[1].strip()
                    sym_name = alias or orig.split(".")[-1]
                    res_path = (
                        self.resolver.resolve(orig.replace(".", "/"), file_path)
                        if self.resolver
                        else None
                    )
                    if not res_path and self.resolver:
                        res_path = self.resolver.resolve(sym_name, file_path)
                    _link_import(sym_name, res_path or "external", node)
                return

            # PHP use extraction: use App\Services\PaymentService;
            if node.type == "namespace_use_declaration":
                for clause in node.children:
                    if clause.type == "namespace_use_clause":
                        q_name, alias = None, None
                        for ch in clause.children:
                            if ch.type in ("qualified_name", "name"):
                                if q_name is None:
                                    q_name = self._get_text(ch)
                                else:
                                    alias = self._get_text(ch)
                        if q_name:
                            sym_name = alias or q_name.split("\\")[-1]
                            res_path = (
                                self.resolver.resolve(q_name.replace("\\", "/"), file_path)
                                if self.resolver
                                else None
                            )
                            if not res_path and self.resolver:
                                res_path = self.resolver.resolve(sym_name, file_path)
                            _link_import(sym_name, res_path or "external", node)
                return

            # C/C++ #include extraction
            if node.type == "preproc_include":
                path_node = node.child_by_field_name("path")
                if path_node:
                    raw_p = self._get_text(path_node).strip('"<> ')
                    res_path = self.resolver.resolve(raw_p, file_path) if self.resolver else None
                    target_path = res_path or "external"
                    if res_path and res_path.endswith((".hpp", ".h")) and self.resolver:
                        impl_cand = res_path.rsplit(".", 1)[0] + ".cpp"
                        if impl_cand in self.resolver.known_files:
                            target_path = impl_cand

                    mod_sym = Path(raw_p).stem
                    _link_import(mod_sym, target_path, node)
                    cap_sym = mod_sym[0].upper() + mod_sym[1:] if mod_sym else mod_sym
                    ctx.imported_symbols[cap_sym] = (target_path, cap_sym)
                return

            if node.type in function_types or node.type in class_types:
                is_func = node.type in function_types
                param_containers: list[Node] = []
                if is_func:
                    saved_local_types = dict(ctx.local_types)
                    # Extract parameter types from Java formal_parameters or C++ parameter_list
                    param_containers = [
                        ch
                        for ch in node.children
                        if ch.type in ("formal_parameters", "parameter_list")
                    ]
                    if not param_containers and (decl := node.child_by_field_name("declarator")):
                        while decl and decl.type in ("pointer_declarator", "reference_declarator"):
                            decl = decl.child_by_field_name("declarator") or next(
                                (
                                    c
                                    for c in decl.children
                                    if c.type
                                    in (
                                        "function_declarator",
                                        "pointer_declarator",
                                        "reference_declarator",
                                    )
                                ),
                                None,
                            )
                        if decl:
                            param_containers = [
                                ch for ch in decl.children if ch.type == "parameter_list"
                            ]
                    for ch in param_containers:
                        for param in ch.children:
                            if param.type in ("formal_parameter", "parameter_declaration"):
                                pt = param.child_by_field_name("type")
                                pn = param.child_by_field_name("name") or param.child_by_field_name(
                                    "declarator"
                                )
                                if pt and pn:
                                    p_tname = self._get_text(pt).split("<")[0].strip("*& ")
                                    p_raw = self._get_text(pn).split("=")[0].split("(")[0]
                                    p_vname = p_raw.strip("*& ")
                                    if p_tname and p_vname:
                                        ctx.local_types[p_vname] = p_tname

                name_node = node.child_by_field_name("name") or (
                    node.child_by_field_name("declarator") if is_func else None
                )
                if name_node:
                    n_text = self._get_text(name_node)
                    # Normalize C++ qualified member names (Calculator::add -> Calculator.add)
                    if "::" in n_text:
                        n_text = n_text.replace("::", ".")
                    name = (
                        n_text.split("(")[0].strip().split()[-1]
                        if ("(" in n_text and is_func)
                        else n_text
                    ).lstrip("*& ")
                    qualified_name = (
                        name if "." in name else self._scoped_name(name, ctx.scope_stack)
                    )
                    node_id = self._get_id(file_path, qualified_name)
                    signature = None
                    if is_func:
                        if param_containers:
                            signature = self._get_text(param_containers[0])
                        elif p_node := node.child_by_field_name("parameters"):
                            signature = self._get_text(p_node)
                        ret_node = (
                            node.child_by_field_name("type")
                            or node.child_by_field_name("returns")
                            or node.child_by_field_name("return_type")
                        )
                        if ret_node and signature:
                            ret_text = self._get_text(ret_node).strip()
                            if ret_text:
                                signature = f"{signature} -> {ret_text}"
                    nodes.append(
                        self._create_node(
                            node_id,
                            name.split(".")[-1],
                            (
                                "function"
                                if is_func
                                else (
                                    "enum"
                                    if node.type in ("enum_declaration", "enum_specifier")
                                    else "class"
                                )
                            ),
                            file_path,
                            node,
                            signature=signature,
                            qualified_name=qualified_name,
                            parent_id=parent_id,
                        )
                    )
                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id, "defines", node=node))
                    if not is_func:
                        ctx.scope_stack.append(name)
                        pushed_scope = True
                        for ch in node.children:
                            if ch.type in ("superclass", "base_clause"):
                                t_name = (
                                    self._get_text(ch).replace("extends", "").lstrip("< ").strip()
                                )
                                t_name = t_name.split("<")[0].strip()
                                if target_id := self._resolve_call_target(t_name, ctx):
                                    edges.append(
                                        self._create_edge(node_id, target_id, "inherits", node=ch)
                                    )
                            elif ch.type == "base_class_clause":
                                for t_node in ch.children:
                                    if t_node.type in ("type_identifier", "scoped_type_identifier"):
                                        t_name = (
                                            self._get_text(t_node)
                                            .replace("::", ".")
                                            .split("<")[0]
                                            .strip()
                                        )
                                        if target_id := self._resolve_call_target(t_name, ctx):
                                            edges.append(
                                                self._create_edge(
                                                    node_id, target_id, "inherits", node=t_node
                                                )
                                            )
                            elif ch.type == "base_list":
                                for t_node in ch.children:
                                    if t_node.type in ("identifier", "type_identifier"):
                                        t_name = self._get_text(t_node).split("<")[0].strip()
                                        if target_id := self._resolve_call_target(t_name, ctx):
                                            rel = (
                                                "implements"
                                                if (
                                                    t_name.startswith("I")
                                                    and len(t_name) > 1
                                                    and t_name[1].isupper()
                                                )
                                                else "inherits"
                                            )
                                            edges.append(
                                                self._create_edge(
                                                    node_id, target_id, rel, node=t_node
                                                )
                                            )
                            elif ch.type in (
                                "interfaces",
                                "super_interfaces",
                                "class_interface_clause",
                            ):
                                interface_nodes: list[Node] = []
                                for sub in ch.children:
                                    if sub.type == "type_list":
                                        interface_nodes.extend(sub.children)
                                    else:
                                        interface_nodes.append(sub)
                                for t_node in interface_nodes:
                                    if t_node.type in ("type_identifier", "name", "identifier"):
                                        t_name = self._get_text(t_node).split("<")[0].strip()
                                        if target_id := self._resolve_call_target(t_name, ctx):
                                            edges.append(
                                                self._create_edge(
                                                    node_id, target_id, "implements", node=t_node
                                                )
                                            )

            elif node.type in ("field_declaration", "local_variable_declaration"):
                # Java & C++ field and local variable declaration tracking
                t_node = node.child_by_field_name("type")
                if t_node:
                    t_name = self._get_text(t_node).split("<")[0].strip("*& ")
                    for child in node.children:
                        if child.type == "variable_declarator":
                            if vn := child.child_by_field_name("name"):
                                vname = self._get_text(vn).strip()
                                ctx.local_types[vname] = t_name
                                if node.type == "field_declaration":
                                    ctx.local_types[f"this.{vname}"] = t_name
                        elif child.type == "field_identifier":
                            vname = self._get_text(child).strip()
                            ctx.local_types[vname] = t_name
                            ctx.local_types[f"this.{vname}"] = t_name

            elif node.type == "assignment_expression":
                # Java / C++ assignments: this.gateway = new PaymentGateway(...)
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if left and right and right.type == "object_creation_expression":
                    t_right = right.child_by_field_name("type")
                    if t_right:
                        c_name = self._get_text(t_right).split("<")[0].strip()
                        l_text = self._get_text(left).strip()
                        ctx.local_types[l_text] = c_name
                        if l_text.startswith("this."):
                            ctx.local_types[l_text[5:]] = c_name

            elif node.type == "declaration":
                # C/C++ local variable declarations: Calculator calc;
                t_node = node.child_by_field_name("type")
                d_node = node.child_by_field_name("declarator")
                if t_node and d_node:
                    t_name = self._get_text(t_node).split("<")[0].strip("*& ")
                    d_text = self._get_text(d_node).split("=")[0].split("(")[0].strip("*& ")
                    if d_text:
                        ctx.local_types[d_text] = t_name

            elif node.type == "call":
                method_node = node.child_by_field_name("method")
                receiver_node = node.child_by_field_name("receiver")
                if method_node:
                    m_name = self._get_text(method_node)
                    if m_name in ("require", "require_relative"):
                        args_node = node.child_by_field_name("arguments")
                        if args_node:
                            for ch in args_node.children:
                                if ch.type in ("string", "simple_symbol"):
                                    raw_p = self._get_text(ch).strip("\"': ")
                                    res_p = (
                                        self.resolver.resolve(raw_p, file_path)
                                        if self.resolver
                                        else None
                                    )
                                    _link_import(Path(raw_p).stem, res_p or "external", node)
                        return
                    elif parent_id:
                        called_name = (
                            f"{self._get_text(receiver_node)}.{m_name}" if receiver_node else m_name
                        )
                        target_id = self._resolve_call_target(called_name, ctx)
                        if target_id:
                            edges.append(
                                self._create_edge(parent_id, target_id, "calls", node=node)
                            )

            elif parent_id and node.type in (
                "call_expression",
                "method_invocation",
                "object_creation_expression",
                "new_expression",
            ):
                if node.type in ("object_creation_expression", "new_expression"):
                    type_node = node.child_by_field_name("type")
                    if not type_node:
                        for c in node.children:
                            if c.type in ("type_identifier", "scoped_type_identifier"):
                                type_node = c
                                break
                    if type_node:
                        called_name = (
                            self._get_text(type_node).split("<")[0].replace("::", ".").strip()
                        )
                        target_id = self._resolve_call_target(called_name, ctx)
                        if target_id:
                            edges.append(
                                self._create_edge(parent_id, target_id, "calls", node=node)
                            )
                else:
                    func_node = node.child_by_field_name("function") or node.child_by_field_name(
                        "name"
                    )
                    obj_node = node.child_by_field_name("object")
                    if func_node:
                        called_name = (
                            self._get_text(func_node).replace("::", ".").replace("->", ".")
                        )
                        if obj_node:
                            obj_text = self._get_text(obj_node).strip()
                            called_name = f"{obj_text}.{called_name}"
                        target_id = self._resolve_call_target(called_name, ctx)
                        if target_id:
                            edges.append(
                                self._create_edge(parent_id, target_id, "calls", node=node)
                            )

            current_scope_id = node_id if node_id else parent_id
            for child in node.children:
                traverse(child, current_scope_id)

            if pushed_scope:
                ctx.scope_stack.pop()

            if saved_local_types is not None:
                ctx.local_types = saved_local_types

        traverse(tree.root_node, file_module_id)
        return nodes, edges
