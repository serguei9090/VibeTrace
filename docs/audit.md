# 📜 VibeTrace Audit Log & Defect Remediation Register

This document tracks all strict critique audit rounds, identified root causes, files modified, and verification tests to ensure engineering continuity and prevent regression loops.

---

## Audit Matrix Overview

| Round | Focus Area | Key Findings | Verification Test File | Status |
| :---: | :--- | :--- | :--- | :---: |
| **1** | Storage & DB Safety | SQLite batch 500-item chunk limit, Watcher WAL storms, Lefthook injection, Relative imports | `tests/test_audit_code_bugs.py` | ✅ Verified |
| **2** | CLI & Routing | MCP `--path` context, CLI `--json` precedence, `:LNone` formatting, Flask tuple methods | `tests/test_audit_code_bugs_round2.py` | ✅ Verified |
| **3** | Cross-Platform & MCP | JS `OPTIONS`/`HEAD` methods, MCP CWD discovery, Neural search resilience, Git spaces | `tests/test_audit_code_bugs_round3.py` | ✅ Verified |
| **4** | Multi-Language AST | C++ pointer methods (`->`), Go grouped parameters, JS namespace re-exports, TS `as` expr | `tests/test_audit_code_bugs_round4.py` | ✅ Verified |
| **5** | Resolver & Scoping | Ruby/PHP resolver injection, TS/JS `@/` & `~/` aliases, Walrus operator, DB path isolation | `tests/test_audit_code_bugs_round5.py` | ✅ Verified |
| **6** | Polyglot & Windows | Polyglot stdlib classification, Windows backslash DB queries, CJS exports, Ruby AST | `tests/test_audit_code_bugs_round6.py` | ✅ Verified |
| **7** | Systems & Limits | PHP namespace use & heritage, reusable `_add_node` helper, $<500$ LOC ceilings | `tests/test_audit_code_bugs_round7.py` | ✅ Verified |
| **8** | Query & REST API | Trailing `()` query drops, Go `var_spec` unwrapping, ModuleResolver fallback, Saved trace API | `tests/test_audit_code_bugs_round8.py` | ✅ Verified |
| **9** | Git & Watchdog | Subtree Git SHA poisoning, Watcher gitignore backslashes, Fullstack route priority, UTF-8 Git | `tests/test_audit_code_bugs_round9.py` | ✅ Verified |
| **10** | LSP, Sync & Sec | LSP Windows encoded URI drive letters, `ensure_index_fresh` pruned SHA recovery, impact backslash, sqlite-vec | `tests/test_audit_code_bugs_round10.py` | ✅ Verified |
| **11** | CLI & Formatter | CLI global flags after subcommand, search trailing parens, `cls.` resolution, WebSocket set cleanup | `tests/test_audit_code_bugs_round11.py` | ✅ Verified |
| **12** | API Sec & Git Delta | Loopback CORS origin regex, `/source` indexed file guard, Git delta disk truth, unified ignore rules | `tests/test_audit_code_bugs_round12.py` | ✅ Verified |
| **13** | MCP & Call Trees | Whitespace docstring safety, MCP reindex resolved root, GraphTraverser visited reset, dotfile prefix | `tests/test_audit_code_bugs_round13.py` | ✅ Verified |
| **14** | CLI Flags & Super Calls | CLI `--no-sync` wiring, subdirectory `normalize_path`, non-git freshness cursor, Python `super()` calls | `tests/test_audit_code_bugs_round14.py` | ✅ Verified |
| **15** | Heritage & References | JS/TS class & interface heritage, Go interface embedding, reference inheritance filter, `--root` override | `tests/test_audit_code_bugs_round15.py` | ✅ Verified |
| **16** | Concurrency & Vectors | Dynamic JIT concurrency lock, DuckDB cursor isolation, LanceDB vector dimensions config, UTF-8 decoding | `tests/test_concurrency.py` | ✅ Verified |
| **17** | AST Error Recovery | Multi-language tree-sitter error recovery, C++ namespace resolution, Go method receiver normalization | `tests/test_multiparser.py` | ✅ Verified |
| **18** | DB Retries & Atomicity | SQLite busy timeout retry with exponential backoff (`db_retry.py`), transaction atomicity in batch indexing | `tests/test_audit_code_bugs_round18.py` | ✅ Verified |
| **19** | Git Delta & Call Trees | Git delta copy/rename status parsing (`C100`, `R100`), call-tree line-0 guard, JS default export resolution | `tests/test_audit_code_bugs_round19.py` | ✅ Verified |
| **20** | C++/Java/C# Heritage | C++/Java/C# inheritance clauses (`base_clause`, `base_list`), generic type stripping (`<T>`), sibling scoping | `tests/test_audit_code_bugs_round20.py` | ✅ Verified |
| **21** | Vector Store Purge | Auto-purge vector store on `clear_file` / reindex, FastAPI transport path normalization (`normalize_path`) | `tests/test_audit_code_bugs_round21.py` | ✅ Verified |
| **22** | MCP Reindex & Slices | Multi-language code slice markdown fences, `ReindexInput.embed` flag wiring, single-file resolver registration | `tests/test_audit_code_bugs_round22.py` | ✅ Verified |
| **23** | Builtin Filtering & CLI | Multi-language builtin filtering (`builtins.py`), phantom edge elimination, CLI `serve --path`, API lifespan | `tests/test_audit_code_bugs_round23.py` | ✅ Verified |
| **24** | Go Modules & Git SHA | Go `go.mod` & Cargo `Cargo.toml` self-package resolution, Git SHA-256 (64-char) hash support | `tests/test_audit_code_bugs_round24.py` | ✅ Verified |

