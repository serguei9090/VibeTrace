# VibeTrace Comprehensive Audit: Specialized Engines (SonarLint + Semgrep)

**Timestamp:** 2026-11-09T19:41:00-05:00  
**Scope:** `src/` (SonarLint Headless LSP + Semgrep SAST CLI + OSV Lockfile Intelligence)

---

## 1. Quality Audit - Specialized Tier-2 Engine (`vibetrace audit quality --path src --sonar --summary`)

- **Total Quality Findings:** **1,266** (vs 1,145 in default mode, **+121 additional specialized findings**, +10.5% detection lift)
  - **High:** 224 (vs 203 default)
  - **Medium:** 610 (vs 510 default)
  - **Low:** 432 (identical)
- **Approved Exceptions:** 663 | **Modified:** 104 | **Unapproved:** 499

### Specialized Detections Unlocked by SonarLint LSP
| Rule ID | Severity | Description | Count | Files Impacted |
|:---|:---:|:---|:---:|:---|
| `python:S8415` | `MEDIUM` | Unused FastAPI parameter or route dependency | **30** | `server_api.py`, `server_api_docs.py`, `server_api_engineering.py` |
| `python:S9409` | `MEDIUM` | Redundant type checks or isinstance collisions | **14** | `baseline.py`, `caller_guard.py`, `config.py` |
| `python:S1172` | `MEDIUM` | Unused method arguments in polyglot engines | **10** | `cli_project.py`, `golang_lint.py`, `lsp_bridge.py` |
| `python:S3516` | `MEDIUM` | Methods with identical return value branches | **9** | `cli_audit.py`, `cli_commands.py`, `cli_docs.py` |
| `python:S5713` | `MEDIUM` | Unhandled subprocess error or broad OS exception | **7** | `biome.py`, `db_queries.py`, `lsp_bridge.py` |
| `python:S8786` | `HIGH` | Catastrophic ReDoS regex vulnerability | **6** | `chunker.py`, `regex_analyzer.py`, `route_extractor.py` |
| `python:S8572` | `MEDIUM` | Unclosed SQLite or vector file resource handle | **5** | `sarif_ingest.py`, `vector_store.py`, `watcher.py` |
| `python:S5869` | `MEDIUM` | Duplicate regex character classes | **3** | `route_extractor.py`, `rule_sync.py` |
| `python:S1871` | `MEDIUM` | Duplicate `if-elif` code branches | **3** | `css_analyzer.py`, `rubocop.py`, `shellcheck.py` |
| `python:S8409` | `MEDIUM` | Missing HTTP status documentation | **3** | `server_api_docs.py`, `server_api_engineering.py` |

---

## 2. Security Audit - Specialized Tier-2 Engine (`vibetrace audit security --sonar --summary`)

- **Total Security Findings:** **122** (vs 115 in default mode, **+7 specialized findings**)
  - **Critical:** 4
  - **High:** 29 (vs 23 default, **+6 ReDoS vulnerabilities**)
  - **Medium:** 68 (vs 67 default, **+1 CWE-732 permission flaw**)
  - **Low:** 21 (identical)

### Key Vulnerabilities Detected
1. **`CRITICAL` CWE-502 (Deserialization of Untrusted Data):**
   - File: `src/vibetrace/quality/rule_sync.py`
2. **`HIGH` CWE-1333 (Regular Expression Denial of Service - ReDoS):**
   - Files: `chunker.py`, `regex_analyzer.py`, `route_extractor.py` (6 instances identified via Sonar AST + regex analyzer)
3. **`MEDIUM` CWE-732 (Incorrect Permission Assignment to Critical Resource):**
   - File: `src/vibetrace/hooks.py`
4. **`CRITICAL` & `HIGH` CWE-1395 (Third-Party Vulnerabilities in Lockfiles):**
   - `uv.lock` & `Cargo.lock`
