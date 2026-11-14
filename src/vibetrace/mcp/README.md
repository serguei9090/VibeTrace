# MCP

## Overview

FastMCP server exposing VibeTrace code-intelligence tools to AI assistants (Antigravity, Cursor, Claude Desktop). Tools are grouped into three families: read-only structural queries registered directly on the server, engineering/refactoring tools registered by `tools_engineering`, and federated multi-repository project tools registered by `tools_project`. Every tool accepts a Pydantic input model and returns either Markdown or JSON.

## Interface

- [`server.py`](./server.py) — `mcp = FastMCP("vibetrace_mcp")`; module-level tool functions and `main()` entry point for the `vibetrace-mcp` console script.
- [`schemas.py`](./schemas.py) — `ResponseFormat` (`markdown`, `json`), `TraceDirection` (`up`, `down`, `both`), and input models: `StructuralSummaryInput`, `CallStackInput`, `ImpactAnalysisInput`, `ReindexInput`, `ReferencesInput`, `DependenciesInput`, `ListSavedTracesInput`, `SaveTraceInput`, `DeleteSavedTraceInput`, `SearchInput`, `SemanticSearchInput`, `TracePathInput`, `CodeSliceInput`, `CallTreeInput`, `DocSearchInput`, `DocCatalogsInput`, `CallerGuardInput`, `VibeBundleInput`, `ProjectStatusInput`, `ProjectSearchInput`, `ProjectGuardInput`, `ProjectLinksInput`, `AuditSplitsInput`, `ApproveSplitInput`, `AuditSecurityInput`, `ApproveSecurityExceptionInput`, `RemediateVulnerabilityInput`, `ReviewChangesInput`, `ExplainDependencyInput`, `RefactorMethodInput`, `PatchDependencyInput`, `CheckDocsDriftInput`, `DiagnoseSymbolInput`.
- [`formatters.py`](./formatters.py) — Markdown renderers: `format_structural_summary_md`, `format_semantic_search_md`, `format_trace_path_md`, `format_code_slice_md`, `format_references_md`, `format_dependencies_md`, `format_signature_search_md`, `format_impact_analysis_md`, `format_saved_traces_md`, `format_doc_search_md`, `format_doc_catalogs_md`, `format_caller_guard_markdown`, `format_vibe_bundle_markdown`, `format_split_audit_md`.
- [`tools_engineering.py`](./tools_engineering.py) — `vibetrace_explain_dependency`, `vibetrace_refactor_method`, `vibetrace_patch_dependency`, `vibetrace_check_docs_drift`, `vibetrace_diagnose_symbol`, `register_engineering_tools`; constants `VIBETRACE_DIR`, `INDEX_DB_NAME`, `INDEX_NOT_FOUND_MSG`.
- [`tools_project.py`](./tools_project.py) — `vibetrace_project_status`, `vibetrace_project_search`, `vibetrace_project_guard`, `vibetrace_project_links`, `register_project_tools`.

Tools registered on `mcp` in `server.py`:

- `vibetrace_get_structural_summary` (read-only) — file structure or token-budgeted PageRank repo map.
- `vibetrace_get_call_stack` (read-only) — callers (`up`), callees (`down`), or both.
- `vibetrace_impact_analysis` (read-only) — blast radius of a file.
- `vibetrace_find_references` (read-only) — references to a symbol.
- `vibetrace_get_dependencies` (read-only) — internal, third-party, and stdlib imports.
- `vibetrace_search_by_signature` (read-only) — SQL-pattern signature search.
- `vibetrace_semantic_search` (read-only) — hybrid RRF search over AST symbols, FTS5 BM25, and vectors.
- `vibetrace_trace_path` (read-only) — shortest execution path between two symbols.
- `vibetrace_list_saved_traces` (read-only) — list saved trace queries.
- `vibetrace_save_trace` (mutation) — create or update a saved trace query.
- `vibetrace_delete_saved_trace` (destructive) — delete a saved trace query.
- `vibetrace_get_code_slice` (read-only) — source lines plus callee contracts.
- `vibetrace_get_call_tree` (read-only) — recursive top-down call tree.
- `vibetrace_reindex_project` (mutation) — reindex a file or directory.
- `vibetrace_search_docs` — documentation hybrid RRF search.
- `vibetrace_list_doc_catalogs` — list local and global documentation catalogs.
- `vibetrace_check_caller_guard` — pre-flight caller blast-radius guard.
- `vibetrace_get_vibe_bundle` — one-shot token-budgeted task context bundle.
- `vibetrace_audit_splits` (read-only) — audit execution bifurcations.
- `vibetrace_approve_split` (mutation) — record a split approval baseline.
- `vibetrace_audit_security` (read-only) — security and dependency audit.
- `vibetrace_approve_security_exception` (mutation) — record a security exception baseline.
- `vibetrace_remediate_vulnerability` (read-only) — AI remediation instructions.
- `vibetrace_review_changes` (read-only) — unified review of security, splits, and caller guard.

