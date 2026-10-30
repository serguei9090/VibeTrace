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
    format_caller_guard_markdown,
    format_code_slice_md,
    format_dependencies_md,
    format_doc_catalogs_md,
    format_doc_search_md,
    format_impact_analysis_md,
    format_references_md,
    format_saved_traces_md,
    format_semantic_search_md,
    format_signature_search_md,
    format_split_audit_md,
    format_structural_summary_md,
    format_trace_path_md,
    format_vibe_bundle_markdown,
)
from vibetrace.mcp.schemas import (
    ApproveSecurityExceptionInput,
    ApproveSplitInput,
    AuditSecurityInput,
    AuditSplitsInput,
    CallerGuardInput,
    CallStackInput,
    CallTreeInput,
    CodeSliceInput,
    DeleteSavedTraceInput,
    DependenciesInput,
    DocCatalogsInput,
    DocSearchInput,
    ImpactAnalysisInput,
    ListSavedTracesInput,
    ReferencesInput,
    ReindexInput,
    RemediateVulnerabilityInput,
    ResponseFormat,
    ReviewChangesInput,
    SaveTraceInput,
    SearchInput,
    SemanticSearchInput,
    StructuralSummaryInput,
    TracePathInput,
    VibeBundleInput,
)
from vibetrace.mcp.schemas import (
    TraceDirection as TraceDirection,
)
from vibetrace.mcp.tools_project import register_project_tools
from vibetrace.services.graph_service import (
    analyze_file_dependencies,
    build_call_stack,
    build_call_tree,
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
from vibetrace.services.saved_traces_service import (
    SavedTraceCreate,
    create_saved_trace,
    delete_saved_trace_by_name,
    get_saved_trace_by_name,
    list_saved_traces,
)

# Compatibility aliases for existing tests and callers
_get_context_for_path = get_context_for_path
_normalize_path = normalize_path
_safe_str = safe_str
_handle_error = handle_error

mcp = FastMCP("vibetrace_mcp")

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
_MUTATION = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)


