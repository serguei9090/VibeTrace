# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
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