---

## Detailed Round-by-Round Findings

### Round 1: Storage, Concurrency & Security
* **SQLite Batch Variable Limit (`db.py`)**: Chunked node/edge inserts into batches of $\le 500$ items to prevent SQLite's 999-variable limit crash (`sqlite3.OperationalError: too many SQL variables`).
* **Watcher WAL Storm Prevention (`watcher.py`)**: Debounced centrality calculations and SQLite commits using threading timer to prevent lock contention during burst file saves.
* **Lefthook Hook Injection (`hooks.py`)**: Sanitized shell commands and paths written to `lefthook.yml` to prevent arbitrary command execution.
* **Relative Import Dot Resolution (`base.py`, `python_parser.py`)**: Fixed dotted relative imports (`from . import helper`, `from ..sub import mod`) in Python AST parser.

### Round 2: CLI Contracts & Route Extractions
* **MCP Multi-Project Discovery (`server.py`)**: Added `--path` parameter support to `vibetrace mcp` server for targeting external workspaces.
* **CLI JSON Precedence (`cli.py`, `cli_commands.py`)**: Suppressed progress output and directed JSON directly to stdout when `--json` flag is set.
* **Markdown `:LNone` Line Number Bug (`formatters.py`)**: Filtered out unindexed line numbers to avoid printing `:LNone` in CLI and MCP call-stack outputs.
* **Flask Tuple Route Methods (`route_extractor.py`)**: Extended regex to match `@app.route(..., methods=("GET", "POST"))` tuple definitions in addition to lists.

### Round 3: MCP Resilience & Cross-Platform Git
* **JS Route HTTP Methods (`js_parser.py`)**: Added AST extraction support for `OPTIONS` and `HEAD` HTTP route handlers.
* **MCP CWD Path Normalization (`server.py`)**: Normalized relative paths against server root before querying SQLite.
* **Hybrid Search Vector Fallback (`hybrid_search.py`)**: Ensured neural vector exceptions fall back gracefully to BM25 full-text search without crashing queries.
* **Git Path Quoting (`git_utils.py`)**: Stripped surrounding double and single quotes from filenames containing spaces in Git diff outputs.

### Round 4: Polyglot AST Extraction Edge Cases
* **C++ Pointer Method Access (`generic_parser.py`)**: Extracted method calls via pointer dereference (`ptr->method()`) and scope operator (`Class::method()`).
* **Go Grouped Parameters (`go_parser.py`)**: Expanded comma-separated parameter declarations (`func foo(a, b string, c int)`) so every parameter receives its annotated type.
* **JS Namespace Re-Exports (`js_parser.py`)**: Handled `export * as ns from './module'` syntax by creating proper namespace module edges.
* **TypeScript `as` Expression Unwrapping (`js_parser.py`)**: Unwrapped `as_expression` nodes during variable assignment so cast expressions (`const c = obj as Type`) retain their type.

