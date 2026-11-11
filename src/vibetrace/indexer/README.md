# Indexer

## Overview

Code-graph indexing subsystem for VibeTrace. Parses source trees into a SQLite
graph of nodes (functions, classes, methods, endpoints) and edges (calls,
imports, inheritance, references, routes), persists them with FTS5 full-text
search, optional vector embeddings, incremental content-hash tracking, and
cross-file edge resolution.

Storage layout, schema, and migration rules live in `schema.sql`; the tree-sitter
language extractors live in the child package [Parsers](./parsers/README.md).

## Interface

- [src/vibetrace/indexer/db.py:IndexerDB] - SQLite graph store; inherits query mixin, owns schema init, migrations, node/edge writes, file hashing, centrality, pruning.
- [src/vibetrace/indexer/db_queries.py:DBQueriesMixin] - AST, FTS5 lexical, recursive-CTE path, and embedding queries.
- [src/vibetrace/indexer/models.py:Node] - Pydantic graph node record.
- [src/vibetrace/indexer/models.py:Edge] - Pydantic graph edge record.
- [src/vibetrace/indexer/db_retry.py:with_write_retry] - Decorator retrying locked/busy SQLite writes.
- [src/vibetrace/indexer/db_retry.py:interprocess_write_lock] - Cross-process advisory file lock for writers.
- [src/vibetrace/indexer/main.py:index_file] - Index one file into the graph.
- [src/vibetrace/indexer/main.py:reindex_all] - Recursive/incremental reindex entry point returning telemetry.
- [src/vibetrace/indexer/main.py:is_ignored_path] - Shared ignore rule for walk, git delta, and watcher.
- [src/vibetrace/indexer/main.py:load_ignore_spec] / [src/vibetrace/indexer/main.py:load_gitignore] - Build ignore spec from `.gitignore` plus configured excludes.
- [src/vibetrace/indexer/resolver.py:ModuleResolver] - Resolve module names to project-relative file paths.
- [src/vibetrace/indexer/embeddings.py:EmbeddingEngine] - Local embedding encode, index, and semantic search.
- [src/vibetrace/indexer/embeddings.py:try_load_sqlite_vec] - Loads the optional `sqlite-vec` extension into a connection.
- [src/vibetrace/indexer/embeddings.py:EmbeddingConfig] - Embedding pipeline configuration contract.
- [src/vibetrace/indexer/embeddings.py:build_symbol_document] / [src/vibetrace/indexer/embeddings.py:truncate_and_normalize] / [src/vibetrace/indexer/embeddings.py:pack_vector] / [src/vibetrace/indexer/embeddings.py:unpack_vector] / [src/vibetrace/indexer/embeddings.py:cosine_similarity] - Symbol text build, Matryoshka truncation, blob serialization, similarity.
- [src/vibetrace/indexer/vector_store.py:VectorStore] - Protocol for embedding storage and ANN search.
- [src/vibetrace/indexer/vector_store.py:LanceDBVectorStore] - Disk-backed LanceDB implementation.
- [src/vibetrace/indexer/vector_store.py:get_vector_store] - Factory selecting vector engine from configuration.
- [src/vibetrace/indexer/git_utils.py:get_git_sha] / [src/vibetrace/indexer/git_utils.py:get_git_delta] - Git SHA read and incremental changed/deleted file diff.
- [src/vibetrace/indexer/hooks.py:detect_hook_provider] / [src/vibetrace/indexer/hooks.py:get_hooks_status] / [src/vibetrace/indexer/hooks.py:install_hooks] / [src/vibetrace/indexer/hooks.py:install_git_hooks] / [src/vibetrace/indexer/hooks.py:install_lefthook_config] / [src/vibetrace/indexer/hooks.py:uninstall_hooks] - Automated post-commit/post-merge index hook management.
- [src/vibetrace/indexer/hooks.py:HOOK_SCRIPT_BLOCK] - Hook script body written into the installed git hook.
- [src/vibetrace/indexer/watcher.py:CodeChangeHandler] / [src/vibetrace/indexer/watcher.py:start_observer] / [src/vibetrace/indexer/watcher.py:stop_observer] / [src/vibetrace/indexer/watcher.py:start_watcher_blocking] - Filesystem watcher driving incremental reindex.
- [src/vibetrace/indexer/lsp_bridge.py:LSPClient] - Stdio JSON-RPC language-server client.
- [src/vibetrace/indexer/lsp_bridge.py:resolve_unresolved_edges] / [src/vibetrace/indexer/lsp_bridge.py:resolve_unresolved_edges_polyglot] - Rewire unresolved call edges using LSP definitions.
- [src/vibetrace/indexer/lsp_bridge.py:get_lsp_command_for_language] / [src/vibetrace/indexer/lsp_bridge.py:get_default_lsp_command] - Detect installed language servers.
- [src/vibetrace/indexer/lsp_bridge.py:LSP_SERVER_CANDIDATES] / [src/vibetrace/indexer/lsp_bridge.py:EXT_TO_LANGUAGE] - Per-language server command candidates and file-extension to language mapping.
- [src/vibetrace/indexer/lsp_bridge.py:MultiLanguageLSPManager] / [src/vibetrace/indexer/lsp_bridge.py:LSPDaemonManager] - Pooled language-server lifecycle.
- [src/vibetrace/indexer/rust_bridge.py:reindex_workspace_hybrid] - Workspace scan, SHA-256 diff, single-transaction commit.
- [src/vibetrace/indexer/rust_bridge.py:PythonFileDelta] / [src/vibetrace/indexer/rust_bridge.py:PythonWorkspaceScan] - Per-file change record and workspace scan result returned by the pure-Python fallback scanner.
- [src/vibetrace/indexer/rust_bridge.py:scan_workspace_python] / [src/vibetrace/indexer/rust_bridge.py:scan_workspace_native] / [src/vibetrace/indexer/rust_bridge.py:extract_batch_native] / [src/vibetrace/indexer/rust_bridge.py:is_native_available] / [src/vibetrace/indexer/rust_bridge.py:get_native_version] - Native Rust core access with pure-Python fallback.
- [src/vibetrace/indexer/scip_importer.py:ScipImporter] / [src/vibetrace/indexer/scip_importer.py:parse_scip_symbol_name] - Import SCIP index data into the graph.
- [src/vibetrace/indexer/schema.sql] - Tables: `indexed_files`, `nodes`, `edges`, `file_imports`, `edge_targets`, `nodes_fts`, `node_embeddings`, `meta`, `saved_traces`, `symbol_doc_links`, `function_taint_contracts`.
- Child contract: [Parsers](./parsers/README.md) - Tree-sitter language extractors producing the node/edge records persisted here.

