# VibeTrace: The Code Intelligence & Execution Trace MCP

[![CI](https://github.com/serguei9090/VibeTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/serguei9090/VibeTrace/actions)
[![PyPI](https://img.shields.io/pypi/v/vibetrace.svg)](https://pypi.org/project/vibetrace/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)](pyproject.toml)

> **Philosophy**: Structural Truth > Statistical Guessing

**VibeTrace** is an intelligent code analysis and navigation engine that treats your codebase as a living execution nervous system. Instead of relying solely on probabilistic embedding models that miss call hierarchies or hallucinate links, VibeTrace builds a deterministic relational graph of your code using Tree-sitter AST extraction, SQLite FTS5 lexical ranking, and bidirectional BFS pathfinding with exact 1-indexed call-site line numbers.

Designed from the ground up for **AI Coding Agents** (Antigravity, Claude, Cursor, Cline) and developers working across massive codebases.

---

## ⚡ Key Capabilities

- **🚀 1-Click Workspace Setup (`vibetrace init`)**: Configures local `.gitignore` rules, bundles and installs the `vibetrace-navigator` agent skill, sets up an isolated project database, and performs initial indexing.
- **🗺️ Bidirectional BFS Tracepaths (`vibetrace trace`)**: Finds the shortest and alternative call chains between any two functions, classes, or HTTP endpoints with exact `file.py:L<line>` coordinates.
- **🔍 4-Stream Hybrid Search (`vibetrace search`)**: Reciprocal Rank Fusion (RRF) combining deterministic AST symbol matching, SQLite FTS5 BM25 lexical ranking, PageRank centrality, and optional 256d Matryoshka vectors (`EmbeddingGemma 2`).
- **✂️ Surgical Source Slicing (`vibetrace slice`)**: Returns only the exact lines of a symbol and its outgoing callee signatures, saving 85–95% of LLM context window tokens.
- **🌳 Top-Down Recursive Call Hierarchy (`vibetrace call-tree`)**: Generates an ASCII call tree from an entry point down to leaf database or system primitives with cycle detection.
- **💥 Transitive Blast Radius (`vibetrace impact`)**: 3-level caller impact analysis (Direct, Secondary, Deep) to evaluate the ripple effect before refactoring.
- **🤖 Native Model Context Protocol (`vibetrace mcp`)**: 10 purpose-built MCP tools that empower AI coding agents to navigate, trace, and inspect code surgically.
- **🌐 Map Room 2.0 Visualizer (`vibetrace serve`)**: Interactive `@xyflow/react` + ELK.js web graph visualizer with live agent radar tracking.

---

## 💻 Supported Languages

VibeTrace provides first-class multi-language AST extraction:

| Language | Extensions | Support Level | Features |
| :--- | :--- | :--- | :--- |
| **Python** | `.py` | 🔥 Full | Classes, Functions, Imports, Wildcards, Decorators, Visibility, Type hints |
| **JavaScript / TypeScript** | `.js`, `.jsx`, `.ts`, `.tsx` | 💎 High | ES Imports, CommonJS `require()` destructuring, Dynamic `import()`, Interfaces, Classes, JSX elements |
| **Go** | `.go` | ✅ Solid | Packages, Structs, Interfaces, Receivers, Constructor types, `var` specs |
| **Rust** | `.rs` | ✅ Solid | Crates, `mod item;` submodules, `mod.rs` barrel resolution, Traits, Impls |
| **Java** | `.java` | ✅ Solid | Packages, Classes, Interfaces, Method-local variable types, Call hierarchies |
| **C / C++** | `.c`, `.cpp`, `.h`, `.hpp` | 🛠️ Solid | Functions, Classes, Structs, Preprocessor includes |
| **C# / Ruby / PHP** | `.cs`, `.rb`, `.php` | 🛠️ Generic | Structural symbol extraction |

---

## 🚀 Quick Start

### Installation

#### Global Tool via `uv` (Recommended)
```bash
uv tool install vibetrace
```

#### Via `pip`
```bash
pip install vibetrace
```

#### Optional: Local Vector Embeddings (EmbeddingGemma 2 + sqlite-vec)
```bash
uv tool install "vibetrace[embeddings]"
```

---

### Initializing Any Project

Navigate to any codebase and run:

```bash
cd /path/to/my-project

# 1-click onboarding: updates .gitignore, installs agent skill, indexes codebase
vibetrace init
```

That's it! `vibetrace.db` is created locally inside that project and never shared across projects.

---

### Core CLI Usage

```bash
# 1. 4-Stream Hybrid Search
vibetrace search "authentication flow"

# 2. Bidirectional Execution Trace
vibetrace trace "handleLogin" "verifyPassword"

# Trace web route to backend service handler
vibetrace trace "GET /api/users" "queryUsersFromDB"

# Bookmark and re-run saved traces
vibetrace trace "handleLogin" "verifyPassword" --save-as "AuthFlow"
vibetrace trace --saved "AuthFlow"

# 3. Surgical Code Slicing (token-efficient)
vibetrace slice "AuthService.login"

# 4. Top-Down Recursive Call Tree
vibetrace call-tree "main" --depth 3

# 5. Blast Radius Impact Analysis
vibetrace impact src/auth/service.py

# 6. Launch Map Room Web Visualizer (http://localhost:8000)
vibetrace serve
```

---

## 🤖 Configuring AI Agents (Claude Desktop, Cursor, Antigravity, Cline)

Add VibeTrace to your agent's MCP configuration (`claude_desktop_config.json`, `antigravity.json`, or Cursor MCP settings):

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

The agent will automatically gain access to:
- `vibetrace_semantic_search`
- `vibetrace_trace_path`
- `vibetrace_get_code_slice`
- `vibetrace_impact_analysis`
- `vibetrace_find_references`
- `vibetrace_get_call_tree`

---

## 🛠️ Development & Contributing

```bash
git clone https://github.com/serguei9090/VibeTrace.git
cd VibeTrace

# Install dependencies
uv sync

# Run the full test suite (164 tests)
uv run pytest

# Lint and format
uv run ruff check .
uv run ruff format .
uv run pyright
```

---

## 📜 License

MIT License. Copyright (c) 2026 Serguei.
