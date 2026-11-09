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
| `vibetrace index` | `[path] [--embed \| --no-embed] [--lsp] [--force]` | Incremental AST + FTS5 BM25 indexing (with sliding window LSP pipelining, auto GPU/CPU vector embeddings, and orphan purging). | `vibetrace index . --embed --lsp` |
| `vibetrace search` | `<query> [--limit N] [--with-docs]` | 4-stream Hybrid RRF search across AST, BM25, PageRank, and vectors. Cross-queries docs with `--with-docs`. | `vibetrace search "upsert_node" --limit 10` |
| `vibetrace trace` | `<from> <to> [--save-as NAME] [--saved NAME] [--list-saved]` | Reachable BFS execution tracepath with hop-by-hop `:L<line>` call sites and multi-route bifurcation detection. | `vibetrace trace handleLogin verifyPassword` |
| `vibetrace guard` | `<symbol> [--staged] [--strict]` | Pre-flight caller inspection and blast-radius advisory. Git pre-commit mode via `--staged`. | `vibetrace guard upsert_node` |
| `vibetrace bundle` | `<query> [--budget TOKENS]` | Assembles 1-shot task context (docs + code slice + callee contracts + caller guard + sibling tests). | `vibetrace bundle "add auth middleware" --budget 1500` |
| `vibetrace diagnose` | `<symbol> [--depth N] [--no-code]` | 360° symbol investigation: slice, callee contracts, callers with lines, blast radius, specs, and tests. | `vibetrace diagnose upsert_node` |
| `vibetrace review` | `[--ci] [--strict] [--min-coverage N]` | Unified 1-call AI review gate: Security/Taint, Splits, Caller Guard, and 80% test coverage. | `vibetrace review --ci --min-coverage 80` |
| `vibetrace deps` | `why <pkg> \| tree \| conflicts` | Transitive dependency graph resolution, root causation chains, and diamond conflict detection. | `vibetrace deps why certifi` |
| `vibetrace refactor` | `--split-method [--lines START:END] [--name HELPER] [--write]` | AST variable lifetime analysis and surgical helper method extraction for cognitive complexity reduction. | `vibetrace refactor --file src/db.py --symbol run --split-method --lines 20:45 --write` |
| `vibetrace audit` | `splits \| security \| quality \| fix-deps \| pr-taint` | Comprehensive code & security audits: splits, AST SAST/taint, auto-patching, and PR inline reviews. | `vibetrace audit security --summary` |
| `vibetrace audit security` | `[--summary] [--file PATH] [--staged] [--image TAG] [--skip-trivy] [--json] [--sarif] [--ci] [--skip-deps] [--approve]` | Polyglot SAST, taint reachability, secrets, container OS CVEs (Trivy), staged blocker, and baseline triage. | `vibetrace audit security --summary` |
| `vibetrace audit quality` | `[--summary] [--file PATH] [--json] [--sarif] [--fix] [--ci] [--fail-on SEV] [--approve]` | Polyglot code quality audit across 8 languages with Sonar RSPEC parity, safe `--fix` AST codemods, and SARIF export. | `vibetrace audit quality --summary` |
| `vibetrace docs` | `catalog \| index \| search \| check \| sync-links` | Documentation RAG, live symbol bi-directional linking, and doc reference drift auditing. | `vibetrace docs check` |
| `vibetrace slice` | `<symbol> [--file PATH]` | Surgical source line extraction (`start..end`) + outgoing callee contracts for LLM context savings. | `vibetrace slice IndexerDB.upsert_node` |
| `vibetrace call-tree` | `<symbol> [--depth N]` | Top-down recursive call tree hierarchy with cycle detection and call lines. | `vibetrace call-tree hybrid_search --depth 3` |
| `vibetrace call-stack` | `<symbol> [--depth N]` | Bidirectional call stack exploration (both incoming callers and outgoing callees). | `vibetrace call-stack upsert_node` |
| `vibetrace impact` | `<file> [--depth N]` | 3-level blast radius impact analysis (Direct L1, Secondary L2, Deep L3). | `vibetrace impact src/vibetrace/db.py` |
| `vibetrace summary` | `<file> [--budget TOKENS]` | Structural outline of a file or PageRank centrality-ranked repository map. | `vibetrace summary . --budget 1024` |
| `vibetrace references` | `<symbol> [--scope PATH]` | Locate all incoming call sites and symbol references across the project. | `vibetrace references upsert_node` |
| `vibetrace dependencies` | `<file> [--reverse]` | Categorize imported modules (internal project, stdlib, third-party packages). | `vibetrace dependencies src/vibetrace/db.py` |
| `vibetrace project` | `init \| status \| search \| guard \| links \| index` | Federated multi-repository workspace coordination, cross-repo call guards, and API contract matching. | `vibetrace project status` |
| `vibetrace serve` | `[--host HOST] [--port PORT]` | Launch the Map Room 2.0 visualizer and FastAPI + WebSocket daemon. | `vibetrace serve --port 8000` |
| `vibetrace init` | `[--force] [--project]` | 1-click workspace onboarding (`.gitignore`, `.vibetrace/`, agent skill installation). | `vibetrace init` |
| `vibetrace hooks` | `install \| remove \| status` | Configure Lefthook or native git post-commit and pre-commit automation. | `vibetrace hooks install` |

