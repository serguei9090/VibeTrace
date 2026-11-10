import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vibetrace.constants import DEFAULT_MAX_DEPTH, MAX_TRAVERSAL_DEPTH
from vibetrace.indexer.watcher import start_observer, stop_observer
from vibetrace.server_api_docs import docs_router
from vibetrace.server_api_engineering import get_engineering_router
from vibetrace.server_api_project import get_project_router
from vibetrace.server_api_saved_traces import get_saved_traces_router
from vibetrace.services.graph_service import (
    analyze_file_dependencies,
    build_call_stack,
    build_call_tree,
    compute_impact_analysis,
    find_symbol_references,
    get_code_slice,
    get_context_for_path,
    get_downstream_reachable_symbols,
    normalize_path,
    read_file_lines,
    trace_execution_path,
)
from vibetrace.services.split_audit_service import (
    audit_splits,
    record_split_approval,
)


class ReindexPayload(BaseModel):
    """Payload to trigger project or file reindexing via REST API."""

    file_path: str | None = None
    force: bool = False
    embed: bool = False
    docs: bool = True


class AgentFocusPayload(BaseModel):
    """Payload broadcast to Map Room UI when an AI agent queries VibeTrace."""

    type: str = "agent_focus"
    event_type: str
    node_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    from_symbol: str | None = None
    to_symbol: str | None = None


class SplitApprovePayload(BaseModel):
    """Payload to approve an architectural split bifurcation."""

    symbol: str
    qualified_name: str | None = None
    file_path: str = ""
    code_hash: str = ""
    branches: list[str] = Field(default_factory=list)
    status: str = "approved"
    rationale: str = "Architectural review verified"
    reviewed_by: str = "developer"


class SecurityApprovePayload(BaseModel):
    """Payload to record a security exception in .vibetrace/security.toml."""

    finding_id: str
    rule_id: str
    file_path: str
    code_hash: str
    rationale: str = "Security review verified"
    severity: str = "HIGH"
    status: str = "APPROVED"
    reviewed_by: str = "developer"
    category: str = "ast_pattern"
    symbol_name: str | None = None
    tag: str | None = None


class SecurityRemediatePayload(BaseModel):
    """Payload to generate structured remediation guidance for a security finding."""

    finding_id: str | None = None
    file_path: str | None = None


class QualityApprovePayload(BaseModel):
    """Payload to record a quality exception in .vibetrace/quality.toml."""

    finding_id: str
    rule_id: str
    file_path: str
    code_hash: str
    rationale: str = "Code quality review verified"
    severity: str = "HIGH"
    status: str = "APPROVED"
    reviewed_by: str = "developer"
    category: str = "cognitive_complexity"
    symbol_name: str | None = None
    tag: str | None = "intentional"


# Global Managers
class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)

    async def broadcast(self, message: str) -> None:
        conns = list(self.active_connections)
        if not conns:
            return

        async def _safe_send(ws: WebSocket) -> None:
            try:
                await ws.send_text(message)
            except Exception:
                self.disconnect(ws)

        await asyncio.gather(*(_safe_send(c) for c in conns), return_exceptions=True)


manager = ConnectionManager()
root_override = os.environ.get("VIBETRACE_PROJECT_ROOT")
db, project_root = get_context_for_path(".", root_override=root_override)


# Lifespan for Watcher
@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()

    def on_change() -> None:
        asyncio.run_coroutine_threadsafe(manager.broadcast("refresh"), loop)

    observer = None
    try:
        if Path(project_root).is_dir():
            observer = start_observer(str(project_root), db, on_change)
    except Exception as exc:
        print(f"Warning: Failed to start filesystem observer on {project_root}: {exc}")

    yield

    if observer is not None:
        stop_observer(observer)
    try:
        db.close()
    except Exception:
        pass


