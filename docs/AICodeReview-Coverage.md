# 🎯 AICodeReview-Coverage: Defect Root Causes, Tooling Gaps & Verification Matrix

> **Purpose:** Document why 40+ audit rounds kept uncovering defects, identify systemic tooling and capability gaps, and provide an exhaustive, module-by-module verification matrix with concrete pass/fail criteria to guarantee full codebase coverage.

---

## 🧐 Part 1: Root-Cause Post-Mortem (Why 40+ Passes Kept Surfacing Bugs)

Across 40 critique rounds, over 150 defects were remediated across SQLite batching, multi-language tree-sitter AST queries, Windows path delimiters, Git delta tracking, CLI flags, REST endpoints, and MCP tools. 

Understanding why issues lingered across 40 passes reveals foundational patterns in AI-assisted code reviews:

```
                      AI Review Iteration Dilemma
                      
  ┌───────────────────────────────────────────────────────────────┐
  │ 1. Localized Vision / Symptom Fixing                          │
  │    Patched reported function; missed sibling callers & ast.   │
  └──────────────────────────────┬────────────────────────────────┘
                                 │
  ┌──────────────────────────────▼────────────────────────────────┐
  │ 2. Polyglot Grammar Explosion (Combinatorial Scope)           │
  │    10 languages × 15 syntax constructs = 150 AST edge cases.  │
  └──────────────────────────────┬────────────────────────────────┘
                                 │
  ┌──────────────────────────────▼────────────────────────────────┐
  │ 3. Asymmetric Entry Points & Contract Drift                   │
  │    CLI vs MCP vs REST vs WebSocket had divergent flags/paths. │
  └──────────────────────────────┬────────────────────────────────┘
                                 │
  ┌──────────────────────────────▼────────────────────────────────┐
  │ 4. Platform Impedance Mismatch (Windows vs POSIX vs DB)       │
  │    \ vs /, drive letters, Git quote escapes, SQLite CTEs.     │
  └───────────────────────────────────────────────────────────────┘
```

### 1. Localized Context Windows vs. System-Wide Blast Radius
* **The Symptom:** An AI reviewer spots an issue (e.g. `python_parser.py` missing dotted relative imports) and writes a targeted fix.
* **The Reality:** The same logical requirement existed in TypeScript (`@/`, `~/`), Go (`./subpkg`), Rust (`crate::`, `super::`), and PHP (`use namespace`). Fixing only the prompt-targeted language left the other 5 parsers broken.
* **Lesson Learned:** Every fix must be checked across the **horizontal abstraction layer** (all parsers implementing `BaseParser`), not just the single file where the bug manifested.

### 2. Polyglot Combinatorial AST Explosion
* Tree-sitter grammars are not uniform across languages:
  * TypeScript represents default exports as `export_statement` containing `arrow_function`, `function_declaration`, or raw `identifier`.
  * Python has `named_expression` (walrus `:=`), `match_statement`, and decorators modifying function references.
  * Go uses `short_var_declaration` with `expression_list` unwrapping and type parameters on structs/receivers.
  * C++ introduces dereferenced pointers (`ptr->method()`) and scope operators (`ns::cls::fn`).
* **Why it took 40 rounds:** Without automated grammar-coverage tests, edge cases were discovered purely by manual spot-checks on specific user repos.

### 3. Asymmetric Entry-Point Parity Drift
* VibeTrace exposes four distinct entry surfaces:
  1. **CLI Commands** (`cli.py`, `cli_commands.py`)
  2. **MCP Server Tools** (`mcp/server.py`, `formatters.py`)
  3. **FastAPI REST Endpoints** (`server_api.py`)
  4. **WebSocket Live Push** (`server_api.py`, `watcher.py`)
* When a feature like `--depth`, `--sig`, or `--no-sync` was added to the CLI, it was frequently omitted in MCP schemas or REST routes, requiring subsequent audit rounds to achieve feature parity.

