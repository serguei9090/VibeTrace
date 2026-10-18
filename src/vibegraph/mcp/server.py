"""
VibeGraph MCP Server - Code Intelligence Tools.

Exposes FastMCP tools for structural code analysis, hybrid search, and execution pathfinding.
"""

import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from vibegraph.indexer.main import reindex_all
from vibegraph.mcp.formatters import (
    format_code_slice_md,
    format_semantic_search_md,
    format_structural_summary_md,
    format_trace_path_md,
)
from vibegraph.mcp.schemas import (
    CallStackInput,
    CallTreeInput,
    CodeSliceInput,
    DependenciesInput,
    ImpactAnalysisInput,
    ReferencesInput,
    ReindexInput,
    ResponseFormat,
    SearchInput,
    SemanticSearchInput,
    StructuralSummaryInput,
    TraceDirection,
    TracePathInput,
)
from vibegraph.services.graph_service import (
    GraphTraverser,
    build_call_tree,
    categorize_file_dependencies,
    compute_impact_analysis,
    format_call_tree_md,
    get_code_slice,
    get_context_for_path,
    handle_error,
    normalize_path,
    notify_ui_focus,
    safe_str,
    trace_execution_path,
)
from vibegraph.services.hybrid_search import hybrid_search

# Compatibility aliases for existing tests and callers
_get_context_for_path = get_context_for_path
_normalize_path = normalize_path
_safe_str = safe_str
_handle_error = handle_error

mcp = FastMCP("vibegraph_mcp")

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