app = FastAPI(title="VibeTrace API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # Loopback only: a wildcard would let any website read project source via the API.
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(docs_router)
app.include_router(get_saved_traces_router(lambda: db))
app.include_router(get_project_router())
app.include_router(get_engineering_router(lambda: db))


@app.get("/api/hooks")
def get_hooks_endpoint():
    """Inspect current git hook installation status."""
    from vibetrace.indexer.hooks import get_hooks_status

    return get_hooks_status(Path(project_root))


@app.post("/api/hooks")
def post_hooks_endpoint(
    action: Literal["install", "uninstall"] = Query(default="install"),
    provider: Literal["auto", "lefthook", "git"] = Query(default="auto"),
):
    """Install or uninstall automated git hooks for the project."""
    from vibetrace.indexer.hooks import install_hooks, uninstall_hooks

    if action == "install":
        ok, msg = install_hooks(Path(project_root), provider=provider)
    else:
        ok, msg = uninstall_hooks(Path(project_root))

    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "action": action, "message": msg}


@app.get("/graph")
@app.get("/api/graph")
def get_graph(file_path: str | None = None):
    """
    Return nodes and edges.
    If file_path is provided, filter nodes by that file (plus connected edges).
    """
    if file_path:
        norm_file = normalize_path(file_path, Path(project_root))
        return db.get_graph_data(norm_file)
    return db.get_graph_data(None)


@app.get("/api/node/{node_id}/neighbors")
def get_node_neighbors(
    node_id: str,
    direction: Literal["up", "down", "both"] = Query(default="both"),
    all_relations: bool = Query(default=False),
):
    """
    Return immediate 1-hop caller and/or callee neighbors for a node.
    Used for instant, lightweight progressive drill-down in the Map Room.
    """
    node = db.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    rel_types = None if all_relations else ("calls", "routes", "inherits", "implements", "imports")
    callers = []
    callees = []

    if direction in ("up", "both"):
        callers = db.get_neighbors(node_id, "up", relation_types=rel_types)
    if direction in ("down", "both"):
        callees = db.get_neighbors(node_id, "down", relation_types=rel_types)

    # Collect unique neighbor nodes and edges
    neighbor_nodes = []
    edges = []
    seen_nodes = set()

    for c in callers:
        nid = c["neighbor_id"]
        if nid not in seen_nodes:
            seen_nodes.add(nid)
            neighbor_nodes.append(
                {
                    "id": nid,
                    "name": c["name"],
                    "qualified_name": c["qualified_name"],
                    "kind": c["kind"],
                    "file_path": c["file_path"],
                    "start_line": c["start_line"],
                    "end_line": c["end_line"],
                    "signature": c["signature"],
                }
            )
        edges.append(
            {
                "from_node_id": nid,
                "to_node_id": node_id,
                "relation_type": c["relation_type"],
                "call_line": c["call_line"],
            }
        )

    for c in callees:
        nid = c["neighbor_id"]
        if nid not in seen_nodes:
            seen_nodes.add(nid)
            neighbor_nodes.append(
                {
                    "id": nid,
                    "name": c["name"],
                    "qualified_name": c["qualified_name"],
                    "kind": c["kind"],
                    "file_path": c["file_path"],
                    "start_line": c["start_line"],
                    "end_line": c["end_line"],
                    "signature": c["signature"],
                }
            )
        edges.append(
            {
                "from_node_id": node_id,
                "to_node_id": nid,
                "relation_type": c["relation_type"],
                "call_line": c["call_line"],
            }
        )

    return {
        "center_node": node,
        "nodes": neighbor_nodes,
        "edges": edges,
        "caller_count": len(callers),
        "callee_count": len(callees),
    }


