"""
VibeTrace MCP Server - Code Intelligence Tools.

Exposes FastMCP tools for structural code analysis, hybrid search, and execution pathfinding.
"""

import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from vibetrace.indexer.embeddings import EmbeddingEngine
from vibetrace.indexer.main import reindex_all
from vibetrace.mcp.formatters import (
    format_code_slice_md,
    format_dependencies_md,
    format_impact_analysis_md,
    format_references_md,
    format_semantic_search_md,
    format_signature_search_md,
    format_structural_summary_md,
    format_trace_path_md,
)
from vibetrace.mcp.schemas import (
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
    TracePathInput,
)
from vibetrace.mcp.schemas import (
    TraceDirection as TraceDirection,
)
from vibetrace.services.graph_service import (
    build_call_stack,
    build_call_tree,
    categorize_file_dependencies,
    compute_impact_analysis,
    find_symbol_references,
    format_call_tree_md,
    get_code_slice,
    get_context_for_path,
    handle_error,
    normalize_path,
    notify_ui_focus,
    safe_str,
    trace_execution_path,
)
from vibetrace.services.hybrid_search import hybrid_search

# Compatibility aliases for existing tests and callers
_get_context_for_path = get_context_for_path
_normalize_path = normalize_path
_safe_str = safe_str
_handle_error = handle_error

mcp = FastMCP("vibetrace_mcp")

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


