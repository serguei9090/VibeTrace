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
- **⚡ 1-Shot Context Packing (`vibetrace bundle`)**: Combines relevant architecture docs, target code slices, callee contracts, caller blast radius, and sibling test patterns within a bounded token budget ($\le 1,500$ tokens) for zero-shot LLM completion.
- **🛡️ Pre-Flight Caller Guard (`vibetrace guard`)**: AST incoming caller inspection with exact call lines and blast-radius advisories to prevent breaking sibling callers before modifying function signatures.
- **🚦 Git Pre-Commit Gate (`vibetrace guard --staged`)**: Validates modified symbol signatures in staged git changes against active callers to prevent breaking commits (`--strict`).
- **⚖️ Architectural Split Audit & Code Triage (`vibetrace audit splits`)**: Automated detection of multi-module execution bifurcations with deterministic SHA-256 code hashing and version-controlled Git baselines (`.vibetrace/splits.toml`). Includes interactive terminal triage and non-interactive CI verification (`--ci`, `--strict`).
- **📚 Scoped Documentation RAG (`vibetrace docs`)**: Multi-catalog Markdown, PDF, and text ingestion with hybrid BM25 + vector RRF, isolated local vs global scoping, and explicit provenance tags (`[LOCAL:cat]`, `[GLOBAL:cat]`).
- **🗺️ Reachable Tracepaths & Multi-Route Analysis (`vibetrace trace`)**: Zero dead-end execution traces via recursive SQLite CTE reachability checks (`GET /api/reachable`), multi-path BFS finding alternative simple paths (`Route 1` vs `Route 2`), and in-path bifurcation markers.
- **🔍 4-Stream Hybrid Search (`vibetrace search`)**: Reciprocal Rank Fusion (RRF) combining deterministic AST symbol matching, SQLite FTS5 BM25 lexical ranking, PageRank centrality, and optional 256d Matryoshka vectors (`EmbeddingGemma 2`). Cross-queries code + docs via `--with-docs`.
- **✂️ Surgical Source Slicing (`vibetrace slice`)**: Returns only the exact lines of a symbol and its outgoing callee signatures, saving 85–95% of LLM context window tokens.
- **🌳 Top-Down Recursive Call Hierarchy (`vibetrace call-tree`)**: Generates an ASCII call tree from an entry point down to leaf database or system primitives with cycle detection.
- **💥 Transitive Blast Radius (`vibetrace impact`)**: 3-level caller impact analysis (Direct, Secondary, Deep) to evaluate the ripple effect before refactoring.
- **🛡️ Security Audit (`vibetrace audit security`)**: Offline AST scanner (CWE-22/78/89/95/327/502/798) plus lockfile CVE lookup via Google OSV.dev (24h SQLite cache, silent offline fallback), SonarQube-style remediation guidance, call-graph taint reachability (injection sinks reachable from endpoints/CLI are escalated, with the source-to-sink path), hash-tripwire Git baseline (`.vibetrace/security.toml`), Map Room 🛡️ badges and 1-click triage.
- **🤖 Native Model Context Protocol (`vibetrace mcp`)**: Purpose-built FastMCP tools that empower AI coding agents to navigate, trace, guard, inspect, and audit code splits and security findings surgically.
- **🌐 Map Room 2.0 Visualizer (`vibetrace serve`)**: Interactive `@xyflow/react` + ELK.js web visualizer with a 60 FPS Macro Clustered Package Graph (Option 1), live Caller Guard badges, in-browser Split Audit approval popovers, and Documentation RAG Explorer Drawer.

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
# 1. One-Shot Vibe Bundle (docs + code slice + caller guard + test fixture)
vibetrace bundle "add auth token to middleware" --budget 1500

# 2. Pre-Flight Caller Guard
vibetrace guard "upsert_node"
# Pre-commit staged changes blast-radius gate
vibetrace guard --staged --strict

# 3. Documentation RAG (local vs global scoping)
vibetrace docs catalog add core docs/
vibetrace docs index
vibetrace docs search "rate limiting" --scope local

# 4. 4-Stream Hybrid Search (with optional docs cross-query)
vibetrace search "authentication flow" --with-docs

# 5. Reachable Execution Trace & Multi-Route Analysis
vibetrace trace "handleLogin" "verifyPassword"
vibetrace trace "GET /api/users" "queryUsersFromDB"
vibetrace trace "handleLogin" "verifyPassword" --save-as "AuthFlow"
vibetrace trace --saved "AuthFlow"

# 6. Architectural Split Audit & Code Triage
vibetrace audit splits
# Interactive triage prompt to approve benign bifurcations into .vibetrace/splits.toml
vibetrace audit splits --approve
# CI/CD gate with deterministic hash tripwires (exits 1 if unapproved or modified)
vibetrace audit splits --ci --strict

# 6b. Security Audit (AST + Def-Use Taint + Auto-Syncing Rule Catalog + OSV CVEs)
vibetrace audit security
# CI gate; --skip-deps stays fully offline, --update forces CVE + rule catalog refresh
vibetrace audit security --ci --fail-on high --skip-deps
# Disable taint escalation; add custom sanitizers under [taint] sanitizers = ["fn"]
# or configure rule auto-sync TTL under [sync] rules_ttl_days = 7 in .vibetrace/security.toml
vibetrace audit security --no-taint
# Emit AI remediation prompts and compliant patch patterns
vibetrace audit security --fix
# Export OASIS SARIF 2.1.0 for GitHub Code Scanning, or batch-approve with disposition tags
vibetrace audit security --sarif
vibetrace audit security --approve --tag no-alternative --rationale "Verified safe"