@app.get("/trace")
@app.get("/api/trace")
def get_trace(
    from_symbol: str = Query(..., min_length=1),
    to_symbol: str = Query(..., min_length=1),
    max_depth: int = Query(default=DEFAULT_MAX_DEPTH, ge=1, le=MAX_TRAVERSAL_DEPTH),
    engine: str = Query(default="bfs", pattern="^(bfs|cte)$"),
    detect_splits: bool = Query(default=True),
):
    """Compute shortest execution tracepath(s) between two symbols via BFS or recursive CTE."""
    res = trace_execution_path(
        db, from_symbol, to_symbol, max_depth=max_depth, engine=engine, detect_splits=detect_splits
    )
    return res.model_dump()


@app.get("/api/reachable")
def get_reachable_symbols(
    from_symbol: str = Query(..., min_length=1),
    max_depth: int = Query(default=DEFAULT_MAX_DEPTH, ge=1, le=MAX_TRAVERSAL_DEPTH),
):
    """Get all downstream reachable symbols from a given start symbol."""
    symbols = get_downstream_reachable_symbols(db, from_symbol, max_depth=max_depth)
    return {
        "from_symbol": from_symbol,
        "count": len(symbols),
        "reachable_symbols": symbols,
    }


@app.get("/api/splits/audit")
def get_splits_audit(
    symbol: str | None = Query(default=None),
):
    """Audits detected architectural bifurcations against .vibetrace/splits.toml baseline."""
    report = audit_splits(db, Path(project_root), symbol=symbol)
    return report.model_dump()


@app.post("/api/splits/approve")
def approve_split_endpoint(payload: SplitApprovePayload):
    """Records architectural approval of a code split with deterministic SHA-256 hash."""
    path = record_split_approval(
        project_root=Path(project_root),
        symbol=payload.symbol,
        qualified_name=payload.qualified_name or payload.symbol,
        file_path=payload.file_path,
        code_hash=payload.code_hash,
        branches=payload.branches,
        status=payload.status,
        rationale=payload.rationale,
        reviewed_by=payload.reviewed_by,
    )
    return {"status": "ok", "baseline_path": str(path)}


@app.get("/api/security/audit")
def get_security_audit_endpoint(
    file_path: str | None = Query(default=None),
    skip_deps: bool = Query(default=False),
    update: bool = Query(default=False),
    no_taint: bool = Query(default=False),
):
    """Audits codebase and dependencies for security flaws against .vibetrace/security.toml."""
    from vibetrace.security.service import SecurityService

    report = SecurityService.audit_project(
        db=db,
        project_root=Path(project_root),
        file_filter=file_path,
        check_dependencies=not skip_deps,
        force_update_cves=update,
        analyze_taint=not no_taint,
    )
    return report.model_dump()


@app.post("/api/security/approve")
def approve_security_exception_endpoint(payload: SecurityApprovePayload):
    """Records an accepted security exception into .vibetrace/security.toml baseline."""
    from vibetrace.security.service import SecurityService

    SecurityService.record_exception(
        project_root=Path(project_root),
        finding_id=payload.finding_id,
        rule_id=payload.rule_id,
        file_path=payload.file_path,
        code_hash=payload.code_hash,
        rationale=payload.rationale,
        severity=payload.severity,
        status=payload.status,
        reviewed_by=payload.reviewed_by,
        category=payload.category,
        symbol_name=payload.symbol_name,
        tag=payload.tag,
    )
    return {"status": "ok", "baseline_file": ".vibetrace/security.toml"}


@app.post("/api/security/remediate")
def remediate_security_finding_endpoint(payload: SecurityRemediatePayload):
    """Generates AI remediation guidance and compliant replacement snippets."""
    from vibetrace.security.service import SecurityService

    return SecurityService.build_remediation_bundle(
        db=db,
        project_root=Path(project_root),
        finding_id=payload.finding_id,
        file_filter=payload.file_path,
    )


