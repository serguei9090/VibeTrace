# Doc Catalog Glob Patterns

* **Author:** serguei9090
* **Date:** 2026-11-11
* **Status:** Reviewed by 7 Goldfish lenses (design, codebase details, zoom-out, comprehension, implementation readiness, structural, inventive catalyst)

## Overview

VibeTrace has two documentation paths. Only one lacks a feature.

The **RAG catalog path** indexes documents so natural-language search can answer
questions ("which contract documents authentication?"). Catalog entries must be
existing files or directories. A pattern such as `src/**/README.md` is silently
ignored, so the 26 EGM contract READMEs under `src/` cannot be reached by
natural-language search.

The **symbol-link path** matches code symbols to Markdown sections by name. It
already covers `src/` READMEs and already feeds `vibetrace slice` and
`vibetrace diagnose`. No code change is needed there.

The feature: let a documentation catalog entry be a glob expression. Rules fixed
by the requester:

* Markdown only (`.md`, `.markdown`). PDF and TXT stay on folder ingestion.
* The base directory is not fixed to the project root. Any subtree can be
  targeted, including the contract READMEs that EGM writes into subfolders.
* Matching is case-insensitive. `README.md`, `readme.md`, `ReadMe.MD` are the
  same file.
* The default exclude list is the one the codebase already uses.
* A catalog accepts more than one selection: one folder, the whole project, or
  two separate paths.

## Analysis

### Current behaviour, verified against code

* **Pattern entries are dropped silently.**
  `DocIndexService._collect_catalog_files` (`src/vibetrace/docs/service.py:222-233`)
  resolves each entry as a path and skips it when the path does not exist.
* **Same-catalog duplicates are already prevented.**
  * One run: `_is_valid_doc_file` keeps a `seen_resolved` set of resolved
    absolute paths (`service.py:200-208`).
  * Across runs: unchanged files are skipped by mtime (`service.py:256`).
  * Chunk rows are replaced, not appended: `DocDB.upsert_chunks` deletes chunks
    for `(catalog, file_path)` before inserting (`src/vibetrace/docs/db.py:151`),
    and `chunk_id` is `sha256(f"{catalog}:{file_path}:{start}:{end}:{idx}")[:24]`
    (`db.py:157-158`).
* **Cross-catalog copies are intentional.** Chunk identity includes the catalog
  name, so local and global scopes stay separate.
* **Slice and diagnose already surface the EGM READMEs.**
  `SymbolDocLinker.find_markdown_files` walks `**/*.md` from the project root
  (`src/vibetrace/docs/symbol_linker.py:433-447`). `sync_links` matches
  backticked identifiers against AST symbols. `CodeSliceResult.linked_docs`
  (`src/vibetrace/services/trace_service.py:553-557`) and
  `SymbolDiagnosisReport.linked_docs`
  (`src/vibetrace/services/diagnose_service.py:161-170`) read those rows.
* **This workspace had never synced links.** All three databases held 0 rows in
  `symbol_doc_links`. `uv run vibetrace docs sync-links` created 1979 links, and
  `uv run vibetrace slice DocIndexService` now prints the
  `src/vibetrace/docs/README.md` Interface and Side Effects sections.

### Corrections to the first draft of this brief

The Goldfish review found four factual errors in the earlier draft:

* **Split-brain claim was too broad.** `/api/docs/index`
  (`src/vibetrace/server_api_docs.py:87`) and `/api/docs/search`
  (`server_api_docs.py:107`) use `get_local_doc_db(root)`, which resolves to
  `.vibetrace/vibetrace.db`, same as the CLI
  (`src/vibetrace/services/graph_service.py:158-175`). Only `/api/docs/check`
  (`server_api_docs.py:127`) and `/api/docs/sync-links` (`server_api_docs.py:142`)
  hardcode `.vibetrace/index.db`. MCP engineering tools hardcode
  `INDEX_DB_NAME = "index.db"` (`src/vibetrace/mcp/tools_engineering.py:35`).
* **The exclude list applies to one path only.** `symbol_linker.find_markdown_files`
  prunes `_IGNORED_DIRS` (`symbol_linker.py:330-350`, used at `:442`). The RAG
  walk `_gather_path_candidates` (`service.py:188-197`) prunes nothing.
* **Counts were wrong.** `src/web/node_modules` holds 332 `.md` files and 351
  files with supported extensions, not 363. `src` as a catalog entry yields 378
  files in total.
* **"26 EGM contracts" is imprecise.** 26 READMEs exist under `src/` excluding
  `node_modules`. EGM bootstrap created or corrected them, and the correction
  policy preserves handwritten content in each file.

### Stale-content cleanup, the rule fixed by the requester

