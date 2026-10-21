"""Tree-sitter generic parser for C++, Java, C#, and other system languages."""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node

from vibegraph.indexer.db import Edge
from vibegraph.indexer.db import Node as DBNode
from vibegraph.indexer.parsers.base import LanguageParser, ParserContext
from vibegraph.indexer.resolver import ModuleResolver

JAVA_BUILTINS: set[str] = {
    "System",
    "String",
    "Integer",
    "Double",
    "Float",
    "Long",
    "Short",
    "Byte",
    "Character",
    "Boolean",
    "Math",
    "Object",
    "Class",
    "Thread",
    "Runnable",
    "Exception",
    "RuntimeException",
    "Throwable",
    "Error",
    "StringBuilder",
    "StringBuffer",
    "Override",
    "Deprecated",
    "SuppressWarnings",
}

CPP_BUILTINS: set[str] = {
    "printf",
    "scanf",
    "malloc",
    "free",
    "calloc",
    "realloc",
    "sizeof",
    "memcpy",
    "memset",
    "std",
    "cout",
    "cin",
    "cerr",
    "endl",
    "exit",
    "abort",
    "assert",
}


class GenericParser(LanguageParser):
    """Generic parser for languages with function/class extraction and call resolution."""

    def __init__(self, language: str, resolver: ModuleResolver | None = None) -> None:
        super().__init__(language, resolver)
        self.language_name = language
        if language == "java":
            self.builtins = JAVA_BUILTINS
        elif language in ("cpp", "c"):
            self.builtins = CPP_BUILTINS

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

        # Pre-resolve sibling package files in the same directory (standard for Java packages)
        if self.resolver:
            curr_dir = str(Path(file_path).parent).replace("\\", "/")
            for kf in self.resolver.known_files:
                if kf != file_path and kf.startswith(f"{curr_dir}/"):
                    sibling_base = Path(kf).stem
                    ctx.imported_symbols[sibling_base] = (kf, sibling_base)

        function_types = {
            "function_definition",
            "function_declaration",
            "method_declaration",
            "constructor_declaration",
            "function_item",
            "method_definition",
        }
        class_types = {
            "class_definition",
            "class_declaration",
            "class_specifier",
            "struct_declaration",
            "struct_specifier",
            "interface_declaration",
            "trait_item",
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
                        # Try resolving by class name
                        res_path = self.resolver.resolve(sym_name, file_path)
                    m_path = res_path or "external"
                    ctx.imported_symbols[sym_name] = (m_path, sym_name)
                    m_id = self._get_id(m_path, sym_name, kind="module")
                    if not any(x.id == m_id for x in ctx.nodes):
                        ctx.nodes.append(self._create_node(m_id, sym_name, "module", m_path, node))
                    ctx.edges.append(self._create_edge(file_module_id, m_id, "imports", node=node))
                return

            # C/C++ #include extraction
            if node.type == "preproc_include":
                path_node = node.child_by_field_name("path")
                if path_node:
                    raw_p = self._get_text(path_node).strip('"<> ')
                    res_path = self.resolver.resolve(raw_p, file_path) if self.resolver else None
                    if res_path:
                        # Prefer implementation file (.cpp) over header (.hpp) if present
                        target_path = res_path
                        if res_path.endswith((".hpp", ".h")) and self.resolver:
                            impl_cand = res_path.rsplit(".", 1)[0] + ".cpp"
                            if impl_cand in self.resolver.known_files:
                                target_path = impl_cand

                        mod_sym = Path(res_path).stem
                        ctx.imported_symbols[mod_sym] = (target_path, mod_sym)
                        cap_sym = mod_sym[0].upper() + mod_sym[1:] if mod_sym else mod_sym
                        ctx.imported_symbols[cap_sym] = (target_path, cap_sym)

                        m_id = self._get_id(target_path, mod_sym, kind="module")
                        if not any(x.id == m_id for x in ctx.nodes):
                            ctx.nodes.append(
                                self._create_node(m_id, mod_sym, "module", target_path, node)
                            )
                        ctx.edges.append(
                            self._create_edge(file_module_id, m_id, "imports", node=node)
                        )
                return

            if node.type in function_types or node.type in class_types:
                is_func = node.type in function_types
                if is_func:
                    saved_local_types = dict(ctx.local_types)
                    # Extract parameter types from Java formal_parameters or C++ parameter_list
                    for ch in node.children:
                        if ch.type in ("formal_parameters", "parameter_list"):
                            for param in ch.children:
                                if param.type in ("formal_parameter", "parameter_declaration"):
                                    pt = param.child_by_field_name("type")
                                    pn = param.child_by_field_name(
                                        "name"
                                    ) or param.child_by_field_name("declarator")
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
                    )
                    qualified_name = (
                        name if "." in name else self._scoped_name(name, ctx.scope_stack)
                    )
                    node_id = self._get_id(file_path, qualified_name)
                    nodes.append(
                        self._create_node(
                            node_id,
                            name.split(".")[-1],
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

            elif parent_id and node.type in ("call_expression", "method_invocation"):
                func_node = node.child_by_field_name("function") or node.child_by_field_name("name")
                obj_node = node.child_by_field_name("object")
                if func_node:
                    called_name = self._get_text(func_node).replace("::", ".")
                    if obj_node:
                        obj_text = self._get_text(obj_node).strip()
                        called_name = f"{obj_text}.{called_name}"
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
