# VibeTrace 2.0 API, CLI & MCP Reference

## 1. Global Installation & Toolchain (`uv tool`)

Install VibeTrace system-wide so that `vibetrace` is available globally across your machine:

```bash
# Global editable install (changes to local code reflect immediately without reinstalling)
uv tool install --force --python 3.12 -e .

# Or standard global installation
uv tool install --python 3.12 .
```

Verify the CLI from any directory:
```bash
vibetrace --help
```

---

## 2. Unified CLI Reference (`vibetrace`)

| Command | Subcommands / Flags | Description | Example |
| :--- | :--- | :--- | :--- |
| `vibetrace index` | `[path] [--embed] [--lsp] [--force]` | Incremental AST + FTS5 BM25 indexing (optional EmbeddingGemma 2 vectors and LSP type inference). | `vibetrace index . --embed` |
| `vibetrace search` | `<query> [--limit N] [--with-docs]` | 4-stream Hybrid RRF search across AST, BM25, PageRank, and vectors. Cross-queries docs with `--with-docs`. | `vibetrace search "upsert_node" --limit 10` |
| `vibetrace trace` | `<from> <to> [--save-as NAME] [--saved NAME] [--list-saved]` | Reachable BFS execution tracepath with hop-by-hop `:L<line>` call sites and multi-route bifurcation detection. | `vibetrace trace handleLogin verifyPassword` |
| `vibetrace audit` | `splits [--ci] [--strict] [--approve] [--json]` | Cross-module architectural split detection with deterministic SHA-256 baselines and CI tripwires. | `vibetrace audit splits --ci --strict` |
| `vibetrace guard` | `<symbol> [--staged] [--strict]` | Pre-flight caller inspection and blast-radius advisory. Git pre-commit mode via `--staged`. | `vibetrace guard upsert_node` |
| `vibetrace bundle` | `<query> [--budget TOKENS]` | Assembles 1-shot task context (docs + code slice + callee contracts + caller guard + sibling tests). | `vibetrace bundle "add auth middleware" --budget 1500` |
| `vibetrace docs` | `catalog {add,list,remove} \| index \| search` | Multi-catalog documentation RAG with local/global scoping and hybrid BM25 + vector search. | `vibetrace docs search "rate limiting" --scope local` |
| `vibetrace slice` | `<symbol> [--file PATH]` | Surgical source line extraction (`start..end`) + outgoing callee contracts for LLM context savings. | `vibetrace slice IndexerDB.upsert_node` |
| `vibetrace call-tree` | `<symbol> [--depth N]` | Top-down recursive call tree hierarchy with cycle detection and call lines. | `vibetrace call-tree hybrid_search --depth 3` |
| `vibetrace call-stack` | `<symbol> [--depth N]` | Bidirectional call stack exploration (both incoming callers and outgoing callees). | `vibetrace call-stack upsert_node` |
| `vibetrace impact` | `<file> [--depth N]` | 3-level blast radius impact analysis (Direct L1, Secondary L2, Deep L3). | `vibetrace impact src/vibetrace/db.py` |
| `vibetrace summary` | `<file>` | Structural symbol outline of a single file (classes, methods, functions). | `vibetrace summary src/vibetrace/mcp/server.py` |
| `vibetrace references` | `<symbol> [--scope PATH]` | Locate all incoming call sites and symbol references across the project. | `vibetrace references upsert_node` |
| `vibetrace dependencies` | `<file>` | Categorize imported modules (internal project, stdlib, third-party packages). | `vibetrace dependencies src/vibetrace/db.py` |
| `vibetrace serve` | `[--host HOST] [--port PORT]` | Launch the Map Room 2.0 visualizer and FastAPI + WebSocket daemon. | `vibetrace serve --port 8000` |
| `vibetrace init` | `[--force]` | 1-click workspace onboarding (`.gitignore`, `.vibetrace/`, agent skill installation). | `vibetrace init` |
| `vibetrace hooks` | `install \| remove \| status` | Configure Lefthook or native git post-commit and pre-commit automation. | `vibetrace hooks install` |

---

## 3. FastMCP Server Configuration (`vibetrace mcp`)

Run directly via `stdio` (default for Claude Desktop, Cursor, Antigravity, Cline) or `--transport sse --port 8000`.

### Client Configuration
Add to `claude_desktop_config.json`, `antigravity.json`, or Cursor MCP settings:
```json
{
  "mcpServers": {
    "vibetrace": {
      "command": "vibetrace",
      "args": ["mcp", "--path", "/path/to/your/project"]
    }
  }
}
```

### Complete MCP Tools Catalog (20 Tools)

