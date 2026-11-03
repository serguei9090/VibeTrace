---
name: vibetrace-navigator
description: Surgical codebase discovery, execution tracepath analysis, blast-radius verification, token-efficient source slicing, and documentation RAG via VibeTrace CLI and MCP tools. Use this skill whenever exploring a repository, consulting project or master documentation, tracing how an entry point reaches a target function, checking callers before refactoring, or reading specific symbol definitions without loading entire files.
---

# VibeTrace Navigator Skill

VibeTrace indexes the repository's Tree-sitter AST, call/inheritance graph (with exact 1-indexed `call_line` numbers), SQLite FTS5 BM25 lexical index, documentation RAG catalogs (.md, .txt, .pdf), and optional local `EmbeddingGemma 2` (256d Matryoshka) vectors in SQLite databases.

---

## ⚡ Supercharged 100% Vibecoding Workflow

When starting a coding task or refactoring, use the dedicated Vibecoding superpowers to complete tasks in one shot with zero broken callers:

### 1. One-Shot Task Bundle (`bundle`)
Get everything needed for a task—relevant architecture specs, target code implementation, callee contracts, caller blast radius, and sibling test fixtures—in a single token-budgeted payload (<= 1,500 tokens):

- **MCP Tool:** `vibetrace_get_vibe_bundle(intent="add auth token to middleware", token_budget=1500)`
- **CLI:** `uv run vibetrace bundle "add auth token to middleware" --budget 1500`

### 2. 360° Symbol Diagnosis Meta-Endpoint (`diagnose`)
When you need to thoroughly investigate an existing function or method before refactoring, get a complete 360° dossier—source slice, callee contracts, all incoming callers with exact `:L<line>` call sites, transitive blast radius, linked architectural specs, and exercising test fixtures—in 1 single call:

- **MCP Tool:** `vibetrace_diagnose_symbol(symbol_name="upsert_node", max_depth=2)`
- **REST API:** `GET /api/diagnose?symbol=upsert_node`
- **CLI:** `uv run vibetrace diagnose upsert_node`

### 3. Pre-Flight Caller Guard (`guard`)
**Rule 1 Requirement:** Never modify a function or method signature without inspecting all incoming callers first. The Caller Guard checks every caller in the AST graph, highlights exact call lines, and computes an active blast-radius advisory:

- **MCP Tool:** `vibetrace_check_caller_guard(symbol_name="upsert_node")`
- **MCP Staged Check:** `vibetrace_check_caller_guard(staged=True)`
- **CLI:** `uv run vibetrace guard upsert_node`

### 3. Git Pre-Commit Blast-Radius Gate (`guard --staged`)
Verify that modified function signatures across all staged git changes do not break or orphan incoming callers:
- **CLI (Advisory Check):** `uv run vibetrace guard --staged`
- **CLI (Strict Pre-Commit Gate):** `uv run vibetrace guard --staged --strict`

### 4. Architectural Split Path Audit & Code Review (`audit splits`)
Detect cross-module execution bifurcations (functions branching into 2+ external modules) and verify their normalized AST SHA-256 tripwires against `.vibetrace/splits.toml`:
- **MCP Tool (Audit):** `vibetrace_audit_splits(symbol="")`
- **MCP Tool (Approve):** `vibetrace_approve_split(symbol="reindex_all", status="approved", rationale="Verified orchestrator")`
- **CLI:** `uv run vibetrace audit splits` / `uv run vibetrace audit splits --approve` / `uv run vibetrace audit splits --ci --strict`