@mcp.tool(name="vibetrace_get_structural_summary", annotations=_READ_ONLY)
async def vibetrace_get_structural_summary(params: StructuralSummaryInput) -> str:
    """Get a concise structural summary of a file (classes, functions, methods)."""
    try:
        db, root = _get_context_for_path(params.file_path)
        norm_path = _normalize_path(params.file_path, root)
        total, rows = db.get_file_structure(norm_path, params.limit, params.offset)

        if total == 0:
            return (
                f"No structure found for {params.file_path}. "
                "File might not be indexed. Try vibetrace_reindex_project first."
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


@mcp.tool(name="vibetrace_get_call_stack", annotations=_READ_ONLY)
async def vibetrace_get_call_stack(params: CallStackInput) -> str:
    """Trace function calls up (callers) or down (callees)."""
    try:
        db, root = _get_context_for_path(params.file_path)
        norm_file = _normalize_path(params.file_path, root) if params.file_path else None
        found, output, start_nodes = build_call_stack(
            db,
            params.node_name,
            direction=params.direction.value,
            max_depth=params.depth,
            file_path=norm_file,
        )
        if not found:
            return f"Node '{params.node_name}' not found. Is the file indexed?"

        notify_ui_focus(
            "call_stack",
            [n["id"] for n in start_nodes],
            summary=f"Tracing call stack for {params.node_name}",
        )
        if params.response_format == ResponseFormat.JSON:
            res = {
                "found": found,
                "symbol": params.node_name,
                "direction": params.direction.value,
                "depth": params.depth,
                "call_stack": output,
                "nodes": [n["id"] for n in start_nodes],
            }
            return json.dumps(res, indent=2)
        return "\n".join(output)
    except Exception as e:
        return handle_error(e, f"tracing {params.node_name}")


@mcp.tool(name="vibetrace_impact_analysis", annotations=_READ_ONLY)
async def vibetrace_impact_analysis(params: ImpactAnalysisInput) -> str:
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
        return format_impact_analysis_md(norm_path, total_impact, impacts_by_level)
    except Exception as e:
        return handle_error(e, f"analyzing impact for {params.file_path}")


@mcp.tool(name="vibetrace_find_references", annotations=_READ_ONLY)
async def vibetrace_find_references(params: ReferencesInput) -> str:
    """Find where a specific function or class is called/used."""
    try:
        db, root = _get_context_for_path(params.file_path or params.scope_path)
        norm_file = _normalize_path(params.file_path, root) if params.file_path else None
        found, targets, summaries = find_symbol_references(
            db, params.symbol_name, file_path=norm_file, kind=params.kind
        )
        if not found:
            if params.response_format == ResponseFormat.JSON:
                return json.dumps(
                    {
                        "symbol": params.symbol_name.strip().removesuffix("()"),
                        "found": False,
                        "targets": [],
                        "total_references": 0,
                    },
                    indent=2,
                )
            return f"Symbol '{params.symbol_name}' not found in index."

        if params.response_format == ResponseFormat.JSON:
            total_refs = sum(len(s["references"]) for s in summaries)
            return json.dumps(
                {
                    "symbol": params.symbol_name.strip().removesuffix("()"),
                    "found": True,
                    "target_count": len(targets),
                    "total_references": total_refs,
                    "targets": summaries,
                },
                indent=2,
            )
        return format_references_md(params.symbol_name, summaries)
    except Exception as e:
        return handle_error(e, f"finding references for {params.symbol_name}")


@mcp.tool(name="vibetrace_get_dependencies", annotations=_READ_ONLY)
async def vibetrace_get_dependencies(params: DependenciesInput) -> str:
    """List modules and symbols imported by a specific file."""
    try:
        db, root = _get_context_for_path(params.file_path)
        norm_path = _normalize_path(params.file_path, root)
        internal, third_party, stdlib = categorize_file_dependencies(db, norm_path)
        return format_dependencies_md(norm_path, internal, third_party, stdlib)
    except Exception as e:
        return handle_error(e, f"getting dependencies for {params.file_path}")


@mcp.tool(name="vibetrace_search_by_signature", annotations=_READ_ONLY)
async def vibetrace_search_by_signature(params: SearchInput) -> str:
    """Search for functions matching a specific signature pattern."""
    try:
        db, _ = _get_context_for_path(params.scope_path)
        rows = db.search_by_signature_pattern(
            params.pattern, limit=params.limit, offset=params.offset
        )
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(rows, indent=2)
        return format_signature_search_md(params.pattern, rows)
    except Exception as e:
        return handle_error(e, f"searching signature {params.pattern}")


@mcp.tool(name="vibetrace_semantic_search", annotations=_READ_ONLY)
async def vibetrace_semantic_search(params: SemanticSearchInput) -> str:
    """Hybrid RRF search across AST symbols, SQLite FTS5 BM25, and EmbeddingGemma 2 vectors."""
    try:
        db, root = _get_context_for_path(params.scope_path)
        engine = EmbeddingEngine(project_root=root)
        results = hybrid_search(db, params.query, limit=params.limit, embedding_engine=engine)

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


@mcp.tool(name="vibetrace_trace_path", annotations=_READ_ONLY)
async def vibetrace_trace_path(params: TracePathInput) -> str:
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


@mcp.tool(name="vibetrace_get_code_slice", annotations=_READ_ONLY)
async def vibetrace_get_code_slice(params: CodeSliceInput) -> str:
    """Read exact source lines (start_line..end_line) and callee contracts for a symbol."""
    try:
        db, root = _get_context_for_path(params.scope_path or params.file_path)
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


@mcp.tool(name="vibetrace_get_call_tree", annotations=_READ_ONLY)
async def vibetrace_get_call_tree(params: CallTreeInput) -> str:
    """Explore recursive top-down call tree from an entry point down to leaves."""
    try:
        db, _ = _get_context_for_path(params.scope_path or params.file_path)
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
    name="vibetrace_reindex_project",
    annotations=ToolAnnotations(
        title="Reindex Project Files",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def vibetrace_reindex_project(params: ReindexInput) -> str:
    """Reindex a file or directory recursively."""
    try:
        db, root = _get_context_for_path(params.path, auto_sync=False)
        # "." means the resolved project root; relative paths resolve within project root.
        if params.path in ("", "."):
            target_path = root
        else:
            p = Path(params.path)
            target_path = p.resolve() if p.is_absolute() else (root / p).resolve()

        with redirect_stdout(sys.stderr):
            reindex_all(
                db,
                str(target_path),
                verbose=True,
                force=params.force,
                project_root=root,
                use_lsp=params.use_lsp,
            )
        msg = f"✅ Successfully reindexed: {target_path}"
        if params.embed:
            from vibetrace.indexer.embeddings import EmbeddingEngine

            engine = EmbeddingEngine(project_root=root)
            indexed_count = engine.index_missing_embeddings(db)
            msg += f" (generated {indexed_count} vector embeddings)"
        return safe_str(msg)
    except Exception as e:
        return handle_error(e, f"reindexing {params.path}")


def main() -> None:
    """Entry point for the vibetrace-mcp console script."""
    mcp.run()


if __name__ == "__main__":
    main()