@app.get("/api/quality/audit")
def get_quality_audit_endpoint(
    file_path: str | None = Query(default=None),
    threshold: int = Query(default=15),
    include_tests: bool = Query(default=False),
):
    """Audits codebase for complexity and smells against .vibetrace/quality.toml."""
    from vibetrace.quality.service import QualityService

    report = QualityService.audit_project(
        db=db,
        project_root=Path(project_root),
        file_filter=file_path,
        complexity_threshold=threshold,
        include_tests=include_tests,
    )
    return report.model_dump()


@app.post("/api/quality/approve")
def approve_quality_exception_endpoint(payload: QualityApprovePayload):
    """Records an accepted quality exception into .vibetrace/quality.toml baseline."""
    from vibetrace.quality.service import QualityService

    QualityService.record_exception(
        project_root=Path(project_root),
        finding_id=payload.finding_id,
        rule_id=payload.rule_id,
        file_path=payload.file_path,
        code_hash=payload.code_hash,
        rationale=payload.rationale,
        severity=payload.severity,
        status=payload.status,
        reviewed_by=payload.reviewed_by,
        category=payload.category,
        symbol_name=payload.symbol_name,
        tag=payload.tag,
    )
    return {"status": "ok", "baseline_file": ".vibetrace/quality.toml"}


@app.get("/call-tree")
@app.get("/api/call-tree")
def get_call_tree(
    symbol: str = Query(..., min_length=1),
    depth: int = Query(default=3, ge=1, le=10),
    file_path: str | None = Query(default=None),
):
    """Explore top-down execution call-tree from an entry point."""
    norm_file = normalize_path(file_path, Path(project_root)) if file_path else None
    res = build_call_tree(db, symbol, max_depth=depth, file_path=norm_file)
    return res.model_dump()


@app.get("/call-stack")
@app.get("/api/call-stack")
def get_call_stack_endpoint(
    symbol: str = Query(..., min_length=1),
    direction: Literal["up", "down", "both"] = Query(default="both"),
    depth: int = Query(default=3, ge=1, le=10),
    file_path: str | None = Query(default=None),
    all_relations: bool = Query(default=False),
):
    """Explore bidirectional execution call stack (callers up, callees down, or both)."""
    norm_file = normalize_path(file_path, Path(project_root)) if file_path else None
    rel_types = None if all_relations else ("calls", "routes")
    found, output, start_nodes = build_call_stack(
        db,
        symbol,
        direction=direction,
        max_depth=depth,
        file_path=norm_file,
        relation_types=rel_types,
    )
    if not found:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found in index")
    return {
        "found": True,
        "symbol": symbol,
        "direction": direction,
        "depth": depth,
        "call_stack": output,
        "nodes": [n["id"] for n in start_nodes],
    }


