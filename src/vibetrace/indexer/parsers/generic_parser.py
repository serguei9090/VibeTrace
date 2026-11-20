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
from vibetrace.indexer.parsers.route_extractor import (
    extract_enterprise_class_prefix,
    extract_enterprise_routes,
)
from vibetrace.indexer.resolver import ModuleResolver


def _strip_generic_args(text: str) -> str:
    """Strip balanced generic/template `<...>` arguments from a symbol or call name."""
    if "<" not in text:
        return text
    out: list[str] = []
    depth = 0
    for ch in text:
        if ch == "<":
            depth += 1
        elif ch == ">" and depth > 0:
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out) if depth == 0 else text


def _clean_type_name(raw: str) -> str:
    """Extract base type identifier from nullable, union, generic, or namespaced annotations."""
    clean = raw.strip().lstrip("?")
    if "|" in clean:
        parts = [p.strip().lstrip("?") for p in clean.split("|")]
        clean = next((p for p in parts if p and p.lower() != "null"), parts[0])
    clean = _strip_generic_args(clean).split("<")[0]
    if "\\" in clean:
        clean = clean.rsplit("\\", 1)[-1]
    return clean.strip("*&$ ")


def _clean_php_member_chain(raw: str) -> str:
    """Normalize PHP `$this->foo->bar` or `$h` into `this.foo.bar` or `h`."""
    norm = raw.replace("?->", ".").replace("->", ".").strip()
    return ".".join(seg.strip().lstrip("$") for seg in norm.split(".") if seg.strip())


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

    def _extract_node_annotations(self, node: Node) -> list[str]:
        """Extract annotations (Java) or attributes (C#) for a class or method."""
        annotations: list[str] = []
        if self.language_name == "java":
            mods = next((c for c in node.children if c.type == "modifiers"), None)
            source_nodes = mods.children if mods else node.children
            for ch in source_nodes:
                if ch.type in ("annotation", "marker_annotation"):
                    annotations.append(self._get_text(ch))
        elif self.language_name == "c_sharp":
            for ch in node.children:
                if ch.type == "attribute_list":
                    annotations.append(self._get_text(ch))
        return annotations

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

        mod_id_key = (
            f"{file_path}::{module_name}" if self.language_name in ("c", "cpp") else module_name
        )
        file_module_id = self._get_id(file_path, mod_id_key, kind="module")
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
            "trait_declaration",
            "record_declaration",
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

        def _link_import(sym: str, target: str, n: Node, register_symbol: bool = True) -> None:
            if register_symbol:
                ctx.imported_symbols[sym] = (target, sym)
            mod_q = f"{target}::{sym}" if target != "external" else sym
            m_id = self._get_id(target, mod_q, kind="module")
            if not any(x.id == m_id for x in ctx.nodes):
                ctx.nodes.append(self._create_node(m_id, sym, "module", target, n))
            edge_conf = "exact" if target != "external" else "inferred"
            if not any(
                e.from_node_id == file_module_id
                and e.to_node_id == m_id
                and e.relation_type == "imports"
                for e in ctx.edges
            ):
                ctx.edges.append(
                    self._create_edge(file_module_id, m_id, "imports", node=n, confidence=edge_conf)
                )

        def _find_companion_impl(header_path: str) -> str | None:
            if not self.resolver or not header_path.endswith((".hpp", ".h")):
                return None
            stem = header_path.rsplit(".", 1)[0]
            candidates: list[str] = []
            for ext in (".cpp", ".cc", ".cxx", ".c"):
                candidates.append(stem + ext)
            if header_path.startswith("include/"):
                sub_stem = stem[len("include/") :]
                base_stem = Path(header_path).stem
                for ext in (".cpp", ".cc", ".cxx", ".c"):
                    candidates.append(f"src/{sub_stem}{ext}")
                    candidates.append(f"src/{base_stem}{ext}")
            for cand in candidates:
                if cand != file_path and cand in self.resolver.known_files:
                    return cand
            return None

        def _extract_php_use_clause(clause: Node, prefix: str, decl_node: Node) -> None:
            q_name, alias = None, None
            for ch in clause.children:
                if ch.type in ("qualified_name", "name"):
                    if q_name is None:
                        q_name = self._get_text(ch)
                    else:
                        alias = self._get_text(ch)
            if not q_name:
                return
            clean_q = q_name.lstrip("\\")
            full_q = f"{prefix}\\{clean_q}" if prefix else clean_q
            sym_name = alias or full_q.split("\\")[-1]
            res_path = (
                self.resolver.resolve(full_q.replace("\\", "/"), file_path)
                if self.resolver
                else None
            )
            if not res_path and self.resolver:
                res_path = self.resolver.resolve(sym_name, file_path)
            target = res_path or "external"
            register_sym = not (target == "external" and sym_name in self.builtins)
            _link_import(sym_name, target, decl_node, register_symbol=register_sym)

        def traverse(node: Node, parent_id: str | None = None) -> None:
            node_id = None
            pushed_scope = False
            saved_local_types: dict[str, str] | None = None
            saved_class_prefix: str | None = None

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
                    ns_slash = orig.replace(".", "/")
                    res_path = self.resolver.resolve(ns_slash, file_path) if self.resolver else None
                    if not res_path and self.resolver:
                        res_path = self.resolver.resolve(sym_name, file_path)
                    if res_path:
                        _link_import(sym_name, res_path, node)
                    elif self.resolver:
                        matched_cs = sorted(
                            kf
                            for kf in self.resolver.known_files
                            if kf.endswith(".cs")
                            and any(
                                kf.startswith(f"{pfx}/") and "/" not in kf[len(pfx) + 1 :]
                                for pfx in (ns_slash, f"src/{ns_slash}")
                            )
                        )
                        if matched_cs:
                            _link_import(sym_name, matched_cs[0], node)
                            for kf in matched_cs:
                                stem = Path(kf).stem
                                ctx.imported_symbols[stem] = (kf, stem)
                        else:
                            _link_import(sym_name, "external", node)
                    else:
                        _link_import(sym_name, "external", node)
                return

            # PHP use extraction: use App\Services\PaymentService;
            if node.type == "namespace_use_declaration":
                group_prefix = ""
                for ch in node.children:
                    if ch.type == "namespace_name":
                        group_prefix = self._get_text(ch).strip("\\")
                    elif ch.type == "namespace_use_clause":
                        _extract_php_use_clause(ch, "", node)
                    elif ch.type == "namespace_use_group":
                        for sub in ch.children:
                            if sub.type == "namespace_use_clause":
                                _extract_php_use_clause(sub, group_prefix, node)
                return

            # C/C++ #include extraction
            if node.type == "preproc_include":
                path_node = node.child_by_field_name("path")
                if path_node:
                    raw_p = self._get_text(path_node).strip('"<> ')
                    res_path = self.resolver.resolve(raw_p, file_path) if self.resolver else None
                    target_path = res_path or "external"
                    if res_path and res_path.endswith((".hpp", ".h")):
                        if impl_cand := _find_companion_impl(res_path):
                            target_path = impl_cand

                    mod_sym = Path(raw_p).stem
                    register_sym = not (target_path == "external" and mod_sym in self.builtins)
                    _link_import(mod_sym, target_path, node, register_symbol=register_sym)
                    if res_path and target_path != res_path:
                        ctx.imported_symbols[f"{mod_sym}__hdr"] = (res_path, mod_sym)
                    if register_sym and mod_sym:
                        cap_sym = mod_sym[0].upper() + mod_sym[1:]
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
                            if param.type in (
                                "formal_parameter",
                                "parameter_declaration",
                                "parameter",
                                "simple_parameter",
                                "property_promotion_parameter",
                            ):
                                pt = param.child_by_field_name("type")
                                pn = param.child_by_field_name("name") or param.child_by_field_name(
                                    "declarator"
                                )
                                if pt and pn:
                                    p_tname = _clean_type_name(self._get_text(pt))
                                    p_raw = self._get_text(pn).split("=")[0].split("(")[0]
                                    p_vname = p_raw.strip("*&$ ")
                                    if p_tname and p_vname:
                                        ctx.local_types[p_vname] = p_tname
                                        if param.type == "property_promotion_parameter":
                                            ctx.local_types[f"this.{p_vname}"] = p_tname

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
                    if is_func:
                        node_kind = "function"
                    elif node.type in ("enum_declaration", "enum_specifier"):
                        node_kind = "enum"
                    else:
                        node_kind = "class"

                    visibility = None
                    for c in node.children:
                        if c.type in ("visibility_modifier", "modifiers"):
                            vis_text = self._get_text(c).strip().lower()
                            if "private" in vis_text:
                                visibility = "private"
                            elif "protected" in vis_text:
                                visibility = "protected"
                            elif "public" in vis_text:
                                visibility = "public"

                    fn_annotations = self._extract_node_annotations(node) if is_func else []
                    new_node = self._create_node(
                        node_id,
                        name.split(".")[-1],
                        node_kind,
                        file_path,
                        node,
                        signature=signature,
                        visibility=visibility,
                        decorators=fn_annotations or None,
                        qualified_name=qualified_name,
                        parent_id=parent_id,
                    )
                    nodes.append(new_node)
                    if is_func and fn_annotations:
                        ep_nodes, r_edges = extract_enterprise_routes(
                            fn_annotations,
                            new_node,
                            file_path,
                            self._get_id,
                            class_prefix=ctx.current_class_prefix,
                            controller_name=ctx.scope_stack[-1] if ctx.scope_stack else "",
                        )
                        nodes.extend(ep_nodes)
                        edges.extend(r_edges)

                    if parent_id:
                        edges.append(self._create_edge(parent_id, node_id, "defines", node=node))
                    if not is_func:
                        ctx.scope_stack.append(name)
                        pushed_scope = True
                        saved_class_prefix = ctx.current_class_prefix
                        class_ann = self._extract_node_annotations(node)
                        if class_pfx := extract_enterprise_class_prefix(
                            class_ann, controller_name=name
                        ):
                            ctx.current_class_prefix = class_pfx
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

            elif node.type in (
                "field_declaration",
                "local_variable_declaration",
                "local_declaration_statement",
                "property_declaration",
            ):
                # Java, C#, C++, & PHP field/property and local variable declaration tracking
                t_node = node.child_by_field_name("type")
                var_decl_node = node
                if not t_node:
                    for ch in node.children:
                        if ch.type == "variable_declaration":
                            t_node = ch.child_by_field_name("type") or (
                                ch.children[0] if ch.children else None
                            )
                            var_decl_node = ch
                            break

                if t_node:
                    t_name = _clean_type_name(self._get_text(t_node))
                    for child in var_decl_node.children:
                        if child.type == "variable_declarator":
                            vn = child.child_by_field_name("name") or (
                                child.children[0] if child.children else None
                            )
                            eff_tname = t_name
                            if eff_tname == "var":
                                ctor = next(
                                    (
                                        c
                                        for c in child.children
                                        if c.type == "object_creation_expression"
                                    ),
                                    None,
                                )
                                if ctor and (ct := ctor.child_by_field_name("type")):
                                    eff_tname = _clean_type_name(self._get_text(ct))
                            if vn and eff_tname and eff_tname != "var":
                                vname = self._get_text(vn).strip()
                                ctx.local_types[vname] = eff_tname
                                if node.type == "field_declaration":
                                    ctx.local_types[f"this.{vname}"] = eff_tname
                        elif child.type == "field_identifier":
                            vname = self._get_text(child).strip()
                            ctx.local_types[vname] = t_name
                            ctx.local_types[f"this.{vname}"] = t_name
                        elif child.type == "property_element":
                            vn = child.child_by_field_name("name") or next(
                                (c for c in child.children if c.type == "variable_name"),
                                None,
                            )
                            if vn and t_name:
                                vname = self._get_text(vn).strip().lstrip("$")
                                if vname:
                                    ctx.local_types[vname] = t_name
                                    ctx.local_types[f"this.{vname}"] = t_name

            elif node.type == "assignment_expression":
                # Java / C++ / PHP assignments: this.gateway = new PaymentGateway(...)
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if left and right and right.type == "object_creation_expression":
                    t_right = right.child_by_field_name("type") or next(
                        (c for c in right.children if c.type in ("name", "qualified_name")),
                        None,
                    )
                    if t_right:
                        c_name = _clean_type_name(self._get_text(t_right))
                        l_text = _clean_php_member_chain(self._get_text(left))
                        if c_name and l_text:
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
                        called_name = m_name
                        if receiver_node:
                            if receiver_node.type == "call":
                                inner_m = receiver_node.child_by_field_name("method")
                                inner_r = receiver_node.child_by_field_name("receiver")
                                if (
                                    inner_m
                                    and self._get_text(inner_m) == "new"
                                    and inner_r
                                    and inner_r.type in ("constant", "scope_resolution")
                                ):
                                    cls_text = (
                                        self._get_text(inner_r).replace("::", ".").lstrip(":")
                                    )
                                    if cls_text:
                                        called_name = f"{cls_text}.{m_name}"
                            elif receiver_node.type in (
                                "constant",
                                "scope_resolution",
                                "identifier",
                                "self",
                                "instance_variable",
                            ):
                                recv_text = (
                                    self._get_text(receiver_node).replace("::", ".").lstrip(":@")
                                )
                                if recv_text:
                                    called_name = f"{recv_text}.{m_name}"
                        target_id = self._resolve_call_target(called_name, ctx)
                        if target_id:
                            edges.append(
                                self._create_edge(parent_id, target_id, "calls", node=node)
                            )

            elif parent_id and node.type == "field_initializer":
                # C++ constructor member initializer: : x(a), helper(initHelper())
                args_node = None
                for c in node.children:
                    if c.type == "argument_list":
                        args_node = c
                        break
                if args_node:
                    for arg in args_node.children:
                        if arg.type == "call_expression":
                            f_node = arg.child_by_field_name("function")
                            if f_node:
                                c_name = _strip_generic_args(
                                    self._get_text(f_node).replace("::", ".").replace("->", ".")
                                )
                                if t_id := self._resolve_call_target(c_name, ctx):
                                    edges.append(
                                        self._create_edge(parent_id, t_id, "calls", node=arg)
                                    )

            elif parent_id and node.type in (
                "call_expression",
                "method_invocation",
                "invocation_expression",
                "object_creation_expression",
                "new_expression",
                "function_call_expression",
                "member_call_expression",
                "nullsafe_member_call_expression",
                "scoped_call_expression",
            ):
                called_name = ""
                if node.type in ("object_creation_expression", "new_expression"):
                    type_node = node.child_by_field_name("type")
                    if not type_node:
                        for c in node.children:
                            if c.type in (
                                "type_identifier",
                                "scoped_type_identifier",
                                "name",
                                "qualified_name",
                            ):
                                type_node = c
                                break
                    if type_node:
                        raw_t = _strip_generic_args(self._get_text(type_node)).replace("::", ".")
                        raw_t = raw_t.lstrip("\\").strip()
                        if "\\" in raw_t:
                            short = raw_t.rsplit("\\", 1)[-1]
                            called_name = (
                                short
                                if (short in ctx.imported_symbols or short in ctx.defined_symbols)
                                else raw_t.replace("\\", ".")
                            )
                        else:
                            called_name = raw_t
                elif node.type == "scoped_call_expression":
                    scope_node = node.child_by_field_name("scope")
                    name_node = node.child_by_field_name("name")
                    if scope_node and name_node:
                        scope_raw = self._get_text(scope_node).strip()
                        name_text = self._get_text(name_node).strip()
                        if scope_raw == "static":
                            scope_text = "self"
                        elif "\\" in scope_raw:
                            clean_sc = scope_raw.lstrip("\\")
                            last_seg = clean_sc.rsplit("\\", 1)[-1]
                            scope_text = (
                                last_seg
                                if last_seg in ctx.imported_symbols
                                else clean_sc.replace("\\", ".")
                            )
                        else:
                            scope_text = scope_raw
                        called_name = f"{scope_text}.{name_text}"
                elif node.type in ("member_call_expression", "nullsafe_member_call_expression"):
                    obj_node = node.child_by_field_name("object")
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        name_text = self._get_text(name_node).strip()
                        if obj_node:
                            obj_raw = self._get_text(obj_node)
                            if "(" in obj_raw:
                                called_name = name_text
                            else:
                                clean_obj = _clean_php_member_chain(obj_raw)
                                called_name = f"{clean_obj}.{name_text}" if clean_obj else name_text
                        else:
                            called_name = name_text
                elif node.type == "function_call_expression":
                    func_node = node.child_by_field_name("function")
                    if func_node:
                        called_name = self._get_text(func_node).lstrip("\\").replace("\\", ".")
                else:
                    func_node = node.child_by_field_name("function") or node.child_by_field_name(
                        "name"
                    )
                    obj_node = node.child_by_field_name("object")
                    if func_node:
                        called_name = _strip_generic_args(
                            self._get_text(func_node).replace("::", ".").replace("->", ".")
                        )
                        if obj_node:
                            obj_text = _strip_generic_args(self._get_text(obj_node).strip())
                            called_name = f"{obj_text}.{called_name}"

                if (
                    called_name
                    and not any(ch.isspace() for ch in called_name)
                    and ("(" not in called_name or called_name.startswith("super()."))
                ):
                    target_id = self._resolve_call_target(called_name, ctx)
                    if target_id:
                        edges.append(self._create_edge(parent_id, target_id, "calls", node=node))

            current_scope_id = node_id if node_id else parent_id
            try:
                for child in node.children:
                    traverse(child, current_scope_id)
            finally:
                if pushed_scope:
                    ctx.scope_stack.pop()
                    if saved_class_prefix is not None:
                        ctx.current_class_prefix = saved_class_prefix

                if saved_local_types is not None:
                    ctx.local_types = saved_local_types

        traverse(tree.root_node, file_module_id)
        self.last_imported_symbols = dict(ctx.imported_symbols)
        return nodes, edges
