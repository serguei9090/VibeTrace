# VibeGraph: The Nervous System MCP
## This is a personal learning project created for educational purposes and to explore different code concepts with ai concepts. 

* **Status:** Personal sandbox / Portfolio piece.
* **License:** This project is open-source and available for public educational use under the MIT License.
* **Purpose:** Academic research and technical skill development.
* 
![CI](https://github.com/serguei9090/VibeGraph/actions/workflows/ci.yml/badge.svg)

> **Philosophy**: Structural Truth > Statistical Guessing

VibeGraph is a code intelligence system that treats your codebase as a **nervous system**. Instead of vector embeddings and similarity search, it uses Tree-sitter for AST parsing and SQLite for relational queries. The result? A structural graph that AI agents can query and you can visualize in real-time.

## Features

- **🧠 Structural Indexing**: Tree-sitter AST parsing for high-fidelity extraction of classes, functions, and types.
- **🛡️ Advanced Metadata**: Now indexes **Visibility** (public/private) and **Decorators** (e.g., `@mcp.tool`, `@property`).
- **🔍 MCP Server**: AI-queryable tools for deep code reasoning.
- **⚡ Transitive Impact Analysis**: Trace the "Blast Radius" of a change up to 3 levels deep.
- **🗺️ Map Room**: React Flow visualizer with live updates and force-directed layouts.
- **🧬 Breadcrumb Tracing**: Call stacks include full context paths (`A > B > C`), preventing confusion in deep recursion.
- **📦 Dependency Intelligence**: Automatically categorizes imports into **Internal**, **Third-Party**, and **Standard Library**.
- **🧪 Fully Tested**: Robust Pytest suite covering the parser, MCP tools, and API.

## Supported Languages

VibeGraph uses Tree-sitter for high-fidelity structural extraction. Support levels vary by language:

| Language | Extensions | Support Level | Extracted Features |
| :--- | :--- | :--- | :--- |
| **Python** | `.py` | 🔥 Full | Classes, Functions, Imports, Docstrings, **Decorators**, **Visibility** |
| **JavaScript/TS** | `.js`, `.ts`, `.tsx` | 💎 High | Classes, Functions, Interfaces, Inherits/Implements, **Decorators** |
| **Go** | `.go` | ✅ Solid | Structs, Interfaces, Functions, Methods |
| **Rust** | `.rs` | ✅ Solid | Structs, Traits, Impls, Functions |
| **Java** | `.java` | 🛠️ Generic | Classes, Methods, Inheritance (Base extraction) |
| **C / C++** | `.c`, `.cpp`, `.h` | 🛠️ Generic | Functions, Classes/Structs |
| **C# / Ruby / PHP**| `.cs`, `.rb`, `.php` | 🛠️ Generic | Basic Structural Nodes |

## Quick Start

### Prerequisites
- Python 3.11 or 3.12 (Python 3.13+ not yet supported due to `tree-sitter-languages` compatibility).
- Node.js 18+ (for frontend).
- [`uv`](https://github.com/astral-sh/uv) package manager.

### Installation

```bash
# Clone and navigate
git clone https://github.com/serguei9090/VibeGraph.git
cd VibeGraph

# Install Python dependencies (core AST + FTS5 BM25)
uv sync

# Optional: Install local EmbeddingGemma 2 + sqlite-vec semantic search extras
uv pip install -e ".[embeddings]"

# Install frontend dependencies (Bun + Biome + React Flow v12)
bun install --cwd src/web
```

### Global CLI & MCP Installation via `uv tool`

You can install VibeGraph system-wide so that `vibegraph` and `vibegraph-mcp` are available globally from any directory:

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

### Unified `vibegraph` CLI

VibeGraph provides a unified CLI for both AI agents and developers:

```bash
# 1. Index your codebase (Git-SHA delta cache + AST + FTS5 BM25 + Centrality Boost)
vibegraph index .

# Optional: Index with local 256d Matryoshka EmbeddingGemma 2 vectors
vibegraph index . --embed

# Optional: Enable LSP hybrid bridge for dynamic types and re-exports
vibegraph index . --lsp

# 2. Hybrid 4-Stream RRF Search (AST Exact + FTS5 BM25 + Vectors + Centrality Boost)
vibegraph search "upsert_node"

# 3. Bidirectional BFS Execution Tracepath with exact call-site line numbers
vibegraph trace vibegraph_get_structural_summary format_structural_summary_md

# Trace web endpoints to backend handlers (Framework Route Mapping)
vibegraph trace "GET /graph" get_graph

# Save and bookmark architectural tracepaths
vibegraph trace "GET /graph" get_graph --save-as "GraphEndpointFlow"
vibegraph trace --list-saved
vibegraph trace --saved "GraphEndpointFlow"

# 4. Top-Down Recursive Call-Tree Hierarchy with line coordinates and cycle detection
vibegraph call-tree hybrid_search --depth 3

# 5. Surgical Source Code Slice (exact start_line..end_line + outgoing callee contracts)
vibegraph slice IndexerDB.upsert_node

# 6. Transitive 3-Level Blast Radius (Impact Analysis)
vibegraph impact src/vibegraph/indexer/db.py

# 7. Automated Hook Management (Post-Commit / Post-Merge Indexing)
vibegraph hooks status
vibegraph hooks install
vibegraph hooks uninstall

# 8. Start the FastAPI & WebSocket Map Room Server (http://localhost:8000)
vibegraph serve
```

### Zero-Friction Indexing: Lazy JIT & Git Hooks

VibeGraph guarantees your index is always fresh without repetitive manual commands:
- **Lazy JIT Auto-Sync**: Queries (CLI and MCP) automatically check for uncommitted/dirty file edits using Git-SHA and file modification time deltas. Changes are incrementally re-indexed on the fly in $<20\text{ms}$ before queries execute (pass `--no-sync` to disable).
- **Automated Post-Commit / Post-Merge Hooks**: Run `vibegraph hooks install` to automatically detect and append background indexing to Lefthook (`lefthook.yml`) or native Git hooks (`.git/hooks/`). This cleanly updates the local index without polluting commit history or causing merge conflicts.


### Running the Web Map Room 2.0

```bash
bun run --cwd src/web dev
# Visualizer runs on http://localhost:5173
```

### Starting the MCP Server (Claude Desktop / Cursor / Antigravity)

VibeGraph includes a FastMCP server running over `stdio` or `sse`.

**Connecting to Claude Desktop / Cursor / Antigravity:**
Add VibeGraph to your `claude_desktop_config.json` or IDE MCP settings:
```json
{
  "mcpServers": {
    "vibegraph": {
      "command": "vibegraph-mcp"
    }
  }
}
```

Or without installing globally:
```json
{
  "mcpServers": {
    "vibegraph": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/VibeGraph", "vibegraph-mcp"]
    }
  }
}
```

## MCP Tools (The Intelligence Layer)

VibeGraph exposes 10 **Model Context Protocol (MCP)** tools in `src/vibegraph/mcp/server.py`:

- **`vibegraph_get_call_tree(symbol_name, max_depth, file_path)`**: **Top-Down Recursive Call Hierarchy**. Explores direct and nested callees from an entry point down to leaf database or system primitives with cycle-safe ASCII connectors and exact call-site line numbers.
- **`vibegraph_trace_execution_path(from_symbol, to_symbol, max_depth)`**: **Bidirectional BFS** execution tracepath finding shortest and divergent call chains between two symbols with exact `file_path:L<call_line>` hops.
- **`vibegraph_get_code_slice(symbol_name, file_path, include_callees)`**: Reads only the exact `start_line..end_line` of a symbol from disk plus compact signatures of its direct outgoing callees in a single round-trip.
- **`vibegraph_search(query, limit, scope_path)`**: 3-stream **Reciprocal Rank Fusion (RRF)** combining deterministic AST symbol matching, SQLite FTS5 BM25 lexical ranking, and local `EmbeddingGemma 2` (256d Matryoshka) cosine similarity.
- **`vibegraph_get_impact(file_path)`**: **Transitive Impact Analysis**. Returns Direct (L1), Secondary (L2), and Deep (L3) blast radius of changing a file with dependency risk scoring.
- **`vibegraph_get_structure(file_path)`**: Returns all definitions in a file with visibility icons (`[f]`, `[c]`), scoped `qualified_name`, signatures, and decorators.
- **`vibegraph_find_references(symbol_name, scope_path)`**: Finds all call-sites and references to a specific function or class across the project.
- **`vibegraph_get_file_tree(scope_path)`**: Lists all indexed files organized by directory.
- **`vibegraph_reindex_project(path)`**: Triggers an incremental refresh of the index for a directory or file.

## Map Room 2.0 (`@xyflow/react` v12 + ELK.js)

The Web Visualizer (`src/web/`) features 3 switchable ELK layout engines and live AI Agent telemetry:

### Switchable Layout Modes
- **Architecture Map (`rectpacking`)**: Hierarchical District $\rightarrow$ File $\rightarrow$ Symbol compound node packing.
- **Tracepath LR (`layered` RIGHT)**: Left-to-right execution pipeline with `#1, #2, ...` hop badges and `:L<call_line>` edge labels.
- **Blast Radius (`layered` DOWN)**: Tiered L0 $\rightarrow$ L1 $\rightarrow$ L2 $\rightarrow$ L3 upstream impact tree.

### Interactive Drawers & Live Agent Radar
- **Tracepath Bar (`TracePathBar.tsx`)**: Pick any two symbols with autocomplete to isolate and animate the shortest execution path between them.
- **Code Inspector Drawer (`CodeInspectorDrawer.tsx`)**: Click any symbol node or call edge to view its exact source code slice (`start_line..end_line`), docstring, and highlighted `call_line`.
- **Follow AI Agent (`AgentRadarBadge.tsx`)**: Live WebSocket sync (`/api/agent-focus`) that automatically highlights symbols and tracepaths as your AI agent queries the codebase via CLI or MCP.

## Project Structure

```text
VibeGraph/
├── .agents/skills/
│   ├── mcp-builder/          # MCP server authoring skill
│   └── vibegraph-navigator/  # Surgical code navigation & tracepath skill
├── docs/
│   ├── api/                  # CLI & MCP tool reference
│   ├── architecture/         # System roadmap & architecture docs
│   └── guides/               # Developer guides
├── src/vibegraph/
│   ├── cli.py                # Unified vibegraph CLI
│   ├── indexer/              # Modular Tree-sitter parsers, FTS5, & EmbeddingGemma 2
│   ├── mcp/                  # MCP Server tools, Pydantic V2 schemas, & formatters
│   ├── services/             # Graph traversal, Hybrid RRF search, & Trace/Slice services
│   └── server_api.py         # FastAPI + WebSocket backend (/graph, /trace, /source, /api/agent-focus)
├── src/web/                  # React 19 + @xyflow/react v12 + ELK.js + Biome frontend
└── tests/                    # Pytest suite (51 tests)
```

## License

MIT