When a selected file disappears from disk, or stops matching every entry in its
catalog, its stored chunks must be removed. When a selected file's content
changes, its chunks must be replaced. Two ways were offered: hard delete, or a
`removed` tag. Hard delete wins: `delete_file_chunks` and the delete-then-insert
`upsert_chunks` already implement it, and a tag would add a status column plus
filters in every read path. The `source_pattern` column with a `removed` tag
stays as an upgrade path only if audit history is ever required.

The cleanup rule, implemented once patterns expand correctly:

* `seen` is the union of files matched by all entries of the catalog in this
  run.
* Every stored `file_path` of the catalog that is not in `seen` has its chunks
  deleted, and the removal is reported in `warnings` (catalog, count, up to
  five example paths). This is per-file, never a silent whole-catalog wipe.
* Guard: if the catalog has entries, all of them are patterns, and `seen` is
  empty while stored rows exist, the run warns and skips deletion entirely.
  That state means a typo or a wrong base, not a real mass deletion.
* Guard: `is_targeted_file` resolves `Path(paths[0])` against `base_dir`, not
  the process working directory, and a pattern entry is never a targeted file.
* A literal entry that no longer exists produces an entry-level warning; the
  files it used to contribute are removed by the per-file rule, because a
  deleted folder is a real removal, not a typo.
* A file whose content changed is re-chunked by the mtime check and its rows
  are replaced by `upsert_chunks`, so old content never lingers.

### Base directory resolution

Resolution order for every entry, decided once and used by indexing, cleanup,
and the watcher:

