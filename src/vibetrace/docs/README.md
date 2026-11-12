# Docs

## Overview

Documentation RAG subsystem for VibeTrace. Turns Markdown, plain text, and PDF files into heading-aware chunks, indexes them into SQLite with FTS5 full-text and vector embeddings, runs hybrid reciprocal-rank-fusion retrieval over local and global stores, and maintains bi-directional links between AST symbols and Markdown specifications.

## Interface

- [`__init__.py`](./__init__.py) — package exports: `DocCatalogInfo`, `DocCatalogManager`, `DocChunkItem`, `DocDB`, `DocIndexService`, `DocSearchQuery`, `DocSearchService`, `HeadingChunk`, `chunk_markdown`, `chunk_pdf`, `chunk_text`, `get_global_doc_db`, `get_local_doc_db`.
- [`chunker.py`](./chunker.py) — `HeadingChunk` dataclass (`title`, `heading_path`, `start_line`, `end_line`, `content`); `chunk_markdown(content, max_chars=1500)`; `chunk_text(content, max_chars=1200, overlap_lines=4)`; `chunk_pdf(pdf_path, max_chars=1500)`; private `_split_long_section`.
- Shared exclude set `IGNORED_DIRS` / `IGNORED_DIRS_CASEFOLDED` lives outside this package in [`src/vibetrace/constants.py`](../../constants.py); the catalog walk and the symbol linker both import it from there.
- [`db.py`](./db.py) — `DocDB` class: `get_file_mtime`, `delete_file_chunks`, `upsert_chunks`, `search_bm25`, `search_vector`, `get_catalogs_summary`; module functions `re_clean_query`, `get_global_doc_db`, `get_local_doc_db`.
- [`patterns.py`](./patterns.py) — glob expansion for catalog entries: `is_pattern(entry)`, `split_pattern(entry, base_dir)`, `match_segments(pattern_segments, path_segments)`, `expand_pattern(entry, base_dir) -> (files, warnings)`; constants `MAGIC_CHARS`, `MARKDOWN_EXTS`, `MAX_PATTERN_MATCHES`.
- [`schemas.py`](./schemas.py) — Pydantic models `DocChunkItem`, `DocCatalogInfo`, `DocSearchQuery`.
- [`service.py`](./service.py) — `DocCatalogManager` (`list_catalogs`, `add_path`, `remove_catalog`), `DocIndexService` (`index_catalog`, `SUPPORTED_DOC_EXTS`, `MAX_DOC_FILE_SIZE`), `DocSearchService` (`search`, `_fuse_rrf_results`); module helpers `get_global_config_path`, `get_project_config_path`, `_read_catalogs_from_toml`, `_write_catalogs_to_toml`.
- [`symbol_linker.py`](./symbol_linker.py) — `SymbolDocLinker` (`extract_backticked_symbols`, `find_markdown_files`, `sync_links`, `get_linked_docs_for_symbol`, `audit_doc_links`); Pydantic models `LinkedDocReference`, `DocLinkAuditFinding`, `DocLinkAuditReport`; function `estimate_tokens`.

## Invariants