@app.get("/source")
@app.get("/api/source")
def get_source(
    file_path: str = Query(..., min_length=1),
    start_line: int | None = Query(default=None, ge=1),
    end_line: int | None = Query(default=None, ge=1),
):
    """Safely return a 1-indexed source code slice from an indexed project file."""
    # Serve only indexed source files so secrets (.env, keys) are never exposed.
    norm_path = normalize_path(file_path, Path(project_root))
    if db.get_file_hash(norm_path) is None:
        raise HTTPException(status_code=404, detail=f"File not indexed: '{file_path}'")
    try:
        snippet, s_line, e_line = read_file_lines(
            Path(project_root), norm_path, start_line, end_line
        )
        return {
            "file_path": norm_path,
            "start_line": s_line,
            "end_line": e_line,
            "source_code": snippet,
        }
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@app.get("/api/search")
def search_nodes_endpoint(
    q: str = Query(..., min_length=1),
    kind: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    hybrid: bool = Query(default=False, description="Use 4-stream hybrid RRF search"),
    with_docs: bool = Query(default=False, description="Include matching documentation chunks"),
):
    """Search code symbols and endpoints by name, qualified name, or lexical query."""
    doc_payload: list[dict[str, Any]] = []
    active_root = (
        Path(os.environ["VIBETRACE_PROJECT_ROOT"]).resolve()
        if os.environ.get("VIBETRACE_PROJECT_ROOT")
        else Path(project_root)
    )
    if with_docs:
        try:
            from vibetrace.docs.service import DocSearchService

            doc_hits = DocSearchService.search(
                query=q,
                project_root=active_root,
                scope="local",
                limit=5,
            )
            doc_payload = [d.model_dump() for d in doc_hits]
        except Exception:
            doc_payload = []

    if hybrid:
        from vibetrace.indexer.embeddings import EmbeddingEngine
        from vibetrace.services.hybrid_search import hybrid_search

        engine = EmbeddingEngine(project_root=Path(project_root))
        results = hybrid_search(
            db,
            q,
            limit=limit,
            offset=offset,
            embedding_engine=engine,
        )
        if kind:
            results = [r for r in results if r.kind == kind]
        code_data = [r.model_dump() for r in results]
        if with_docs:
            return {"code": code_data, "docs": doc_payload}
        return code_data

    fetch_limit = (limit + offset) if not kind else max((limit + offset) * 5, 50)
    ast_results = db.search_ast_exact_or_prefix(q, limit=fetch_limit)
    if kind:
        ast_results = [r for r in ast_results if r.get("kind") == kind]

    fts_results = db.search_lexical(q, limit=fetch_limit)
    if kind:
        fts_results = [r for r in fts_results if r.get("kind") == kind]

    seen_ids: set[str] = set()
    combined: list[dict[str, Any]] = []
    for r in ast_results + fts_results:
        if r["id"] not in seen_ids:
            seen_ids.add(r["id"])
            combined.append(r)

    code_slice = combined[offset : offset + limit]
    if with_docs:
        return {"code": code_slice, "docs": doc_payload}
    return code_slice


