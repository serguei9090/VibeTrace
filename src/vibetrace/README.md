# vibetrace

## Overview

Python package for VibeTrace, a code intelligence and execution trace tool. This
directory holds the top-level modules that make up the command line interface, the
HTTP API server, project configuration, and the external tool installer. The
subsystem packages below it hold the indexing, analysis, and service logic that
these modules call into.

- `cli.py` defines the `argparse` parser and the `main` entry point for the
  `vibetrace` console script (`[src/vibetrace/cli.py:build_parser]`,
  `[src/vibetrace/cli.py:main]`).
- `cli_commands.py`, `cli_audit.py`, `cli_config.py`, `cli_deps.py`, `cli_docs.py`,
  `cli_project.py`, `cli_refactor.py`, `cli_tools.py`, and `cli_vibe.py` implement
  the individual subcommands.
- `server_api.py` builds the FastAPI application (`[src/vibetrace/server_api.py:app]`)
  and mounts the routers from `server_api_docs.py`, `server_api_project.py`,
  `server_api_engineering.py`, and `server_api_saved_traces.py`.
- `config.py` owns the `.vibetrace.toml` schema and loader
  (`[src/vibetrace/config.py:VibeTraceConfig]`, `[src/vibetrace/config.py:load_config]`,
  `[src/vibetrace/config.py:get_config_path]`).
- `constants.py` holds the shared token-budget and traversal limits
  (`[src/vibetrace/constants.py:CHARS_PER_TOKEN]`).
- `taxonomy.py` loads the bundled CWE catalog
  (`[src/vibetrace/taxonomy.py:CweTaxonomyManager]`,
  `[src/vibetrace/taxonomy.py:CWE_CATALOG_PATH]`).
- `tools_manager.py` installs and tracks optional external tools
  (`[src/vibetrace/tools_manager.py:ToolsManager]`,
  `[src/vibetrace/tools_manager.py:ToolStatus]`).
- `skill_installer.py` installs the bundled agent skill into a project
  (`[src/vibetrace/skill_installer.py:detect_project_ecosystems]`,
  `[src/vibetrace/skill_installer.py:get_bundled_skill_content]`,
  `[src/vibetrace/skill_installer.py:install_skill]`,
  `[src/vibetrace/skill_installer.py:initialize_workspace]`,
  `[src/vibetrace/skill_installer.py:configure_gitignore]`).

## Interface

- Console script `vibetrace` -> `vibetrace.cli:main`; console script
  `vibetrace-mcp` -> `vibetrace.mcp.server:main`
  (`[pyproject.toml:[project.scripts]]`).
- `build_parser()` assembles the CLI parser and registers every subcommand
  (`[src/vibetrace/cli.py:build_parser]`), including the `config` and `project`
  command groups (`[src/vibetrace/cli_config.py:register_config_parser]`,
  `[src/vibetrace/cli_project.py:register_project_parser]`).
- Command handlers registered on the parser:
  - Index and navigation: `cmd_index`, `cmd_search`, `cmd_trace`, `cmd_slice`,
    `cmd_call_tree`, `cmd_call_stack`, `cmd_impact`, `cmd_references`,
    `cmd_dependencies`, `cmd_summary`, `cmd_test`, `cmd_diagnose`
    (`[src/vibetrace/cli_commands.py:cmd_index]`).
  - Audit and review: `cmd_audit_splits`, `cmd_audit_security`, `cmd_audit_quality`,
    `cmd_review`, `cmd_audit_fix_deps`, `cmd_audit_pr_taint`
    (`[src/vibetrace/cli_audit.py:cmd_audit_splits]`).
  - Project and tooling: `cmd_init`, `cmd_hooks`, `cmd_serve`, `cmd_skill`, `cmd_mcp`
    (`[src/vibetrace/cli_commands.py:cmd_init]`), `cmd_project`
    (`[src/vibetrace/cli_project.py:cmd_project]`), `cmd_tools`
    (`[src/vibetrace/cli_tools.py:cmd_tools]`), `cmd_config_quality`
    (`[src/vibetrace/cli_config.py:cmd_config_quality]`).
  - Dependencies, docs, refactoring, guard: `cmd_deps`
    (`[src/vibetrace/cli_deps.py:cmd_deps]`), `cmd_docs`
    (`[src/vibetrace/cli_docs.py:cmd_docs]`), `cmd_refactor`
    (`[src/vibetrace/cli_refactor.py:cmd_refactor]`), `cmd_guard` and `cmd_bundle`
    (`[src/vibetrace/cli_vibe.py:cmd_guard]`).
