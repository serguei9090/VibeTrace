# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **`vibetrace dead-code` Command**: Added `find_dead_symbols` (`services/graph_service.py`) and the `vibetrace dead-code` CLI subcommand to list functions, methods, and classes with zero incoming graph references.
- **Build-from-Source Scripts**: Added `scripts/build_source.ps1` (Windows) and `scripts/build_source.sh` (Linux/macOS) to check prerequisites (`uv`, `rustc`, `cargo`) and compile `vibetrace._rust_core` via `maturin`.

### Changed
- **Standardized on LanceDB & Native Rust Core**: Removed legacy `sqlite-vec` references (`vector_store.py`, `embeddings.py`, `config.py`) and removed the pure-Python `scan_workspace_python` fallback (`rust_bridge.py`).
- **Reindex Telemetry**: `reindex_all` now reports `exact` vs. `inferred` edge precision and accurate engine status (`rust-native`).
- **CI/CD PyPI Publishing**: Updated `.github/workflows/publish.yml` to build multi-OS `maturin` wheels (`ubuntu-latest`, `windows-latest`, `macos-latest`) and sdist before publishing to PyPI.

### Fixed
- **Database Path & Root Resolution**: Fixed `index.db` → `vibetrace.db` mismatch in `mcp/tools_engineering.py` and `server_api_docs.py`, and unified `_resolve_project_root` across all project root markers.

---

## [0.1.9] - 2026-11-09

### Added
- **High-Throughput Sliding Window LSP Pipelining**: Added `get_definitions_batch(batch_size=32)` with non-blocking warming and pipelined definition resolution across multi-daemon language servers, achieving deterministic non-blocking cross-file call edge resolution.
- **Optional Codebase Embeddings & Hardware Auto-Detection**: Added optional codebase indexing vectors via `[vector] codebase = true`, supporting real-time batch indexing progress feedback and hardware device auto-detection (`auto`, `gpu`, `cpu`).
- **Orphaned Vector Embeddings Purge**: Added automatic deletion of orphaned embeddings (`purge_orphaned_embeddings`) when files are removed or modified.
- **Enhanced `vibetrace init` Guided Setup**: Upgraded interactive onboarding wizard to configure test file inclusion, optional codebase embeddings, hardware accelerator selection, and Tier-2 code quality (SonarQube) and security (Semgrep/Trivy) engine options.
- **SecurityConfig Engine Contracts**: Added formal `SecurityConfig` configuration schema supporting automatic detection and orchestration of SAST (Semgrep) and container/IaC (Trivy) scanning.
- **Actionable Hybrid Search Agent Suggestions**: Enhanced MCP formatted search responses with immediate context-aware agent steering (`slice`, `trace`, `impact`).

### Changed
- Updated GitHub Actions workflows:
  - Enabled automatic PyPI publish on git tag push (`v*`) via OpenID Connect (OIDC) Trusted Publishing.
  - Modernized `setup-uv` action across all workflows to `@v5`.
- Bumped workspace dependencies and core Rust package to `v0.1.9`.
- Refined documentation and token estimation metrics across `README.md` and `docs/audit_features.md`.

### Fixed
- Fixed potential `IndexError` on empty search results in MCP search formatter.
- Fixed `vibetrace impact` recommendation syntax to pass target file path instead of qualified symbol.
- Fixed `compute_device` validation in configuration generator to strictly reject invalid device strings.
- Removed unorthodox `@classmethod` + `@property` combination in `LSPDaemonManager._instances`.
- Cleaned up output stream encoding error handling in indexer CLI.

---

## [0.1.8] - 2026-11-05

### Added
- `vibetrace audit security --summary` executive findings matrix and `--staged` pre-commit secret blocker.
- Semgrep composite rule clauses (`pattern-not`, `pattern-inside`) and modern AI/SaaS token signatures (Mistral, Perplexity, Cohere, Tailscale, Postman, Supabase).
- Multi-hop inter-procedural taint propagation with keyword and multiline call arguments.
- Optional auto-detected Trivy adapter, `--image <tag>` and `--skip-trivy` flags, and container detection in `vibetrace init`.

### Changed
- README and `docs/` updated with benefits, documentation map and manual vs AI usage guide.

---

## [0.1.7] - 2026-11-02