## Invariants

- `schema.sql` must exist beside `db.py`; `IndexerDB._init_db` raises `FileNotFoundError` when it is missing [src/vibetrace/indexer/db.py:IndexerDB._init_db].
- `nodes.id` is the deterministic hash of file path plus qualified name, and `qualified_name` is `NOT NULL` [src/vibetrace/indexer/schema.sql].
- Edge confidence is restricted to `exact` (AST/type resolved) or `inferred` (heuristic fallback) [src/vibetrace/indexer/schema.sql].
- Migration backfills `qualified_name`, `parent_id`, `centrality`, `call_line`, and `confidence` on pre-existing databases; pre-existing external edges are marked `inferred` [src/vibetrace/indexer/db.py:IndexerDB._init_db].
- `batch_save_file_index` clears prior nodes, edges, FTS5 rows, embeddings, doc links, taint contracts, and import rows for the file and writes the new set in one transaction [src/vibetrace/indexer/db.py:IndexerDB.batch_save_file_index].
- `clear_file` removes inbound and outbound edges for the file, not only rows owned by it [src/vibetrace/indexer/db.py:IndexerDB.clear_file].
- `prune_orphaned_edges` deletes only `confidence = 'exact'` edges whose target node no longer exists [src/vibetrace/indexer/db.py:IndexerDB.prune_orphaned_edges].
- `update_centrality_scores` stores log-normalized in-degree centrality by default; PageRank runs only when `use_pagerank=True` [src/vibetrace/indexer/db.py:IndexerDB.update_centrality_scores].
- Cross-file linking resolves in two levels: exact match through `file_imports` transitive re-export (up to 4 hops), otherwise `inferred` when exactly one candidate exists [src/vibetrace/indexer/db_queries.py:DBQueriesMixin.link_cross_file_edges].
- `reindex_all` advances the `last_indexed_git_sha` metadata cursor only when no file failed, so failed files are retried on the next run [src/vibetrace/indexer/main.py:reindex_all].
- `is_ignored_path` ignores any path whose directory components are dot-prefixed or listed in `SKIP_DIRS` [src/vibetrace/indexer/main.py:is_ignored_path].
- `get_git_delta` returns `None` unless the base SHA is 40 or 64 hex characters, and `get_git_sha` validates SHA shape before use [src/vibetrace/indexer/git_utils.py:get_git_delta], [src/vibetrace/indexer/git_utils.py:get_git_sha].
- `with_write_retry` raises `ValueError` when `max_attempts < 1` [src/vibetrace/indexer/db_retry.py:with_write_retry].
- `EmbeddingConfig.dimensions` is constrained to 64-768 [src/vibetrace/indexer/embeddings.py:EmbeddingConfig].
- `get_vector_store` raises `NotImplementedError` for engine `sqlite-vec` and `ValueError` for any other unsupported engine [src/vibetrace/indexer/vector_store.py:get_vector_store].

