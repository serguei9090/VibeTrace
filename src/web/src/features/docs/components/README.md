# Components

## Overview

Documentation RAG workspace UI: catalog sync manager, semantic search over documentation chunks, and chunk reader cards.

## Interface

- [`CatalogCard`](CatalogCard.tsx) — props `{ catalog: DocCatalog, onIndex: (catalogName: string, isGlobal: boolean) => Promise<void>, indexing: boolean }`; re-index button disabled while `indexing`.
- [`DocChunkCard`](DocChunkCard.tsx) — props `{ chunk: DocChunk }`; collapsed body shows first 260 chars, expanded shows full content with max-height 400px.
- [`DocsWorkspaceView`](DocsWorkspaceView.tsx) — props `{ apiUrl: string }`; two-pane layout (catalogs aside + RAG search section); module-level caches `cachedCatalogs` and `cachedSearches` keyed by `${query}:${scope}`.

## Invariants

- Initial search runs with query `"architecture"` and scope `"all"` on mount [src/web/src/features/docs/components/DocsWorkspaceView.tsx:DocsWorkspaceView].
- Search request limit fixed at 16 chunks with params `q`, `scope`, `limit` [src/web/src/features/docs/components/DocsWorkspaceView.tsx:DocsWorkspaceView].
- `onIndex(catalogName, isGlobal)` receives `catalog.scope === "global"` as `isGlobal` [src/web/src/features/docs/components/CatalogCard.tsx:CatalogCard].
- Indexing clears `cachedCatalogs` and all `cachedSearches` entries before refetching catalogs [src/web/src/features/docs/components/DocsWorkspaceView.tsx:DocsWorkspaceView].

## Side Effects

- `DocsWorkspaceView`: GET `/api/docs/catalogs`, GET `/api/docs/search`, POST `/api/docs/index`; mutates module-level caches; `console.error` on failure [src/web/src/features/docs/components/DocsWorkspaceView.tsx:DocsWorkspaceView].

## Verification

- Run: `cd src/web && bun run build`