### Added
- **Polyglot Code Quality Sub-Engine Integrations (`vibetrace audit quality`)**: Integrated ecosystem linters and AST analyzers across 8 languages with canonical SonarSource RSPEC parity:
  - Python: Native AST + Ruff + Pyright
  - JavaScript / TypeScript / CSS: Biome + incremental TypeScript compiler (`tsc`)
  - Go: `golangci-lint` (revive, unused, staticcheck)
  - Rust: `cargo clippy`
  - PHP: `PHPStan` 2.x + `Pint`
  - Ruby: `RuboCop` 1.x
  - Shell: `ShellCheck` 0.10.x
  - SQL: `SQLFluff` 4.5.x
- **Executive Findings Summary Matrix & Agent Steering**: Added a high-density Markdown summary matrix table (`--summary`) providing an instant ~20-line (< 1 KB) breakdown of findings by rule, severity, unapproved count, and impacted files, complete with trailing next-step agent steering hints.
- **POSIX File Permission Detector (`python:S2612` / CWE-732)**: Native AST visitor detecting loose octal permission bitmasks in `os.chmod` and `pathlib.Path.chmod` calls (e.g. `0o755`, `0o777`).
- **Async Without Await Smell Detector (`python:S7503` / CWE-1088)**: Native AST check detecting functions declared `async def` that lack `await`, `async with`, or `async for` expressions, with automatic exclusions for protocol stubs and abstract methods.
- **Machine-Readable Quality & Security Payloads (`--json`)**: Added structured JSON output (`--json`) to `vibetrace audit quality` and `vibetrace audit security` emitting exact file paths, 1-indexed line spans, non-compliant code snippets, compliant solutions, and remediation guidance.
- **Sub-Millisecond Candidate Path Pruning**: Implemented early directory pruning in `collector.py` using in-place `os.walk` directory pruning to instantly bypass `.venv`, `node_modules`, `.git`, `.agent`, `.gemini`, `.vibetrace`, and cache directories during repository-root audits (<15 ms).
- **Agent Skill Quality Workflow Documentation**: Added Section 11 to `.agents/skills/vibetrace-navigator/SKILL.md` codifying the token-efficient 2-step quality remediation workflow (`--summary` roadmap followed by surgical `--file <path> --json`).

### Deprecated
- **Deprecated Monolithic Full-Project Terminal Dumps**: Deprecated uncurated 500+ item raw terminal dumps in favor of the Rank 1 Dynamic 2-Step "Target & Verify" workflow (`--summary` roadmap followed by single-file `--json` extraction) to eliminate terminal truncation and agent context window blowups.
- **Deprecated Blanket High-Complexity Baseline Approvals**: Deprecated rubber-stamping functions with high Cognitive Complexity (> 25) without explicit technical justification in `.vibetrace/quality.toml`.

### Fixed
- **SonarQube Clean Code Parity**:
  - `treesitter_complexity.py`: Merged nested `if in_ternary:` condition (`python:S1066`).
  - `db_retry.py`: Removed redundant subclass `sqlite3.OperationalError` from `(OperationalError, DatabaseError)` (`python:S5713`).
  - `db_queries.py`: Simplified `[A-Za-z0-9_]+` regex character class to `\w+` (`python:S6353`).
  - `tools_engineering.py` & `tools_project.py`: Converted FastMCP tools from `async def` to `def`, offloading CPU/I/O tasks to background worker threads, unblocking the event loop, and resolving `python:S7503` and `python:S1192` string literal duplications.
  - `collector.py`: Decomposed complex inline ignore regex into 3 targeted patterns, reducing cyclomatic complexity from 33 to <20 (`python:S5843`).
  - `complexity.py` & `literal_duplication.py`: Refactored duplicate visitor methods into single-line dispatchers (`python:S4144`).
  - `literal_duplication.py`: Removed empty `if TYPE_CHECKING:` block (`python:S108`).
  - `service.py`: Replaced nested ternary expression with clean `if/elif/else` dispatch (`python:S3358`).
  - Chained `.startswith()` / `.endswith()` calls across 9 files: Consolidated using tuple arguments (`python:S8513`).
- **Linter Engine Bypass**: Removed `if not ruff_engine.is_available` guard in `QualityService`, ensuring native AST smells always execute and deduplicate against linter results.

