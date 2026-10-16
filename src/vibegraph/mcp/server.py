"""
VibeGraph MCP Server - Code Intelligence Tools.

Exposes FastMCP tools for structural code analysis and graph intelligence.
"""

import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from vibegraph.indexer.main import reindex_all
from vibegraph.mcp.schemas import (
    CallStackInput,
    DependenciesInput,
    ImpactAnalysisInput,
    ReferencesInput,
    ReindexInput,
    ResponseFormat,
    SearchInput,
    StructuralSummaryInput,
    TraceDirection,
)
from vibegraph.services.graph_service import (
    GraphTraverser,
    categorize_file_dependencies,
    compute_impact_analysis,
    get_context_for_path,
    handle_error,
    normalize_path,
    safe_str,
)

# Compatibility aliases for existing tests and callers
_get_context_for_path = get_context_for_path
_normalize_path = normalize_path
_safe_str = safe_str
_handle_error = handle_error

mcp = FastMCP("vibegraph_mcp")


@mcp.tool(
    name="vibegraph_get_structural_summary",
    annotations=ToolAnnotations(
        title="Get File Structure Summary",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
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

        if params.response_format == ResponseFormat.JSON:
            nodes = [
                {
                    "name": row["name"],
                    "kind": row["kind"],
                    "signature": row["signature"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "decorators": json.loads(row["decorators"]) if row["decorators"] else [],
                    "visibility": row["visibility"] or "public",
                }
                for row in rows
            ]
            response = {
                "file_path": norm_path,
                "total": total,
                "count": len(nodes),
                "offset": params.offset,
                "has_more": total > params.offset + len(nodes),
                "next_offset": (
                    params.offset + len(nodes) if total > params.offset + len(nodes) else None
                ),
                "nodes": nodes,
            }
            return json.dumps(response, indent=2)

        end_idx = min(params.offset + len(rows), total)
        header = (
            f"Structure for `{norm_path}` "
            f"(showing {params.offset + 1}-{end_idx} of {total} nodes):\n"
        )
        summary = [header]

        for row in rows:
            icon = "[f]"
            if row["kind"] == "class":
                icon = "[c]"
            elif row["kind"] == "module":
                icon = "[m]"

            vis_mark = ""
            if row["visibility"] == "private":
                vis_mark = " 🔒"
            elif row["visibility"] == "exported":
                vis_mark = " 🌐"

            decorators = ""
            if row["decorators"]:
                decs = json.loads(row["decorators"])
                for d in decs:
                    decorators += f"\n  {d}"

            info = f"- {icon} **{row['name']}**{vis_mark}"
            if row["signature"]:
                info += f" `{row['signature']}`"
            info += f" (L{row['start_line']}-{row['end_line']})"
            if decorators:
                info += decorators
            summary.append(info)

        if end_idx < total:
            remaining = total - end_idx
            next_offset = params.offset + params.limit
            summary.append(
                f"\n... {remaining} more nodes available (use offset={next_offset} to see more)"
            )

        return "\n".join(summary)
    except Exception as e:
        return handle_error(e, f"analyzing {params.file_path}")


@mcp.tool(
    name="vibegraph_get_call_stack",
    annotations=ToolAnnotations(
        title="Trace Call Stack",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def vibegraph_get_call_stack(params: CallStackInput) -> str:
    """Trace function calls up (callers) or down (callees)."""
    try:
        db, root = _get_context_for_path(params.file_path)
        norm_file = _normalize_path(params.file_path, root) if params.file_path else None
        start_nodes = db.find_nodes_by_name(params.node_name, norm_file)

        if not start_nodes:
            return f"Node '{params.node_name}' not found. Is the file indexed?"

        traverser = GraphTraverser(db)

        for start_node in start_nodes:
            traverser.output.append(
                f"### Trace for `{start_node['name']}` ({start_node['kind']}) "
                f"in `{start_node['file_path']}`"
            )

            if params.direction in (TraceDirection.UP, TraceDirection.BOTH):
                traverser.output.append("\n**Callers (Incoming):**")
                traverser.traverse(
                    start_node["id"],
                    1,
                    params.depth,
                    "up",
                    0,
                    path_stack=[start_node["name"]],
                )
                if not any(safe_str("←") in line for line in traverser.output[-5:]):
                    traverser.output.append("  (no callers found)")
                traverser.visited.clear()

            if params.direction in (TraceDirection.DOWN, TraceDirection.BOTH):
                traverser.output.append("\n**Callees (Outgoing):**")
                traverser.traverse(
                    start_node["id"],
                    1,
                    params.depth,
                    "down",
                    0,
                    path_stack=[start_node["name"]],
                )
                if not any(safe_str("→") in line for line in traverser.output[-5:]):
                    traverser.output.append("  (no callees found)")

        return "\n".join(traverser.output)
    except Exception as e:
        return handle_error(e, f"tracing {params.node_name}")


@mcp.tool(
    name="vibegraph_impact_analysis",
    annotations=ToolAnnotations(
        title="Analyze Change Impact",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def vibegraph_impact_analysis(params: ImpactAnalysisInput) -> str:
    """Analyze what other files/functions break if this file is modified."""
    try:
        db, root = _get_context_for_path(params.file_path)
        norm_path = _normalize_path(params.file_path, root)

        total_impact, impacts_by_level = compute_impact_analysis(db, norm_path)
        if total_impact == 0:
            file_nodes = db.get_nodes_in_file(norm_path)
            if not file_nodes:
                return f"No nodes found in {params.file_path}. Is it indexed?"

        output = [f"## Impact Analysis for `{norm_path}`"]
        if total_impact == 0:
            output.append("✅ No external dependencies found. Safe to refactor internally.")
        else:
            output.append(f"**Total Impact**: {total_impact} components affected up to 3 levels.\n")

            if impacts_by_level[1]:
                output.append("### Level 1: Direct Impact")
                output.extend(sorted(set(impacts_by_level[1])))
                output.append("")

            if impacts_by_level[2]:
                output.append("### Level 2: Secondary Impact (Ripple Effect)")
                output.extend(sorted(set(impacts_by_level[2])))
                output.append("")

            if impacts_by_level[3]:
                output.append("### Level 3: Deep Impact")
                output.extend(sorted(set(impacts_by_level[3])))
                output.append("")

        return "\n".join(output)
    except Exception as e:
        return handle_error(e, f"analyzing impact for {params.file_path}")


@mcp.tool(
    name="vibegraph_find_references",
    annotations=ToolAnnotations(
        title="Find Symbol References",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def vibegraph_find_references(params: ReferencesInput) -> str:
    """Find where a specific function or class is called/used."""
    try:
        db, _ = get_context_for_path(params.scope_path)
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
                    output.append(
                        f"- Used by `{ref['name']}` in `{ref['file_path']}` "
                        f"(L{ref['start_line']}) [{ref['relation_type']}]"
                    )
            else:
                output.append(f"\n### Usages of {target_desc}\n- No direct calls found.")

        return "\n".join(output)
    except Exception as e:
        return handle_error(e, f"finding references for {params.symbol_name}")


@mcp.tool(
    name="vibegraph_get_dependencies",
    annotations=ToolAnnotations(
        title="Get File Dependencies",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
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


@mcp.tool(
    name="vibegraph_search_by_signature",
    annotations=ToolAnnotations(
        title="Search by Signature",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def vibegraph_search_by_signature(params: SearchInput) -> str:
    """Search for functions matching a specific signature pattern."""
    try:
        db, _ = get_context_for_path(params.scope_path)
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
        db, _ = get_context_for_path(params.path)
        target_path = Path(params.path).resolve()

        with redirect_stdout(sys.stderr):
            reindex_all(db, str(target_path), verbose=True)
        return safe_str(f"✅ Successfully reindexed: {target_path}")
    except Exception as e:
        return handle_error(e, f"reindexing {params.path}")


def main():
    """Entry point for the vibegraph-mcp console script."""
    mcp.run()


if __name__ == "__main__":
    main()
