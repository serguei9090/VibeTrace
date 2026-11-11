# Federation

## Overview

Multi-repository project workspace federation for VibeTrace. Loads a `.vibetrace-project.toml` manifest describing member repositories, indexes each member into its own SQLite database, attaches those databases into one in-memory SQLite session (or a centralized PostgreSQL schema), and answers cross-repository symbol search, blast-radius guard, and HTTP contract correlation queries.

## Interface

- [`__init__.py`](./__init__.py) — package exports: `PROJECT_CONFIG_FILENAME`, `FederatedCaller`, `FederatedGuardReport`, `FederatedNode`, `FederatedSQLiteEngine`, `MemberStatus`, `ProjectManifest`, `ProjectSettings`, `ProjectWorkspace`, `RepoMember`, `StorageBackend`, `auto_discover_members`, `find_project_root`, `load_project_manifest`, `save_project_manifest`.
- [`config.py`](./config.py) — `PROJECT_CONFIG_FILENAME = ".vibetrace-project.toml"`; `find_project_root`, `load_project_manifest`, `save_project_manifest`, `auto_discover_members`.
- [`models.py`](./models.py) — Pydantic models `RepoMember`, `ProjectSettings`, `ProjectManifest`, `FederatedNode`, `FederatedCaller`, `FederatedGuardReport`, `MemberStatus`.
- [`project.py`](./project.py) — `ProjectWorkspace` (`load`, `init`, `engine`, `close`, `list_members`, `add_member`, `remove_member`, `index_member`, `index_all`, `search`, `guard`, `discover_cross_repo_links`); context-manager protocol via `__enter__` / `__exit__`.
- [`storage.py`](./storage.py) — `FederatedSQLiteEngine` (`_get_conn`, `close`, `get_member_statuses`, `get_cross_repo_links`, `search`, `get_cross_repo_guard`); helper `_sanitize_alias`.
- [`storage_base.py`](./storage_base.py) — `StorageBackend` runtime-checkable Protocol: `close`, `get_member_statuses`, `search`, `get_cross_repo_guard`.
- [`storage_postgres.py`](./storage_postgres.py) — `PostgresStorageBackend` (`init_schema`, `close`, `get_member_statuses`, `search`, `get_cross_repo_guard`); constant `POSTGRES_SCHEMA_SQL`.
- [`cross_repo_linker.py`](./cross_repo_linker.py) — `CrossRepoLinker` (`discover_links`); Pydantic model `CrossRepoLink`; helper `_normalize_route`.

## Invariants