1. Normalise the entry: `\` becomes `/`, a leading `./` is stripped.
2. If the entry has no magic characters, keep the existing literal behaviour:
   resolve against `base_dir` when relative, use as is when absolute.
3. Otherwise split the entry into segments. The base is the leading run of
   literal segments before the first magic segment. An empty prefix means
   `base_dir` itself, so `**/README.md` starts at the catalog root.
4. The base is `base_dir / prefix` for relative entries, or the absolute prefix
   for absolute entries. `base_dir` is the argument `index_catalog` already
   receives: the root that owns the config for local catalogs, `Path.home()`
   for global catalogs (`src/vibetrace/cli_docs.py:159-177`). Never the process
   working directory on its own.
5. If the resolved base is not a directory, the entry matches nothing and
   produces a warning naming the entry and the missing base. On a
   case-sensitive filesystem a wrong-case base prefix is exactly this case.
6. A pattern containing a `..` segment is rejected with a warning. Absolute
   patterns are the supported way to select files outside the base.
7. Matching walks the base with `os.walk(followlinks=False)`, prunes directory
   names whose case-folded form is in the shared exclude set, keeps files whose
   case-folded suffix is `.md` or `.markdown`, and matches the path relative to
   the base against the glob part segment by segment, case-folded.

### Matching rules that must be pinned

* **Detection.** An entry is a pattern when it contains `*`, `?`, `[`, or `]`
  and does not exist as a literal path. A real directory named `foo[1]` stays a
  path.
* **Segment rules.** Patterns are split on `/`. A segment that is exactly `**`
  matches zero or more path segments. Every other segment is matched with
  `fnmatch` semantics, so `*`, `?`, and `[...]` never cross a `/`.
* **Case folding.** Both pattern segments and path segments are compared
  case-folded. This has to be application code: `PurePath.full_match` with a
  case flag only exists from Python 3.13, the project floor is
  `requires-python = ">=3.10"`, and `Path.glob` follows host filesystem case
  rules, so the same catalog would select different files on Linux and Windows.
  `fnmatch.fnmatch` alone is wrong for two reasons: its `*` crosses `/`, and
  `**` is not special.
* **Markdown only.** The pattern path admits `.md` and `.markdown` only
  (case-folded). `SUPPORTED_DOC_EXTS` (`service.py:184`) stays as it is for
  directory entries, so PDF and TXT keep working through folders.
* **Sorting.** Matched files are sorted by their relative path, case-folded,
  so two runs produce the same order.
* **Symlinks.** The walk does not follow symlinks, matching the existing
  `os.walk(followlinks=False)` behaviour (`service.py:192`).

### Exclude list

`_IGNORED_DIRS` (`symbol_linker.py:330-350`) covers `.git`, `.venv`,
`node_modules`, `dist`, `build`, `scratch`, `archive`, `__pycache__`,
`.ruff_cache`, `.pytest_cache`, `.gemini`, `.vscode`, `.idea`, `target`, `tmp`,
`temp`, `site`, `htmlcov`, `extensions`.

The set moves to a leaf module so both consumers share one definition. It is
applied to pattern expansion and to the plain directory walk, so a folder entry
and a pattern entry behave the same. Today a folder entry on `src` pulls in 332
`node_modules` Markdown files. That change fixes existing behaviour, so it is
called out in the contract update.

Directory names are compared case-folded, matching the case-insensitive rule.

### Live sync blind spot

`IndexerDB` watcher `_handle_doc_change` checks
`p.is_relative_to(self.root_path / cat_p_str)`
(`src/vibetrace/indexer/watcher.py:131`). A pattern string never contains a real
file, so edits to pattern-selected files would never trigger re-indexing. The
watcher needs to test the changed path against the pattern entries.

### Cost

Each matched file costs one embedding call (`service.py:267-273`), one FTS5 row
and one vector row (`db.py:86-95`, `db.py:157-163`). Query cost grows with the
total chunk count, because vector search scans every stored embedding
(`db.py:255-286`). A pattern such as `**/*.md` over a repository with web
assets turns a one-time indexing cost into permanent per-query latency. A
warning above a large match count is cheap insurance.

### Pre-existing issues found by the review, outside this feature

* `service.py:11` imports `tomllib`, which needs Python 3.11 while
  `pyproject.toml:6` declared `requires-python = ">=3.10"`. Resolved by raising
  the floor to `>=3.11` (requester decision). No `tomli` backport is added. The
  glob matcher stays custom because `PurePath.full_match` with a case flag
  still needs Python 3.13.
* Three different database resolution rules exist (`graph_service.py:158-175`,
  `server_api_docs.py:127,142`, `mcp/tools_engineering.py:35`). One resolver
  module would remove the drift.
* `_write_catalogs_to_toml` (`service.py:66-79`) rewrites the whole
  `[docs.catalogs]` section, so user comments in that section are lost on the
  next `catalog add`.

## Goals vs. Non-Goals

**In-Scope Goals**

* Accept glob expressions in `[docs.catalogs]` entries and in
  `vibetrace docs catalog add`.
* Match Markdown files only (`.md`, `.markdown`).
* Resolve the base from the `base_dir` of the catalog being indexed, so any
  subtree can be targeted.
* Match case-insensitively across the whole pattern.
* Apply the shared codebase exclude list to pattern expansion and to folder
  entries.
* Accept several selections in one catalog and across catalogs.
* Keep ingestion duplicate-free across repeated runs.
* Remove stored chunks when a selected file disappears, and replace them when
  its content changes. Both paths already exist (`delete_file_chunks`,
  `upsert_chunks`) and are kept.
* Warn and delete nothing when every entry in a catalog is a pattern and the
  whole catalog matched zero files, which points at a typo or a wrong base.
* Warn on zero-match patterns, on missing base directories, on removed files,
  and on very large match counts.
* Keep pattern-selected files live-syncing through the watcher.
* Keep `src/vibetrace/docs/README.md` EGM contract accurate.

**Out-of-Scope Non-Goals**

* Regex or brace expressions such as `*.{md,markdown}`.
* Globbing for `.txt`, `.pdf`, or any other format. Those stay on folder
  ingestion.
* Ignore files (`.gitignore`, `.egmignore`) inside catalogs. Only the codebase
  default exclude list applies.
* Cross-catalog content deduplication. Chunk identity includes the catalog by
  design.
* The database resolver refactor and the `tomllib` floor fix.
* Automatically indexing every README without an explicit catalog entry.

## Alternatives Weighed

* **Rejected: point a catalog at `src/`.** Works today with zero code, but
  pulls in 332 `node_modules` Markdown files and pollutes search results.
* **Rejected: list all 26 README paths explicitly.** Works today, but must be
  edited by hand whenever EGM bootstrap adds or removes a contract file, and
  the same list is needed in every checkout.
* **Rejected: reuse the symbol-link path for natural-language search.** It is
  exact-symbol lookup, not text retrieval, so it cannot answer questions about
  documents that mention no symbol name.
* **Rejected: add a dependency for matching or walking.** `fnmatch`, `os.walk`,
  and `pathlib` cover the work.
* **Rejected: rely on `Path.glob`.** Host-dependent case rules, so the same
  catalog selects different files on Linux and Windows. The inventive-catalyst
  lens verified the behaviour of `PurePath.full_match`, `PurePath.match`,
  `Path.glob`, `glob.glob`, and `fnmatch` on Python 3.10 and confirmed no
  standard call gives portable case-insensitive globstar.
* **Rejected: intent metadata instead of globs** (for example a `kind =
  "contract"` catalog or EGM frontmatter). It selects only by metadata, not by
  path shape, needs a config model change, and requires the producer to stamp
  every file. Glob keeps path-shape selection with no schema change.
* **Rejected: two-part config entries** (`{ base = "src", glob = "**/README.md" }`).
  Same result as prefix-derived base, but needs a TOML schema change and makes
  the entry format inconsistent with the rest of the config.