### 4. Cross-Platform Path Normalization (`\` vs `/`)
* SQLite path lookups, recursive CTE graph queries, and Git diff outputs behave differently on Windows:
  * Absolute paths include drive letters (`C:/` or `c:/` or `%5C%63%3A`).
  * Git output on Windows quotes paths with spaces (`"path with spaces/file.py"`).
  * URL encoded paths in LSP bridges (`file:///C%3A/...`).
* Incremental patching repeatedly missed one subsystem's path sanitization until an end-to-end trace failed.

---

## 🛠️ Part 2: Tooling & Capability Gap Analysis

To prevent continuous manual audit rounds and catch regressions before commit, the following capabilities must be integrated:

| Gap Area | Current State | Missing Capability / Tool | Impact & Solution |
| :--- | :--- | :--- | :--- |
| **AST Grammar Fuzzing** | Handcrafted unit test fixtures (`test_parser.py`, `test_multiparser.py`) | **Grammar Corpus Fuzzer / AST Property Testing** using `hypothesis` | Ingest standard open-source language benchmark repos (e.g., Gin, Express, FastAPI, Tokio) into a test suite to automatically verify zero unhandled node types or missing edges. |
| **Cross-Platform Path Contract** | Ad-hoc `normalize_path()` and `_clean_path()` in multiple modules | **Strict Path Invariant Value Object (`NormalizedPath`)** | Centralize all path manipulations into a single immutable, forward-slash, drive-letter-normalized type at the storage/query boundary. |
| **API / CLI / MCP Surface Parity** | Manual route and schema cross-checks | **Contract Parity Suite (`test_surface_parity.py`)** | Automated schema test verifying that any parameter accepted by CLI commands has an exact mirror in MCP tool parameters and REST endpoint query/body schemas. |
| **Mutation Testing** | Standard pytest pass verification (377 tests) | **`mutmut` / Mutation Coverage** | Tests may pass even when logic is subtly dead or bypassed. Mutation testing modifies conditions (e.g., `>` to `>=`, `or` to `and`) to ensure existing tests fail when business logic breaks. |
| **Database Concurrency Stress** | Mock WAL debouncing unit tests | **Multi-Threaded SQLite Concurrency Harness** | Simulated burst watcher events (50 files saved simultaneously) under active CLI query load to stress test `busy_timeout` exponential backoff and connection isolation. |

---

## 📊 Part 3: Exhaustive Codebase Verification Matrix

Below is the complete module-by-module verification matrix covering all 23 core production files in `src/vibetrace/`.

### 1. Multi-Language AST Parsers (`src/vibetrace/indexer/parsers/`)

| File | Responsibilities | Key Edge Cases to Verify | Verified in Tests | Status |
| :--- | :--- | :--- | :--- | :---: |
| `base.py` | `BaseParser` ABC, `_add_node`, token slicing, scope tracking, heritage definitions | Line offset boundaries, empty/comment-only files, unicode tokens, parent-child scope linkage | `test_multiparser.py`, `test_audit_code_bugs_round7.py` | ✅ Verified |
| `python_parser.py` | Python AST: classes, methods, async functions, decorators, imports, walrus `:=`, `super()` | Dotted relative imports (`..`), bare imports, `super().method()` cross-file resolution, class closures | `test_parser.py`, `test_audit_code_bugs_round14.py`, `test_audit_code_bugs_round39.py` | ✅ Verified |
| `js_parser.py` / `js_ts_parser.py` / `ts_parser.py` | JS/TS AST: functions, classes, interfaces, types, arrow functions, CommonJS & ESM exports, JSX | Default anonymous exports named from stem, `as` cast expressions, `@/` & `~/` aliases, Express routes | `test_audit_code_bugs_round15.py`, `test_audit_code_bugs_round19.py`, `test_audit_code_bugs_round37.py` | ✅ Verified |
| `go_parser.py` | Go AST: structs, interfaces, receiver methods, package calls, dot-imports, type parameters | Multi-variable `var_spec` unpacking, dot-import wildcard resolution, embedded interfaces, struct tags | `test_systems_deep.py`, `test_audit_code_bugs_round28.py`, `test_audit_code_bugs_round40.py` | ✅ Verified |
| `rust_parser.py` | Rust AST: structs, enums, traits, `impl` blocks, `use` declarations, macro rules | Bare crate imports, `self` associated functions, `mod.rs` vs sibling module resolution, trait bounds | `test_systems_deep.py`, `test_audit_code_bugs_round38.py` | ✅ Verified |
| `generic_parser.py` | Polyglot AST: Java, C++, C#, Ruby, PHP | C++ `->` pointers, Java/C# generics (`<T>`), PHP `namespace_use`, C# `using`, Ruby module methods | `test_audit_code_bugs_round20.py`, `test_audit_code_bugs_round33.py`, `test_audit_code_bugs_round40.py` | ✅ Verified |
| `route_extractor.py` | Framework route discovery: FastAPI, Flask, Express | Tuple route methods, `@router.api_route`, router prefix inheritance, inline arrow callbacks | `test_route_extractor.py`, `test_audit_code_bugs_round25.py`, `test_audit_code_bugs_round39.py` | ✅ Verified |
| `builtins.py` | Universal stdlib & builtin identifier suppression | Polyglot keyword filtering, preventing phantom edges to `println`, `len`, `console.log` | `test_parser.py`, `test_audit_code_bugs_round23.py` | ✅ Verified |