## Side Effects

- Writes and migrates the SQLite index database (default `.vibetrace/vibetrace.db` or `vibetrace_context/vibetrace.db`) in WAL mode [src/vibetrace/indexer/db.py:IndexerDB.__init__], [src/vibetrace/indexer/db.py:IndexerDB._get_conn].
- Deletes rows in `nodes`, `edges`, `nodes_fts`, `node_embeddings`, `symbol_doc_links`, `file_imports`, `edge_targets`, `function_taint_contracts`, and `indexed_files` while reindexing or clearing files [src/vibetrace/indexer/db.py:IndexerDB.batch_save_file_index], [src/vibetrace/indexer/db.py:IndexerDB.clear_file].
- Creates and locks a sibling `<db_path>.write.lock` file to serialize multi-process writers [src/vibetrace/indexer/db_retry.py:interprocess_write_lock].
- Creates LanceDB data directories and writes embedding rows for local vector URIs [src/vibetrace/indexer/vector_store.py:LanceDBVectorStore._get_table].
- Runs `git rev-parse HEAD`, `git diff --name-status`, and `git status --porcelain` subprocesses against the repository [src/vibetrace/indexer/git_utils.py:get_git_sha], [src/vibetrace/indexer/git_utils.py:get_git_delta].
- Writes and removes hook content in `.git/hooks/post-commit`, `.git/hooks/post-merge`, `lefthook.yml`, and `.lefthook.yml` [src/vibetrace/indexer/hooks.py:install_git_hooks], [src/vibetrace/indexer/hooks.py:install_lefthook_config], [src/vibetrace/indexer/hooks.py:uninstall_hooks].
- Launches language-server subprocesses and exchanges JSON-RPC over stdio [src/vibetrace/indexer/lsp_bridge.py:LSPClient.start].
- Starts a recursive watchdog observer that reindexes changed, created, moved, and deleted files [src/vibetrace/indexer/watcher.py:start_observer].
- Downloads the HuggingFace sentence-transformers model on first load when it is not cached locally [src/vibetrace/indexer/embeddings.py:EmbeddingEngine._load_model].
- Uses the optional compiled `vibetrace._rust_core` extension for parallel scanning and falls back to the pure-Python scanner when absent [src/vibetrace/indexer/rust_bridge.py:is_native_available].
- Prints indexing progress and throughput telemetry to stdout [src/vibetrace/indexer/main.py:reindex_all].

## Verification

- Run: `uv run pytest`