### 5. Polyglot Security, IaC, Git Secret, Inter-Procedural Taint & CVE Audit (`audit security`)
Run Tree-sitter AST SAST (`py`, `js`, `ts`, `tsx`, `go`, `rs`), zero-dependency IaC scanning (`Dockerfile`, `docker-compose.yml`, K8s, GitHub Actions), Gitleaks-style git secret & Shannon entropy scanning (working tree + last 10 commits + tracked `.env`), inter-procedural positional argument-to-parameter taint reachability, language-aware auto-syncing CWE Top 25 / Semgrep YAML rules (`.vibetrace/rules/*.yaml`), and hydrated OSV.dev lockfile CVE scanning (`uv.lock`, `bun.lock`, `poetry.lock`, `requirements.txt`):
- **MCP Tool (Audit):** `vibetrace_audit_security(scope_path="", check_dependencies=True, analyze_taint=True)`
- **MCP Tool (Remediate):** `vibetrace_remediate_vulnerability(finding_id="", scope_path="")`
- **MCP Tool (Approve Exception with Disposition Tag):** `vibetrace_approve_security_exception(finding_id="SEC-...", rationale="No alternative API", tag="no-alternative")`
- **Inline Auditable Suppression Tag:** `# nosec [no-alternative]: reason` (or `risk-accepted`, `false-positive`, `compensating-control`)
- **CLI:**
  ```bash
  uv run vibetrace audit security --skip-deps --ci
  uv run vibetrace audit security --fix
  uv run vibetrace audit security --sarif
  uv run vibetrace audit security --approve --tag no-alternative --rationale "Verified safe"
  ```

### 6. Transitive Dependency Graph Resolution & Diamond Conflict Detector (`deps`)
Locate upstream causation paths for any sub-dependency, render direct manifest trees, and detect multi-version diamond collisions:
- **MCP Tool:** `vibetrace_explain_dependency(package_name="certifi", action="why")`
- **MCP Tool (Tree):** `vibetrace_explain_dependency(action="tree", max_depth=3)`
- **MCP Tool (Conflicts):** `vibetrace_explain_dependency(action="conflicts")`
- **CLI:**
  ```bash
  uv run vibetrace deps why certifi
  uv run vibetrace deps tree --depth 3
  uv run vibetrace deps conflicts
  ```

### 7. AST Method Extraction & Cognitive Complexity Refactor (`refactor`)
Analyze AST variable lifetimes and execute surgical helper method extraction for complex functions with automated parameter and return tuple resolution:
- **MCP Tool (Split Suggestions):** `vibetrace_refactor_method(file_path="src/parser.py", symbol_name="parse_file")`
- **MCP Tool (Apply Extraction):** `vibetrace_refactor_method(file_path="src/parser.py", symbol_name="parse_file", start_line=45, end_line=72, helper_name="_extract_header", apply=True)`
- **CLI:**
  ```bash
  uv run vibetrace refactor --file src/parser.py --symbol parse_file
  uv run vibetrace refactor --file src/parser.py --symbol parse_file --split-method --lines 45:72 --name _extract_header --write
  ```

### 8. Dependency Vulnerability Auto-Patcher & Reachability Triage (`audit fix-deps`)
Automatically bump vulnerable packages via native package managers (`uv`, `bun`, `cargo`, `go`) or safely record unpatched CVEs with 0 call edges in `.vibetrace/security.toml`:
- **MCP Tool:** `vibetrace_patch_dependency(package_name="requests", apply=True)`
- **CLI:** `uv run vibetrace audit fix-deps --apply`
- **CLI (Suppress Unpatched):** `uv run vibetrace audit fix-deps --suppress-unpatched`

### 9. Automated PR Taint Bot & Inline Remediation Generator (`audit pr-taint`)
Generate ready-to-merge GitHub PR comments containing complete 3-hop taint traces and synthesized parameterized replacements:
- **CLI (Inspect PR diff):** `uv run vibetrace audit pr-taint --base main`
- **CLI (Auto-apply remediations):** `uv run vibetrace audit pr-taint --apply`
- **CLI (CI Summary):** `uv run vibetrace audit pr-taint --output-md pr-review.md`

### 10. Unified 1-Call AI Self-Review & Quality Gate (`review`)
Before completing any coding task or committing, run the single unified Quality Gate that executes **Security + IaC + Git Secret + Inter-Procedural Taint**, **Split Path Audit**, **Hybrid Test Coverage Gate** (instant AST test-caller reachability + `coverage.xml`/`lcov.info` line coverage with an 80% default target), and **Caller Guard** in one pass:
- **MCP Tool:** `vibetrace_review_changes(scope="changed", check_dependencies=False, min_coverage=80.0, enforce_coverage=False)`
- **REST API:** `GET /api/review?scope=changed&min_coverage=80`
- **CLI:** `uv run vibetrace review` / `uv run vibetrace review --ci --strict --min-coverage 80`