- HTTP API: `app = FastAPI(title="VibeTrace API", lifespan=lifespan)`
  (`[src/vibetrace/server_api.py:app]`) with routes `/graph`, `/trace`,
  `/api/reachable`, `/api/splits/audit`, `/api/splits/approve`, `/api/security/audit`,
  `/api/security/approve`, `/api/security/remediate`, `/api/quality/audit`,
  `/api/quality/approve`, `/call-tree`, `/call-stack`, `/source`, `/api/search`,
  `/api/search/signature`, `/api/agent-focus`, `/api/impact`, `/api/slice`,
  `/api/references`, `/api/vibe/guard`, `/api/vibe/bundle`, `/api/dependencies`,
  `/api/summary`, `/api/reindex`, `/api/review`, `/api/skill`, `/api/hooks`
  (`[src/vibetrace/server_api.py:app.get]`). Route handlers: `get_graph`,
  `get_trace`, `get_reachable_symbols`, `get_splits_audit`, `approve_split_endpoint`,
  `get_security_audit_endpoint`, `approve_security_exception_endpoint`,
  `remediate_security_finding_endpoint`, `get_quality_audit_endpoint`,
  `approve_quality_exception_endpoint`, `get_call_tree`, `get_call_stack_endpoint`,
  `get_source`, `search_nodes_endpoint`, `search_signature_endpoint`,
  `post_agent_focus`, `get_impact_endpoint`, `get_slice_endpoint`,
  `get_references_endpoint`, `get_vibe_guard_endpoint`, `get_vibe_bundle_endpoint`,
  `get_dependencies_endpoint`, `get_summary_endpoint`, `post_reindex_endpoint`,
  `get_unified_review_endpoint`, `get_skill_endpoint`, `post_skill_endpoint`,
  `get_hooks_endpoint`, `post_hooks_endpoint`, `get_node_neighbors`, and the
  WebSocket `websocket_endpoint` (`[src/vibetrace/server_api.py:websocket_endpoint]`).
- Router factories: `get_engineering_router`
  (`[src/vibetrace/server_api_engineering.py:get_engineering_router]`),
  `get_project_router` (`[src/vibetrace/server_api_project.py:get_project_router]`),
  `get_saved_traces_router`
  (`[src/vibetrace/server_api_saved_traces.py:get_saved_traces_router]`), plus the
  documentation endpoints in `server_api_docs.py`
  (`[src/vibetrace/server_api_docs.py:index_documentation]`): `/api/docs/catalogs`
  (`list_catalogs`, `add_catalog`, `remove_catalog`), `/api/docs/index`
  (`index_documentation`), `/api/docs/search` (`search_documentation`),
  `/api/docs/check` (`check_docs_drift`), and `/api/docs/sync-links`
  (`sync_doc_links`).
- Request payloads: `ReindexPayload`, `AgentFocusPayload`, `SplitApprovePayload`,
  `SecurityApprovePayload`, `SecurityRemediatePayload`, `QualityApprovePayload`
  (`[src/vibetrace/server_api.py:ReindexPayload]`), plus the `ConnectionManager`
  that tracks live WebSocket clients
  (`[src/vibetrace/server_api.py:ConnectionManager]`).
- Router request payloads: `AddCatalogRequest`, `IndexCatalogRequest`
  (`[src/vibetrace/server_api_docs.py:AddCatalogRequest]`), `PatchDepsRequest`,
  `RefactorSplitRequest`
  (`[src/vibetrace/server_api_engineering.py:PatchDepsRequest]`).
- Configuration models: `ProjectConfig`, `DatabaseConfig`, `VectorConfig`,
  `IndexerConfig`, `LinterConfigs`, `QualityConfig`, `SecurityConfig`,
  `VibeTraceConfig` (`[src/vibetrace/config.py:VibeTraceConfig]`), with
  `load_config`, `save_default_config`, `resolve_linter_mode`, and
  `save_linter_mode` (`[src/vibetrace/config.py:load_config]`).
- Shared limits: `CHARS_PER_TOKEN`, `DEFAULT_VIBE_BUNDLE_BUDGET`,
  `DEFAULT_REPO_MAP_BUDGET`, `DEFAULT_MAX_DEPTH`, `MAX_TRAVERSAL_DEPTH`, and
  `DEFAULT_SAVED_TRACE_DEPTH` (`[src/vibetrace/constants.py:CHARS_PER_TOKEN]`).
