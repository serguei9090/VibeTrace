# EGM Contract Drift Report — `src/` README Contracts

* **Author:** serguei9090
* **Date:** 2026-11-11
* **Scope:** 26 EGM contract files generated under `src/` (Peanuts and Hay READMEs)
* **Method:** `uv run vibetrace docs check --path . --json` plus two purpose-built
  verification scripts (citation resolution and code-to-document coverage)

## Overview

This report checks the generated `src/` README contracts against the source code in
two directions:

1. **Document to code** — every code citation in a contract must point at a file and
   symbol that exist.
2. **Code to document** — every public module-level symbol in a directory must be
   mentioned in that directory's contract.

Two tools were used because `vibetrace docs check` resolves symbols against a
codebase AST that does not contain class attributes, database column names,
configuration keys, or exception names. Those symbols appear as broken links in the
tool output even though they are correct in the contracts. The raw tool output is
therefore not a usable pass/fail signal on its own, and every finding was
re-verified against the source text.

## Tool Results

| Check | Result |
| --- | --- |
| `vibetrace docs check --path . --json` (whole repository) | 198 document files, 1759 symbol references, 570 flagged, `passed: false` |
| Findings inside `src/` contracts | 243 (all 26 contracts included) |
| Flagged symbols absent from all of `src/` source | 0 after fixes |
| Citations resolved inside the 26 contracts | 621 checked, 0 broken |
| Public module-level symbols missing from their contract | 0 after fixes |

Most of the 570 repository-wide findings are in files outside `src/`: skill
definitions, agent persona files, and the root `README.md`. Those were not part of
this bootstrap and are out of scope.

## Findings Fixed

### Document-to-code errors (hallucinations)

| Contract | Problem | Fix |
| --- | --- | --- |
| `src/web/src/features/map-room/hooks/README.md` | Claimed `setState` calls in `useMapRoomState`. No such identifier exists; the hook uses `useState` setters such as `setNodes`, `setEdges`, and `setRevealedNodeIds`. | Restated the side effect with the real setter names. |
| `src/web/src/features/map-room/utils/README.md` | Cited `elkLayout` as the module-level ELK instance in `elkLayout.ts`. The identifier in that file is `elk` (`const elk = new ELK()`). | Citation corrected to `elk` and the declaration quoted. |

### Missing coverage (code present, contract silent)

95 public module-level symbols had no mention in their directory contract. All were
added, grouped by directory:

| Contract | Symbols added |
| --- | --- |
| `src/vibetrace/README.md` | `CHARS_PER_TOKEN`, `DEFAULT_VIBE_BUNDLE_BUDGET`, `DEFAULT_REPO_MAP_BUDGET`, `DEFAULT_MAX_DEPTH`, `MAX_TRAVERSAL_DEPTH`, `DEFAULT_SAVED_TRACE_DEPTH`, `get_config_path`, `register_config_parser`, `register_project_parser`, `install_skill`, `initialize_workspace`, `configure_gitignore`, `ReindexPayload`, `AgentFocusPayload`, `SplitApprovePayload`, `SecurityApprovePayload`, `SecurityRemediatePayload`, `QualityApprovePayload`, `ConnectionManager`, `AddCatalogRequest`, `IndexCatalogRequest`, `PatchDepsRequest`, `RefactorSplitRequest`, and the 31 route handler functions in `server_api.py` |
| `src/vibetrace/indexer/README.md` | `try_load_sqlite_vec`, `HOOK_SCRIPT_BLOCK`, `LSP_SERVER_CANDIDATES`, `EXT_TO_LANGUAGE`, `PythonFileDelta`, `PythonWorkspaceScan` |
| `src/vibetrace/indexer/parsers/README.md` | `FASTAPI_ROUTE_RE`, `FLASK_ROUTE_RE`, `FLASK_METHODS_RE`, `API_ROUTER_RE`, `BLUEPRINT_RE`, `API_ROUTE_RE`, `SPRING_MAPPING_RE`, `SPRING_REQUEST_MAPPING_RE`, `CSHARP_HTTP_RE`, `CSHARP_ROUTE_RE` |
| `src/vibetrace/quality/README.md` | `NATIVE_RULE_KEYS`, `QUALITY_BASELINE_FILE`, `BUILTIN_DECLARATIVE_QUALITY_RULES` |
| `src/vibetrace/quality/adapters/README.md` | `BUNDLED_PRESET_PATH` in `biome.py`, `python_pyright.py`, and `python_ruff.py` |
| `src/vibetrace/security/README.md` | `SHARED_SANITIZERS`, `PYTHON_SANITIZERS`, `JS_TS_SANITIZERS`, `GO_SANITIZERS`, `CSHARP_SANITIZERS`, `MAX_DEPTH`, `OSV_QUERY_URL`, `OSV_QUERYBATCH_URL`, `PINNED_CATALOG_REF`, `DEFAULT_RULES_FEED_URL`, `BUNDLED_SECURITY_CATALOG_PATH`, `TRUSTED_COMMUNITY_YAML_FEEDS` |
| `src/web/src/features/map-room/hooks/README.md` | `UseVibeSyncOptions` |

## Checks That Passed Without Change

- Every HTTP route listed in `src/vibetrace/README.md` exists as a decorator in
  `server_api.py` or one of the mounted routers.
- Every documentation route listed in `src/vibetrace/README.md` matches the
  `docs_router` prefix `/api/docs` in `server_api_docs.py`.
- Every relative link in the 26 contracts points at a file that exists.
- All cited files and symbols resolve to the source text of the cited file.

## Known Limits of This Report

- The coverage check compares names, not behavior. A symbol can be listed and still
  be described inaccurately. Descriptions added in this pass were read from the
  source before being written.
- `vibetrace docs check` still exits with code 1 because of findings in files
  outside `src/` and because its AST does not model attributes, table columns, or
  configuration keys. That exit code is not a contract defect signal.
- Test files and generated assets under `src/web/e2e`, `src/web/public`,
  `src/web/src/assets`, and `src/vibetrace/resources/presets` stay outside the
  contract scope.

## Reproduction

Scratch scripts live in `docs/reviews_temp/` (gitignored):

```bash
uv run vibetrace docs check --path . --json > docs/reviews_temp/drift_raw.json
uv run python docs/reviews_temp/verify_drift.py
uv run python docs/reviews_temp/check_citations.py
uv run python docs/reviews_temp/check_coverage.py
```