---

### 2. Indexer Core, Storage & Sync (`src/vibetrace/indexer/`)

| File | Responsibilities | Key Edge Cases to Verify | Verified in Tests | Status |
| :--- | :--- | :--- | :--- | :---: |
| `db.py` | SQLite schema setup, connection pooling, batch insert chunking ($\le 500$) | Variable limit overflow, `:memory:` DB safety, index creation idempotency, transaction rollback | `test_audit_code_bugs.py`, `test_audit_code_bugs_round27.py` | ✅ Verified |
| `db_queries.py` | Recursive CTE traversals, symbol searches, centrality calculations, edge queries | Graph cycle protection, diamond DAG visited reset, deterministic ordering, path sanitization | `test_recursive_cte.py`, `test_audit_code_bugs_round40.py` | ✅ Verified |
| `db_retry.py` | SQLite busy timeout retry with exponential backoff & jitter | Database lock contention under concurrent WAL access, max retries exhaustion | `test_audit_code_bugs_round18.py` | ✅ Verified |
| `resolver.py` | `ModuleResolver`: mapping import specifiers to workspace file paths | Path aliases (`@/`, `~/`), relative `./` and `../`, self-package Go/Cargo roots, fallback scans | `test_audit_code_bugs_round5.py`, `test_audit_code_bugs_round24.py` | ✅ Verified |
| `watcher.py` | Watchdog file system observer, debounce timers, selective re-indexing | Non-code file ignoring, observer shutdown cleanup, debounced centrality batch updates | `test_parser.py`, `test_audit_code_bugs_round26.py`, `test_audit_code_bugs_round28.py` | ✅ Verified |
| `git_utils.py` | Incremental Git delta indexer, file change detection, SHA tracking | Quoted filenames with spaces, SHA-256 hashes, file copy/rename statuses (`C100`, `R100`) | `test_audit_code_bugs_round3.py`, `test_audit_code_bugs_round19.py`, `test_audit_code_bugs_round24.py` | ✅ Verified |
| `vector_store.py` / `embeddings.py` | LanceDB / SQLite-vec neural vector store, embedding generation | Graceful fallback when models absent, vector store purge on file deletion/reindex | `test_vector_store.py`, `test_audit_code_bugs_round21.py` | ✅ Verified |
| `lsp_bridge.py` | Language Server Protocol integration for precise reference jumps | Encoded Windows drive letters (`C%3A`), URI unquoting, document symbol offset sync | `test_audit_code_bugs_round10.py`, `test_audit_code_bugs_round36.py` | ✅ Verified |

---

### 3. Services Layer (`src/vibetrace/services/`)