* **Deferred: `source_pattern` column on `doc_chunks` plus a `removed` tag.** Gives per-entry audit history. Not needed for per-file removal, and it is a schema migration.

## Key Decisions & Staging for Next Steps

* **Detection:** magic characters `*?[]`, literal path wins when the entry
  exists on disk. Backslashes normalised to `/` before the check.
* **Base:** the `base_dir` passed to `index_catalog`. Local catalogs use the
  root that owns the config, global catalogs use `Path.home()`.
* **Matcher:** own segment matcher with globstar `**` matching zero or more
  segments, `fnmatch` semantics inside a segment, case-folded comparison,
  sorted output.
* **Files:** `.md` and `.markdown` only for patterns. Folder entries keep
  `SUPPORTED_DOC_EXTS`.
* **Exclude list:** one shared set in `src/vibetrace/docs/constants.py`, used by
  pattern expansion and by the folder walk.
* **Cleanup:** per-file removal as described above. Hard delete, never a silent
  wipe. The all-patterns-zero-match guard and the `base_dir` fix for
  `is_targeted_file` ship in Stage 1.
* **Python floor:** `requires-python` moves from `>=3.10` to `>=3.11` so the
  stdlib `tomllib` import is valid. No backport dependency.
* **Dedup:** unchanged. Resolved-path set, mtime skip, delete-then-insert
  chunks, hash-based chunk id.
* **Warnings:** `logger.warning` plus a `warnings` list in the `index_catalog`
  return value, printed by the CLI.
* **Large-match guard:** warn above a threshold of 1000 matched files, name the
  pattern, do not block.
* **Live sync:** the watcher tests changed paths against pattern entries.

**Files to change and staging**

* *Stage 0 (one line):* `pyproject.toml`: `requires-python = ">=3.11"`.
* *Stage 1 (core):*
  1. `src/vibetrace/constants.py`: shared `IGNORED_DIRS` + `IGNORED_DIRS_CASEFOLDED`
     (top-level shared constants module, used by docs and any other scanner).
  2. `src/vibetrace/docs/patterns.py` (new): `is_pattern`, `expand_pattern`,
     segment matcher with `**` matching zero or more segments, per-segment
     `fnmatch`, case-folded comparison, sorted output. Leaf module, no internal
     imports, so no import cycle is possible.
  3. `src/vibetrace/docs/service.py`: pattern expansion in
     `_collect_catalog_files`, exclude pruning in `_gather_path_candidates`,
     the cleanup rule plus both guards, the `is_targeted_file` fix, a
     `warnings` list in the `index_catalog` return value, and the large-match
     warning.
  4. `src/vibetrace/docs/symbol_linker.py`: import the shared `IGNORED_DIRS`
     instead of defining it locally.
  5. `src/vibetrace/cli_docs.py`: print warnings from index results.
  6. `src/vibetrace/docs/README.md`: contract update naming both new modules,
     the shared exclude set, and the no-silent-wipe invariant.
  7. `tests/test_docs_catalog_glob.py` (new): one test file, no new framework.
* *Stage 2 (integration):*
  8. `src/vibetrace/indexer/watcher.py`: match changed paths against pattern
     entries so live sync works for pattern-selected files.
  9. `src/vibetrace/cli.py`: `catalog add` help text names patterns.
  10. `src/vibetrace/docs/service.py` `add_path`: warn on a malformed pattern.
* *Stage 3 (optional):* `vibetrace docs catalog preview` dry run; preserve user
  comments in `[docs.catalogs]` when the section is rewritten.

**Verification**

`uv run pytest tests/test_docs_catalog_glob.py -x`, plus the existing
`tests/test_docs_rag.py` and `tests/test_symbol_doc_linking.py`.

Test cases: `src/**/README.md` selects both `README.md` and `readme.MD` under
`src` and nothing under `src/node_modules`; `src/*.md` does not cross a
separator; `src/*/doc?.md` matches `doc1.md`; `**` matches zero segments;
deleting a matched file removes its chunk rows while other files stay; a
catalog whose only entries are patterns and which matches zero keeps existing
rows and reports a warning; re-running an unchanged catalog adds no rows; a
`..` pattern is rejected with a warning.

**Resolved questions**

* Stale content: hard delete, not a `removed` tag. `delete_file_chunks` and
  `upsert_chunks` already implement remove and replace.
* Folder entries are pruned by the shared exclude list too, so folder and
  pattern entries behave the same. Behaviour change, flagged in the contract:
  a `src` folder entry stops indexing 332 `node_modules` Markdown files.
* Watcher pattern support ships in Stage 2.
* Python floor moves to `>=3.11`.