### Code Quality & AI Agent Remediation Workflow (`vibetrace audit quality`)

VibeTrace enforces the **Rank 1 Dynamic 2-Step "Target & Verify" Loop** to prevent terminal buffer truncation and AI agent context window overflow:

#### Step 1: High-Density Executive Findings Matrix (< 1 KB)
```bash
uv run vibetrace audit quality --summary
```
Emits a compact Markdown table listing findings grouped by severity, Sonar RSPEC rule, unapproved count, and top impacted files.

#### Step 2: Surgical Single-File JSON Extraction
```bash
uv run vibetrace audit quality --file <impacted_file> --json
```
Emits structured JSON with exact line spans, code snippets, compliant solutions, and remediation advice for automated AI patching.

#### Automated Quality Fixing (`--fix`) & SARIF Export
```bash
# Apply automated safe fixes via Ruff, Biome, and surgical AST codemods (S7503 async def, S1066 mergeable ifs)
vibetrace audit quality --fix

# Export findings in OASIS SARIF 2.1.0 JSON format for GitHub Code Scanning and IDE viewers
vibetrace audit quality --sarif > quality-results.sarif
```

#### Continuous Integration & Baseline Triage
```bash
# CI/CD Quality Gate (fails build on unapproved or modified code smells)
vibetrace audit quality --ci --fail-on high

# Approve benign or performance-critical exceptions into .vibetrace/quality.toml
vibetrace audit quality --approve --tag intentional --rationale "Tight numerical processing loop"
```

#### Map Room REST API Endpoints
- `GET /api/quality/audit?file_path=<path>&threshold=15`: Returns full `QualityAuditReport` JSON with cognitive complexity matrices and findings.
- `POST /api/quality/approve`: Accepts `QualityApprovePayload` to persist 1-click in-browser exception approvals into `.vibetrace/quality.toml`.

### Security Audit & Pre-Commit Protection Workflow (`vibetrace audit security`)

VibeTrace protects codebases against vulnerabilities and secret leakage with an agent-first workflow:

#### Step 1: High-Density Executive Findings Matrix (< 1 KB)
```bash
uv run vibetrace audit security --summary
```
Emits a compact Markdown table listing findings grouped by severity, CWE/rule ID, unapproved count, taint reachability status, and top impacted files.

#### Step 2: Surgical Single-File JSON Extraction
```bash
uv run vibetrace audit security --file <impacted_file> --json
```
Emits structured JSON with exact line spans, code snippets, source-to-sink variable taint chains, and remediation guidance.

#### Pre-Commit Staged Secret Blocker (`--staged`)
```bash
# Integrates directly into Git pre-commit hooks to block uncommitted API keys & tokens
vibetrace audit security --staged
```
Scans staged diffs (`git diff --cached`) for high-entropy secrets and modern AI/SaaS tokens (OpenAI, Anthropic, Mistral, Perplexity, Cohere, Tailscale, Postman, Supabase, AWS, GitHub). Exits with code 1 if any secret is detected.

#### Multi-Hop Inter-Procedural Taint & Advanced Semgrep Clauses
- **Multi-Hop Taint Propagation:** Traces user-controlled inputs across helper function call chains (`entrypoint -> service -> sink`), tracking both positional and keyword argument bindings and multiline call expressions.
- **Advanced Semgrep Clauses:** Supports composite `patterns` with `pattern-not` (negative assertions to eliminate false positives) and `pattern-inside` (scope enclosure validation).
- **SARIF 2.1.0 Export:** Integrates with GitHub Advanced Security and IDE code scanners via `--sarif`.

#### Container & Deep OS Package CVE Auditing (Optional Trivy Bridge)
- **Auto-Detection:** When container artifacts (`Dockerfile`, `docker-compose.yml`, `k8s/`) are present and `trivy` is installed on `$PATH`, `vibetrace audit security` automatically inspects base OS libraries (`glibc`, `openssl`, `libssl3`) and merges findings into the Executive Security Matrix.
- **Image Scanning:**
  ```bash
  vibetrace audit security --image myapp:latest
  ```
  Audits built local or remote container image tags for OS vulnerabilities without requiring separate tools.