## Invariants

- Every tool wraps its body in `try`/`except` and returns `handle_error(e, ...)` on failure instead of raising [src/vibetrace/mcp/server.py:vibetrace_get_structural_summary].
- Tool annotations are fixed: `_READ_ONLY` (`readOnlyHint=True`), `_MUTATION` (`readOnlyHint=False`, `destructiveHint=False`), `_DESTRUCTIVE` (`destructiveHint=True`); all three set `idempotentHint=True` and `openWorldHint=False` [src/vibetrace/mcp/server.py:_READ_ONLY].
- `ResponseFormat` defaults to `ResponseFormat.MARKDOWN`; JSON output is emitted with `json.dumps(..., indent=2)` [src/vibetrace/mcp/schemas.py:ResponseFormat].
- `CallStackInput.depth` is bounded to `1..10` and defaults to `1`; `all_relations=False` restricts traversal to relation types `("calls", "routes")` [src/vibetrace/mcp/schemas.py:CallStackInput].
- `StructuralSummaryInput.budget_tokens` is bounded to `64..8192`; when set, the tool returns `RepoMapService.generate_budgeted_repo_map` instead of file structure [src/vibetrace/mcp/schemas.py:StructuralSummaryInput].
- `StructuralSummaryInput.limit` is bounded to `1..500` (default 100) and `offset` is `>= 0` [src/vibetrace/mcp/schemas.py:StructuralSummaryInput].
- `TracePathInput.max_depth` is bounded to `1..15` and defaults to `6`; `engine` is `"bfs"` (default) or `"cte"`; `saved_name` overrides `from_symbol`/`to_symbol` and reuses the saved depth only when `max_depth` is still the default `6` [src/vibetrace/mcp/schemas.py:TracePathInput].
- `SaveTraceInput.name` is bounded to 1..100 characters and `max_depth` to `1..20` (default 6) [src/vibetrace/mcp/schemas.py:SaveTraceInput].
- `VibeBundleInput.token_budget` is bounded to `200..8000` and defaults to `1500` [src/vibetrace/mcp/schemas.py:VibeBundleInput].
- `DocSearchInput.limit` is bounded to `1..50` (default 5) and `scope` is `"local"` (default), `"global"`, or `"all"` [src/vibetrace/mcp/schemas.py:DocSearchInput].
- `ProjectSearchInput.limit` is bounded to `1..100` (default 20); `SearchInput.limit` to `1..200` (default 50); `SemanticSearchInput.limit` to `1..100` (default 15); `CallTreeInput.max_depth` to `1..10` (default 3) [src/vibetrace/mcp/schemas.py:ProjectSearchInput].
- `RefactorMethodInput.apply` defaults to `False`, so a refactor plan is a dry-run preview unless the caller opts in; when `start_line`/`end_line` are omitted the tool only lists candidate split blocks [src/vibetrace/mcp/schemas.py:RefactorMethodInput].
- `PatchDependencyInput.apply` defaults to `False`; `suppress_unpatched` is the separate opt-in for recording unpatched CVEs [src/vibetrace/mcp/schemas.py:PatchDependencyInput].
- `ApproveSplitInput.status` defaults to `"approved"` and `rationale` to `"Architectural review verified"`; `ApproveSecurityExceptionInput.status` defaults to `"APPROVED"`, `severity` to `"HIGH"`, and `reviewed_by` to `"agent"` [src/vibetrace/mcp/schemas.py:ApproveSplitInput].
- `AuditSecurityInput.skip_deps` maps to `check_dependencies=not params.skip_deps` and `no_taint` maps to `analyze_taint=not params.no_taint` [src/vibetrace/mcp/server.py:vibetrace_audit_security].
- `ReviewChangesInput.enforce_coverage` is forced on whenever `min_coverage` is supplied: `params.enforce_coverage or (params.min_coverage is not None)` [src/vibetrace/mcp/server.py:vibetrace_review_changes].
- `vibetrace_semantic_search` fetches at most 3 documentation chunks and only when `include_docs=True`; failures are swallowed and produce `doc_chunks = None` [src/vibetrace/mcp/server.py:vibetrace_semantic_search].
- `tools_engineering._resolve_project_root` walks upward looking for standard project root markers (`.vibetrace`, `vibetrace_context`, `.vibetrace.toml`, `vibetrace.toml`, `.git`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`) and falls back to the resolved target path [src/vibetrace/mcp/tools_engineering.py:_resolve_project_root].
- `tools_engineering` tools require `<root>/.vibetrace/vibetrace.db` and return `INDEX_NOT_FOUND_MSG` ("Error: VibeTrace index not found. Run index first.") when it is missing [src/vibetrace/mcp/tools_engineering.py:vibetrace_patch_dependency].
- `vibetrace_explain_dependency` requires `package_name` for `action="why"`; `action` is `"why"` (default), `"tree"`, or `"conflicts"` [src/vibetrace/mcp/schemas.py:ExplainDependencyInput].
- `tools_project._resolve_workspace` raises `FileNotFoundError` when no `.vibetrace-project.toml` exists in the target path or its parents [src/vibetrace/mcp/tools_project.py:_resolve_workspace].
- `tools_project` tools close the workspace in a `finally` block, releasing the federated engine connection [src/vibetrace/mcp/tools_project.py:vibetrace_project_status].
- Compatibility aliases `_get_context_for_path`, `_normalize_path`, `_safe_str`, `_handle_error` mirror the service functions of the same name [src/vibetrace/mcp/server.py:_get_context_for_path].

## Side Effects

- `vibetrace_reindex_project` runs `reindex_all` against the index database and optionally calls `EmbeddingEngine.index_missing_embeddings` to store 256d vectors [src/vibetrace/mcp/server.py:vibetrace_reindex_project].
- `vibetrace_save_trace` inserts a saved trace row through `create_saved_trace`; `vibetrace_delete_saved_trace` removes one through `delete_saved_trace_by_name` [src/vibetrace/mcp/server.py:vibetrace_save_trace].
- `vibetrace_approve_split` writes the approval baseline to `.vibetrace/splits.toml` via `record_split_approval` [src/vibetrace/mcp/server.py:vibetrace_approve_split].
- `vibetrace_approve_security_exception` writes the exception baseline to `.vibetrace/security.toml` via `SecurityService.record_exception` [src/vibetrace/mcp/server.py:vibetrace_approve_security_exception].
- `vibetrace_check_docs_drift` with `sync=True` rewrites the `symbol_doc_links` table via `SymbolDocLinker.sync_links` [src/vibetrace/mcp/tools_engineering.py:vibetrace_check_docs_drift].
- `vibetrace_refactor_method` with `apply=True` writes the refactored source to disk via `RefactorService.apply_refactor_plan`; with `apply=False` it only returns a preview [src/vibetrace/mcp/tools_engineering.py:vibetrace_refactor_method].
- `vibetrace_patch_dependency` with `apply=True` or `suppress_unpatched=True` mutates lockfiles or the security baseline via `DependencyAutoPatcher.apply_patch_plan` [src/vibetrace/mcp/tools_engineering.py:vibetrace_patch_dependency].
- `vibetrace_audit_security` runs `SecurityService.audit_project`, which may resolve CVEs and scan the project tree [src/vibetrace/mcp/server.py:vibetrace_audit_security].
- Read-only tools call `notify_ui_focus`, which publishes focus events for the web UI [src/vibetrace/mcp/server.py:vibetrace_get_structural_summary].
- `vibetrace_reindex_project` redirects stdout to stderr with `redirect_stdout(sys.stderr)` while indexing [src/vibetrace/mcp/server.py:vibetrace_reindex_project].
- `main()` reconfigures stdout and stderr to UTF-8 on Windows before calling `mcp.run()` [src/vibetrace/mcp/server.py:main].

## Verification

- Run: `uv run pytest`