### 11. Sonar-Grade Code Quality & Cognitive Complexity Audit (`audit quality`)
Audit code for Cognitive Complexity (RSPEC-3776), deeply nested control flow (RSPEC-134), mergeable conditionals (RSPEC-1066), identical function bodies (RSPEC-4144), string literal duplication (RSPEC-1192), POSIX file permissions (RSPEC-2612), and dead code (RSPEC-1144) using native AST visitors combined with polyglot linters (Ruff, Biome, Clippy, PHPStan, RuboCop, ShellCheck, SQLFluff):

#### 🤖 AI Agent Token-Efficient 2-Step Remediation Workflow:
To avoid context/terminal truncation on large repositories, follow this 2-step workflow:
1. **Phase 1: Executive Overview (`--summary`)**:
   ```bash
   uv run vibetrace audit quality --summary
   ```
   *Returns a 20-line matrix table (<1 KB) summarizing unapproved and approved counts by rule across all languages, without dumping full text.*
2. **Phase 2: Surgical Target Extraction (`--file <path> --json`)**:
   ```bash
   uv run vibetrace audit quality --file <target_file> --json
   ```
   *Returns compact, parseable JSON containing exact `start_line`, `end_line`, `non_compliant_snippet`, `compliant_snippet`, and `remediation_guidance` for that file.*
3. **Automated Safe Fixes (`--fix`)**:
   ```bash
   uv run vibetrace audit quality --fix
   ```
   *Applies safe AST/linter transformations automatically (redundant parens, chained startswith tuples, import cleanup).*
4. **Baseline Exceptions (`--approve`)**:
   ```bash
   uv run vibetrace audit quality --approve --tag legacy --rationale "Technical debt baseline verified"
   ```
   *Records unapproved findings into `.vibetrace/quality.toml`.*

---

## Standard 4-Step Surgical Workflow (`Docs -> Locate -> Trace/Impact -> Slice`)

## Step 0: Consult Project & Master Docs (`docs`)

Before modifying code or planning architecture, consult project-specific requirements, design specs, or global master tech-stack guidelines:

### Scoping & Cross-Contamination Guardrails
- **Default (`scope="local"`):** Searches **only** the current project's documentation catalogs defined in `.vibetrace.toml`. This prevents global generalities from contaminating project-specific truths.
- **Master Stack (`scope="global"`):** Explicitly queries your personal master documentation catalogs in `~/.vibetrace/config.toml` for standard stack playbooks.
- **Combined (`scope="all"`):** Searches both local and global documentation simultaneously using 2-stream hybrid Reciprocal Rank Fusion (RRF).
- **Answer Scope Tags:** Each returned chunk is tagged with `[LOCAL:catalog]` or `[GLOBAL:catalog]` so you always know the provenance of every document snippet.

### Via MCP Tool
- `vibetrace_search_docs(query="<architecture, API contract, or standard>", scope="local", limit=5)`
- `vibetrace_list_doc_catalogs()`
- `vibetrace_check_docs_drift(sync=True)` (audits documentation for dead or broken codebase symbol references)

### Via CLI
```bash
# Add folders to local project catalog and index
uv run vibetrace docs catalog add core docs/
uv run vibetrace docs index

# Search documentation (defaults to local project docs)
uv run vibetrace docs search "authentication flow"
# Search master stack documentation
uv run vibetrace docs search "rust error handling pattern" --scope global

# Audit documentation for broken symbol references and sync links
uv run vibetrace docs check
uv run vibetrace docs sync-links
```

---

## Step 1: Locate Symbols (`search` / `summary`)

Find the exact symbol name, `qualified_name` (`ClassName.method_name`), file path, and `start_line..end_line` using Hybrid Reciprocal Rank Fusion (RRF):