# 7. Surgical Code Slicing (token-efficient)
vibetrace slice "AuthService.login"

# 8. Top-Down Recursive Call Tree
vibetrace call-tree "main" --depth 3

# 9. Blast Radius Impact Analysis
vibetrace impact src/auth/service.py

# 10. Launch Map Room Web Visualizer (http://localhost:8000)
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

The agent gains access to 20 surgical code intelligence tools:
- `vibetrace_get_vibe_bundle` (One-shot task context packed within token budget)
- `vibetrace_check_caller_guard` (Pre-flight caller inspection and blast-radius advisory)
- `vibetrace_audit_splits` (Scan codebase for execution bifurcations and check status against baseline)
- `vibetrace_approve_split` (Approve a split path into `.vibetrace/splits.toml` with SHA-256 hash)
- `vibetrace_search_docs` (Hybrid documentation RAG search with local/global scope)
- `vibetrace_list_doc_catalogs` (List configured documentation catalogs)
- `vibetrace_semantic_search` (4-stream hybrid AST + BM25 + Vector + PageRank)
- `vibetrace_trace_path` (Bidirectional reachable execution pathfinding with exact call lines)
- `vibetrace_get_code_slice` (Surgical source lines with outgoing callee contracts)
- `vibetrace_impact_analysis` (3-level transitive blast radius)
- `vibetrace_find_references` (Find all incoming calls and references)
- `vibetrace_get_call_tree` (Top-down recursive call tree hierarchy)
- `vibetrace_get_call_stack` (Bidirectional call stack analysis)
- `vibetrace_get_dependencies` (Categorize imports: internal/stdlib/third-party)
- `vibetrace_get_structural_summary` (File AST symbol layout)
- `vibetrace_search_by_signature` (Find functions matching specific signature patterns)
- `vibetrace_save_trace` / `vibetrace_list_saved_traces` (Saved trace bookmarks)
- `vibetrace_delete_saved_trace` (Delete saved trace bookmarks)
- `vibetrace_reindex_project` (Trigger incremental or full AST reindexing)

---

## ⚙️ Project Configuration (`.vibetrace.toml`)

VibeTrace projects are configured via an optional `.vibetrace.toml` at the project root (automatically generated when running `vibetrace init`):

```toml
# VibeTrace Configuration Contract

[project]
# Project identifier (used as partition discriminator key in central DBs)
name = "my-service"

[database]
# Storage backend: "sqlite" (default). Future enterprise: "postgres"
backend = "sqlite"
# Local path for SQLite (stored safely inside .vibetrace/)
path = ".vibetrace/vibetrace.db"
# Connection busy timeout in seconds
busy_timeout_seconds = 30.0

[vectors]
# Vector engine: "lancedb" (recommended), "sqlite-vec", or "none"
engine = "lancedb"
# Local directory or remote cloud URI (e.g. "s3://company-vectors/my-service")
uri = ".vibetrace/vectors.lance"
# Embedding model and Matryoshka dimension
model_id = "google/embeddinggemma-2"
dimensions = 256
batch_size = 32

[indexer]
# Working-tree Git-SHA delta debounce window (<20ms check)
debounce_seconds = 2.0
# Auto-sync working tree changes during queries
auto_sync = true
```

---

## 🗺️ Architectural Roadmap: Pluggable Storage & Enterprise Scale

VibeTrace uses a **Storage Adapter Interface** decoupling application logic, AST parsers, and FastMCP tools from the physical database engines:

```
┌────────────────────────────────────────────────────────────────────────┐
│  Presentation & Logic (CLI, MCP Tools, AST Parsers, Graph Traverser)  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (Calls IndexerDB & VectorStore)
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │             Storage Adapter Interface (DB)              │
       └──────────────┬───────────────────────────┬──────────────┘
                      │                           │
            [ Scenario 1: Hybrid ]       [ Scenario 2: Unified ]
                      │                           │
          ┌───────────┴──────────┐                │
          ▼                      ▼                ▼
     PostgreSQL               LanceDB         PostgreSQL
  (Relational Graph)      (Vector Embeddings) (Graph + pgvector)
  • Scalable SQL          • Serverless on S3  • Everything in 1
  • Centralized Pool      • Sub-ms ANN        • Single Cloud DB
```

### 1. Current Phase: Serverless In-Process (SQLite + LanceDB)
- **Zero background services**: In-process SQLite for the AST call graph and FTS5 BM25 search.
- **Embedded LanceDB**: Disk-backed 256d Matryoshka vector ANN search with $<50$MB RAM overhead and sub-millisecond cosine retrieval.

### 2. Enterprise Roadmap: PostgreSQL Storage Adapters
For centralized team servers, monorepos, and cloud multi-tenancy:
- **Hybrid PostgreSQL + LanceDB**: Swap SQLite with a PostgreSQL adapter (`backend = "postgres"` in `.vibetrace.toml`) while keeping LanceDB streaming from S3/GCS.
- **Unified PostgreSQL (`pgvector`)**: Store relational graph, AST entities, and vector embeddings in a single centralized PostgreSQL cluster with HNSW indices.
- **Zero Code Modification**: Because AST extraction and MCP tools interface through abstract storage contracts, transitioning to PostgreSQL requires updating only the connection config in `.vibetrace.toml`.

---

## 🛠️ Development & Contributing

```bash
git clone https://github.com/serguei9090/VibeTrace.git
cd VibeTrace

# Install dependencies
uv sync

# Run the full test suite (420+ tests across 56 audit rounds)
uv run pytest

# Lint and format
uv run ruff check .
uv run ruff format .
uv run pyright
```

---

## 📜 License

MIT License. Copyright (c) 2026 Serguei.