### Round 5: Import Aliases & Scoping Isolation
* **ParserFactory Resolver Propagation (`parser.py`)**: Passed `resolver` to Ruby and PHP parsers in `ParserFactory.get_parser`.
* **Path Aliases (`@/`, `~/`) in ModuleResolver (`resolver.py`)**: Supported modern Next.js/Vite/TS path aliases resolving to `src/*` or root.
* **Walrus Operator `:=` in Python (`python_parser.py`)**: Handled `named_expression` AST nodes so variables defined in `if (val := func()):` register local types.
* **DB Path Isolation in Services (`graph_service.py`)**: Used `get_context_for_path` to prevent cross-project database collisions.

### Round 6: Polyglot Classification & Windows Queries
* **Standard Library Classification (`graph_service.py`)**: Added stdlib module registries for Go, Rust, Python, and Node.js in `categorize_file_dependencies`.
* **Windows Backslash Normalization in DB Queries (`db_queries.py`, `db.py`)**: Ensured all file path queries and recursive CTE traversals convert `\` to `/`.
* **CommonJS `module.exports` Extraction (`js_parser.py`)**: Added AST support for `module.exports = { a, b }` and `exports.foo = bar`.
* **Ruby AST Grammar Nodes (`generic_parser.py`)**: Added tree-sitter node extractors for `class`, `module`, and `method` definitions.

### Round 7: Heritage & Backend File Size Ceilings
* **PHP Namespace & Heritage (`generic_parser.py`)**: Supported `namespace_use_declaration` and class `extends`/`implements` in PHP.
* **Reusable `_add_node` AST Helper (`base.py`)**: Centralized common node extraction to maintain all parser files strictly $<500$ LOC (`js_parser.py`: 491, `base.py`: 430, `generic_parser.py`: 433).

### Round 8: Query Normalization & REST API
* **Trailing `()` in Query Resolution (`trace_service.py`, `call_tree_service.py`, `server.py`)**: Stripped trailing parentheses and whitespace so queries like `calculate()` match SQLite symbols.
* **Go `var_spec` `expression_list` AST Unwrap (`go_parser.py`)**: Unwrapped tree-sitter Go `expression_list` to capture constructor types in `var calc = NewCalculator()` and multi-variable declarations.
* **ModuleResolver Fallback (`resolver.py`)**: Sanitized `./` prefixes and added path segment fallback algorithm for unregistered package root files.
* **Saved Trace by ID Endpoint (`saved_traces_service.py`, `server_api.py`)**: Added `get_saved_trace_by_id` and registered `GET /api/saved-traces/{trace_id}`.

### Round 9: Subtree Caching, Watcher Gitignore & Git Encoding
* **Subtree Git SHA Poisoning (`main.py`)**: Guarded `db.set_meta("last_indexed_git_sha", current_sha)` to only record HEAD when indexing the project root, preventing skipped files in subsequent full runs.
* **Watcher Gitignore Windows Backslash Mismatch (`watcher.py`)**: Replaced `\` with `/` on relative filepaths before evaluating against `pathspec.PathSpec`.
* **Fullstack Route Priority & Code Slices (`trace_service.py`)**: Included `"routes": 1` in BFS pathfinding priority and allowed `"routes"` edges in `get_code_slice` callee contracts.
* **Git Delta UTF-8 Quotepath (`git_utils.py`)**: Passed `-c core.quotepath=false` and `encoding="utf-8"` to Git subprocesses to prevent non-ASCII UTF-8 filenames from being deleted.

### Round 10: LSP Bridge, Freshness Sync, Blast Radius & Vector Security
* **LSP Bridge Windows Encoded URI Drive Letters (`lsp_bridge.py`)**: Decoded percent-encoded drive letters (`file://c%3A/...`) before URI parsing to prevent losing the drive letter and corrupting definitions on Windows.
* **`ensure_index_fresh` Pruned SHA Recovery (`graph_service.py`)**: Handled rebased, squashed, or pruned Git commits where `get_git_delta` returns `None` by triggering full reconciliation reindexing.
* **`compute_impact_analysis` Windows Path Normalization (`graph_service.py`)**: Normalized Windows backslashes `\` and leading `./` in `compute_impact_analysis` to prevent same-file function calls from being counted as external blast-radius impacts.
* **SQLite-Vec Extension Exception Safety (`embeddings.py`)**: Guaranteed `conn.enable_load_extension(False)` always runs in a `finally` block when loading sqlite-vec to prevent leaving extension loading permanently enabled.

### Round 11: CLI Global Flags, Search Parens, `cls.` Resolution & WebSocket Cleanup
* **Global Flags After Subcommand (`cli.py`)**: `--root`, `--no-sync`, `--json` now accepted after any subcommand; subparser copies use `argparse.SUPPRESS` defaults so root-level placement is never clobbered.
* **Trailing `()` in Search (`hybrid_search.py`, `db_queries.py`)**: Queries like `calculate()` are normalized so the AST exact-match stream (tier 0) still hits.
* **Python `cls.method()` Resolution (`parsers/base.py`)**: Added `cls.` to class-scope receiver prefixes so classmethod calls resolve to `Class.method` instead of phantom IDs.
* **Idempotent WebSocket Disconnect (`server_api.py`)**: `ConnectionManager.active_connections` is now a `set` with `discard()`, eliminating `ValueError` on double disconnect.

### Round 12: API Security Boundary, Git Delta Integrity & Unified Ignore Rules
* **Cross-Origin Source Exfiltration (`server_api.py`)**: CORS restricted to loopback origins (no wildcard, no credentials); `/source` serves only files present in `indexed_files`, so `.env`/keys are never readable.
* **Re-Created Files Purged by Delta (`git_utils.py`)**: Disk is now the source of truth — paths deleted in history but present on disk are re-indexed instead of cleared.
* **Failed Writes Advancing Git SHA (`main.py`)**: `index_file(failed=...)` records errors; `reindex_all` only advances `last_indexed_git_sha` when every file persisted, so failures retry next run.
* **Divergent Ignore Rules (`main.py`, `watcher.py`)**: Single `is_ignored_path()` on root-relative paths shared by full walk, git delta, and watcher; delta no longer indexes `dist/`/dot-dirs and the watcher no longer ignores everything when an ancestor folder is named `build`/`dist`.

### Round 13: MCP Reindex Root, Formatter Safety, Call-Stack Visited Reset & Path Prefix
* **Whitespace-Only Docstring Crash (`mcp/formatters.py`)**: Extracted the first non-empty line in `format_semantic_search_md` so whitespace-only docstrings (`""" """`) do not raise `IndexError`.
* **MCP Reindex CWD Pollution (`mcp/server.py`)**: Resolved `params.path` values of `""` or `"."` to the context-resolved `root` in `vibetrace_reindex_project` instead of `Path(".").resolve()` (the MCP server process CWD).
* **`GraphTraverser` Visited-Set Leak (`mcp/server.py`)**: Cleared `traverser.visited` before each `up` and `down` traversal pass per start node so multi-symbol call stacks render full subtrees.
* **Dot-Directory Path Corruption (`services/graph_service.py`)**: Replaced `lstrip("./")` with `removeprefix("./")` in `compute_impact_analysis` so dot-prefixed paths (e.g. `.github/workflows/ci.py`) are not stripped of their leading dot.

### Round 14: CLI `--no-sync` Wiring, Subdirectory Path Normalization, Non-Git Freshness Guard & Python `super()` Call Resolution
* **CLI `--no-sync` Flag Reachability (`cli_commands.py`)**: Propagated `auto_sync = not getattr(args, "no_sync", False)` to `get_context_for_path()` in `cmd_search`, `cmd_trace`, `cmd_slice`, `cmd_call_tree`, `cmd_impact`, and `cmd_summary`, enabling agents to bypass JIT reindexing for fast read-only queries.
* **Subdirectory `normalize_path` Resolution (`services/graph_service.py`)**: Checked `p.exists()` before falling back to `abs_root / p` so running CLI commands from workspace subdirectories accurately resolves relative file paths to their indexed root-relative locations.
* **Non-Git Workspace Reindexing Storm (`indexer/main.py`, `services/graph_service.py`)**: Persisted `last_indexed_git_sha = "non-git"` in `meta` on successful initial indexing and short-circuited subsequent `ensure_index_fresh()` calls in non-git directories, preventing infinite full-tree disk walks.
* **Python `super().method()` AST Call Resolution (`parsers/python_parser.py`, `parsers/base.py`)**: Registered superclass in `ctx.local_types["super()"]` during class definition extraction and resolved `super().method()` / `super(Class, self).method()` to `Base.method` across intra-file and cross-module inheritance trees instead of emitting phantom node edges.

### Round 15: JS/TS Class & Interface Heritage, Go Interface Embedding, Reference Discovery & CLI `--root` Override
* **JS/TS Class & Interface Heritage Extraction (`parsers/js_parser.py`)**: Inspected anonymous AST child nodes `class_heritage` and `extends_type_clause` instead of querying non-existent named field `class_heritage`, emitting `inherits` and `implements` edges for TS/JS classes and interfaces and resolving `super.method()` calls to base methods.
* **Go Interface Embedding Composition (`parsers/go_parser.py`)**: Extracted embedded interface nodes (`type_elem`, `type_identifier`, `qualified_type`) inside `interface_type` declarations and emitted `inherits` edges between composite Go interfaces.
* **Symbol References Inheritance Visibility (`indexer/db_queries.py`)**: Expanded `get_symbol_references` SQL filter to include `inherits` and `implements` alongside `calls` and `references`, ensuring usages of classes, structs, traits, and interfaces are fully visible in reference lookups.
* **CLI Subcommand `--root` Flag Propagation (`cli_commands.py`)**: Wired `--root` override into `cmd_hooks` via `root_override` and into `cmd_mcp` and `cmd_skill` via path resolution, ensuring custom project roots are respected across all subcommands.

### Round 16: Concurrency Safety, Cursor Lifecycle & Vector Store Configuration
* **Dynamic JIT Concurrency Lock (`services/graph_service.py`)**: Synchronized concurrent `ensure_index_fresh` requests using per-root threading locks to prevent concurrent index corruption during parallel agent tool queries.
* **DuckDB Cursor Lifecycle Discipline (`indexer/vector_store.py`)**: Enforced explicit cursor cleanup and connection lifecycle management in analytics and similarity queries.
* **LanceDB Dynamic Dimension Configuration (`config.py`, `indexer/embeddings.py`)**: Propagated embedding dimension overrides from `vibetrace.toml` (`vectors.dimensions = 256`) through to LanceDB table schemas.
* **Safe UTF-8 File Slicing (`services/graph_service.py`)**: Handled mixed-encoding or non-UTF-8 characters gracefully during surgical source code slicing without raising unhandled decode exceptions.

### Round 17: Multi-Language Error Recovery & Scoping
* **Tree-Sitter Syntax Error Recovery (`parsers/base.py`)**: Implemented tolerant AST node iteration skipping broken or unparseable syntax nodes so partially typed or edited files index valid symbols.
* **C++ Nested Namespace Resolution (`parsers/generic_parser.py`)**: Normalized multi-part C++ scoped names (`foo::bar::baz`) into canonical dot notation (`foo.bar.baz`).
* **Go Method Receiver Value vs. Pointer Normalization (`parsers/go_parser.py`)**: Unified pointer and value receivers (`(s *Service)` vs `(s Service)`) into consistent type scopes.

### Round 18: SQLite Busy Timeout Retries & Transaction Atomicity
* **SQLite Busy Timeout Backoff (`indexer/db_retry.py`)**: Wrapped database operations in exponential backoff retry loops on `sqlite3.OperationalError: database is locked`.
* **Atomic Batch Transaction Boundaries (`indexer/db.py`)**: Wrapped symbol and edge indexing in explicit transactional savepoints, rolling back partial states if a batch write fails.

### Round 19: Git Delta Copy/Rename Statuses & Call Tree Edge Cases
* **Git Delta Copy and Rename Parsing (`indexer/git_utils.py`)**: Handled complex status codes (`C100`, `R100`, `R095`) from `git diff --name-status` so copied or renamed files are tracked correctly as added/modified.
* **Call Tree Line-0 Normalization (`services/call_tree_service.py`)**: Guarded 1-indexed call-site line formatting against `line == 0` or missing line numbers.
* **JavaScript Default Export Resolution (`parsers/js_parser.py`)**: Resolved `export default function` and `export default class` references cleanly across CommonJS and ES module boundaries.

### Round 20: C++/Java/C# Heritage, Generic Type Stripping & Sibling Scoping
* **Heritage AST Extraction (`parsers/generic_parser.py`)**: Extracted inheritance from C++ `base_class_clause`, Java `super_interfaces`, and C# `base_list`, capturing `inherits` and `implements` relationships.
* **Generic Type Stripping (`parsers/generic_parser.py`)**: Stripped generic type parameters (`List<String>` -> `List`, `Repository<User>` -> `Repository`) before call target resolution to avoid phantom symbols.
* **Sibling Package Scoping (`parsers/generic_parser.py`)**: Automatically resolved same-directory sibling package classes for Java and C# without explicit import declarations.

### Round 21: Vector Store Auto-Purge & Transport Path Normalization
* **Vector Store Auto-Purge on Reindex (`indexer/db.py`)**: Wired `delete_file_embeddings` into `clear_file` and `batch_save_file_index` to prevent stale embeddings from persisting in LanceDB as ghost symbols.
* **FastAPI Transport Path Normalization (`server_api.py`)**: Applied `normalize_path` to `/source` and `/graph` query parameters so absolute paths (`I:/...`) and `./` resolve to relative database paths.

### Round 22: Multi-Language Code Slices, Reindex Embed Flag & Single-File Resilience
* **Multi-Language Markdown Code Slices (`mcp/formatters.py`)**: Expanded `_EXT_LANG_MAP` to include `.java`, `.c`, `.h`, `.cpp`, `.cc`, `.cxx`, `.hpp`, `.cs`, `.rb`, and `.php` syntax fences; guarded `(result.kind or "symbol")`.
* **MCP Reindex Vector Embeddings Option (`mcp/schemas.py`, `mcp/server.py`)**: Added `embed: bool = False` to `ReindexInput` and wired `vibetrace_reindex_project` to compute vector embeddings when requested.
* **Single-File Reindexing Resilience (`indexer/main.py`)**: Passed `failed=failed` error tracking to single-file indexing and registered newly indexed files into `ModuleResolver.add_file`.

### Round 23: Multi-Language Builtin Filtering & Map Room Lifespan Hardening
* **Multi-Language Standard Library Builtin Filtering (`parsers/builtins.py`, `parsers/base.py`)**: Centralized builtins for 10 languages and updated `_resolve_call_target` to filter dotted builtin calls (`System.out.println`, `Promise.resolve`, `Console.WriteLine`, `std.cout`), eliminating phantom call edges.
* **CLI `serve --path` Option Parity (`cli.py`, `cli_commands.py`)**: Added `--path` to `vibetrace serve` and set `VIBETRACE_PROJECT_ROOT`.
* **API Lifespan Watcher Hardening (`server_api.py`)**: Wrapped `start_observer` in `try-except` within `lifespan` so file-watcher failures on headless environments do not crash FastAPI.
* **MCP Signature Search Location Formatting (`mcp/server.py`)**: Guarded `start_line` formatting against `None` to prevent `:LNone` outputs.

### Round 24: Go Module & Cargo Crate Resolution & Git SHA-256 Support
* **Go Module Self-Package Resolution (`indexer/resolver.py`)**: Implemented `_detect_go_module()` to parse `go.mod` in workspace root and stripped module prefixes so fully qualified internal package imports (`github.com/org/app/pkg/auth`) resolve to local project files.
* **Cargo Crate Import Resolution (`indexer/resolver.py`)**: Implemented `_detect_cargo_package()` to parse crate names from `Cargo.toml` and resolve `crate_name::module` imports across libraries and tests.
* **Git SHA-256 (64-char) Repository Hash Support (`indexer/git_utils.py`)**: Extended `get_git_sha` validation to accept 64-character hexadecimal commit hashes from modern repositories initialized with `--object-format=sha256`.

---

## Non-Regression Policy & Guidelines
1. **Never Revert Approved Contracts**: Do not flip edge relation semantics or path normalization without cross-checking existing test files (`tests/test_fullstack_routes.py`, `tests/test_audit_code_bugs_*.py`).
2. **File Line Ceilings**: Keep all backend files strictly under 500 lines (Rules 10 & 12).
3. **No Mocks / Zero Stubs**: Every audit finding must be backed by a concrete, runnable test using the real parser, database, or Git CLI.
