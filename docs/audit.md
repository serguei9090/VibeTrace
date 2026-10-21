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

---

## Non-Regression Policy & Guidelines
1. **Never Revert Approved Contracts**: Do not flip edge relation semantics or path normalization without cross-checking existing test files (`tests/test_fullstack_routes.py`, `tests/test_audit_code_bugs_*.py`).
2. **File Line Ceilings**: Keep all backend files strictly under 500 lines (Rules 10 & 12).
3. **No Mocks / Zero Stubs**: Every audit finding must be backed by a concrete, runnable test using the real parser, database, or Git CLI.
