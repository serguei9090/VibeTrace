"""Helper utilities for Python AST parsing and symbol extraction.

Extracts import resolution, match-case pattern references, deferred fixture links,
and __all__ re-export resolution to keep the primary PythonParser focused and modular.
"""

from __future__ import annotations

from typing import Any

from tree_sitter import Node

from vibetrace.indexer.db import Edge
from vibetrace.indexer.db import Node as DBNode
from vibetrace.indexer.parsers.base import ParserContext


def extract_python_imports(parser: Any, node: Node, ctx: ParserContext) -> None:
    """Processes import and import_from statements, registering module edges and symbols."""
    if node.type == "import_statement":
        for child in node.children:
            if child.type == "dotted_name":
                module_name = parser._get_text(child)
                resolved = parser._register_imported_module(module_name, ctx, node)
                target = resolved or "external"
                ctx.imported_symbols[module_name] = (target, "__module__")
                ctx.imported_symbols[module_name.split(".")[-1]] = (target, "__module__")
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node:
                    module_name = parser._get_text(name_node)
                    alias = parser._get_text(alias_node) if alias_node else module_name
                    resolved = parser._register_imported_module(module_name, ctx, node)
                    target = resolved or "external"
                    ctx.imported_symbols[alias] = (target, "__module__")

    elif node.type == "import_from_statement":
        if not (module_name_node := node.child_by_field_name("module_name")):
            return
        module_name = parser._get_text(module_name_node)
        is_bare_dots = not module_name.strip(".")
        resolved_mod = None
        if not is_bare_dots:
            resolved_mod = parser._register_imported_module(module_name, ctx, node)
        elif parser.resolver:
            resolved_mod = parser.resolver.resolve(module_name, ctx.file_path)

        seen_import_kw = False
        for child in node.children:
            if child.type == "import":
                seen_import_kw = True
                continue
            if not seen_import_kw:
                continue

            if child.type in ("wildcard_import", "*") or parser._get_text(child) == "*":
                target_module = resolved_mod or "external"
                ctx.imported_symbols["*"] = (target_module, "__module__")
                continue

            orig_name: str | None = None
            local_name: str | None = None
            if child.type == "dotted_name":
                orig_name = local_name = parser._get_text(child)
            elif child.type == "aliased_import":
                n_node = child.child_by_field_name("name")
                a_node = child.child_by_field_name("alias")
                if n_node:
                    orig_name = parser._get_text(n_node)
                    local_name = parser._get_text(a_node) if a_node else orig_name

            if orig_name and local_name:
                sub_query = (
                    f"{module_name}{orig_name}"
                    if module_name.endswith(".")
                    else f"{module_name}.{orig_name}"
                )
                sub_resolved = (
                    parser.resolver.resolve(sub_query, ctx.file_path) if parser.resolver else None
                )
                if sub_resolved:
                    parser._register_imported_module(sub_query, ctx, node)
                    ctx.imported_symbols[local_name] = (sub_resolved, "__module__")
                else:
                    target_mod = resolved_mod or "external"
                    ctx.imported_symbols[local_name] = (target_mod, orig_name)
                    if is_bare_dots and target_mod != "external" and target_mod != ctx.file_path:
                        parser._register_imported_module(module_name, ctx, node)


def extract_pattern_references(parser: Any, pat: Node, parent_id: str, ctx: ParserContext) -> None:
    """Extract type and attribute references from Python match-case patterns."""
    for ch in pat.children:
        if ch.type == "class_pattern":
            if cls_name_node := ch.children[0] if ch.children else None:
                cls_name = parser._get_text(cls_name_node).strip()
                if t_id := parser._resolve_call_target(cls_name, ctx):
                    ctx.edges.append(
                        parser._create_edge(parent_id, t_id, "references", node=cls_name_node)
                    )
        elif ch.type == "dotted_name" and "." in parser._get_text(ch):
            dot_text = parser._get_text(ch).strip()
            if t_id := parser._resolve_call_target(dot_text, ctx):
                ctx.edges.append(parser._create_edge(parent_id, t_id, "references", node=ch))
        extract_pattern_references(parser, ch, parent_id, ctx)


def resolve_deferred_fixtures(
    parser: Any,
    ctx: ParserContext,
    edges: list[Edge],
) -> None:
    """Resolves deferred test fixture parameter dependencies into call edges."""
    for fn_node_id, fn_node, p_names in ctx.deferred_fixture_links:
        for p_name in p_names:
            if p_name in ("self", "cls"):
                continue
            if p_name in ctx.test_fixtures:
                fix_id = ctx.test_fixtures[p_name]
                edges.append(parser._create_edge(fn_node_id, fix_id, "calls", node=fn_node))
            elif p_name in ctx.imported_symbols:
                target_id = parser._resolve_call_target(p_name, ctx)
                if target_id:
                    edges.append(parser._create_edge(fn_node_id, target_id, "calls", node=fn_node))


def link_all_exports(
    parser: Any,
    tree_root: Node,
    ctx: ParserContext,
    nodes: list[DBNode],
) -> None:
    """Links explicit __all__ re-exports to imported symbols."""
    for child in tree_root.children:
        if child.type != "expression_statement":
            continue
        for sub in child.children:
            if sub.type != "assignment":
                continue
            left = sub.child_by_field_name("left")
            if not left or parser._get_text(left).strip() != "__all__":
                continue
            right = sub.child_by_field_name("right")
            if not right or right.type not in ("list", "tuple"):
                continue
            for item in right.children:
                if item.type != "string":
                    continue
                exp_name = parser._get_text(item).strip("\"'")
                if exp_name in ctx.imported_symbols:
                    target_f, orig_n = ctx.imported_symbols[exp_name]
                    exp_id = parser._get_id(ctx.file_path, exp_name)
                    if not any(n.id == exp_id for n in ctx.nodes):
                        if parser.resolver:
                            mod_name = parser.resolver.get_module_name(ctx.file_path)
                        else:
                            mod_name = ctx.file_path.split("/")[-1].replace(".py", "")
                        nodes.append(
                            parser._create_node(
                                exp_id,
                                exp_name,
                                "variable",
                                ctx.file_path,
                                item,
                                signature=f"{exp_name} (re-exported)",
                                visibility="public",
                                qualified_name=f"{mod_name}.{exp_name}",
                                parent_id=ctx.file_module_id,
                            )
                        )
                    if target_f != "external":
                        target_id = parser._get_id(target_f, orig_n)
                        ctx.edges.append(
                            parser._create_edge(exp_id, target_id, "references", node=item)
                        )
