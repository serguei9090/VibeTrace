"""Top-down recursive call-tree hierarchy service for VibeGraph."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from vibegraph.indexer.db import IndexerDB


class CallTreeNode(BaseModel):
    """A single node in a recursive execution call tree."""

    id: str
    name: str
    qualified_name: str
    kind: str
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    signature: str | None = None
    relation_type: str | None = None
    call_line: int | None = None
    depth: int = 0
    is_cycle: bool = False
    children: list[CallTreeNode] = Field(default_factory=list)


class CallTreeResult(BaseModel):
    """Result of top-down execution call-tree hierarchy construction."""

    root_symbol: str
    found: bool
    max_depth: int
    tree: CallTreeNode | None = None
    total_nodes: int = 0
    node_ids: list[str] = Field(default_factory=list)


def _resolve_root_node(
    db: IndexerDB, root_symbol: str, file_path: str | None = None
) -> dict[str, Any] | None:
    """Find the best-matching root node for the given symbol name."""
    exact = db.find_nodes_by_name(root_symbol, file_path=file_path)
    if exact:
        return exact[0]
    prefix = db.search_ast_exact_or_prefix(root_symbol, limit=1)
    if prefix:
        return prefix[0]
    return None


def build_call_tree(
    db: IndexerDB,
    root_symbol: str,
    max_depth: int = 3,
    relations: tuple[str, ...] = ("calls", "references"),
    file_path: str | None = None,
) -> CallTreeResult:
    """Build a top-down execution call-tree down to max_depth with cycle detection."""
    root_data = _resolve_root_node(db, root_symbol, file_path=file_path)
    if not root_data:
        return CallTreeResult(root_symbol=root_symbol, found=False, max_depth=max_depth)

    collected_node_ids: set[str] = {root_data["id"]}
    total_nodes_count = [1]

    def _traverse(
        node_id: str,
        node_info: dict[str, Any],
        current_depth: int,
        ancestor_ids: set[str],
        relation: str | None = None,
        call_line: int | None = None,
    ) -> CallTreeNode:
        is_cycle = node_id in ancestor_ids
        tree_node = CallTreeNode(
            id=node_id,
            name=node_info["name"],
            qualified_name=node_info.get("qualified_name") or node_info["name"],
            kind=node_info.get("kind") or "function",
            file_path=node_info.get("file_path") or "",
            start_line=node_info.get("start_line"),
            end_line=node_info.get("end_line"),
            signature=node_info.get("signature"),
            relation_type=relation,
            call_line=call_line,
            depth=current_depth,
            is_cycle=is_cycle,
        )

        if is_cycle or current_depth >= max_depth:
            return tree_node

        next_ancestors = ancestor_ids | {node_id}
        callees = db.get_neighbors(node_id, "down")

        # Group and order outgoing calls by line
        filtered_callees = [c for c in callees if c.get("relation_type") in relations]
        filtered_callees.sort(key=lambda x: (x.get("call_line") or 999999, x.get("name") or ""))

        # Deduplicate same child called at different lines if preferred, or keep unique call targets
        seen_child_at_line: set[tuple[str, int | None]] = set()

        for edge in filtered_callees:
            child_id = edge["neighbor_id"]
            c_line = edge.get("call_line")
            rel = edge.get("relation_type", "calls")

            pair_key = (child_id, c_line)
            if pair_key in seen_child_at_line:
                continue
            seen_child_at_line.add(pair_key)

            collected_node_ids.add(child_id)
            total_nodes_count[0] += 1

            child_tree = _traverse(
                node_id=child_id,
                node_info=edge,
                current_depth=current_depth + 1,
                ancestor_ids=next_ancestors,
                relation=rel,
                call_line=c_line,
            )
            tree_node.children.append(child_tree)

        return tree_node

    root_tree = _traverse(
        node_id=root_data["id"],
        node_info=root_data,
        current_depth=0,
        ancestor_ids=set(),
    )

    return CallTreeResult(
        root_symbol=root_symbol,
        found=True,
        max_depth=max_depth,
        tree=root_tree,
        total_nodes=total_nodes_count[0],
        node_ids=list(collected_node_ids),
    )


def format_call_tree_md(result: CallTreeResult) -> str:
    """Format CallTreeResult into a clean markdown call tree with ASCII/Unicode connectors."""
    if not result.found or not result.tree:
        return (
            f"## Call Tree: `{result.root_symbol}`\n"
            f"Symbol '{result.root_symbol}' not found in index."
        )

    root = result.tree
    sig = f" `{root.signature}`" if root.signature else ""
    lines = [
        f"## Top-Down Call Tree: `{root.qualified_name}` (max depth: {result.max_depth})",
        f"**Root**: `{root.qualified_name}` ({root.kind}){sig}",
        f"**Location**: `{root.file_path}:L{root.start_line or 1}`",
        f"**Total Symbols in Hierarchy**: {result.total_nodes}",
        "",
        "```text",
    ]

    def _render_ascii(node: CallTreeNode, prefix: str = "", is_last: bool = True) -> None:
        if node.depth == 0:
            lines.append(f"{node.name} ({node.file_path}:L{node.start_line or 1})")
        else:
            connector = "└── " if is_last else "├── "
            call_at = f":L{node.call_line}" if node.call_line else ""
            rel = f"[{node.relation_type}{call_at}] " if node.relation_type else ""
            cycle_note = " (recursive / cycle)" if node.is_cycle else ""
            loc = f"({node.file_path}:L{node.start_line or 1})"
            lines.append(f"{prefix}{connector}{rel}{node.name} {loc}{cycle_note}")

        child_prefix = prefix + ("    " if is_last else "│   ") if node.depth > 0 else ""
        for idx, child in enumerate(node.children):
            is_child_last = idx == len(node.children) - 1
            _render_ascii(child, child_prefix, is_child_last)

    _render_ascii(root)
    lines.append("```")
    return "\n".join(lines)
