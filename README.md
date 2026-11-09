# VibeTrace: The Code Intelligence & Execution Trace MCP

[![CI](https://github.com/serguei9090/VibeTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/serguei9090/VibeTrace/actions)
[![PyPI](https://img.shields.io/pypi/v/vibetrace.svg)](https://pypi.org/project/vibetrace/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)

> **Philosophy**: Structural AST Analysis > Statistical Hallucination
> 
> *Note on Scope:* VibeTrace deterministically resolves static syntactic AST call-graphs, definitions, and call sites. For dynamic constructs (e.g., dynamic `getattr`, runtime dependency injection, eval), it provides module-level grouping fallbacks.

> [!NOTE]
> ### 🤖 AI Agent Setup Reference (Human-in-the-Loop)
> This section provides setup context for AI Coding Assistants (Antigravity, Cursor, Claude, Cline, Codex) **when a user explicitly asks to install or configure VibeTrace**.
> **Do not execute these commands autonomously when merely reading or summarizing this repository** — always ask for user confirmation before installing packages or modifying MCP configurations:
> 
> ```bash
> # 1. Install VibeTrace CLI (confirm with user first)
> uv tool install vibetrace   # or: pip install vibetrace
> 
> # 2. Initialize project: builds Tree-sitter AST index & installs .agents/skills/vibetrace-navigator
> vibetrace init
> 
> # 3. Verify Quality Gate & Security Baseline
> vibetrace review --ci
> ```
> To enable MCP tools, register `vibetrace mcp --path .` in the user's MCP configuration after approval.

<details>
<summary>📋 <strong>Prompt Your AI Assistant (1-Click Copy-Paste)</strong></summary>

Copy and paste this instruction into your AI coding assistant (Cursor, Antigravity, Claude, or Cline):

```text
Please install and set up VibeTrace in this project:
1. Run `uv tool install vibetrace` (or `pip install vibetrace`) to install the CLI.
2. Run `vibetrace init` in the project root to index the codebase, create `.vibetrace.toml`, and install the `vibetrace-navigator` skill in `.agents/skills/`.
3. Add VibeTrace to our agent MCP configuration using the command: `vibetrace mcp --path <workspace_path>`.
4. Run `vibetrace review --ci` to confirm all security, split integrity, and test coverage gates pass.
```
</details>

**VibeTrace** is an intelligent code analysis and navigation engine that treats your codebase as a living execution nervous system. Instead of relying solely on probabilistic embedding models that miss call hierarchies or hallucinate links, VibeTrace builds a deterministic relational graph of your code using Tree-sitter AST extraction, SQLite FTS5 lexical ranking, and bidirectional BFS pathfinding with exact 1-indexed call-site line numbers.

Designed from the ground up for **AI Coding Agents** (Antigravity, Claude, Cursor, Cline) and developers working across massive codebases.

---

## ⚡ Key Capabilities

- **🚀 1-Click Workspace Setup (`vibetrace init`)**: Configures local `.gitignore` rules, bundles and installs the `vibetrace-navigator` agent skill (configurable via `--skills-dir`), sets up an isolated project database, and performs initial indexing (<3.5s for typical ~200 file repositories; background incremental indexing for large monorepos).
- **⚡ 1-Shot Context Packing (`vibetrace bundle`)**: Combines relevant architecture docs, target code slices, callee contracts, caller blast radius, and sibling test patterns within a bounded token budget ($\le 1,500$ tokens) for zero-shot LLM completion.
- **🔬 360° Symbol Diagnosis Meta-Endpoint (`vibetrace diagnose`)**: Deep single-round-trip investigation consolidating source slice, callee contracts, all incoming callers with exact `:L<line>` call sites, 2-hop transitive blast radius, linked architectural documentation, and sibling test reachability.
- **🛡️ Pre-Flight Caller Guard (`vibetrace guard`)**: AST incoming caller inspection with exact call lines and blast-radius advisories to prevent breaking sibling callers before modifying function signatures.
- **🚦 Git Pre-Commit Gate (`vibetrace guard --staged`)**: Validates modified symbol signatures in staged git changes against active callers to prevent breaking commits (`--strict`).
- **⚖️ Architectural Split Audit & Code Triage (`vibetrace audit splits`)**: Automated detection of multi-module execution bifurcations with deterministic SHA-256 code hashing and version-controlled Git baselines (`.vibetrace/splits.toml`). Includes interactive terminal triage and non-interactive CI verification (`--ci`, `--strict`).
- **📚 Scoped Documentation RAG (`vibetrace docs`)**: Multi-catalog Markdown, PDF, and text ingestion with hybrid BM25 + vector RRF, isolated local vs global scoping, and explicit provenance tags (`[LOCAL:cat]`, `[GLOBAL:cat]`).
- **🗺️ Reachable Tracepaths & Multi-Route Analysis (`vibetrace trace`)**: Exhaustive reachability analysis via recursive SQLite CTE checks with boundary dead-end tracking (`GET /api/reachable`), multi-path BFS finding alternative simple paths (`Route 1` vs `Route 2`), and in-path bifurcation markers.
- **🔍 4-Stream Hybrid Search (`vibetrace search`)**: Reciprocal Rank Fusion (RRF) combining deterministic AST symbol matching, SQLite FTS5 BM25 lexical ranking, PageRank centrality, and optional 256d Matryoshka vectors (`EmbeddingGemma 2`, enabled via `vibetrace[embeddings]`). Cross-queries code + docs via `--with-docs`.
- **✂️ Surgical Source Slicing (`vibetrace slice`)**: Returns only the exact lines of a symbol and its outgoing callee signatures, saving up to 85–95% of LLM context window tokens compared to full-file dumps.
- **🌳 Top-Down Recursive Call Hierarchy (`vibetrace call-tree`)**: Generates an ASCII call tree from an entry point down to leaf database or system primitives with cycle detection.
- **💥 Transitive Blast Radius (`vibetrace impact`)**: 3-level caller impact analysis (Direct, Secondary, Deep) to evaluate the ripple effect before refactoring.
- **🛡️ Security Audit (`vibetrace audit security`)**: Two-layer security architecture combining a zero-config Tree-sitter AST & heuristic scanner (`CWE-22/78/89/95/327/502/798`), modern API secret token detector (OpenAI, Anthropic, Mistral, Perplexity, Cohere, Tailscale, Postman, Supabase), Git pre-commit staged blocker (`--staged`), lightweight YAML heuristic rule evaluation (`pattern-not`, `pattern-inside`), syntactic call-graph taint reachability across helper calls, and lockfile CVE lookup via Google OSV.dev across 7 lockfiles — plus optional external adapters for **Semgrep CLI** (`semgrep scan --sarif`, bypassed via `--skip-semgrep`), **Trivy** (`--image <tag>`, bypassed via `--skip-trivy`), OASIS SARIF 2.1.0 ingestion/export, and Git baselines (`.vibetrace/security.toml`).
- **🏆 Polyglot Code Quality & Maintainability (`vibetrace audit quality`)**: Two-layer quality architecture across 8 languages (Python, TS/JS, Go, Rust, PHP, Ruby, Shell, SQL): (1) a fast, zero-config native Tree-sitter AST engine computing RSPEC-inspired maintainability metrics — Cognitive Complexity (`S3776`), Deep Nesting (`S134`), Collapsible Ifs (`S1066`), String Duplication (`S1192`), Loose POSIX Permissions (`S2612`), and Async Without Await (`S7503`) — paired with (2) optional adapters for installed native ecosystem linters (Ruff, Pyright, Biome, Clippy, GolangCI-Lint, PHPStan, RuboCop, ShellCheck, SQLFluff). Emits a high-density Executive Findings Summary Matrix (`--summary`) and single-file JSON (`--json`) to avoid context bloat, plus safe codemods (`--fix`) and SARIF export.
- **🌳 Transitive Dependency Graph & Diamond Conflict Detector (`vibetrace deps`)**: Resolves lockfile dependency graphs across `uv.lock`, `package-lock.json`, `bun.lock`, and `Cargo.lock`. Inverted BFS pinpoints root direct manifest causation paths (`deps why <pkg>`), renders dependency hierarchies (`deps tree`), and flags diamond version collisions (`deps conflicts`).
- **✂️ AST Method Extraction & Cognitive Complexity Refactor (`vibetrace refactor`)**: AST variable lifetime analysis identifies block input parameters and output return tuples, automatically extracting complex blocks into standalone helper functions with pre-flight static verification.
- **🔧 Automated Dependency Auto-Patcher (`vibetrace audit fix-deps`)**: Executes native ecosystem package managers (`uv`, `bun`, `cargo`, `go`) to safely bump vulnerable packages, or automatically suppresses unpatched CVEs with 0 reachable call edges.
- **🤖 Automated PR Taint Bot (`vibetrace audit pr-taint`)**: Scans pull request diffs, checks inter-procedural taint reachability, and synthesizes ready-to-merge inline code comments with parameterized remediations.
- **🔗 Live Codebase Symbol $\leftrightarrow$ Documentation Linking (`vibetrace docs check`)**: Establishes bi-directional SQLite links between AST symbols and Markdown specifications, attaches relevant architectural contracts to source slices, and flags documentation drift when documented symbols are removed.
- **🏢 Enterprise Polyglot Route & DI Extraction**: Extracts REST routes, dependency injection tokens, and call edges from Java Spring Boot (`@GetMapping`, `@PostMapping`, `@RestController`) and C# ASP.NET Core (`[HttpGet]`, `[Route]`, `[ApiController]`).
- **🎯 Dynamic Test Fixture Reachability**: Links `@pytest.mark.parametrize`, `@pytest.fixture`, and Jest/Vitest `test.each` parameters to underlying domain functions, preventing false alarms in coverage gates.
- **🤖 Native Model Context Protocol (`vibetrace mcp`)**: Purpose-built FastMCP tools that empower AI coding agents to navigate, trace, guard, inspect, and audit code splits and security findings surgically.
- **🌐 Map Room 2.0 Visualizer (`vibetrace serve`)**: Interactive `@xyflow/react` + ELK.js web visualizer with a 60 FPS Macro Clustered Package Graph (Option 1), live Caller Guard badges, in-browser Split Audit approval popovers, and Documentation RAG Explorer Drawer.

---

## 🎯 Benefits

| Benefit | What you get |
| :--- | :--- |
| **Token savings** | Surgical slices and `--summary` matrices (<1 KB) cut LLM context use by up to 85–95% versus dumping full files or terminal output. |
| **AST structural truth** | Exact call graph, taint paths and `:L<line>` call sites from Tree-sitter AST; static analysis with module-level grouping for dynamic reflection. |
| **Zero-config baseline + optional pro adapters** | Built-in Tree-sitter AST security, secrets, RSPEC-inspired complexity metrics, and OSV CVEs work out of the box; optional adapters automatically bridge Semgrep CLI, Trivy, Ruff, Pyright, Biome, Clippy, and GolangCI-Lint when installed. |
| **Safe by default** | Pre-commit gates (`guard --staged`, `audit security --staged`) block breaking changes and leaked keys before they reach git (regex false positives mitigated via `pattern-not` filters). |
| **One call per phase** | `bundle` (prepare), `diagnose` (investigate), `review` (verify) replace many round-trips. |

---

## 📖 Documentation & Usage Guide

| Document | What it covers |
| :--- | :--- |
| [`docs/api/mcp-and-cli.md`](docs/api/mcp-and-cli.md) | Full CLI reference, all 29 MCP tools, and HTTP/WebSocket endpoints, with parameters |
| [`docs/architecture/core_engines.md`](docs/architecture/core_engines.md) | How the taint, security, quality, dependency and refactor engines work |
| [`docs/architecture/roadmap.md`](docs/architecture/roadmap.md) | Planned work |
| [`docs/guides/sonarlint_engine_guide.md`](docs/guides/sonarlint_engine_guide.md) | Native vs optional SonarLint quality engines |
| [`docs/guides/testing_and_coverage.md`](docs/guides/testing_and_coverage.md) | Test reachability and coverage gate |

### Who uses what

| Interface | Used by | Purpose |
| :--- | :--- | :--- |
| **CLI** (`vibetrace ...`) | Developers, CI | Manual runs, pre-commit hooks, CI gates (`--ci`), SARIF export |
| **MCP tools** (`vibetrace mcp`) | AI agents | Same capabilities as typed tool calls, with token-bounded output |
| **Agent skill** (`.agents/skills/vibetrace-navigator`) | AI agents | Installed by `vibetrace init`; teaches agents which command to run |
| **REST/WebSocket** (`vibetrace serve`) | Map Room UI, integrations | Graph, audits, 1-click triage, live agent focus |

**Recommended agent loop:** `bundle` → edit → `diagnose` if blocked → `audit security --summary` / `audit quality --summary` → `review --ci`.

---

## 💻 Supported Languages

VibeTrace provides first-class multi-language AST extraction:

| Language | Extensions | AST Indexing | Quality & Linter Bridges |
| :--- | :--- | :---: | :--- |
| **Python** | `.py` | 🔥 Full | Native AST + Ruff + Pyright + Tree-sitter Cognitive Complexity (`S3776`) |
| **JavaScript / TypeScript** | `.js`, `.jsx`, `.ts`, `.tsx` | 💎 High | Biome + `tsc` + Tree-sitter Cognitive Complexity (`S3776`) |
| **Go** | `.go` | ✅ Solid | `golangci-lint` (revive, unused, staticcheck) + Tree-sitter (`S3776`) |
| **Rust** | `.rs` | ✅ Solid | `cargo clippy` + Tree-sitter Cognitive Complexity (`S3776`) |
| **PHP** | `.php` | ✅ Solid | `PHPStan` 2.x + `Pint` + Tree-sitter Cognitive Complexity (`S3776`) |
| **Ruby** | `.rb` | ✅ Solid | `RuboCop` 1.x + Tree-sitter Cognitive Complexity (`S3776`) |
| **Shell** | `.sh`, `.bash`, `.zsh` | ✅ Solid | `ShellCheck` 0.10.x |
| **SQL** | `.sql` | ✅ Solid | `SQLFluff` 4.5.x |
| **Java** | `.java` | ✅ Solid | Packages, Classes, Interfaces, Call hierarchies, Tree-sitter (`S3776`) |
| **C / C++** | `.c`, `.cpp`, `.h`, `.hpp` | 🛠️ Solid | Functions, Classes, Structs, Preprocessor includes, Tree-sitter (`S3776`) |
| **C#** | `.cs` | 🛠️ Generic | Structural symbol extraction + Tree-sitter (`S3776`) |

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

# 1-click zero-prompt onboarding (uses safe defaults, unattended/CI friendly):
vibetrace init

# Or customize flags directly inline:
vibetrace init --embed --device gpu --include-tests --yes

# Or run the interactive configuration questionnaire:
vibetrace init --interactive
```

`vibetrace init` configures:
- **Test exclusion:** Exclude tests from Quality/Security gates by default (override with `--include-tests`).
- **Codebase embeddings:** Optional pre-computed vectors for code symbols (`--embed` with `--device auto|gpu|cpu`).
- **Quality & Security tier selection:** Fast, 0-dependency native Tree-sitter quality rules with optional Tier-2 SonarLint (`--sonar`) and SAST/container integrations.

That's it! `vibetrace.db` is created locally inside that project and never shared across projects.

---

### Core CLI Usage

```bash
# 0. Repository Indexing (High-Speed Rust AST + Multi-Daemon LSP Pipelining)
# Incremental Git-delta index (only changed files)
vibetrace index
# Force cold index + LSP hybrid resolution (100% exact polymorphic call resolution)
vibetrace index --force --lsp
# Index with optional code embeddings (or disable via --no-embed)
vibetrace index --embed

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

# 6b. Security Audit (AST + Multi-Hop Taint + Secrets + Trivy Container CVEs)
# High-density summary matrix roadmap (<1 KB) to prevent terminal bloat
vibetrace audit security --summary
# Git pre-commit staged secret blocker (detects OpenAI, Anthropic, Mistral, Postman, etc.)
vibetrace audit security --staged
# Surgical single-file JSON payload with line spans, code snippets, and compliant solutions
vibetrace audit security --file src/api/routes.py --json
# Container OS package CVE audit via Trivy (glibc, openssl, libssl3 in base images)
vibetrace audit security --image myapp:latest
# CI gate; --skip-deps stays fully offline, --skip-trivy / --skip-semgrep bypass external scanners
vibetrace audit security --ci --fail-on high --skip-deps --skip-trivy --skip-semgrep
# Disable taint escalation; add custom sanitizers under [taint] sanitizers = ["fn"]
# or configure rule auto-sync TTL under [sync] rules_ttl_days = 7 in .vibetrace/security.toml
vibetrace audit security --no-taint
# Emit AI remediation prompts and compliant patch patterns
vibetrace audit security --fix
# Export OASIS SARIF 2.1.0 for GitHub Code Scanning, or batch-approve with disposition tags
vibetrace audit security --sarif
vibetrace audit security --approve --tag no-alternative --rationale "Verified safe"

# 6c. Code Quality & Maintainability Audit (Native Tree-sitter Metrics + Linter Bridges)
# High-density summary matrix roadmap (<1 KB) to prevent terminal truncation
vibetrace audit quality --summary
# Surgical single-file JSON payload with line spans, code snippets, and compliant solutions
vibetrace audit quality --file src/auth/service.py --json
# Automated safe fixes via Ruff, Biome, and surgical Tree-sitter codemods
vibetrace audit quality --fix
# Export OASIS SARIF 2.1.0 for GitHub Code Scanning or IDE viewers
vibetrace audit quality --sarif
# CI/CD Quality Gate (fails on unapproved or modified code smells)
vibetrace audit quality --ci --fail-on high
# Interactive triage prompt or direct exception approval with audit rationale
vibetrace audit quality --approve --tag intentional --rationale "Performance critical tight loop"

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

## 🤖 Configuring AI Agents (Antigravity, Cursor, Claude, Cline)

Add VibeTrace to your agent's MCP configuration:

#### Antigravity IDE (`~/.gemini/antigravity/mcp_config.json` or project `.gemini/mcp.json`)
```json
{
  "mcpServers": {
    "vibetrace": {
      "command": "vibetrace",
      "args": ["mcp", "--path", "${workspaceFolder}"]
    }
  }
}
```

#### Claude Desktop (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "vibetrace": {
      "command": "vibetrace",
      "args": ["mcp", "--path", "/absolute/path/to/your/project"]
    }
  }
}
```

#### Cursor (`Settings > Features > MCP`)
- **Name:** `vibetrace`
- **Type:** `command`
- **Command:** `vibetrace mcp --path .`

#### Cline / Roo Code (VS Code MCP Settings)
```json
{
  "mcpServers": {
    "vibetrace": {
      "command": "vibetrace",
      "args": ["mcp", "--path", "${workspaceFolder}"]
    }
  }
}
```

The agent gains access to 29 surgical FastMCP code intelligence tools:
- `vibetrace_review_changes` (Unified 1-call review: Security, Taint, Split Integrity, Caller Guard & 80% Test Gate)
- `vibetrace_get_vibe_bundle` (One-shot task context packed within token budget $\le 1,500$ tokens)
- `vibetrace_diagnose_symbol` (Consolidated 360° symbol diagnosis: slice, callers, blast radius, specs, tests)
- `vibetrace_check_caller_guard` (Pre-flight caller inspection and blast-radius advisory)
- `vibetrace_audit_security` (Polyglot AST scanner, multi-hop taint reachability, secrets, IaC, optional Trivy container CVEs)
- `vibetrace_remediate_vulnerability` (Generate compliant code templates for security findings)
- `vibetrace_approve_security_exception` (Record baseline exceptions in `.vibetrace/security.toml`)
- `vibetrace_audit_splits` (Scan codebase for execution bifurcations and check status against baseline)
- `vibetrace_approve_split` (Approve a split path into `.vibetrace/splits.toml` with SHA-256 hash)
- `vibetrace_explain_dependency` (Transitive dependency root causation, dependency tree, and diamond conflicts)
- `vibetrace_refactor_method` (AST variable lifetime analysis and surgical helper method extraction)
- `vibetrace_patch_dependency` (Automated dependency upgrade plan and dormant CVE suppression)
- `vibetrace_check_docs_drift` (Audit documentation for broken symbol references and sync live AST links)
- `vibetrace_search_docs` (Hybrid documentation RAG search with local/global scope)
- `vibetrace_list_doc_catalogs` (List configured documentation catalogs)
- `vibetrace_semantic_search` (4-stream hybrid AST + BM25 + Vector + PageRank)
- `vibetrace_trace_path` (Bidirectional reachable execution pathfinding with exact call lines)
- `vibetrace_get_code_slice` (Surgical source lines with outgoing callee contracts and linked docs)
- `vibetrace_impact_analysis` (3-level transitive blast radius)
- `vibetrace_find_references` (Find all incoming calls and references)
- `vibetrace_get_call_tree` (Top-down recursive call tree hierarchy)
- `vibetrace_get_call_stack` (Bidirectional call stack analysis)
- `vibetrace_get_dependencies` (Categorize imports: internal/stdlib/third-party)
- `vibetrace_get_structural_summary` (File AST symbol layout or PageRank centrality repo map)
- `vibetrace_search_by_signature` (Find functions matching specific signature patterns)
- `vibetrace_save_trace` / `vibetrace_list_saved_traces` (Saved trace bookmarks)
- `vibetrace_delete_saved_trace` (Delete saved trace bookmarks)
- `vibetrace_reindex_project` (Trigger incremental or full AST reindexing)
- `vibetrace_project_status` / `vibetrace_project_search` / `vibetrace_project_guard` / `vibetrace_project_links` (Federated multi-repo tools)

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

### 3. Planned Intelligence: Optional Cross-Encoder Re-Ranking (`vibetrace[rerank]`) — Roadmap (Deferred)
- **Optional Plugin Extra**: For conceptual prose searches where exact identifiers are unknown, installable via `uv tool install "vibetrace[rerank]"` or `pip install "vibetrace[rerank]"`.
- **Two-Stage Re-Ranking**: Uses an ONNX-quantized cross-encoder (`flashrank` with `ms-marco-TinyBERT-L-2-v2`) to re-score the top-30 RRF candidates via `--rerank` without sacrificing the sub-second core baseline.
- **Status:** Deferred to future roadmap as current two-stream BM25 + dense embedding RRF search already delivers sub-50ms precision on technical queries without adding package weight.

### 4. Planned Context Optimization: Exact BPE Tokenizer (`tiktoken`) — Roadmap (Deferred or add 10% buffer)
- **Exact Model Vocabulary BPE**: Integration with `tiktoken` for exact token counting across OpenAI, Claude, and Gemini models.
- **Status:** Deferred to future roadmap to keep VibeTrace lightweight and avoid compiled binary C-extension dependencies.
- **Lightweight Alternative:** Calibrate token estimators with a 10% conservative safety headroom ($3.5\text{ chars/token}$ and a 90% budget cap) to mathematically guarantee zero context window overflow without external dependencies.

### 5. Planned Web Explorer: In-Memory WASM AST Indexing — Roadmap (Deferred)
- **Standalone Web / Chrome Extension Showcase**: In-browser AST parsing via `web-tree-sitter` and in-memory SQLite (`sql.js`) to inspect public GitHub repositories directly in the Map Room without running the local Python daemon.
- **Status:** Deferred to future roadmap (or separate standalone browser extension) to keep VibeTrace focused on local CLI developer velocity, deep taint analysis, and agent pair-programming pipelines.

---

## 🛠️ Development & Contributing

### 1. Local Development Setup

```bash
git clone https://github.com/serguei9090/VibeTrace.git
cd VibeTrace

# Install Python dependencies using uv
uv sync

# Install git pre-commit hooks via lefthook
lefthook install

# (Optional) Install frontend UI dependencies
cd src/web && bun install && cd ../..
```

---

### 2. Verification & Quality Gates

Before committing any changes, run the mandatory verification pipeline:

```bash
# 1. Run full test suite
uv run pytest

# 2. Lint and format code
uv run ruff check --fix .
uv run ruff format .
uv run pyright

# 3. Verify Unified AI Quality Gate (80% Test Coverage + 0 High Sec + 0 Splits)
uv run vibetrace review --ci
```

---

### 3. Conventional Commit Strategy

Use structured [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(<scope>): <imperative description>
```
* `feat`: A new feature or capability.
* `fix`: A bug fix.
* `refactor`: Restructuring code without changing external behavior.
* `perf`: Performance optimizations.
* `test`: Adding or correcting tests.
* `docs`: Documentation updates.
* `chore`: Maintenance, version bumps, or dependency updates.

---

### 4. Updating the Changelog

Document all user-facing changes in [`CHANGELOG.md`](CHANGELOG.md) under the `[Unreleased]` section following the [Keep a Changelog](https://keepachangelog.com/) standard:
* `Added`: New capabilities, CLI flags, or MCP tools.
* `Changed`: Modifications to existing functionality.
* `Fixed`: Bug fixes and error resolutions.
* `Security`: Vulnerability patches or rule updates.

---

### 5. Releasing to PyPI (Trusted Publishing)

Releases are automated via GitHub Actions with PyPI Trusted Publishing (OIDC). To publish a new release:

1. **Bump Version:** Update `version = "X.Y.Z"` in `pyproject.toml`.
2. **Update Changelog:** In [`CHANGELOG.md`](CHANGELOG.md), convert `[Unreleased]` into `[X.Y.Z] - YYYY-MM-DD`.
3. **Commit & Tag:**
   ```bash
   git add pyproject.toml CHANGELOG.md
   git commit -m "chore(release): vX.Y.Z"
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   ```
4. **Push Release:**
   ```bash
   git push origin main --tags
   ```
5. **Automated Publishing:** GitHub Actions triggers `.github/workflows/publish.yml`, builds the wheel via `uv build`, and securely publishes to PyPI without long-lived API tokens.

---

## 📜 License

MIT License. Copyright (c) 2026 Serguei.
