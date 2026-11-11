# Resources

## Overview

Static package resources shipped with the VibeTrace Python distribution. Holds the agent skill definition (`SKILL.md`) describing the VibeTrace Navigator workflow, the bundled CWE taxonomy catalog used by the security engine, the bundled declarative security rule catalog used by the rule-sync engine, and the Sonar-grade lint presets consumed by the quality adapters.

## Interface

- [`SKILL.md`](./SKILL.md) — agent skill manifest (`name: vibetrace-navigator`) documenting the MCP tools, CLI commands, and REST endpoints of the VibeTrace workflow.
- [`__init__.py`](./__init__.py) — package marker with docstring `VibeTrace package resources.`; exports no symbols.
- [`cwe_catalog.json`](./cwe_catalog.json) — JSON object keyed by canonical CWE id (e.g. `CWE-5`); each entry carries `id`, `name`, `description`, and `owasp`. Loaded by `vibetrace.taxonomy.CweTaxonomyManager` through `vibetrace.taxonomy.CWE_CATALOG_PATH`.
- [`security_catalog.json`](./security_catalog.json) — JSON object with `version` and a `rules` array; each rule carries `id`, `cwe_id`, `severity`, `title`, `message`, `pattern`, `languages`, and `compliant_snippet`. Loaded by `vibetrace.security.rule_sync.RuleCatalogManager` through `vibetrace.security.rule_sync.BUNDLED_SECURITY_CATALOG_PATH`.
- Preset files consumed by the quality adapters (referenced by path, not imported):
  - `presets/biome_sonar.json` — Biome lint preset loaded by `vibetrace.quality.adapters.biome`.
  - `presets/pyright_sonar.json` — Pyright type-checker preset loaded by `vibetrace.quality.adapters.python_pyright`.
  - `presets/ruff_sonar.toml` — Ruff lint preset loaded by `vibetrace.quality.adapters.python_ruff`.

## Invariants

- `cwe_catalog.json` is a flat mapping of CWE ids to objects; the taxonomy loader normalizes any id spelling (`89`, `cwe-89`, `CWE_89`) to the canonical `CWE-<n>` form before lookup [src/vibetrace/taxonomy.py:normalize_cwe_id].
- `CweTaxonomyManager` only loads the catalog when `CWE_CATALOG_PATH` is a file and logs a warning when the file cannot be parsed; a missing catalog leaves the manager without entries [src/vibetrace/taxonomy.py:CweTaxonomyManager].
- `security_catalog.json` carries a `version` field and a `rules` array; rule ids follow the `<ecosystem>:<CWE>-<slug>` shape (e.g. `web:CWE-79-xss`) [src/vibetrace/resources/security_catalog.json].
- `RuleCatalogManager` only loads the bundled catalog when `BUNDLED_SECURITY_CATALOG_PATH` is a file [src/vibetrace/security/rule_sync.py:RuleCatalogManager].
- `presets/ruff_sonar.toml` pins `target-version = "py310"` and `line-length = 100`; `presets/biome_sonar.json` caps `noExcessiveCognitiveComplexity` at `maxAllowedComplexity: 15` and `useMaxParams` at `max: 7` [src/vibetrace/resources/presets/ruff_sonar.toml].
- `presets/pyright_sonar.json` sets `typeCheckingMode: "standard"` and reports optional-member access and general type issues as warnings [src/vibetrace/resources/presets/pyright_sonar.json].
- The security git-secret scanner excludes `security_catalog.json` from secret scanning so the bundled rule file is never reported as a finding [src/vibetrace/security/git_secret_scanner.py].

## Side Effects

- None. Every file in this directory is read-only package data; nothing here writes to disk, opens sockets, or mutates global state.
- Consumers read these files from disk at import or first use: `vibetrace.taxonomy.CweTaxonomyManager` reads `cwe_catalog.json`, `vibetrace.security.rule_sync.RuleCatalogManager` reads `security_catalog.json`, and the quality adapters read the files under `presets/` [src/vibetrace/taxonomy.py:CweTaxonomyManager].

## Verification

- Run: `uv run pytest`