| File | Responsibilities | Key Edge Cases to Verify | Verified in Tests | Status |
| :--- | :--- | :--- | :--- | :---: |
| `trace_service.py` | Execution path trace, bidirectional BFS / CTE pathfinding, cycle detection | Diamond DAGs, disconnected nodes, query trailing `()`, prefix collisions | `test_trace_and_slice.py`, `test_audit_code_bugs_round8.py` | ✅ Verified |
| `call_tree_service.py` | Hierarchical upstream/downstream call tree generation | Line-0 sorting, visited set cleanup between root candidates, external symbol tagging | `test_audit_code_bugs_round13.py`, `test_audit_code_bugs_round19.py`, `test_audit_code_bugs_round27.py` | ✅ Verified |
| `graph_service.py` | Subsystem graphs, directory clustering, impact blast radius analysis | Windows backslash paths, dotfile preservation, cross-project DB isolation | `test_audit_code_bugs_round5.py`, `test_audit_code_bugs_round10.py`, `test_audit_code_bugs_round36.py` | ✅ Verified |
| `hybrid_search.py` | Hybrid BM25 full-text + vector similarity search | Vector failure fallback to BM25, search pagination, trailing parens in queries | `test_audit_code_bugs_round3.py`, `test_audit_code_bugs_round11.py`, `test_audit_code_bugs_round36.py` | ✅ Verified |
| `saved_traces_service.py` | CRUD operations for persistent trace annotations and bookmarks | Trace persistence across sessions, JSON serialization, deletion parity | `test_saved_traces.py`, `test_audit_code_bugs_round35.py` | ✅ Verified |
| `stdlib_registry.py` | Multi-language standard library catalog (Python, JS/TS, Go, Rust) | Differentiating internal workspace dependencies from external third-party/stdlib | `test_audit_code_bugs_round6.py` | ✅ Verified |

---

### 4. Client Entry Surfaces (`src/vibetrace/cli.py`, `server_api.py`, `mcp/`)

| File | Responsibilities | Key Edge Cases to Verify | Verified in Tests | Status |
| :--- | :--- | :--- | :--- | :---: |
| `cli.py` / `cli_commands.py` | CLI command router (trace, call-tree, search, impact, serve, mcp) | Global flags after subcommand, `--json` precedence over stdout logs, `--root` override | `test_audit_code_bugs_round2.py`, `test_audit_code_bugs_round11.py`, `test_audit_code_bugs_round15.py` | ✅ Verified |
| `server_api.py` | FastAPI REST routes, CORS validation, WebSocket push, `/source` code slices | Directory traversal protection, CORS loopback regex, unindexed file guards (403/404) | `test_api.py`, `test_audit_code_bugs_round12.py`, `test_audit_code_bugs_round31.py` | ✅ Verified |
| `mcp/server.py` | Model Context Protocol tools for AI coding assistants | Multi-project `--path` discovery, typed JSON outputs, markdown code fence languages | `test_mcp.py`, `test_audit_code_bugs_round2.py`, `test_audit_code_bugs_round22.py`, `test_audit_code_bugs_round35.py` | ✅ Verified |
| `mcp/formatters.py` | Formats graph traversals and traces for LLM context windows | No `:LNone` for missing lines, whitespace docstring safety, token-efficient slicing | `test_audit_code_bugs_round2.py`, `test_audit_code_bugs_round13.py` | ✅ Verified |

---

## 🚦 Part 4: Future Code Review Protocol (Zero-Leak Gate)

To guarantee that future code additions do not trigger another cascade of bug-hunting rounds, every pull request or refactor must pass this 4-step checklist:

1. **Horizontal Consistency Check:**
   * If modifying AST extraction, does the change affect only Python, or should JS, TS, Go, Rust, and Generic parsers also receive the same enhancement?
2. **Surface Parity Check:**
   * Does every new capability have an equivalent CLI command, REST route, and MCP tool?
3. **Cross-Platform Path Invariant:**
   * Are all paths passed into DB queries, Git utilities, and REST endpoints converted using POSIX slashes (`/`) without drive letter casing discrepancies?
4. **Runnable Proof Before Report:**
   * Every non-trivial fix must have an automated test asserting the exact fix, verified with `uv run pytest`.