- **Fast Local Bypass:** Pass `--skip-trivy` to execute an instant (< 1s) code-only audit without external binary execution.




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

### Complete MCP Tools Catalog (29 FastMCP Tools)

| Tool Name | Key Parameters | Functionality |
| :--- | :--- | :--- |
| `vibetrace_get_vibe_bundle` | `query`, `budget`, `scope_path` | Packs relevant docs, code slices, callee contracts, caller guard, and test fixtures into a token-budgeted payload ($\le 1,500$ tokens). |
| `vibetrace_diagnose_symbol` | `symbol_name`, `max_depth`, `include_code` | 360° symbol investigation: source slice, callee contracts, callers with `:L<line>`, blast radius, specs, and tests. |
| `vibetrace_check_caller_guard` | `symbol_name`, `staged`, `file_path`, `scope_path` | Pre-flight caller inspection with exact call-site line numbers and blast-radius advisory. Supports git staged diffs. |
| `vibetrace_review_changes` | `scope`, `check_dependencies`, `min_coverage`, `enforce_coverage` | Unified 1-call AI review gate: Security/Taint, Splits, Caller Guard, and 80% test coverage. |
| `vibetrace_audit_security` | `scope_path`, `check_dependencies`, `analyze_taint` | Multi-language AST SAST, zero-dependency IaC scanning, git secrets, inter-procedural taint reachability, and OSV lockfile CVEs. |
| `vibetrace_approve_security_exception` | `finding_id`, `rationale`, `tag`, `scope_path` | Approves a security baseline exception in `.vibetrace/security.toml` with auditable disposition tags. |
| `vibetrace_remediate_vulnerability` | `finding_id`, `file_path`, `scope_path` | Synthesizes compliant, parameterized code replacement templates for security findings. |
| `vibetrace_audit_splits` | `scope_path`, `response_format` | Scans repository for cross-module execution splits and validates each against `.vibetrace/splits.toml` baseline. |
| `vibetrace_approve_split` | `caller_name`, `callee_a`, `callee_b`, `reason`, `scope_path` | Approves a split path into `.vibetrace/splits.toml` with normalized SHA-256 caller hash tripwire. |
| `vibetrace_explain_dependency` | `package_name`, `action`, `max_depth`, `scope_path` | Transitive dependency graph resolution: explains why a sub-dependency is installed, renders dependency trees, or detects diamond collisions. |
| `vibetrace_refactor_method` | `file_path`, `symbol_name`, `start_line`, `end_line`, `helper_name`, `apply` | AST variable lifetime analysis and surgical helper method extraction for cognitive complexity reduction. |
| `vibetrace_patch_dependency` | `package_name`, `apply`, `suppress_unpatched`, `scope_path` | Builds and applies dependency upgrade plans via native package managers, or suppresses unpatched CVEs with 0 call edges. |
| `vibetrace_check_docs_drift` | `sync`, `scope_path`, `response_format` | Audits documentation for broken or dead codebase symbol references and synchronizes bi-directional AST links. |
| `vibetrace_trace_path` | `from_symbol`, `to_symbol`, `max_depth`, `include_divergent` | Bidirectional BFS shortest and alternative execution pathfinding with exact call-site coordinates. |
| `vibetrace_search_docs` | `query`, `catalog`, `scope`, `limit` | Hybrid RRF documentation search (.md, .txt, .pdf) across local project and global master catalogs. |
| `vibetrace_list_doc_catalogs` | `scope_path`, `response_format` | Lists registered local and global documentation catalogs with chunk counts and storage paths. |
| `vibetrace_semantic_search` | `query`, `limit`, `scope_path` | 4-stream Hybrid RRF search combining AST symbol matching, BM25, PageRank, and vector embeddings. |
| `vibetrace_get_code_slice` | `symbol_name`, `file_path`, `include_callees` | Exact line-accurate code slice (`start_line..end_line`) + outgoing callee contracts and linked documentation in 1 round-trip. |
| `vibetrace_impact_analysis` | `file_path`, `depth` | Transitive 3-level blast radius analysis (Direct L1, Secondary L2, Deep L3). |
| `vibetrace_find_references` | `symbol_name`, `scope_path` | Locate all incoming call sites and references to a symbol across the project. |
| `vibetrace_get_call_tree` | `symbol_name`, `max_depth`, `file_path` | Top-down recursive call tree hierarchy with ASCII connectors, call-site lines, and cycle safety. |
| `vibetrace_get_call_stack` | `symbol_name`, `depth`, `scope_path` | Bidirectional call stack exploration (callers above and callees below). |
| `vibetrace_get_dependencies` | `file_path`, `scope_path` | Categorize imports into internal project modules, standard library, and third-party packages. |
| `vibetrace_get_structural_summary` | `file_path`, `budget_tokens`, `scope_path` | Structural outline of classes, functions, and methods or PageRank centrality-ranked repo map. |
| `vibetrace_search_by_signature` | `signature_pattern`, `scope_path` | Find symbols matching specific signature or parameter patterns. |
| `vibetrace_save_trace` | `name`, `from_symbol`, `to_symbol`, `description` | Bookmark an execution trace for instant retrieval. |
| `vibetrace_list_saved_traces` | `scope_path`, `response_format` | List all saved trace bookmarks. |
| `vibetrace_delete_saved_trace` | `name`, `scope_path` | Remove a saved trace bookmark. |
| `vibetrace_reindex_project` | `path`, `embed`, `force` | Incremental AST and vector refresh of the workspace. |
| `vibetrace_project_status` | `scope_path` | Inspect health, index status, and node counts of all member repositories in a federated workspace. |
| `vibetrace_project_search` | `query`, `repo`, `scope_path` | Federated cross-repository hybrid search or single-repo scoped search. |
| `vibetrace_project_guard` | `symbol`, `scope_path` | Federated cross-repository blast-radius guard across all member services. |
| `vibetrace_project_links` | `scope_path` | Cross-repository route contract matching (client HTTP calls $\leftrightarrow$ server handlers). |

