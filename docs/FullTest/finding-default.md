# VibeTrace Comprehensive Audit: Default Mode Findings

**Timestamp:** 2026-11-09T19:36:00-05:00  
**Scope:** `src/` (Native Tree-sitter AST + LSP hybrid engine + Git-SHA content cache)

---

## 1. Indexing & Graph Construction Metrics (Step 1)
- **Status:** Complete (100% files parsed)
- **Engine:** PyO3 Hybrid Rust Engine (`_rust_core`) + Tree-sitter AST + EmbeddingGemma 2 (256d Matryoshka vectors)
- **Indexed Graph:**
  - Nodes: >15,000 structural entities (functions, classes, methods, imports)
  - Edges: >53,000 call, inheritance, and import relationships
  - LSP Resolution Percentage: **48.45%** on initial full cold-pass

---

## 2. Quality Audit - Default Mode (`vibetrace audit quality --path src --summary`)

- **Total Quality Findings:** 1,145
  - **High:** 203
  - **Medium:** 510
  - **Low:** 432
- **Baseline Exception Status:**
  - Approved Exceptions: 663
  - Modified Exceptions: 103
  - **Unapproved Findings Needing Attention:** 379
- **Complexity Metrics:**
  - Average Cognitive Complexity: **9.4**
  - Maximum Cognitive Complexity: **601** (`extract` in `crates/vibetrace-core/src/parser/mod.rs` & AST scanner)

### Summary Breakdown
| Severity | Rule ID / RSPEC | Unapproved | Approved | Key Impacted Files |
|:---|:---|:---:|:---:|:---|
| `MEDIUM` | `python:S134` (Deep nesting) | **216** | 132 | `ast_scanner.py`, `auto_patcher.py`, `base.py` |
| `MEDIUM` | `python:S1066` (Mergeable IFs) | **32** | 17 | `ast_scanner.py`, `auto_patcher.py`, `base.py` |
| `MEDIUM` | `typescript:S134` (Deep nesting) | **32** | 15 | `SplitAuditCard.tsx`, `graphTraversal.ts`, `packageGraph.ts` |
| `LOW` | `python:S1192` (String literals) | **32** | 325 | `ast_scanner.py`, `base.py`, `baseline.py` |
| `HIGH` | `python:S3776` (Cognitive complexity) | **23** | 146 | `ast_scanner.py`, `auto_patcher.py`, `base.py` |
| `MEDIUM` | `python:S5843` (Regex complexity) | **11** | 7 | `ast_scanner.py`, `contracts.py`, `git_secret_scanner.py` |
| `HIGH` | `typescript:S3776` (Cognitive complexity) | **10** | 18 | `CallerGuardBadge.tsx`, `CodeInspectorDrawer.tsx` |
| `MEDIUM` | `python:S1144` (Unused private methods) | **3** | 5 | `lsp_bridge.py`, `models.py`, `osv_client.py` |
| `MEDIUM` | `python:S4144` (Identical method implementations) | **3** | 4 | `refactor_service.py`, `smells.py` |
| `MEDIUM` | `python:S2612` (Insecure file permissions) | **3** | 0 | `hooks.py`, `tools_manager.py` |
| `MEDIUM` | `python:S3358` (Nested ternary operators) | **2** | 0 | `service.py`, `tools_manager.py` |

---

## 3. Security Audit - Default Mode (`vibetrace audit security --path src --summary`)

- **Total Security Findings:** 115
  - **Critical:** 4
  - **High:** 23
  - **Medium:** 67
  - **Low:** 21
- **Unapproved Findings:** 115 (No baseline suppressing security issues)

### Summary Breakdown
| Severity | CWE / Rule | Unapproved | Taint Path | Key Impacted Files |
|:---|:---|:---:|:---:|:---|
| `CRITICAL` | `CWE-502` (Deserialization of Untrusted Data) | **1** | Direct | `src/vibetrace/quality/rule_sync.py` |
| `CRITICAL` | `CWE-1395` (Dependency Vulnerability) | **3** | Lockfile | `uv.lock` |
| `HIGH` | `CWE-1395` (Dependency Vulnerability) | **23** | Lockfile | `Cargo.lock`, `uv.lock` |
| `MEDIUM` | `CWE-1395` (Dependency Vulnerability) | **48** | Lockfile | `Cargo.lock`, `uv.lock` |
| `MEDIUM` | `CWE-1078` (Weak Secret / ReDoS) | **18** | AST Code | `ast_scanner.py`, `contracts.py`, `git_secret_scanner.py` |
| `MEDIUM` | `CWE-327` (Use of Broken Cryptographic Algorithm) | **1** | AST Code | `crates/vibetrace-core/src/parser/mod.rs` |
| `LOW` | `CWE-1395` (Dependency Vulnerability) | **17** | Lockfile | `Cargo.lock`, `uv.lock` |
| `LOW` | `CWE-1078` (Weak Secret / Regex) | **4** | AST Code | `ast_scanner.py`, `git_secret_scanner.py`, `scip_importer.py` |