@mcp.tool(name="vibegraph_get_structural_summary", annotations=_READ_ONLY)
async def vibegraph_get_structural_summary(params: StructuralSummaryInput) -> str:
    """Get a concise structural summary of a file (classes, functions, methods)."""
    try:
        db, root = _get_context_for_path(params.file_path)
        norm_path = _normalize_path(params.file_path, root)
        total, rows = db.get_file_structure(norm_path, params.limit, params.offset)

        if total == 0:
            return (
                f"No structure found for {params.file_path}. "
                "File might not be indexed. Try vibegraph_reindex_project first."
            )

        notify_ui_focus(
            "structural_summary",
            [r["id"] for r in rows],
            summary=f"Inspecting structure of {norm_path}",
        )

        if params.response_format == ResponseFormat.JSON:
            nodes = [
                {
                    "name": row["name"],
                    "qualified_name": row.get("qualified_name") or row["name"],
                    "kind": row["kind"],
                    "signature": row["signature"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "decorators": json.loads(row["decorators"]) if row["decorators"] else [],
                    "visibility": row["visibility"] or "public",
                }
                for row in rows
            ]
            return json.dumps(
                {
                    "file_path": norm_path,
                    "total": total,
                    "count": len(nodes),
                    "offset": params.offset,
                    "has_more": total > params.offset + len(nodes),
                    "next_offset": (
                        params.offset + len(nodes) if total > params.offset + len(nodes) else None
                    ),
                    "nodes": nodes,
                },
                indent=2,
            )

        return format_structural_summary_md(norm_path, total, rows, params.limit, params.offset)
    except Exception as e:
        return handle_error(e, f"analyzing {params.file_path}")


@mcp.tool(name="vibegraph_get_call_stack", annotations=_READ_ONLY)
async def vibegraph_get_call_stack(params: CallStackInput) -> str:
    """Trace function calls up (callers) or down (callees)."""
    try:
        db, root = _get_context_for_path(params.file_path)
        norm_file = _normalize_path(params.file_path, root) if params.file_path else None
        start_nodes = db.find_nodes_by_name(params.node_name, norm_file)

        if not start_nodes:
            return f"Node '{params.node_name}' not found. Is the file indexed?"

        notify_ui_focus(
            "call_stack",
            [n["id"] for n in start_nodes],
            summary=f"Tracing call stack for {params.node_name}",
        )

        traverser = GraphTraverser(db)
        for start_node in start_nodes:
            traverser.output.append(
                f"### Trace for `{start_node['name']}` ({start_node['kind']}) "
                f"in `{start_node['file_path']}`"
            )

            if params.direction in (TraceDirection.UP, TraceDirection.BOTH):
                traverser.output.append("\n**Callers (Incoming):**")
                traverser.traverse(
                    start_node["id"], 1, params.depth, "up", 0, path_stack=[start_node["name"]]
                )
                if not any(safe_str("←") in line for line in traverser.output[-5:]):
                    traverser.output.append("  (no callers found)")
                traverser.visited.clear()

            if params.direction in (TraceDirection.DOWN, TraceDirection.BOTH):
                traverser.output.append("\n**Callees (Outgoing):**")
                traverser.traverse(
                    start_node["id"], 1, params.depth, "down", 0, path_stack=[start_node["name"]]
                )
                if not any(safe_str("→") in line for line in traverser.output[-5:]):
                    traverser.output.append("  (no callees found)")

        return "\n".join(traverser.output)
    except Exception as e:
        return handle_error(e, f"tracing {params.node_name}")


@mcp.tool(name="vibegraph_impact_analysis", annotations=_READ_ONLY)
async def vibegraph_impact_analysis(params: ImpactAnalysisInput) -> str:
    """Analyze what other files/functions break if this file is modified."""
    try:
        db, root = _get_context_for_path(params.file_path)
        norm_path = _normalize_path(params.file_path, root)

        total_impact, impacts_by_level = compute_impact_analysis(db, norm_path)
        file_nodes = db.get_nodes_in_file(norm_path)
        if total_impact == 0 and not file_nodes:
            return f"No nodes found in {params.file_path}. Is it indexed?"

        notify_ui_focus(
            "impact_analysis",
            [n["id"] for n in file_nodes],
            summary=f"Blast radius analysis for {norm_path}",
        )

        output = [f"## Impact Analysis for `{norm_path}`"]
        if total_impact == 0:
            output.append("✅ No external dependencies found. Safe to refactor internally.")
        else:
            output.append(f"**Total Impact**: {total_impact} components affected up to 3 levels.\n")
            for lvl, title in (
                (1, "### Level 1: Direct Impact"),
                (2, "### Level 2: Secondary Impact (Ripple Effect)"),
                (3, "### Level 3: Deep Impact"),
            ):
                if impacts_by_level.get(lvl):
                    output.append(title)
                    output.extend(sorted(set(impacts_by_level[lvl])))
                    output.append("")

        return "\n".join(output)
    except Exception as e:
        return handle_error(e, f"analyzing impact for {params.file_path}")


@mcp.tool(name="vibegraph_find_references", annotations=_READ_ONLY)
async def vibegraph_find_references(params: ReferencesInput) -> str:
    """Find where a specific function or class is called/used."""
    try:
        db, _ = _get_context_for_path(params.scope_path)
        targets = db.find_nodes_by_name(params.symbol_name)

        if not targets:
            return f"Symbol '{params.symbol_name}' not found in index."

        output = [f"## References to `{params.symbol_name}`"]
        for target in targets:
            target_desc = f"`{target['name']}` from `{target['file_path']}`"
            refs = db.get_symbol_references(target["id"])
            if refs:
                output.append(f"\n### Usages of {target_desc}")
                for ref in refs:
                    call_ln = ref.get("call_line") or ref["start_line"]
                    output.append(
                        f"- Used by `{ref['name']}` in `{ref['file_path']}` "
                        f"(L{call_ln}) [{ref['relation_type']}]"
                    )
            else:
                output.append(f"\n### Usages of {target_desc}\n- No direct calls found.")

        return "\n".join(output)
    except Exception as e:
        return handle_error(e, f"finding references for {params.symbol_name}")


@mcp.tool(name="vibegraph_get_dependencies", annotations=_READ_ONLY)
async def vibegraph_get_dependencies(params: DependenciesInput) -> str:
    """List modules and symbols imported by a specific file."""
    try:
        db, root = _get_context_for_path(params.file_path)
        norm_path = _normalize_path(params.file_path, root)
        internal, third_party, stdlib = categorize_file_dependencies(db, norm_path)

        output = [f"## Dependencies for `{norm_path}`"]
        if internal:
            output.append("\n### 🏠 Internal Project Modules")
            for dep in internal:
                output.append(f"- **{dep['name']}** (`{dep['file_path']}`)")
        if third_party:
            output.append("\n### 📦 Third-Party Packages")
            for dep in third_party:
                output.append(f"- **{dep['name']}**")
        if stdlib:
            output.append("\n### 🐍 Standard Library")
            for dep in stdlib:
                output.append(f"- {dep['name']}")
        if not (internal or third_party or stdlib):
            output.append("No explicit imports found in index.")

        return "\n".join(output)
    except Exception as e:
        return handle_error(e, f"getting dependencies for {params.file_path}")


@mcp.tool(name="vibegraph_search_by_signature", annotations=_READ_ONLY)
async def vibegraph_search_by_signature(params: SearchInput) -> str:
    """Search for functions matching a specific signature pattern."""
    try:
        db, _ = _get_context_for_path(params.scope_path)
        rows = db.search_by_signature_pattern(params.pattern, limit=50)

        output = [f"## Signature Search: `{params.pattern}`"]
        if not rows:
            output.append("No matches found.")
        else:
            for row in rows:
                output.append(
                    f"- **`{row['name']}`**: `{row['signature']}`\n"
                    f"  - In `{row['file_path']}`:L{row['start_line']}"
                )
        return "\n".join(output)
    except Exception as e:
        return handle_error(e, f"searching signature {params.pattern}")


@mcp.tool(name="vibegraph_semantic_search", annotations=_READ_ONLY)
async def vibegraph_semantic_search(params: SemanticSearchInput) -> str:
    """Hybrid RRF search across AST symbols, SQLite FTS5 BM25, and EmbeddingGemma 2 vectors."""
    try:
        db, _ = _get_context_for_path(params.scope_path)
        results = hybrid_search(db, params.query, limit=params.limit)

        notify_ui_focus(
            "semantic_search",
            [r.node_id for r in results[:5]],
            summary=f"Searching '{params.query}'",
        )

        if params.response_format == ResponseFormat.JSON:
            return json.dumps([r.model_dump() for r in results], indent=2)
        return format_semantic_search_md(params.query, results)
    except Exception as e:
        return handle_error(e, f"hybrid search for {params.query}")


@mcp.tool(name="vibegraph_trace_path", annotations=_READ_ONLY)
async def vibegraph_trace_path(params: TracePathInput) -> str:
    """Find shortest execution tracepath(s) between two symbols with call-site lines."""
    try:
        db, _ = _get_context_for_path(params.scope_path)
        result = trace_execution_path(
            db,
            params.from_symbol,
            params.to_symbol,
            max_depth=params.max_depth,
            engine=params.engine,
        )

        if result.found:
            notify_ui_focus(
                "trace_path",
                result.node_ids,
                summary=f"Tracepath {params.from_symbol} -> {params.to_symbol}",
                from_symbol=params.from_symbol,
                to_symbol=params.to_symbol,
            )

        if params.response_format == ResponseFormat.JSON:
            return result.model_dump_json(indent=2)
        return format_trace_path_md(result)
    except Exception as e:
        return handle_error(e, f"tracing path {params.from_symbol} -> {params.to_symbol}")


@mcp.tool(name="vibegraph_get_code_slice", annotations=_READ_ONLY)
async def vibegraph_get_code_slice(params: CodeSliceInput) -> str:
    """Read exact source lines (start_line..end_line) and callee contracts for a symbol."""
    try:
        db, root = _get_context_for_path(params.file_path or params.scope_path)
        result = get_code_slice(
            db,
            root,
            params.symbol_name,
            file_path=params.file_path,
            include_callees=params.include_callees,
        )

        if result.found and result.node_id:
            notify_ui_focus(
                "code_slice",
                [result.node_id],
                summary=f"Reading slice {result.qualified_name}",
            )

        if params.response_format == ResponseFormat.JSON:
            return result.model_dump_json(indent=2)
        return format_code_slice_md(result)
    except Exception as e:
        return handle_error(e, f"slicing {params.symbol_name}")


@mcp.tool(name="vibegraph_get_call_tree", annotations=_READ_ONLY)
async def vibegraph_get_call_tree(params: CallTreeInput) -> str:
    """Explore recursive top-down call tree from an entry point down to leaves."""
    try:
        db, _ = _get_context_for_path(params.file_path or params.scope_path)
        result = build_call_tree(
            db,
            params.symbol_name,
            max_depth=params.max_depth,
            file_path=params.file_path,
        )

        if result.found and result.node_ids:
            notify_ui_focus(
                "call_tree",
                result.node_ids,
                summary=f"Exploring call tree for {result.root_symbol}",
            )

        if params.response_format == ResponseFormat.JSON:
            return result.model_dump_json(indent=2)
        return safe_str(format_call_tree_md(result))
    except Exception as e:
        return handle_error(e, f"building call tree for {params.symbol_name}")


@mcp.tool(
    name="vibegraph_reindex_project",
    annotations=ToolAnnotations(
        title="Reindex Project Files",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def vibegraph_reindex_project(params: ReindexInput) -> str:
    """Reindex a file or directory recursively."""
    try:
        db, root = _get_context_for_path(params.path)
        target_path = Path(params.path).resolve()

        with redirect_stdout(sys.stderr):
            reindex_all(db, str(target_path), verbose=True, project_root=root)
        return safe_str(f"✅ Successfully reindexed: {target_path}")
    except Exception as e:
        return handle_error(e, f"reindexing {params.path}")


def main() -> None:
    """Entry point for the vibegraph-mcp console script."""
    mcp.run()


if __name__ == "__main__":
    main()