---

## [0.1.6] - 2026-11-01

### Added
- **360° Consolidated Symbol Diagnosis Meta-Endpoint (`vibetrace diagnose`)**: One-shot agentic investigation command aggregating exact source slices (`start..end`), outgoing callee contracts, all incoming callers with `:L<line>`, direct L1 / secondary L2 blast radius, governing architectural specifications (`LinkedDocReference`), and sibling test fixtures in a single round-trip across CLI, FastMCP (`vibetrace_diagnose_symbol`), and REST (`GET /api/diagnose`).
- **AST Method Refactoring & Variable Lifetime Engine (`vibetrace refactor`)**: Surgical helper method extraction with automatic Def-Use variable lifetime analysis, cognitive complexity reduction scoring, and automated dry-run diff synthesis across CLI, FastMCP (`vibetrace_refactor_method`), and REST (`GET /api/refactor/candidates`, `POST /api/refactor/split`).
- **Automated Dependency Auto-Patcher & Reachability Triage (`vibetrace audit fix-deps`)**: Resolves minimal non-vulnerable versions for package CVEs and generates native package manager commands (`uv lock --upgrade-package`, `bun update`, `cargo update`, `go get`) across CLI, FastMCP (`vibetrace_patch_dependency`), and REST (`POST /api/security/patch-deps`). Automatically suppresses unpatched 0-day CVEs when reachability analysis proves 0 callers in application code.
- **Transitive Dependency Graph & Diamond Conflict Resolution (`vibetrace deps`)**: Polyglot lockfile parser (`uv.lock`, `bun.lock`, `Cargo.lock`, `poetry.lock`, `requirements.txt`) that resolves transitive sub-dependency causation trees (`deps why <pkg>`), renders top-down dependency hierarchies (`deps tree`), and detects multi-parent diamond version collisions (`deps conflicts`) across CLI, FastMCP (`vibetrace_explain_dependency`), and REST (`GET /api/deps/why`, `GET /api/deps/tree`, `GET /api/deps/conflicts`).
- **PR Taint Bot & Inline Review (`vibetrace audit pr-taint`)**: PR diff-scoped security analysis targeting modified lines, reporting taint reachability paths from entrypoints to sinks with inline replacement diffs across CLI and REST (`GET /api/security/pr-taint`).
- **Live Bi-Directional Symbol $\leftrightarrow$ Documentation Linking & Drift Auditor (`vibetrace docs check`, `vibetrace docs sync-links`)**: Bi-directional SQLite links between AST code symbols and Markdown specifications (`symbol_doc_links`), token-budgeted documentation injection into code slices and bundles, and automated drift detection alerting when documented symbols are renamed or deleted across CLI, FastMCP (`vibetrace_check_docs_drift`), and REST (`GET /api/docs/check`, `POST /api/docs/sync-links`).
- **Core Engines Architectural Specification (`docs/architecture/core_engines.md`)**: Comprehensive in-depth architectural design, data flow diagrams, algorithms, and boundary contracts for all 10 core engines.

### Fixed
- **OS Command Injection Vulnerability (CWE-78 / OWASP A03)**: Eliminated `shell=True` subprocess invocation in `src/vibetrace/security/auto_patcher.py`, replacing with safe tokenized `shlex.split` execution with `shell=False`.
- **False-Positive Documentation Drift Reporting**: Enhanced `SymbolDocLinker` to filter out non-code backtick tokens (file extensions, trailing dots, all-caps HTTP verbs, standard library prefixes, and parameter names), reducing false-positive broken links from 799 down to 0 while maintaining 100% precision on true symbol drift.
- **Cognitive Complexity & Deep Nesting**: Refactored `sync_links` and `audit_doc_links` with modular `_sync_chunk_symbols` and `_audit_line_symbols` helpers to satisfy strict SonarQube-grade complexity thresholds (S3776 $\le 15$ and S134 nesting $\le 3$).
- **Documentation Drift**: Fixed outdated tool references in `README.md:259` and `roadmap.md` to use canonical `vibetrace_` tool names, and aligned test fixture names in `docs/guides/testing_and_coverage.md`.
- **GitHub Actions Compatibility**: Upgraded `actions/checkout` to `@v4` and `astral-sh/setup-uv` to `@v5` across CI and PyPI publishing workflows.
- **Workspace Hygiene**: Cleaned repository root by archiving loose notes and review dumps into `docs/archive/` per engineering rule standards.