### Via MCP Tool
- `vibetrace_semantic_search(query="<symbol or natural language intent>", limit=15)`
- `vibetrace_get_structural_summary(file_path="<path/to/file.py>")`

### Via CLI
```bash
uv run vibetrace search "database connection pool"
uv run vibetrace summary src/vibetrace/indexer/db.py
```

---

## Step 2: Trace Execution Paths & Assess Blast Radius (`trace` / `impact`)

Before modifying a function or debugging a multi-hop flow, trace the exact hop-by-hop execution chain (`Node_0 -> ... -> Node_k` with `:L<call_line>`) or compute the 3-level caller blast radius:

### Via MCP Tool
- `vibetrace_trace_path(from_symbol="<entry_point>", to_symbol="<target_symbol>", max_depth=6)`
- `vibetrace_get_call_stack(node_name="<symbol>", direction="both", depth=3)`
- `vibetrace_get_dependencies(file_path="<path/to/file.py>", reverse=False)`
- `vibetrace_impact_analysis(file_path="<path/to/file.py>")`
- `vibetrace_find_references(symbol_name="<ClassName.method_name>")`

### Via CLI
```bash
uv run vibetrace trace vibetrace_get_structural_summary get_file_structure
uv run vibetrace call-stack DocIndexService.index_catalog
uv run vibetrace dependencies src/vibetrace/docs/service.py --reverse
uv run vibetrace impact src/vibetrace/indexer/db.py
```

---

## Step 3: Surgical Source Read (`slice`)

Once you know the target symbol, read **only** its exact source lines (`start_line..end_line`) and direct outgoing callee contracts—saving 85–95% of context window tokens compared to reading whole files:

### Via MCP Tool
- `vibetrace_get_code_slice(symbol_name="IndexerDB.upsert_node", include_callees=True)`

### Via CLI
```bash
uv run vibetrace slice IndexerDB.upsert_node
uv run vibetrace slice extract --file src/vibetrace/indexer/parsers/python_parser.py
```

---

## Keeping the Index Fresh

VibeTrace uses SHA-256 content-hash tracking (`indexed_files`) so incremental re-indexing skips unchanged files in milliseconds:

```bash
uv run vibetrace index .
# Optional: encode local EmbeddingGemma 2 (256d MRL) vectors
uv run vibetrace index . --embed
```

---

## 🌐 Monorepos & Multi-Repo Federation Workflow

When working in a workspace with multiple repositories (e.g. `frontend/`, `backend/`, `shared/`) or a monorepo (`packages/*`, `apps/*`):

### 1. Zero-Complexity Workspace Setup (`project init`)
Initialize the umbrella `.vibetrace-project.toml` manifest with zero configuration. It automatically adopts the directory name as the project name and discovers all sub-packages:

```bash
# Auto-detects all member repos and names the project after the current folder:
uv run vibetrace project init

# Or initialize as a federated project directly:
uv run vibetrace init --project

# Index all member repositories into federated storage:
uv run vibetrace project index
```

### 2. Inspecting Cross-Repository Contract Links (`project links`)
VibeTrace automatically matches client-side HTTP calls (e.g. Next.js `fetch("/api/v1/orders")`) to backend server route handlers (FastAPI `@app.post("/api/v1/orders")`):

- **MCP Tool:** `vibetrace_project_links()`
- **CLI:** `uv run vibetrace project links`

### 3. Federated Cross-Repo Blast Radius Guard (`project guard`)
Before modifying a data model, API route, or function in one repository, check all callers across **all** member repositories:

- **MCP Tool:** `vibetrace_project_guard(symbol="process_payment")`
- **CLI:** `uv run vibetrace project guard process_payment`

### 4. Federated vs. Scoped Search (`project search`)
Search across all repositories simultaneously or limit search to a single member service:

- **MCP Tool:** `vibetrace_project_search(query="checkout", repo="backend")`
- **CLI (All Repos):** `uv run vibetrace project search "checkout"`
- **CLI (Scoped):** `uv run vibetrace project search "checkout" --repo backend`