---

## 4. HTTP & WebSocket API (`src/vibetrace/server_api.py`)

### Core REST Endpoints

| Endpoint | Method | Parameters | Description |
| :--- | :--- | :--- | :--- |
| `/graph` | `GET` | `file_path?: string` | Returns all indexed `nodes` and `edges` for the Map Room visualizer. |
| `/trace` | `GET` | `from_symbol: string, to_symbol: string, max_depth?: int` | Bidirectional BFS trace with multi-route detection and call-site lines. |
| `/api/reachable` | `GET` | `from_symbol: string, max_depth?: int` | Recursive SQLite CTE querying all downstream reachable symbols from a source node. |
| `/api/review` | `GET` | `scope?: string, min_coverage?: float` | Unified AI review gate: Security/Taint, Splits, Caller Guard, and Test Coverage. |
| `/api/security/audit` | `GET` | `check_dependencies?: bool` | Polyglot AST SAST, IaC, secrets, inter-procedural taint, and lockfile CVEs. |
| `/api/security/patch-deps` | `POST` | `PatchDepsRequest` body | Build and optionally apply automated dependency upgrade plans. |
| `/api/security/pr-taint` | `GET` | `base_ref?: string, staged?: bool, pr_number?: int` | PR-scoped diff taint review report with inline suggested replacements. |
| `/api/deps/why` | `GET` | `package: string` | Root causation chains that introduced a transitive dependency. |
| `/api/deps/tree` | `GET` | `package?: string, max_depth?: int` | Top-down dependency hierarchy from root manifests to leaves. |
| `/api/deps/conflicts` | `GET` | _none_ | Detects diamond version collisions across project lockfiles. |
| `/api/diagnose` | `GET` | `symbol: string, depth?: int` | Consolidated 360° symbol diagnosis (slice, callers, blast radius, specs, tests). |
| `/api/refactor/candidates` | `GET` | `file_path: string, symbol_name: string` | Suggests candidate statement blocks for method extraction. |
| `/api/refactor/split` | `POST` | `RefactorSplitRequest` body | Executes AST-based helper method extraction refactoring. |
| `/api/docs/search` | `GET` | `q: string, scope?: string, limit?: int` | 2-stream hybrid RRF documentation search. |
| `/api/docs/check` | `GET` | _none_ | Audits documentation for broken or dead codebase symbol references. |
| `/api/docs/sync-links` | `POST` | _none_ | Synchronizes bi-directional links between documentation and AST symbols. |
| `/api/splits/audit` | `GET` | _none_ | Scans graph for cross-module execution splits against `.vibetrace/splits.toml`. |
| `/api/splits/approve` | `POST` | `SplitApprovalRequest` body | Approves a split path with normalized SHA-256 caller code hash. |
| `/api/reindex` | `POST` | `ReindexPayload` body | Triggers incremental or full codebase reindex via `ReindexService`. |
| `/call-tree` | `GET` | `symbol: string, depth?: int` | Returns top-down recursive call tree hierarchy JSON. |
| `/source` | `GET` | `file_path: string, start_line: int, end_line: int` | Safely reads line slice within project root. |
| `/api/agent-focus` | `POST` | `AgentFocusPayload` body | Broadcasts agent active focus nodes and files to connected Map Room clients. |

### Real-Time WebSocket (`WS /ws`)

- Emits `{"type": "refresh"}` when file system watcher detects code changes.
- Broadcasts `{"type": "agent_focus", ...}` when AI coding agents query symbols or execute tracepaths.
