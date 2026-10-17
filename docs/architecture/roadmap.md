# VibeGraph 10/10 Improvement Plan

This document outlines the roadmap to elevate VibeGraph's MCP tools to a perfect 10/10 score by addressing current blind spots in relational analysis, depth limitations, and metadata richness.

## 1. Core Engine: Intelligent Linker (The "Relational Fix")
The most critical bottleneck is the "External Module Blind Spot." Currently, the indexer treats all package-style imports (e.g., `import vibegraph.indexer.main`) as "external" even if they are local files.

### Implementation Details:
- **`ModuleResolver` Class**: Create a utility that:
    1.  Recursively maps the project directory to build a "Module Registry" (e.g., `{"vibegraph.indexer.db": "src/vibegraph/indexer/db.py"}`).
    2.  Handles common patterns like `src/` or `lib/` as root prefixes.
    3.  Resolves `from . import x` by checking the current file's directory.
- **Node ID Unification**: Ensure that `Parser._get_id("external", "vibegraph.db")` and `Parser._get_id("src/vibegraph/db.py", "db.py")` can be linked if they refer to the same logical entity.

---

## 2. Tool-Specific 10/10 Strategies

### 1. Structural Summary (Target: 10/10)
- **Problem**: Currently lacks "At-a-glance" complexity info.
- **10/10 Feature**: **Decorator & Annotation Support**. 
    - Parse Python decorators (`@mcp.tool`, `@property`).
    - Parse TypeScript/JavaScript `@jsdoc` or `@type` comments.
- **10/10 Feature**: **Visibility Filtering**. Highlight `export`ed members in TS/JS and non-underscore methods in Python.

### 2. Call Stack (Target: 10/10)
- **Problem**: Linear view is hard to follow for deep trees.
- **10/10 Feature**: **Breadcrumb Pathing**. Return the full path from root to current node in each line to preserve context during deep recursion.
- **10/10 Feature**: **Cycle Highlight**. Use distinct symbols or color-coding (if supported) to show where a recursive loop starts.

### 3. Impact Analysis (Target: 10/10)
- **Problem**: 1-level limit missed the "Ripple Effect."
- **10/10 Feature**: **Transitive Propagation**.
    - Level 1: Direct callers.
    - Level 2: Callers of callers (labeled "Secondary Impact").
    - Recursive stop at Level 3 to prevent token overflow.
- **10/10 Feature**: **File-Grouping**. Group impacts by directory to show which "sub-systems" are most affected.

### 4. Find References (Target: 10/10)
- **Problem**: Misses non-call references (e.g. variable usage).
- **10/10 Feature**: **Complete Symbol Table**. Index every identifier usage, not just function calls.
- **10/10 Feature**: **Type-Checking (Basic)**. If a variable `db` is typed as `IndexerDB`, link its method calls to the `IndexerDB` class.

### 5. Get Dependencies (Target: 10/10)
- **Problem**: Treats `os` and `vibegraph.db` the same.
- **10/10 Feature**: **Categorized Output**. 
    - `Internal`: Project files.
    - `StdLib`: Built-in modules.
    - `ThirdParty`: Pip/NPM packages.

### 6. Search by Signature (Target: 10/10)
- **Problem**: String matching is brittle.
- **10/10 Feature**: **Regex & Fuzzy Search**.
    - Support SQL `REGEXP` or `LIKE` with multiple wildcards.
    - Result ranking: Prioritize exact matches over partial ones.

---

## 3. Implementation Schedule

### Phase 1: Relational Integrity (The "Foundations")
- Update `IndexerDB` to support renaming/relinking nodes.
- Update `Parser` to resolve local modules during indexing.
- **Success Metric**: `vibegraph_impact_analysis` correctly identifies project-wide dependencies.

### Phase 2: Metadata & UI (The "Polish")
- Add decorators and visibility to `structural_summary`.
- Add categorized output to `get_dependencies`.
- **Success Metric**: All summaries include `@decorators`.

### Phase 3: Advanced Analysis (The "Intelligence")
- Implement transitive impact (multi-level).
- Implement breadcrumb pathing in call stacks.
- **Success Metric**: `vibegraph_get_call_stack` shows 3+ levels of clear, non-redundant paths.

---

## 4. Completed Architectural Enhancements (Competitor-Inspired Benchmarks)

The following 5 strategic capabilities have been fully implemented, integrated across SQLite, CLI, MCP, and Map Room UI, and validated with a 64-test automated pytest suite:

1. **PageRank & Graph Centrality Boost in Search (Stream 4 RRF)**:
   - In-degree log-normalized centrality scoring computed across graph relations (`calls`, `references`, `inherits`, `implements`, `imports`).
   - Integrated into a 4-stream Reciprocal Rank Fusion (RRF) search formula (`AST Exact + FTS5 BM25 + EmbeddingGemma 2 + Centrality Boost`), naturally surfacing foundational architectural symbols.
2. **Framework Route Edge Mapping (Routes → Handlers → Models)**:
   - Automated detection of web endpoints (FastAPI `@app.get`, `@router.post`, Flask `@app.route`).
   - Synthesizes `endpoint` nodes connected via `routes` edges to handler functions.
   - Distinct amber glow styling, filter toggles, and tracepath integration in Map Room 2.0.
3. **Git-SHA Incremental Delta Cache**:
   - Bi-temporal caching storing `last_indexed_git_sha` in a new SQLite `meta` table.
   - Detects added, modified, deleted, and untracked files via `git diff --name-status` and `git status --porcelain`.
   - Re-indexes only touched files in $<50\text{ms}$ on subsequent runs instead of full tree traversals.
4. **Saved Architectural Trace Queries (Bookmarks & Named Flows)**:
   - Persistent `saved_traces` database table.
   - CLI support: `vibegraph trace --save-as "<name>"`, `vibegraph trace --saved "<name>"`, `vibegraph trace --list-saved`.
   - REST API endpoints: `GET /api/saved-traces`, `POST /api/saved-traces`, `DELETE /api/saved-traces/{id}`, `GET /api/saved-traces/{name}/run`.
   - UI Bookmark selector, save prompt, and one-click deletion in Map Room `TracePathBar`.
5. **LSP Hybrid Bridge for Dynamic Types & Re-Exports**:
   - Preserves Tree-sitter's ultra-fast 0.28s parsing while providing an optional `--lsp` flag.
   - Spawns background JSON-RPC stdio language server (`pyright-langserver`, `typescript-language-server`, `rust-analyzer`, `gopls`).
   - Queries `textDocument/definition` to rewire unresolved dynamic calls and package re-exports to exact target AST definitions.