@mcp.tool(name="vibetrace_get_structural_summary", annotations=_READ_ONLY)
async def vibetrace_get_structural_summary(params: StructuralSummaryInput) -> str:
    """Get a concise structural summary of a file (classes, functions, methods)."""
    try:
        db, root = _get_context_for_path(params.scope_path or params.file_path)
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
                    "name": r["name"],
                    "qualified_name": r.get("qualified_name") or r["name"],
                    "kind": r["kind"],
                    "signature": r["signature"],
                    "start_line": r["start_line"],
                    "end_line": r["end_line"],
                    "decorators": r["decorators"] or [],
                    "visibility": r["visibility"] or "public",
                }
                for r in rows
            ]
            has_more = total > params.offset + len(nodes)
            return json.dumps(
                {
                    "file_path": norm_path,
                    "total": total,
                    "count": len(nodes),
                    "offset": params.offset,
                    "has_more": has_more,
                    "next_offset": params.offset + len(nodes) if has_more else None,
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
        db, root = _get_context_for_path(params.scope_path or params.file_path)
        norm_file = _normalize_path(params.file_path, root) if params.file_path else None
        rel_types = None if params.all_relations else ("calls", "routes")
        found, output, start_nodes = build_call_stack(
            db,
            params.node_name,
            direction=params.direction.value,
            max_depth=params.depth,
            file_path=norm_file,
            relation_types=rel_types,
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
        db, root = _get_context_for_path(params.scope_path or params.file_path)
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
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {
                    "file_path": norm_path,
                    "total_impact": total_impact,
                    "levels": impacts_by_level,
                },
                indent=2,
            )
        return format_impact_analysis_md(norm_path, total_impact, impacts_by_level)
    except Exception as e:
        return handle_error(e, f"analyzing impact for {params.file_path}")


@mcp.tool(name="vibetrace_find_references", annotations=_READ_ONLY)
async def vibetrace_find_references(params: ReferencesInput) -> str:
    """Find where a specific function or class is called/used."""
    try:
        db, root = _get_context_for_path(params.scope_path or params.file_path)
        norm_file = _normalize_path(params.file_path, root) if params.file_path else None
        found, targets, summaries = find_symbol_references(
            db, params.symbol_name, file_path=norm_file, kind=params.kind
        )
        if params.response_format == ResponseFormat.JSON:
            clean = params.symbol_name.strip().removesuffix("()")
            total_refs = sum(len(s["references"]) for s in summaries) if found else 0
            return json.dumps(
                {
                    "symbol": clean,
                    "found": found,
                    "target_count": len(targets),
                    "total_references": total_refs,
                    "targets": summaries,
                },
                indent=2,
            )
        if not found:
            return f"Symbol '{params.symbol_name}' not found in index."
        return format_references_md(params.symbol_name, summaries)
    except Exception as e:
        return handle_error(e, f"finding references for {params.symbol_name}")


@mcp.tool(name="vibetrace_get_dependencies", annotations=_READ_ONLY)
async def vibetrace_get_dependencies(params: DependenciesInput) -> str:
    """List modules and symbols imported by a specific file."""
    try:
        db, root = _get_context_for_path(params.scope_path or params.file_path)
        norm_path = _normalize_path(params.file_path, root)
        analysis = analyze_file_dependencies(db, norm_path)
        if params.response_format == ResponseFormat.JSON:
            payload = {
                "file_path": norm_path,
                "counts": {
                    "internal": len(analysis["internal"]),
                    "third_party": len(analysis["third_party"]),
                    "stdlib": len(analysis["stdlib"]),
                    "dependents": len(analysis["dependents"]),
                    "circular": len(analysis["circular"]),
                    "total_imports": (
                        len(analysis["internal"])
                        + len(analysis["third_party"])
                        + len(analysis["stdlib"])
                    ),
                },
                **analysis,
            }
            return json.dumps(payload, indent=2)
        return format_dependencies_md(
            norm_path,
            analysis["internal"],
            analysis["third_party"],
            analysis["stdlib"],
            dependents=analysis["dependents"],
            circular=analysis["circular"],
            reverse_only=params.reverse,
        )
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
        results = hybrid_search(
            db,
            params.query,
            limit=params.limit,
            offset=params.offset,
            embedding_engine=engine,
        )

        doc_chunks = None
        if params.include_docs:
            try:
                from vibetrace.docs.service import DocSearchService

                doc_chunks = DocSearchService.search(
                    query=params.query,
                    project_root=root,
                    scope="local",
                    limit=3,
                )
            except Exception:
                doc_chunks = None

        notify_ui_focus(
            "semantic_search",
            [r.node_id for r in results[:5]],
            summary=f"Searching '{params.query}'",
        )

        if params.response_format == ResponseFormat.JSON:
            if params.include_docs:
                return json.dumps(
                    {
                        "code_symbols": [r.model_dump() for r in results],
                        "doc_chunks": [d.model_dump() for d in doc_chunks] if doc_chunks else [],
                    },
                    indent=2,
                )
            return json.dumps([r.model_dump() for r in results], indent=2)
        return format_semantic_search_md(params.query, results, doc_chunks=doc_chunks)
    except Exception as e:
        return handle_error(e, f"hybrid search for {params.query}")


@mcp.tool(name="vibetrace_trace_path", annotations=_READ_ONLY)
async def vibetrace_trace_path(params: TracePathInput) -> str:
    """Find shortest execution tracepath(s) between two symbols or execute a saved trace."""
    try:
        db, _ = _get_context_for_path(params.scope_path)
        from_sym = params.from_symbol
        to_sym = params.to_symbol
        depth = params.max_depth

        if params.saved_name:
            saved = get_saved_trace_by_name(db, params.saved_name)
            if not saved:
                return f"Error: Saved trace '{params.saved_name}' not found."
            from_sym = saved.from_symbol
            to_sym = saved.to_symbol
            depth = params.max_depth if params.max_depth != 6 else saved.max_depth

        if not from_sym or not to_sym:
            return "Error: Specify both from_symbol and to_symbol, or provide saved_name."

        result = trace_execution_path(
            db,
            from_sym,
            to_sym,
            max_depth=depth,
            engine=params.engine,
        )

        if result.found:
            notify_ui_focus(
                "trace_path",
                result.node_ids,
                summary=f"Tracepath {from_sym} -> {to_sym}",
                from_symbol=from_sym,
                to_symbol=to_sym,
            )

        if params.response_format == ResponseFormat.JSON:
            return result.model_dump_json(indent=2)
        return format_trace_path_md(result)
    except Exception as e:
        return handle_error(e, f"tracing path {params.from_symbol} -> {params.to_symbol}")


@mcp.tool(name="vibetrace_list_saved_traces", annotations=_READ_ONLY)
async def vibetrace_list_saved_traces(params: ListSavedTracesInput) -> str:
    """List all saved architectural trace queries in this project."""
    try:
        db, _ = _get_context_for_path(params.scope_path)
        traces = list_saved_traces(db)
        if params.response_format == ResponseFormat.JSON:
            return json.dumps([t.model_dump() for t in traces], indent=2)
        return format_saved_traces_md(traces)
    except Exception as e:
        return handle_error(e, "listing saved traces")


@mcp.tool(name="vibetrace_save_trace", annotations=_MUTATION)
async def vibetrace_save_trace(params: SaveTraceInput) -> str:
    """Bookmark or update a persistent architectural trace query."""
    try:
        db, _ = _get_context_for_path(params.scope_path)
        payload = SavedTraceCreate(
            name=params.name,
            from_symbol=params.from_symbol,
            to_symbol=params.to_symbol,
            description=params.description,
            max_depth=params.max_depth,
        )
        saved = create_saved_trace(db, payload)
        info = f"{saved.from_symbol} -> {saved.to_symbol} (depth {saved.max_depth})"
        return f"✅ Saved architectural trace '{saved.name}' ({info})"
    except Exception as e:
        return handle_error(e, f"saving trace {params.name}")


@mcp.tool(name="vibetrace_delete_saved_trace", annotations=_DESTRUCTIVE)
async def vibetrace_delete_saved_trace(params: DeleteSavedTraceInput) -> str:
    """Delete a saved architectural trace query by name."""
    try:
        db, _ = _get_context_for_path(params.scope_path)
        deleted = delete_saved_trace_by_name(db, params.name)
        if not deleted:
            return f"Saved trace '{params.name}' not found."
        return f"✅ Successfully deleted saved trace '{params.name}'."
    except Exception as e:
        return handle_error(e, f"deleting saved trace {params.name}")


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


@mcp.tool(name="vibetrace_reindex_project", annotations=_MUTATION)
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


@mcp.tool()
async def vibetrace_search_docs(params: DocSearchInput) -> str:
    """
    Search documentation files (.md, .txt, .pdf) across local and global catalogs
    using hybrid RRF (BM25 + vector similarity).
    """
    try:
        from vibetrace.docs.service import DocSearchService

        _, root = get_context_for_path(params.scope_path)
        items = DocSearchService.search(
            query=params.query,
            catalog=params.catalog,
            scope=params.scope,
            project_root=root,
            limit=params.limit,
        )
        if params.response_format == ResponseFormat.JSON:
            return json.dumps([it.model_dump() for it in items], indent=2)
        return format_doc_search_md(params.query, items)
    except Exception as e:
        return handle_error(e, f"searching documentation for '{params.query}'")


@mcp.tool()
async def vibetrace_list_doc_catalogs(params: DocCatalogsInput) -> str:
    """List available local and global documentation catalogs with folder paths and chunk counts."""
    try:
        from vibetrace.docs.service import DocCatalogManager

        _, root = get_context_for_path(params.scope_path)
        catalogs = DocCatalogManager.list_catalogs(project_root=root)
        if params.response_format == ResponseFormat.JSON:
            return json.dumps([c.model_dump() for c in catalogs], indent=2)
        return format_doc_catalogs_md(catalogs)
    except Exception as e:
        return handle_error(e, "listing documentation catalogs")


@mcp.tool()
async def vibetrace_check_caller_guard(params: CallerGuardInput) -> str:
    """
    Pre-flight blast-radius guard: inspect all incoming callers, signatures, and call
    sites of a symbol before modifying its implementation or contract. Supports staged changes.
    """
    try:
        from vibetrace.services.caller_guard import CallerGuardService

        db, root = get_context_for_path(params.scope_path)
        if params.staged:
            reports = CallerGuardService.get_staged_caller_guard(db, root)
            if params.response_format == ResponseFormat.JSON:
                return json.dumps([r.model_dump() for r in reports], indent=2)
            if not reports:
                return (
                    "## 🛡️ Pre-Commit Caller Guard Check\n\n"
                    "No modified symbol definitions detected in staged changes."
                )
            return "\n\n".join(format_caller_guard_markdown(r) for r in reports)

        report = CallerGuardService.get_caller_guard(
            db, root, symbol_name=params.symbol_name, file_path=params.file_path
        )
        if params.response_format == ResponseFormat.JSON:
            return report.model_dump_json(indent=2)
        return format_caller_guard_markdown(report)
    except Exception as e:
        return handle_error(e, f"inspecting callers for '{params.symbol_name}'")


@mcp.tool()
async def vibetrace_get_vibe_bundle(params: VibeBundleInput) -> str:
    """
    One-shot task context bundle: pack relevant docs, target code slice, callee
    contracts, caller guard, and sibling test patterns into a token-budgeted payload.
    """
    try:
        from vibetrace.services.vibe_bundle import VibeBundleService

        db, root = get_context_for_path(params.scope_path)
        bundle = VibeBundleService.assemble_bundle(
            db=db,
            root=root,
            intent=params.intent,
            target_symbol=params.target_symbol,
            token_budget=params.token_budget,
        )
        if params.response_format == ResponseFormat.JSON:
            return bundle.model_dump_json(indent=2)
        return format_vibe_bundle_markdown(bundle)
    except Exception as e:
        return handle_error(e, f"assembling vibe bundle for '{params.intent}'")


@mcp.tool(name="vibetrace_audit_splits", annotations=_READ_ONLY)
async def vibetrace_audit_splits(params: AuditSplitsInput) -> str:
    """Audit cross-module execution bifurcations against .vibetrace/splits.toml baseline.

    Detects functions whose outgoing calls route across multiple distinct files.
    """
    try:
        from vibetrace.services.split_audit_service import audit_splits

        db, root = get_context_for_path(params.scope_path)
        report = audit_splits(db, Path(root), symbol=params.symbol)

        if params.response_format == ResponseFormat.JSON:
            return report.model_dump_json(indent=2)
        return format_split_audit_md(report)
    except Exception as e:
        return handle_error(e, "auditing architectural splits")


@mcp.tool(name="vibetrace_approve_split", annotations=_MUTATION)
async def vibetrace_approve_split(params: ApproveSplitInput) -> str:
    """Approve or record an architectural execution split in .vibetrace/splits.toml.

    Uses SHA-256 code hashing to ensure changes trigger review re-validation.
    """
    try:
        from vibetrace.services.split_audit_service import record_split_approval

        _, root = get_context_for_path(params.scope_path)
        baseline_path = record_split_approval(
            project_root=Path(root),
            symbol=params.symbol,
            qualified_name=params.qualified_name or params.symbol,
            file_path=params.file_path,
            code_hash=params.code_hash,
            branches=params.branches,
            status=params.status,
            rationale=params.rationale,
            reviewed_by=params.reviewed_by,
        )
        msg = {
            "status": "ok",
            "symbol": params.symbol,
            "code_hash": params.code_hash,
            "review_status": params.status,
            "baseline_file": str(baseline_path),
        }
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(msg, indent=2)
        hash_short = params.code_hash[:8]
        return (
            f"✅ Successfully approved split for `{params.symbol}` with hash `{hash_short}`.\n"
            f"Recorded in `{baseline_path}`."
        )
    except Exception as e:
        return handle_error(e, f"recording split approval for '{params.symbol}'")


@mcp.tool(name="vibetrace_audit_security", annotations=_READ_ONLY)
async def vibetrace_audit_security(params: AuditSecurityInput) -> str:
    """Audit codebase and lockfile dependencies for security vulnerabilities.

    Detects static AST flaws (CWE-78, CWE-95, CWE-798, CWE-327, CWE-502) and package CVEs.
    """
    try:
        from vibetrace.security.formatters import format_security_audit_md
        from vibetrace.security.service import SecurityService

        db, root = get_context_for_path(params.scope_path)
        report = SecurityService.audit_project(
            db=db,
            project_root=Path(root),
            file_filter=params.file_path,
            check_dependencies=not params.skip_deps,
            force_update_cves=params.update,
            analyze_taint=not params.no_taint,
        )

        if params.response_format == ResponseFormat.JSON:
            return report.model_dump_json(indent=2)
        return format_security_audit_md(report)
    except Exception as e:
        return handle_error(e, "auditing security vulnerabilities")


@mcp.tool(name="vibetrace_approve_security_exception", annotations=_MUTATION)
async def vibetrace_approve_security_exception(params: ApproveSecurityExceptionInput) -> str:
    """Record an approved security exception into .vibetrace/security.toml baseline.

    Uses normalized SHA-256 code hashing to ensure modifications trip verification gates.
    """
    try:
        from vibetrace.security.service import SecurityService

        _, root = get_context_for_path(params.scope_path)
        SecurityService.record_exception(
            project_root=Path(root),
            finding_id=params.finding_id,
            rule_id=params.rule_id,
            file_path=params.file_path,
            code_hash=params.code_hash,
            rationale=params.rationale,
            severity=params.severity,
            status=params.status,
            reviewed_by=params.reviewed_by,
            tag=params.tag,
        )
        msg = {
            "status": "ok",
            "finding_id": params.finding_id,
            "rule_id": params.rule_id,
            "file_path": params.file_path,
            "review_status": params.status,
            "baseline_file": ".vibetrace/security.toml",
        }
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(msg, indent=2)
        return (
            f"✅ Successfully approved security exception `{params.finding_id}`.\n"
            f"File: `{params.file_path}` | Recorded in `.vibetrace/security.toml`."
        )
    except Exception as e:
        return handle_error(e, f"approving security exception '{params.finding_id}'")


@mcp.tool(name="vibetrace_remediate_vulnerability", annotations=_READ_ONLY)
async def vibetrace_remediate_vulnerability(params: RemediateVulnerabilityInput) -> str:
    """Generate structured AI remediation instructions and compliant code patterns for findings."""
    try:
        from vibetrace.security.service import SecurityService

        db, root = get_context_for_path(params.scope_path)
        bundle = SecurityService.build_remediation_bundle(
            db=db,
            project_root=Path(root),
            finding_id=params.finding_id,
            file_filter=params.file_path,
        )
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(bundle, indent=2)
        items = bundle.get("remediations", [])
        if not isinstance(items, list) or not items:
            return "✅ No unapproved findings matched for remediation."
        return "\n\n---\n\n".join(str(it["ai_fix_prompt"]) for it in items if isinstance(it, dict))
    except Exception as e:
        return handle_error(e, "remediating security vulnerability")


@mcp.tool(name="vibetrace_review_changes", annotations=_READ_ONLY)
async def vibetrace_review_changes(params: ReviewChangesInput) -> str:
    """Run unified 1-call AI code review combining Security/Taint, Split Audit, and Caller Guard."""
    try:
        from vibetrace.services.review_service import (
            format_unified_review_md,
            run_unified_review,
        )

        db, root = get_context_for_path(params.scope_path)
        report = run_unified_review(
            db=db,
            project_root=Path(root),
            scope=params.scope,
            check_dependencies=params.check_dependencies,
        )
        if params.response_format == ResponseFormat.JSON:
            return report.model_dump_json(indent=2)
        return format_unified_review_md(report)
    except Exception as e:
        return handle_error(e, "running unified AI code review")


register_project_tools(mcp)


def main() -> None:
    """Entry point for the vibetrace-mcp console script."""
    if sys.platform == "win32":
        try:
            reconfig_out = getattr(sys.stdout, "reconfigure", None)
            if callable(reconfig_out):
                reconfig_out(encoding="utf-8")
            reconfig_err = getattr(sys.stderr, "reconfigure", None)
            if callable(reconfig_err):
                reconfig_err(encoding="utf-8")
        except Exception:
            pass
    mcp.run()


if __name__ == "__main__":
    main()
