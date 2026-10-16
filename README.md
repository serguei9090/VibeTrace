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

### Unified `vibegraph` CLI

VibeGraph provides a unified CLI for both AI agents and developers:

```bash
# 1. Index your codebase (incremental SHA-256 content-hash check + FTS5 BM25)
uv run vibegraph index .

# Optional: Compute local 256d Matryoshka EmbeddingGemma 2 vectors
uv run vibegraph index . --embed

# 2. Hybrid 3-Stream RRF Search (AST exact/prefix + SQLite FTS5 BM25 + EmbeddingGemma 2)
uv run vibegraph search "upsert_node"

# 3. Bidirectional BFS Execution Tracepath with exact call-site line numbers
uv run vibegraph trace vibegraph_get_structural_summary format_structural_summary_md

# 4. Surgical Source Code Slice (exact start_line..end_line + outgoing callee contracts)
uv run vibegraph slice IndexerDB.upsert_node

# 5. Transitive 3-Level Blast Radius (Impact Analysis)
uv run vibegraph impact src/vibegraph/indexer/db.py

# 6. Start the FastAPI & WebSocket Map Room Server (http://localhost:8000)
uv run vibegraph serve
```

### Running the Web Map Room 2.0

```bash
bun run --cwd src/web dev
# Visualizer runs on http://localhost:5173
```

### Starting the MCP Server (Claude Code / Cursor / Antigravity)

```bash
uv run vibegraph-mcp
```

## MCP Tools (The Intelligence Layer)

VibeGraph exposes 10 **Model Context Protocol (MCP)** tools in `src/vibegraph/mcp/server.py`:

- **`vibegraph_semantic_search(query, limit, scope_path)`**: 3-stream **Reciprocal Rank Fusion (RRF)** combining deterministic AST symbol matching, SQLite FTS5 BM25 lexical ranking, and local `EmbeddingGemma 2` (256d Matryoshka) cosine similarity.
- **`vibegraph_trace_path(from_symbol, to_symbol, max_depth)`**: **Bidirectional BFS** execution tracepath finding the shortest call/reference chain between two symbols with exact `file_path:L<call_line>` hops.
- **`vibegraph_get_code_slice(symbol_name, file_path, include_callees)`**: Reads only the exact `start_line..end_line` of a symbol from disk plus compact signatures of its direct outgoing callees.
- **`vibegraph_get_structural_summary(file_path)`**: Returns all definitions in a file with visibility icons (`[f]`, `[c]`), scoped `qualified_name`, signatures, and decorators.
- **`vibegraph_get_call_stack(node_name, direction, depth)`**: Traces callers (`up`), callees (`down`), or `both` with breadcrumb paths (`A > B > C`).
- **`vibegraph_impact_analysis(file_path)`**: **Transitive Impact Analysis**. Returns Direct (L1), Secondary (L2), and Deep (L3) blast radius of changing a file.
- **`vibegraph_get_dependencies(file_path)`**: Categorizes dependencies into Internal modules, 3rd-party packages, and standard library.
- **`vibegraph_find_references(symbol_name, scope_path)`**: Finds all call-sites and references to a specific function or class across the project.
- **`vibegraph_search_by_signature(pattern, scope_path)`**: Searches node signatures using SQL `LIKE` patterns (e.g. `%IndexerDB%`).
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
└── tests/                    # Pytest suite (46 tests)
```

## License

MIT