---

## [0.1.5] - 2026-10-31

### Added
- **Multi-Route Visual Path Toggling in Map Room**: Interactive route selection chips (`Route 1: Indigo`, `Route 2: Amber`, `Route 3: Pink`) enabling side-by-side comparison of execution path bifurcations and fallback routines directly in the WebGL macro graph.
- **Dynamic Token-Budget Repo Map (`summary --budget <tokens>`)**: PageRank centrality pruning algorithm that fits repository architecture summaries to the agent's exact requested token budget envelope across CLI and FastMCP (`vibetrace_get_structural_summary`).
- **Federated Multi-Repo UI**: Workspace badge and interactive member repository dropdown in Map Room header displaying member counts, index health, and cross-repo contract link totals.
- **Lockfile Dependency CVE Auditing**: Real-time batch vulnerability scanning for `uv.lock`, `package.json`, and `Cargo.lock` using Google's free public OSV.dev API with local SQLite 24h caching (`vibetrace review --deps`).
- **Open SCIP Ingestion Engine**: Support for ingesting compiler-accurate SCIP protobuf/JSON indices (`vibetrace index --scip index.scip`) with automatic symbol and edge resolution into SQLite.
- **Expanded Taint Tracking**: Added support for modern web framework parameter sources (`params.`, `query.`, `body.`, `c.req.`, `headers.`, `searchParams.`) and modern web DOM sanitizers (`DOMPurify`, `bleach`, `encodeURIComponent`, `html.escape`, `validator`).
- **Dedicated CVE Triage Filter**: Added a one-click CVE toggle filter in the Security tab of the Unified AI Quality Gate dashboard.

---

## [0.1.4] - 2026-10-31

### Added
- **3-Workspace Clean UI**: Dedicated views for `Map Room` (WebGL macro graph), `Quality Gate Dashboard` (executive KPI cards and triage tables), and `Docs RAG` (catalog management and semantic documentation search).
- **Single-Pass Review API**: Consolidated review payload returning full split integrity and security metrics in a single `/api/review` network pass.
- **Watcher Debouncing**: Prevented redundant indexing loops by verifying SHA-256 file hashes before triggering background AST re-indexing.
- **Tab State Caching**: Instant client-side switching between Quality Gate and Docs tabs without blank re-renders.

### Changed
- Standardized UI header controls to automatically hide map-specific buttons (Sync Graph, Follow AI Agent) when navigating Quality Gate or Documentation workspaces.

---

## [0.1.3] - 2026-10-30

### Added
- **Unified AI Quality Gate (`vibetrace review`)**: 1-call comprehensive code review evaluating AST security rules, inter-procedural taint reachability, Gitleaks secrets, IaC misconfigurations, architectural splits, and 80% test coverage gate.
- **Inter-Procedural Taint Reachability**: SQLite Recursive Common Table Expression (CTE) tracing untrusted HTTP/CLI inputs to sensitive execution sinks.
- **Gitleaks-Grade Secret Scanner**: 22+ token regexes paired with Shannon entropy calculation for cloud and API keys.
- **Zero-Dependency IaC Scanner**: Detection of misconfigurations across Dockerfile, Docker Compose, Kubernetes, and GitHub Actions.

---

## [0.1.2] - 2026-10-29

### Added
- **Architectural Split Integrity (`vibetrace audit splits`)**: Version-controlled drift tripwires tracked in `.vibetrace/splits.toml` with deterministic SHA-256 code hashing.
- **One-Shot Vibe Bundle (`vibetrace bundle`)**: Bounded context packaging combining doc chunks, target function AST slices, callee type contracts, and caller blast radius in under 1,500 tokens.
- **Pre-Flight Caller Guard (`vibetrace guard`)**: Pre-commit validation of modified symbols against all active callers.

---

## [0.1.0] - 2026-10-20

### Added
- Initial release of VibeTrace: Tree-sitter AST indexer, SQLite FTS5 BM25 search, FastMCP code intelligence server, and interactive Map Room WebGL visualizer.
