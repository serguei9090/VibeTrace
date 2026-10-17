# VibeGraph 2.0 API, CLI & MCP Reference

## 1. Global Installation & Deployment (`uv tool`)

Install VibeGraph system-wide so that `vibegraph` and `vibegraph-mcp` are available globally across your machine:

```bash
# Global editable install (changes to local code reflect immediately without reinstalling)
uv tool install --force --python 3.12 -e .

# Or standard global installation
uv tool install --python 3.12 .
```

Verify global commands from any directory:
```bash
vibegraph --help
vibegraph-mcp --help
```

---

## 2. Unified CLI (`vibegraph`)

| Command | Description | Example |
| :--- | :--- | :--- |
| `vibegraph index [path]` | Incremental AST + FTS5 BM25 indexing (`--embed` for EmbeddingGemma 2 256d vectors, `--force` to rebuild all) | `vibegraph index . --embed` |
| `vibegraph search <query>` | 3-Stream Hybrid RRF search across AST, FTS5 BM25, and EmbeddingGemma 2 | `vibegraph search "upsert_node" --limit 10` |
| `vibegraph trace <from> <to>` | Bidirectional BFS execution tracepath with hop-by-hop `:L<call_line>` and divergent path detection | `vibegraph trace entry_func target_func` |
| `vibegraph call-tree <symbol>` | Top-down recursive call hierarchy with line coordinates and cycle detection | `vibegraph call-tree hybrid_search --depth 3` |
| `vibegraph slice <symbol>` | Surgical source code line slice (`start_line..end_line`) + outgoing callee contracts | `vibegraph slice IndexerDB.upsert_node` |
| `vibegraph impact <file>` | 3-level upstream blast-radius analysis (L1/L2/L3 tiers) | `vibegraph impact src/vibegraph/indexer/db.py` |
| `vibegraph summary <file>` | Structural symbol outline of a single file | `vibegraph summary src/vibegraph/mcp/server.py` |
| `vibegraph serve` | Start FastAPI + WebSocket Map Room backend (`127.0.0.1:8000`) | `vibegraph serve --port 8000` |

---

## 3. FastMCP Server (`vibegraph-mcp`)

Run directly via `stdio` (default for Claude Desktop / Cursor / Antigravity) or `--transport sse --port 8000`.

### Client Configuration (Claude Desktop / Cursor / Antigravity)
Add to `claude_desktop_config.json` or IDE MCP configuration:
```json
{
  "mcpServers": {
    "vibegraph": {
      "command": "vibegraph-mcp"
    }
  }
}
```

### Exposed MCP Tools

| Tool Name | Key Parameters | Functionality |
| :--- | :--- | :--- |
| `vibegraph_get_call_tree` | `symbol_name`, `max_depth`, `file_path` | Recursive top-down execution call tree with ASCII connectors, call-site lines, and cycle safety |
| `vibegraph_trace_execution_path` | `from_symbol`, `to_symbol`, `max_depth` | Bidirectional BFS shortest/divergent path finding with exact call sites |
| `vibegraph_get_code_slice` | `symbol_name`, `file_path`, `include_callees` | Exact line-accurate code slice + outgoing callee signatures in 1 round-trip |
| `vibegraph_search` | `query`, `limit`, `scope_path` | 3-stream Hybrid RRF search (AST + BM25 + EmbeddingGemma 2) |
| `vibegraph_get_impact` | `file_path` | Multi-level blast radius analysis (Direct L1, Secondary L2, Deep L3) |
| `vibegraph_get_structure` | `file_path` | Structural outline of classes, functions, visibility, and signatures |
| `vibegraph_find_references` | `symbol_name`, `scope_path` | Locate all incoming call-sites across the project |
| `vibegraph_get_file_tree` | `scope_path` | Directory and file layout of indexed project |
| `vibegraph_reindex_project` | `path` | Incremental AST refresh of workspace |

---

## 4. HTTP & WebSocket Endpoints (`src/vibegraph/server_api.py`)

- `GET /graph?file_path=<optional>`: Returns all indexed `nodes` and `edges`.
- `GET /trace?from_symbol=<str>&to_symbol=<str>&max_depth=6`: Runs Bidirectional BFS and returns `TracePathResult` JSON (`found`, `hops`, `paths`, `node_ids`).
- `GET /call-tree?symbol=<str>&depth=<int>`: Runs recursive call-tree builder and returns `CallTreeResult` JSON (`root_symbol`, `tree`, `total_nodes`, `node_ids`).
- `GET /source?file_path=<str>&start_line=<int>&end_line=<int>`: Safely reads a 1-indexed line slice within the project root (blocks path traversal outside project root with `HTTP 400`).
- `POST /api/agent-focus`: Accepts `AgentFocusPayload` and broadcasts live agent focus updates to connected Map Room WebSocket clients.
- `WS /ws`: Real-time WebSocket stream emitting `"refresh"` on file changes and `{"type": "agent_focus", ...}` when AI agents query VibeGraph.
