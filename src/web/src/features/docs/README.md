# Docs

## Overview

Documentation RAG feature: catalog/chunk domain types plus the workspace UI that indexes local and global doc catalogs and runs scoped semantic search.

## Interface

- [`types.ts`](./types.ts) — `DocCatalog` (`name`, `scope: "local" | "global"`, `paths`, `doc_count`, `chunk_count`) and `DocChunk` (`id`, `catalog`, `file_path`, `heading_path`, `start_line`, `end_line`, `content`, `score`, `scope`, `match_sources`).
- [`./components`](./components/README.md) — `DocsWorkspaceView`, `CatalogCard`, `DocChunkCard`.

## Invariants

- `DocCatalog.scope` and `DocChunk.scope` are limited to `"local" | "global"` [src/web/src/features/docs/types.ts:DocCatalog, types.ts:DocChunk].

## Side Effects

- None in this directory (type declarations only).

## Verification

- Run: `cd src/web && bun run build`