@app.get("/api/search/signature")
def search_signature_endpoint(
    pattern: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Search function signatures matching a SQL LIKE pattern or substring."""
    return db.search_by_signature_pattern(pattern, limit=limit, offset=offset)


@app.post("/api/agent-focus")
async def post_agent_focus(payload: AgentFocusPayload):
    """Broadcast live AI agent activity to connected Map Room WebSocket clients."""
    await manager.broadcast(payload.model_dump_json())
    return {"status": "broadcast", "clients": len(manager.active_connections)}


@app.get("/api/impact")
def get_impact_endpoint(file_path: str = Query(..., min_length=1)):
    """Compute 3-level blast radius impact analysis for a file."""
    norm_path = normalize_path(file_path, Path(project_root))
    if db.get_file_hash(norm_path) is None and not db.get_nodes_in_file(norm_path):
        raise HTTPException(status_code=404, detail=f"File not indexed: '{file_path}'")
    total_impact, impacts_by_level = compute_impact_analysis(db, norm_path)
    return {
        "file_path": norm_path,
        "total_impact": total_impact,
        "levels": impacts_by_level,
    }


@app.get("/api/slice")
def get_slice_endpoint(
    symbol: str = Query(..., min_length=1),
    file_path: str | None = Query(default=None),
    no_callees: bool = Query(default=False),
):
    """Retrieve surgical source code slice and callee contracts for a symbol."""
    norm_file = normalize_path(file_path, Path(project_root)) if file_path else None
    res = get_code_slice(
        db, Path(project_root), symbol, file_path=norm_file, include_callees=not no_callees
    )
    if not res.found:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found")
    return res.model_dump()


@app.get("/api/references")
def get_references_endpoint(
    symbol: str = Query(..., min_length=1),
    file_path: str | None = Query(default=None),
    kind: str | None = Query(default=None),
):
    """Find all incoming calls, references, inheritance, or implementations to a symbol."""
    norm_file = normalize_path(file_path, Path(project_root)) if file_path else None
    clean_symbol = symbol.strip().removesuffix("()")
    found, targets, summaries = find_symbol_references(
        db, clean_symbol, file_path=norm_file, kind=kind
    )
    if not found:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found in index")

    total_refs = sum(len(s["references"]) for s in summaries)
    return {
        "symbol": clean_symbol,
        "target_count": len(targets),
        "total_references": total_refs,
        "targets": summaries,
    }


@app.get("/api/vibe/guard")
def get_vibe_guard_endpoint(
    symbol: str = Query(default=""),
    file_path: str | None = Query(default=None),
    staged: bool = Query(default=False),
    strict: bool = Query(default=False),
):
    """Pre-flight caller inspection to verify blast radius before editing code."""
    from vibetrace.services.caller_guard import CallerGuardService

    active_root = Path(os.environ.get("VIBETRACE_PROJECT_ROOT", project_root))
    if staged:
        reports = CallerGuardService.get_staged_caller_guard(db, active_root)
        serialized = [r.model_dump() for r in reports]
        if strict:
            violation = any(r.total_callers > 0 for r in reports)
            return {"staged": True, "strict": True, "violation": violation, "reports": serialized}
        return serialized

    if not symbol:
        raise HTTPException(
            status_code=422, detail="Symbol parameter is required when staged=False"
        )

    norm_file = normalize_path(file_path, active_root) if file_path else None
    report = CallerGuardService.get_caller_guard(
        db, active_root, symbol_name=symbol, file_path=norm_file
    )
    if not report.found:
        raise HTTPException(status_code=404, detail=report.safety_advisory)
    return report.model_dump()


@app.get("/api/vibe/bundle")
def get_vibe_bundle_endpoint(
    intent: str = Query(..., min_length=1),
    symbol: str | None = Query(default=None),
    budget: int = Query(default=1500, ge=200, le=8000),
):
    """Assemble one-shot task context packed with docs, code slice, caller guard, and tests."""
    from vibetrace.services.vibe_bundle import VibeBundleService

    active_root = Path(os.environ.get("VIBETRACE_PROJECT_ROOT", project_root))
    bundle = VibeBundleService.assemble_bundle(
        db=db,
        root=active_root,
        intent=intent,
        target_symbol=symbol,
        token_budget=budget,
    )
    return bundle.model_dump()


@app.get("/api/dependencies")
def get_dependencies_endpoint(
    file_path: str = Query(..., min_length=1),
    reverse: bool = Query(default=False),
):
    """Categorize file dependencies into internal, third-party, and standard library."""
    norm_path = normalize_path(file_path, Path(project_root))
    if db.get_file_hash(norm_path) is None and not db.get_nodes_in_file(norm_path):
        raise HTTPException(status_code=404, detail=f"File not indexed: '{file_path}'")
    analysis = analyze_file_dependencies(db, norm_path)
    counts = {
        "internal": len(analysis["internal"]),
        "third_party": len(analysis["third_party"]),
        "stdlib": len(analysis["stdlib"]),
        "dependents": len(analysis["dependents"]),
        "circular": len(analysis["circular"]),
        "total": (
            len(analysis["internal"]) + len(analysis["third_party"]) + len(analysis["stdlib"])
        ),
    }
    if reverse:
        return {
            "file_path": norm_path,
            "reverse": True,
            "counts": counts,
            "dependents": analysis["dependents"],
            "circular": analysis["circular"],
        }
    return {
        "file_path": norm_path,
        "counts": counts,
        **analysis,
    }


@app.get("/api/summary")
def get_summary_endpoint(
    file_path: str = Query(default=".", min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    budget: int | None = Query(default=None, ge=50, le=32000),
):
    """Get structural outline of a file or a token-budgeted repo map."""
    if budget is not None:
        from vibetrace.services.repo_map_service import RepoMapService

        res = RepoMapService.generate_budgeted_repo_map(
            db, scope_path=file_path, budget_tokens=budget, project_root=Path(project_root)
        )
        return {
            "scope": file_path,
            **res,
        }

    norm_path = normalize_path(file_path, Path(project_root))
    if db.get_file_hash(norm_path) is None and not db.get_nodes_in_file(norm_path):
        raise HTTPException(status_code=404, detail=f"File not indexed: '{file_path}'")
    total, rows = db.get_file_structure(norm_path, limit, offset)
    return {
        "file_path": norm_path,
        "total": total,
        "nodes": rows,
    }


@app.post("/api/reindex")
def post_reindex_endpoint(payload: ReindexPayload | None = None):
    """Reindex the workspace or a single file on demand."""
    from vibetrace.services.reindex_service import ReindexService

    eff_payload = payload or ReindexPayload()
    try:
        return ReindexService.execute_reindex(
            db=db,
            project_root=Path(project_root),
            file_path=eff_payload.file_path,
            force=eff_payload.force,
            embed=eff_payload.embed,
            docs=eff_payload.docs,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/review")
def get_unified_review_endpoint(
    scope: str = Query(default="changed", description="changed or full"),
    check_dependencies: bool = Query(default=False),
    min_coverage: float | None = Query(default=None),
    enforce_coverage: bool = Query(default=False),
):
    """Run unified 1-call code review combining Security/Taint, Split Audit, and Caller Guard."""
    from vibetrace.services.review_service import run_unified_review

    root = Path(os.environ.get("VIBETRACE_PROJECT_ROOT", project_root)).resolve()
    report = run_unified_review(
        db=db,
        project_root=root,
        scope=scope,
        check_dependencies=check_dependencies,
        min_coverage=min_coverage,
        enforce_coverage=enforce_coverage or (min_coverage is not None),
    )
    return report.model_dump()


@app.get("/api/skill")
def get_skill_endpoint():
    """Inspect whether the vibetrace-navigator skill is installed in the project."""
    root = Path(os.environ.get("VIBETRACE_PROJECT_ROOT", project_root)).resolve()
    local_skill = root / ".agents" / "skills" / "vibetrace-navigator" / "SKILL.md"
    claude_skill = root / ".claude" / "skills" / "vibetrace-navigator" / "SKILL.md"
    home = Path.home()
    global_skill = home / ".gemini" / "antigravity" / "skills" / "vibetrace-navigator" / "SKILL.md"
    return {
        "installed": local_skill.exists() or claude_skill.exists(),
        "paths": {
            "agents": str(local_skill) if local_skill.exists() else None,
            "claude": str(claude_skill) if claude_skill.exists() else None,
            "global": str(global_skill) if global_skill.exists() else None,
        },
    }


@app.post("/api/skill")
def post_skill_endpoint(global_install: bool = Query(default=False)):
    """Install or synchronize the vibetrace-navigator skill for AI agents."""
    from vibetrace.skill_installer import install_skill

    root = Path(os.environ.get("VIBETRACE_PROJECT_ROOT", project_root)).resolve()
    paths = install_skill(root, global_install=global_install)
    return {
        "status": "ok",
        "installed_paths": [str(p) for p in paths],
        "global": global_install,
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        manager.disconnect(websocket)


# Mount static frontend if built (src/web/dist)
web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if not web_dist.exists():
    web_dist = Path(__file__).resolve().parent.parent.parent / "src" / "web" / "dist"

if web_dist.exists() and (web_dist / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="static_web")
else:

    @app.get("/", response_class=HTMLResponse)
    def index_fallback():
        return (
            "<!DOCTYPE html><html><head><title>VibeTrace Map Room</title></head>"
            "<body style='font-family: sans-serif; background: #0f172a; "
            "color: #f8fafc; padding: 2rem;'><h1>VibeTrace API Running</h1>"
            "<p>The Map Room Web UI is not built yet.</p>"
            "<pre style='background: #1e293b; padding: 1rem; border-radius: 8px;'>"
            "bun run --cwd src/web dev</pre></body></html>"
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