- `chunk_text` raises `ValueError` when `max_chars <= 0`; `overlap_lines` is clamped to `>= 0` [src/vibetrace/docs/chunker.py:chunk_text].
- `chunk_markdown` treats fenced code blocks (``` and ~~~) as literal text and never splits them into headings; heading path is the ` > `-joined ancestor stack, default root title `Introduction` [src/vibetrace/docs/chunker.py:chunk_markdown].
- `chunk_markdown` keeps a section as one chunk when it fits `max_chars`, otherwise `_split_long_section` sub-splits it and overlaps by the last 3 lines [src/vibetrace/docs/chunker.py:chunk_markdown].
- `chunk_pdf` returns an empty list when `pypdf` is not installed or the PDF is encrypted with a non-empty password [src/vibetrace/docs/chunker.py:chunk_pdf].
- `DocDB.upsert_chunks` deletes previous chunks for the same `(catalog, file_path)` inside the same transaction; chunk id is the first 24 hex chars of SHA-256 over `catalog:file_path:start_line:end_line:index` [src/vibetrace/docs/db.py:DocDB.upsert_chunks].
- `DocDB.search_bm25` returns `[]` when the query has no token longer than 1 character; FTS5 query joins tokens with `OR` and orders by `bm25(doc_chunks_fts)` ascending [src/vibetrace/docs/db.py:DocDB.search_bm25].
- `DocDB.search_vector` returns `[]` for a zero-norm query vector and skips stored embeddings whose dimension differs from the query dimension [src/vibetrace/docs/db.py:DocDB.search_vector].
- `DocCatalogManager.list_catalogs` returns local catalogs first; a global catalog with the same name as a local catalog is omitted (local precedence) [src/vibetrace/docs/service.py:DocCatalogManager.list_catalogs].
- `DocIndexService.SUPPORTED_DOC_EXTS` is `(".md", ".markdown", ".txt", ".pdf")`; `MAX_DOC_FILE_SIZE` is 10 MB and files above the ceiling are skipped [src/vibetrace/docs/service.py:DocIndexService].
- `DocIndexService.index_catalog` skips files whose mtime is unchanged within `1e-4` unless `force=True`; a file that fails extraction keeps its old chunks [src/vibetrace/docs/service.py:DocIndexService.index_catalog].
- `DocIndexService.index_catalog` removes stale catalog entries only when whole directories were scanned, not when a single file was targeted [src/vibetrace/docs/service.py:DocIndexService.index_catalog].
- Catalog entries containing `*?[]` are glob patterns unless the same string exists as a literal path; patterns match Markdown only (`.md`, `.markdown`), case-insensitively, with the base taken from the literal prefix resolved against the indexing `base_dir` [src/vibetrace/docs/service.py:DocIndexService._collect_catalog_files].
- In a pattern, a `**` segment matches zero or more path segments; other segments match within one segment via case-folded `fnmatch`; `..` segments are rejected with a warning [src/vibetrace/docs/patterns.py:match_segments].
- Pattern expansion and the directory walk both prune `IGNORED_DIRS` (case-folded) [src/vibetrace/constants.py:IGNORED_DIRS] and never follow symlinks [src/vibetrace/docs/patterns.py:expand_pattern].
- `index_catalog` deletes stored chunk rows for files no longer selected and reports them in the returned `warnings`; when every entry is a pattern and nothing matched while rows exist, it warns and deletes nothing [src/vibetrace/docs/service.py:DocIndexService.index_catalog].
- `index_catalog` returns `{"files", "chunks", "warnings"}`; the single-file cleanup guard resolves the entry against `base_dir`, not the process cwd [src/vibetrace/docs/service.py:DocIndexService.index_catalog].
- A pattern matching more than `MAX_PATTERN_MATCHES` (1000) files emits a warning [src/vibetrace/docs/patterns.py:expand_pattern].
- `requires-python` is `>=3.11`, so the stdlib `tomllib` import in `service.py` is valid [pyproject.toml].
- `DocSearchService.search` clamps `limit` to `1..50` and fuses BM25 and vector hits with reciprocal rank fusion using `1.0 / (60.0 + rank)` [src/vibetrace/docs/service.py:DocSearchService.search].
- `DocSearchService.search` searches local before global; when a catalog is requested and exists in the local config, only the local store is queried [src/vibetrace/docs/service.py:DocSearchService.search].
- `SymbolDocLinker.extract_backticked_symbols` requires a candidate to be longer than 2 chars, not all-uppercase, not a known file extension, not a path, and not in the keyword/stdlib ignore sets [src/vibetrace/docs/symbol_linker.py:SymbolDocLinker.extract_backticked_symbols].
- `SymbolDocLinker.sync_links` deletes all existing `symbol_doc_links` rows before re-inserting, and chunks Markdown at `max_chars=1200` [src/vibetrace/docs/symbol_linker.py:SymbolDocLinker.sync_links].
- `SymbolDocLinker.get_linked_docs_for_symbol` returns at most 3 linked sections ordered by `token_count` ascending, stops when the remaining budget drops below 20 tokens, and truncates oversized chunks with the marker `... [Remaining architectural specification truncated]` [src/vibetrace/docs/symbol_linker.py:SymbolDocLinker.get_linked_docs_for_symbol].
- `DocLinkAuditReport.passed` is true only when `broken_links_count == 0`; code fences are skipped during audit [src/vibetrace/docs/symbol_linker.py:SymbolDocLinker.audit_doc_links].
- `DocSearchQuery.limit` is bounded to `1..50` and defaults to 5; `scope` is `"local" | "global" | "all"` [src/vibetrace/docs/schemas.py:DocSearchQuery].

## Side Effects

- `DocDB` opens SQLite connections in WAL journal mode with `busy_timeout = 30000`; `DocDB.__init__` creates the parent directory and the `doc_chunks` / `doc_chunks_fts` schema [src/vibetrace/docs/db.py:DocDB].
- `DocDB.upsert_chunks`, `DocDB.delete_file_chunks` mutate `doc_chunks` and `doc_chunks_fts` inside `BEGIN IMMEDIATE` transactions with rollback on error [src/vibetrace/docs/db.py:DocDB.upsert_chunks].
- `get_global_doc_db` reads/writes `~/.vibetrace/docs.db` (or `$VIBETRACE_GLOBAL_ROOT/docs.db`); `get_local_doc_db` reads/writes `<project_root>/.vibetrace/vibetrace.db`; both are cached in `_DOC_DB_CACHE` behind `_CACHE_LOCK` [src/vibetrace/docs/db.py:get_global_doc_db].
- `_write_catalogs_to_toml` rewrites the `[docs.catalogs]` section of the target TOML file while preserving other sections [src/vibetrace/docs/service.py:_write_catalogs_to_toml].
- `get_global_config_path` creates `~/.vibetrace` (or `$VIBETRACE_GLOBAL_ROOT`) when missing; `DocCatalogManager.add_path` and `DocCatalogManager.remove_catalog` write `.vibetrace.toml` or the global `config.toml` [src/vibetrace/docs/service.py:get_global_config_path].
- `DocCatalogManager.add_path` persists catalog entries verbatim, including glob patterns, into `.vibetrace.toml` or the global `config.toml` [src/vibetrace/docs/service.py:DocCatalogManager.add_path].
- `DocIndexService` reads files from disk, calls `EmbeddingEngine.embed_texts` when the engine is available, and writes chunks through `DocDB.upsert_chunks` [src/vibetrace/docs/service.py:DocIndexService.index_catalog].
- `SymbolDocLinker.sync_links` and `SymbolDocLinker.audit_doc_links` read Markdown from the project tree and write or query `symbol_doc_links` in the indexer database [src/vibetrace/docs/symbol_linker.py:SymbolDocLinker.sync_links].
- `logger.warning` / `logger.debug` output on PDF parse failures, FTS5 errors, embedding failures, and TOML parse errors [src/vibetrace/docs/chunker.py:chunk_pdf].

## Verification

- Run: `uv run pytest`