- `RepoMember.name` must match `^[a-zA-Z0-9_\-]+$` after stripping whitespace; `RepoMember.storage_path` defaults to `.vibetrace/vibetrace.db` [src/vibetrace/federation/models.py:RepoMember].
- `ProjectSettings.storage_backend` is `"sqlite"` or `"postgres"`; `postgres_url` is required and the model rejects the combination `"postgres"` without a URL [src/vibetrace/federation/models.py:ProjectSettings].
- `ProjectManifest.name` is limited to 1..100 characters; `version` defaults to `"1.0.0"` [src/vibetrace/federation/models.py:ProjectManifest].
- `FederatedGuardReport.risk_level` is `"LOW" | "MEDIUM" | "HIGH"` [src/vibetrace/federation/models.py:FederatedGuardReport].
- `find_project_root` walks upward from the start path and returns the first directory containing `.vibetrace-project.toml`, else `None` [src/vibetrace/federation/config.py:find_project_root].
- `load_project_manifest` raises `FileNotFoundError` when the manifest is absent and `ValueError` when `members` is not a list or an entry lacks `name`/`path` [src/vibetrace/federation/config.py:load_project_manifest].
- `auto_discover_members` skips hidden directories and the names `node_modules`, `target`, `vendor`, `dist`, `build`; a candidate qualifies only when it contains one of `.vibetrace`, `.git`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `requirements.txt` [src/vibetrace/federation/config.py:auto_discover_members].
- `ProjectWorkspace.add_member` rejects a duplicate member name; `remove_member` returns `True` only when a member was actually removed, and both persist the manifest and reset the cached engine [src/vibetrace/federation/project.py:ProjectWorkspace.add_member].
- `ProjectWorkspace.index_member` refuses member paths and storage paths that resolve outside the project root or repository directory [src/vibetrace/federation/project.py:ProjectWorkspace.index_member].
- `ProjectWorkspace.index_all` catches per-member failures and reports them as `{"status": "error"}` instead of aborting the batch [src/vibetrace/federation/project.py:ProjectWorkspace.index_all].
- `FederatedSQLiteEngine._get_conn` skips members whose resolved path or database path escapes the project root; schema aliases are sanitized to `repo_<name>` and deduplicated with a numeric suffix [src/vibetrace/federation/storage.py:FederatedSQLiteEngine._get_conn].
- `FederatedSQLiteEngine.search` ranks exact name/qualified-name matches in tier 0, prefix matches in tier 1, and substring matches in tier 2, then sorts by tier, name, and repo [src/vibetrace/federation/storage.py:FederatedSQLiteEngine.search].
- `FederatedSQLiteEngine.get_cross_repo_guard` sets `risk_level` to `"HIGH"` when callers exceed 10 or span more than one repository, `"MEDIUM"` when callers exceed 3, otherwise `"LOW"` [src/vibetrace/federation/storage.py:FederatedSQLiteEngine.get_cross_repo_guard].
- `PostgresStorageBackend.get_cross_repo_guard` uses the same risk thresholds and advisory wording as the SQLite engine [src/vibetrace/federation/storage_postgres.py:PostgresStorageBackend.get_cross_repo_guard].
- `_normalize_route` produces `"{METHOD} {path}"`, strips scheme and host, strips trailing slashes, and falls back to the method `ANY` when no HTTP method is present [src/vibetrace/federation/cross_repo_linker.py:_normalize_route].
- `CrossRepoLinker.discover_links` only emits a link when caller and handler live in different repositories, and deduplicates by `(source_repo, source_symbol, target_repo, target_symbol, contract_key)` [src/vibetrace/federation/cross_repo_linker.py:CrossRepoLinker.discover_links].
- `CrossRepoLinker.discover_links` skips schema aliases that do not match `^[A-Za-z_][A-Za-z0-9_]*$` [src/vibetrace/federation/cross_repo_linker.py:CrossRepoLinker.discover_links].

## Side Effects

- `save_project_manifest` writes `.vibetrace-project.toml` in the project root with `[project]`, `[project.settings]`, and one `[[project.members]]` block per member [src/vibetrace/federation/config.py:save_project_manifest].
- `FederatedSQLiteEngine._get_conn` opens an in-memory SQLite connection and issues `ATTACH DATABASE` for each existing member database file; `close` closes the connection and clears the alias map [src/vibetrace/federation/storage.py:FederatedSQLiteEngine._get_conn].
- `FederatedSQLiteEngine.get_member_statuses` stats member database files and reads `nodes` / `edges` counts from the attached schemas [src/vibetrace/federation/storage.py:FederatedSQLiteEngine.get_member_statuses].
- `ProjectWorkspace.index_member` creates the member database parent directory, opens `IndexerDB`, and calls `vibetrace.indexer.main.reindex_all` on the member repository [src/vibetrace/federation/project.py:ProjectWorkspace.index_member].
- `PostgresStorageBackend._get_connection` imports `psycopg` lazily and connects to `postgres_url`; `init_schema` executes `POSTGRES_SCHEMA_SQL` and commits, creating `vibetrace_projects`, `vibetrace_repos`, `vibetrace_nodes`, `vibetrace_edges` and their indices [src/vibetrace/federation/storage_postgres.py:PostgresStorageBackend.init_schema].
- `PostgresStorageBackend` rolls back the connection on query errors and logs warnings per failed member [src/vibetrace/federation/storage_postgres.py:PostgresStorageBackend.get_member_statuses].
- `logger.warning` output for unsafe schema aliases, failed client/server edge queries, unreadable project directories, and PostgreSQL errors [src/vibetrace/federation/cross_repo_linker.py:CrossRepoLinker.discover_links].

## Verification

- Run: `uv run pytest`
