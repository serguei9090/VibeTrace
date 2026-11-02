from tree_sitter import Node

from vibetrace.indexer.db import Edge
from vibetrace.indexer.db import Node as DBNode
from vibetrace.indexer.parsers.base import LanguageParser, ParserContext
from vibetrace.indexer.parsers.python_helpers import (
    extract_pattern_references,
    extract_python_imports,
    link_all_exports,
    resolve_deferred_fixtures,
)
from vibetrace.indexer.parsers.route_extractor import (
    extract_python_router_prefixes,
    extract_python_routes,
)
from vibetrace.indexer.resolver import ModuleResolver


class PythonParser(LanguageParser):
    """Parser for Python files."""

    def __init__(self, resolver: ModuleResolver | None = None):
        super().__init__("python", resolver)

    def _extract_docstring(self, node: Node) -> str | None:
        if not (body_node := node.child_by_field_name("body")):
            return None
        for child in body_node.children:
            if child.type == "expression_statement" and child.children:
                if child.children[0].type == "string":
                    return self._get_text(child.children[0]).strip("\"'")
            elif child.type != "comment":
                break
        return None

    def _extract_type_alias_name(self, node: Node) -> str | None:
        """Extract symbol identifier from a Python 3.12 PEP 695 type_alias_statement."""
        if not node.named_children:
            return None
        first = node.named_children[0]
        candidates = (
            [first]
            + list(first.children)
            + (list(first.children[0].children) if first.children else [])
        )
        id_node = next((c for c in candidates if c.type == "identifier"), None)
        return self._get_text(id_node).strip() if id_node else None

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
        ctx.router_prefixes = extract_python_router_prefixes(source_code)  # type: ignore[attr-defined]
        # Pre-populate defined top-level symbol names to avoid false-positive builtin filtering
        for child in tree.root_node.children:
            target = child
            if child.type == "decorated_definition":
                target = next(
                    (
                        c
                        for c in child.children
                        if c.type in ("function_definition", "class_definition")
                    ),
                    child,
                )
            if target.type in ("function_definition", "class_definition"):
                name_node = target.child_by_field_name("name")
                if name_node:
                    ctx.defined_symbols.add(self._get_text(name_node))
            elif target.type == "type_alias_statement":
                alias_name = self._extract_type_alias_name(target)
                if alias_name:
                    ctx.defined_symbols.add(alias_name)

        self._traverse(tree.root_node, file_module_id, ctx)

        # Resolve deferred fixtures and explicit __all__ re-exports via modular helpers
        resolve_deferred_fixtures(self, ctx, edges)
        link_all_exports(self, tree.root_node, ctx, nodes)

        return nodes, edges

    def _extract_type_name(self, type_node: Node | None) -> str | None:
        """Extract clean type identifier from type annotation node."""
        if not type_node:
            return None
        text = self._get_text(type_node).strip().removeprefix("typing.")
        if text.startswith("Optional[") and text.endswith("]"):
            text = text[9:-1].strip()
        elif text.startswith("Union[") and text.endswith("]"):
            parts = [p.strip() for p in text[6:-1].split(",") if p.strip() != "None"]
            if parts:
                text = parts[0]
        if "|" in text:
            parts = [p.strip() for p in text.split("|") if p.strip() != "None"]
            if parts:
                text = parts[0]
        text = text.strip("'\"")
        return text if text else None

    def _record_var_type(self, var_name: str, right_node: Node, ctx: ParserContext) -> None:
        if right_node.type == "call":
            func_node = right_node.child_by_field_name("function")
            if func_node:
                ctor_name = self._get_text(func_node).strip()
                if ctor_name and (
                    ctor_name in ctx.imported_symbols
                    or (ctor_name[0].isupper() and "(" not in ctor_name)
                    or "." in ctor_name
                ):
                    ctx.local_types[var_name] = ctor_name
        elif right_node.type in ("identifier", "attribute"):
            right_name = self._get_text(right_node).strip()
            if right_name in ctx.local_types:
                ctx.local_types[var_name] = ctx.local_types[right_name]

    def _extract_param_types(self, params_node: Node, ctx: ParserContext) -> None:
        """Record parameter type annotations into ctx.local_types."""
        for child in params_node.children:
            if child.type in ("typed_parameter", "typed_default_parameter"):
                name_node = child.child_by_field_name("name") or next(
                    (c for c in child.children if c.type == "identifier"), None
                )
                type_node = child.child_by_field_name("type")
                if name_node and type_node:
                    p_name = self._get_text(name_node)
                    p_type = self._extract_type_name(type_node)
                    if p_type:
                        ctx.local_types[p_name] = p_type
            elif child.type == "default_parameter":
                name_node = child.child_by_field_name("name")
                val_node = child.child_by_field_name("value")
                if name_node and val_node:
                    self._record_var_type(self._get_text(name_node), val_node, ctx)

    def _extract_param_names(self, params_node: Node) -> list[str]:
        """Extract parameter names from a parameters node."""
        names: list[str] = []
        for child in params_node.children:
            if child.type == "identifier":
                names.append(self._get_text(child).strip())
            elif child.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
                name_node = child.child_by_field_name("name") or next(
                    (c for c in child.children if c.type == "identifier"), None
                )
                if name_node:
                    names.append(self._get_text(name_node).strip())
        return names

    def _extract_parametrize_candidate_nodes(self, node: Node) -> list[Node]:
        """Recursively collects potential symbol identifier/attribute nodes from argvalues."""
        candidates: list[Node] = []
        if node.type in ("identifier", "attribute"):
            candidates.append(node)
        elif node.type in ("list", "tuple", "set"):
            for child in node.children:
                if child.type not in ("(", ")", "[", "]", "{", "}", ","):
                    candidates.extend(self._extract_parametrize_candidate_nodes(child))
        elif node.type == "call":
            args_node = node.child_by_field_name("arguments")
            if args_node:
                for child in args_node.children:
                    if child.type not in ("(", ")", ",") and child.type != "keyword_argument":
                        candidates.extend(self._extract_parametrize_candidate_nodes(child))
        return candidates

    def _handle_pytest_parametrize(
        self, node_id: str, decorator_nodes: list[Node], ctx: ParserContext
    ) -> None:
        """Extract test target symbols from @pytest.mark.parametrize and link call edges."""
        for dec_node in decorator_nodes:
            call_node = next((c for c in dec_node.children if c.type == "call"), None)
            if not call_node:
                continue
            func_node = call_node.child_by_field_name("function")
            if not func_node:
                continue
            func_text = self._get_text(func_node).strip()
            if "parametrize" not in func_text:
                continue

            args_node = call_node.child_by_field_name("arguments")
            if not args_node:
                continue

            pos_args = [
                c
                for c in args_node.children
                if c.type not in ("(", ")", ",") and c.type != "keyword_argument"
            ]
            val_nodes: list[Node] = []
            if len(pos_args) >= 2:
                val_nodes.append(pos_args[1])
            else:
                for c in args_node.children:
                    if c.type == "keyword_argument":
                        k_name = c.child_by_field_name("name")
                        k_val = c.child_by_field_name("value")
                        if k_name and self._get_text(k_name).strip() == "argvalues" and k_val:
                            val_nodes.append(k_val)

            for v_node in val_nodes:
                candidates = self._extract_parametrize_candidate_nodes(v_node)
                for cand in candidates:
                    cand_text = self._get_text(cand).strip()
                    if cand_text in ("True", "False", "None", ""):
                        continue
                    if cand_text in self.builtins and cand_text not in ctx.defined_symbols:
                        continue
                    target_id = self._resolve_call_target(cand_text, ctx)
                    if target_id:
                        ctx.edges.append(self._create_edge(node_id, target_id, "calls", node=cand))

    def _handle_assignment(self, node: Node, ctx: ParserContext) -> None:
        """Record local variable types from explicit annotations, constructors, or assignments."""
        left_node = node.child_by_field_name("left") or node.child_by_field_name("name")
        if not left_node:
            return
        var_name = self._get_text(left_node).strip()

        type_node = node.child_by_field_name("type")
        if type_node:
            type_name = self._extract_type_name(type_node)
            if type_name:
                ctx.local_types[var_name] = type_name
                return

        right_node = node.child_by_field_name("right") or node.child_by_field_name("value")
        if right_node:
            self._record_var_type(var_name, right_node, ctx)

    def _handle_with_statement(self, node: Node, ctx: ParserContext) -> None:
        """Record context manager variable bindings in ctx.local_types."""
        for clause in node.children:
            if clause.type != "with_clause":
                continue
            for item in clause.children:
                if item.type != "with_item":
                    continue
                for pattern in item.children:
                    if pattern.type == "as_pattern":
                        alias = pattern.child_by_field_name("alias")
                        val = pattern.children[0] if pattern.children else None
                        if alias and val:
                            self._record_var_type(self._get_text(alias).strip(), val, ctx)

    def _traverse(
        self,
        node: Node,
        parent_id: str | None,
        ctx: ParserContext,
        decorators: list[str] | None = None,
        decorator_nodes: list[Node] | None = None,
    ) -> None:
        # Special handling for decorated definitions
        if node.type == "decorated_definition":
            self._handle_decorated(node, parent_id, ctx)
            return

        node_id = None
        pushed_scope = False
        saved_local_types: dict[str, str] | None = None

        # 1. Definitions
        if node.type in ("function_definition", "class_definition"):
            saved_local_types = dict(ctx.local_types)
            node_id, def_name = self._handle_definition(
                node, parent_id, ctx, decorators, decorator_nodes=decorator_nodes
            )
            if def_name:
                ctx.scope_stack.append(def_name)
                pushed_scope = True

        # 2. PEP 695 Type Aliases
        elif node.type == "type_alias_statement":
            self._handle_type_alias(node, parent_id, ctx)

        # 3. Calls
        elif parent_id and node.type == "call":
            self._handle_call(node, parent_id, ctx)

        # 4. Imports
        elif node.type in ("import_statement", "import_from_statement"):
            self._handle_import(node, ctx)

        # 5. Dispatch maps: dictionary with symbol references
        elif parent_id and node.type == "dictionary":
            self._handle_dictionary(node, parent_id, ctx)

        # 6. Assignments (constructor instantiation, walrus := & type annotations)
        elif node.type in ("assignment", "named_expression"):
            self._handle_assignment(node, ctx)

        # 7. Context managers
        elif node.type == "with_statement":
            self._handle_with_statement(node, ctx)

        # 8. Attribute symbol references (e.g. Color.RED)
        elif parent_id and node.type == "attribute":
            attr_text = self._get_text(node).strip()
            if attr_text in ctx.defined_symbols:
                target_id = self._resolve_call_target(attr_text, ctx)
                if target_id and not (node.parent and node.parent.type in ("call", "attribute")):
                    ctx.edges.append(
                        self._create_edge(parent_id, target_id, "references", node=node)
                    )

        # 9. Match case pattern references (e.g. case Point(x, y): or case Status.OK:)
        elif parent_id and node.type == "case_clause":
            for ch in node.children:
                if ch.type == "case_pattern":
                    self._extract_pattern_references(ch, parent_id, ctx)

        # Recurse
        current_scope_id = node_id if node_id else parent_id
        for child in node.children:
            self._traverse(child, current_scope_id, ctx)

        if pushed_scope:
            ctx.scope_stack.pop()

        if saved_local_types is not None:
            if node.type == "function_definition" and ctx.scope_stack:
                # Inside a class method: preserve instance attributes on self for sibling methods
                for k, v in ctx.local_types.items():
                    if k.startswith(("self.", "this.")):
                        saved_local_types[k] = v
            # Restore saved_local_types (cleanly isolates top-level functions and class scopes)
            ctx.local_types = saved_local_types

    def _handle_dictionary(self, node: Node, parent_id: str, ctx: ParserContext) -> None:
        for child in node.children:
            if child.type == "pair":
                val_node = child.child_by_field_name("value")
                if val_node and val_node.type in ("identifier", "attribute"):
                    val_name = self._get_text(val_node)
                    target_id = self._resolve_call_target(val_name, ctx)
                    if target_id:
                        ctx.edges.append(
                            self._create_edge(parent_id, target_id, "references", node=val_node)
                        )

    def _handle_decorated(self, node: Node, parent_id: str | None, ctx: ParserContext) -> None:
        decorator_nodes = [c for c in node.children if c.type == "decorator"]
        decorators = [self._get_text(c) for c in decorator_nodes]
        def_node = next(
            (c for c in node.children if c.type in ("function_definition", "class_definition")),
            None,
        )
        if def_node:
            self._traverse(def_node, parent_id, ctx, decorators, decorator_nodes=decorator_nodes)

    def _handle_definition(
        self,
        node: Node,
        parent_id: str | None,
        ctx: ParserContext,
        decorators: list[str] | None,
        decorator_nodes: list[Node] | None = None,
    ) -> tuple[str | None, str | None]:
        if not (name_node := node.child_by_field_name("name")):
            return None, None

        name = self._get_text(name_node)
        qualified_name = self._scoped_name(name, ctx.scope_stack)
        node_id = self._get_id(ctx.file_path, qualified_name)

        is_enum = False
        if node.type == "class_definition":
            if sc := node.child_by_field_name("superclasses"):
                for arg in sc.children:
                    if arg.type in ("identifier", "attribute"):
                        base_name = self._get_text(arg).split(".")[-1]
                        if base_name in (
                            "Enum",
                            "IntEnum",
                            "StrEnum",
                            "Flag",
                            "IntFlag",
                            "ReprEnum",
                        ):
                            is_enum = True
                            break

        if node.type == "function_definition":
            kind = "function"
        elif is_enum:
            kind = "enum"
        else:
            kind = "class"

        params_node = node.child_by_field_name("parameters")
        if params_node:
            self._extract_param_types(params_node, ctx)
        signature = self._get_text(params_node) if params_node else None
        if signature and node.type == "function_definition":
            if ret_node := node.child_by_field_name("return_type"):
                ret_text = self._get_text(ret_node).strip()
                if ret_text:
                    signature = f"{signature} -> {ret_text}"
        docstring = self._extract_docstring(node)

        visibility = "private" if name.startswith("_") and not name.startswith("__") else "public"

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

        if is_enum and (body_node := node.child_by_field_name("body")):
            for stmt in body_node.children:
                if stmt.type == "expression_statement":
                    for asgn in stmt.children:
                        if asgn.type == "assignment":
                            left = asgn.child_by_field_name("left") or (
                                asgn.children[0] if asgn.children else None
                            )
                            if left and left.type == "identifier":
                                var_name = self._get_text(left).strip()
                                var_qual = f"{qualified_name}.{var_name}"
                                var_id = self._get_id(ctx.file_path, var_qual)
                                ctx.nodes.append(
                                    self._create_node(
                                        var_id,
                                        var_name,
                                        "variable",
                                        ctx.file_path,
                                        asgn,
                                        signature=self._get_text(asgn).strip(),
                                        visibility="public",
                                        qualified_name=var_qual,
                                        parent_id=node_id,
                                    )
                                )
                                ctx.edges.append(
                                    self._create_edge(node_id, var_id, "defines", node=asgn)
                                )
                                ctx.defined_symbols.add(var_name)
                                ctx.defined_symbols.add(var_qual)

        is_fixture = False
        if decorators and node.type == "function_definition":
            is_fixture = any(
                d.strip().startswith(("@pytest.fixture", "@fixture", "pytest.fixture", "fixture"))
                for d in decorators
            )
            if is_fixture:
                ctx.test_fixtures[name] = node_id
                ctx.test_fixtures[qualified_name] = node_id

            if decorator_nodes:
                self._handle_pytest_parametrize(node_id, decorator_nodes, ctx)

        if node.type == "function_definition" and params_node:
            p_names = self._extract_param_names(params_node)
            norm_path = ctx.file_path.replace("\\", "/").lower()
            is_test = name.startswith("test_") or "test" in norm_path or "tests" in norm_path
            if is_fixture or is_test:
                ctx.deferred_fixture_links.append((node_id, node, p_names))

        if decorators:
            endpoint_nodes, route_edges = extract_python_routes(
                decorators,
                new_node,
                ctx.file_path,
                self._get_id,
                router_prefixes=getattr(ctx, "router_prefixes", None),
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
            if sc := node.child_by_field_name("superclasses"):
                for arg in sc.children:
                    if arg.type in ("identifier", "attribute"):
                        base_name = self._get_text(arg)
                        if base_name != "object" and "super()" not in ctx.local_types:
                            ctx.local_types["super()"] = base_name
                        if base_id := self._resolve_call_target(base_name, ctx):
                            ctx.edges.append(
                                self._create_edge(node_id, base_id, "inherits", node=arg)
                            )

        return node_id, name

    def _handle_type_alias(self, node: Node, parent_id: str | None, ctx: ParserContext) -> None:
        name = self._extract_type_alias_name(node)
        if not name:
            return
        qualified_name = self._scoped_name(name, ctx.scope_stack)
        node_id = self._get_id(ctx.file_path, qualified_name)
        signature = self._get_text(node).strip()
        visibility = "private" if name.startswith("_") and not name.startswith("__") else "public"
        new_node = self._create_node(
            node_id,
            name,
            "interface",
            ctx.file_path,
            node,
            signature=signature,
            visibility=visibility,
            qualified_name=qualified_name,
            parent_id=parent_id,
        )
        ctx.nodes.append(new_node)
        ctx.defined_symbols.add(name)
        if parent_id:
            ctx.edges.append(self._create_edge(parent_id, node_id, "defines", node=node))

    def _handle_call(self, node: Node, parent_id: str, ctx: ParserContext) -> None:
        func_node = node.child_by_field_name("function")
        if not func_node:
            return

        called_name = self._get_text(func_node)

        # Dynamic heuristic: getattr(obj, "method") or getattr(obj, "method")()
        if called_name == "getattr":
            g_call = node
        elif (
            func_node.type == "call"
            and (sf := func_node.child_by_field_name("function"))
            and self._get_text(sf) == "getattr"
        ):
            g_call = func_node
        else:
            g_call = None
        if g_call and (s_args := g_call.child_by_field_name("arguments")):
            args = [c for c in s_args.children if c.type not in ("(", ")", ",")]
            if len(args) >= 2 and args[1].type == "string":
                attr_name = self._get_text(args[1]).strip("'\"")
                obj_name = self._get_text(args[0])
                if target_id := self._resolve_call_target(f"{obj_name}.{attr_name}", ctx):
                    ctx.edges.append(self._create_edge(parent_id, target_id, "calls", node=node))
                return

        target_id = self._resolve_call_target(called_name, ctx)
        if target_id:
            ctx.edges.append(self._create_edge(parent_id, target_id, "calls", node=node))

    def _extract_pattern_references(self, pat: Node, parent_id: str, ctx: ParserContext) -> None:
        """Extract type and attribute references from Python match-case patterns."""
        extract_pattern_references(self, pat, parent_id, ctx)

    def _handle_import(self, node: Node, ctx: ParserContext) -> None:
        extract_python_imports(self, node, ctx)