- Shared exclude set for scanned directories: `IGNORED_DIRS` and
  `IGNORED_DIRS_CASEFOLDED` (`[src/vibetrace/constants.py:IGNORED_DIRS]`).
- CWE helpers: `normalize_cwe_id`, `CweTaxonomyManager`
  (`[src/vibetrace/taxonomy.py:normalize_cwe_id]`).
- Tool installation: `ToolsManager`, `ToolStatus`, and the pinned download URL
  `PHPSTAN_PHAR_URL` (`[src/vibetrace/tools_manager.py:PHPSTAN_PHAR_URL]`).
- Skill installation: `detect_project_ecosystems`, `get_bundled_skill_content`,
  and the ignore list `VIBETRACE_IGNORE_PATTERNS`
  (`[src/vibetrace/skill_installer.py:VIBETRACE_IGNORE_PATTERNS]`).
- Subpackage contracts:
  - [docs](./docs/README.md) - documentation RAG: chunking, FTS5 plus vector retrieval,
    symbol to specification linking.
  - [federation](./federation/README.md) - multi-repository workspace federation over
    SQLite or PostgreSQL.
  - [indexer](./indexer/README.md) - tree-sitter indexing, graph persistence, FTS5 and
    optional vector storage.
  - [indexer/parsers](./indexer/parsers/README.md) - per-language tree-sitter
    extractors producing nodes and edges.
  - [mcp](./mcp/README.md) - FastMCP server exposing read-only, engineering, and
    federated project tools.
  - [quality](./quality/README.md) - structural quality rules, baseline management,
    and codemods.
  - [quality/adapters](./quality/adapters/README.md) - external linter and type
    checker adapters.
  - [refactor](./refactor/README.md) - data contracts for AST method extraction.
  - [resources](./resources/README.md) - bundled skill definition, CWE catalog,
    security rule catalog, and linter presets.
  - [security](./security/README.md) - AST and heuristic security scanning, taint
    reachability, CVE lookup, and SARIF ingestion.
  - [security/adapters](./security/adapters/README.md) - Semgrep and Trivy adapters.
  - [services](./services/README.md) - domain services for graph traversal, tracing,
    coverage gating, review, and context bundling.

## Invariants

- The package requires Python 3.11 or newer and is built by hatchling with
  `packages = ["src/vibetrace"]` (`[pyproject.toml:[project]]`,
  `[pyproject.toml:[tool.hatch.build.targets.wheel]]`).
- The `vibetrace` console script must resolve to `vibetrace.cli:main`
  (`[pyproject.toml:[project.scripts]]`).
- The default configuration writes the index database to `.vibetrace/vibetrace.db`
  and the vector store to `.vibetrace/vectors.lance` with 256 dimensions
  (`[src/vibetrace/config.py:generate_default_config_toml]`).
- `load_config` returns the default configuration when no `.vibetrace.toml` is
  present (`[src/vibetrace/config.py:load_config]`).
- `normalize_cwe_id` canonicalizes any CWE spelling to the `CWE-<n>` form
  (`[src/vibetrace/taxonomy.py:normalize_cwe_id]`).
- The CWE catalog is only loaded when `CWE_CATALOG_PATH` is a readable file; a
  missing or unparsable catalog leaves the manager without entries
  (`[src/vibetrace/taxonomy.py:CweTaxonomyManager]`).
- The FastAPI application is created with a lifespan handler and mounts the docs,
  saved traces, project, and engineering routers
  (`[src/vibetrace/server_api.py:app]`).

## Side Effects

- The CLI reads and writes project configuration in `.vibetrace.toml` and the
  global config in the user home directory
  (`[src/vibetrace/config.py:save_default_config]`,
  `[src/vibetrace/config.py:save_linter_mode]`).
- `cmd_init` writes project configuration, installs the agent skill, and triggers
  an initial index (`[src/vibetrace/cli_commands.py:cmd_init]`).
- The API server opens the index database and serves HTTP and WebSocket traffic on
  the configured port (`[src/vibetrace/server_api.py:app]`).
- `ToolsManager` downloads optional external tools such as the PHPStan PHAR from
  `https://github.com/phpstan/phpstan/releases/latest/download/phpstan.phar` and
  installs them under the VibeTrace tool directory
  (`[src/vibetrace/tools_manager.py:PHPSTAN_PHAR_URL]`).
- `get_bundled_skill_content` reads the packaged skill definition from
  `resources/SKILL.md` (`[src/vibetrace/skill_installer.py:get_bundled_skill_content]`).

## Verification

- Run: `uv run pytest`
- Run: `uv run ruff check .`