| Tool Name | Key Parameters | Functionality |
| :--- | :--- | :--- |
| `vibetrace_get_vibe_bundle` | `query`, `budget`, `scope_path` | Packs relevant docs, code slices, callee contracts, caller guard, and test fixtures into a token-budgeted payload. |
| `vibetrace_check_caller_guard` | `symbol_name`, `staged`, `file_path`, `scope_path` | Pre-flight caller inspection with exact call-site line numbers and blast-radius advisory. Supports git staged diffs. |
| `vibetrace_audit_splits` | `scope_path`, `response_format` | Scans repository for cross-module execution splits and validates each against `.vibetrace/splits.toml` baseline. |
| `vibetrace_approve_split` | `caller_name`, `callee_a`, `callee_b`, `reason`, `scope_path` | Approves a split path into `.vibetrace/splits.toml` with normalized SHA-256 caller hash tripwire. |
| `vibetrace_trace_path` | `from_symbol`, `to_symbol`, `max_depth`, `include_divergent` | Bidirectional BFS shortest and alternative execution pathfinding with exact call-site coordinates. |
| `vibetrace_search_docs` | `query`, `catalog`, `scope`, `limit` | Hybrid RRF documentation search (.md, .txt, .pdf) across local project and global master catalogs. |
| `vibetrace_list_doc_catalogs` | `scope_path`, `response_format` | Lists registered local and global documentation catalogs with chunk counts and storage paths. |
| `vibetrace_semantic_search` | `query`, `limit`, `scope_path` | 4-stream Hybrid RRF search combining AST symbol matching, BM25, PageRank, and vector embeddings. |
| `vibetrace_get_code_slice` | `symbol_name`, `file_path`, `include_callees` | Exact line-accurate code slice (`start_line..end_line`) + outgoing callee contracts in 1 round-trip. |
| `vibetrace_impact_analysis` | `file_path`, `depth` | Transitive 3-level blast radius analysis (Direct L1, Secondary L2, Deep L3). |
| `vibetrace_find_references` | `symbol_name`, `scope_path` | Locate all incoming call sites and references to a symbol across the project. |
| `vibetrace_get_call_tree` | `symbol_name`, `max_depth`, `file_path` | Top-down recursive call tree hierarchy with ASCII connectors, call-site lines, and cycle safety. |
| `vibetrace_get_call_stack` | `symbol_name`, `depth`, `scope_path` | Bidirectional call stack exploration (callers above and callees below). |
| `vibetrace_get_dependencies` | `file_path`, `scope_path` | Categorize imports into internal project modules, standard library, and third-party packages. |
| `vibetrace_get_structural_summary` | `file_path`, `scope_path` | Structural outline of classes, functions, methods, visibility, and signatures in a file. |
| `vibetrace_search_by_signature` | `signature_pattern`, `scope_path` | Find symbols matching specific signature or parameter patterns. |
| `vibetrace_save_trace` | `name`, `from_symbol`, `to_symbol`, `description` | Bookmark an execution trace for instant retrieval. |
| `vibetrace_list_saved_traces` | `scope_path`, `response_format` | List all saved trace bookmarks. |
| `vibetrace_delete_saved_trace` | `name`, `scope_path` | Remove a saved trace bookmark. |
| `vibetrace_reindex_project` | `path`, `embed`, `force` | Incremental AST and vector refresh of the workspace. |

---

## 4. HTTP & WebSocket API (`src/vibetrace/server_api.py`)

### Core REST Endpoints

| Endpoint | Method | Parameters | Description |
| :--- | :--- | :--- | :--- |
| `/graph` | `GET` | `file_path?: string` | Returns all indexed `nodes` and `edges` for the Map Room visualizer. |
| `/trace` | `GET` | `from_symbol: string, to_symbol: string, max_depth?: int` | Bidirectional BFS trace with multi-route detection and call-site lines. |
| `/api/reachable` | `GET` | `from_symbol: string, max_depth?: int` | Recursive SQLite CTE querying all downstream reachable symbols from a source node to eliminate dead-end search. |
| `/api/splits/audit` | `GET` | _none_ | Scans graph for cross-module execution splits and validates against `.vibetrace/splits.toml` baseline. |
| `/api/splits/approve` | `POST` | `SplitApprovalRequest` body | Approves a split path into `.vibetrace/splits.toml` with normalized SHA-256 code hash. |
| `/api/reindex` | `POST` | `ReindexRequest` body | Triggers incremental or full codebase reindex via `ReindexService`. |
| `/call-tree` | `GET` | `symbol: string, depth?: int` | Returns top-down recursive call tree hierarchy JSON. |
| `/source` | `GET` | `file_path: string, start_line: int, end_line: int` | Safely reads line slice within project root (blocks traversal with `HTTP 400`). |
| `/api/agent-focus` | `POST` | `AgentFocusPayload` body | Broadcasts agent active focus nodes and files to connected Map Room clients. |

### Real-Time WebSocket (`WS /ws`)

- Emits `{"type": "refresh"}` when file system watcher detects code changes.
- Broadcasts `{"type": "agent_focus", ...}` when AI coding agents query symbols or execute tracepaths.
