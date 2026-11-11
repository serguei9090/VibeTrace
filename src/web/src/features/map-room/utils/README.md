# Utils

## Overview

Graph layout, traversal, package aggregation, symbol tree, and hardware-accelerated canvas rendering utilities for the map room.

## Interface

- [`elkLayout.ts`](./elkLayout.ts) — `NODE_WIDTH`, `NODE_HEIGHT`, `LayoutConfig`, `layoutPackageGraph`, `getLayoutedElements`, `getGraphBounds`; private `layoutArchitectureMap`, `layoutDirectionalGraph`, `layoutExplorerGraph`.
- [`graphTraversal.ts`](./graphTraversal.ts) — `SelectionHighlightResult`, `getEdgeId`, `computeSelectionHighlights`, `buildFlowElements`, `getConnectedComponent`.
- [`packageGraph.ts`](./packageGraph.ts) — `PackageNodeData`, `normalizeDirPath`, `buildPackageGraph`.
- [`treeHierarchy.ts`](./treeHierarchy.ts) — `SymbolTreeNode`, `FileTreeItem`, `DirectoryGroup`, `NeighborInfo`, `isAnonymousSymbol`, `findRootEntryPoints`, `calculateUnrevealedNeighbors`, `buildFileTree`.
- [`webgpuRenderer.ts`](./webgpuRenderer.ts) — `GPUNodeData`, `GPUEdgeData`, `RendererBackend` (`"webgpu" | "webgl" | "canvas2d" | "off"`), `WebGPUDeviceLike`, `WebGPUAdapterLike`, `WebGPUNavigatorLike`, `WebGPURenderer` class.

## Invariants

- `getLayoutedElements` returns `{ nodes: [], edges }` when no `vibeNode` content nodes exist; catches ELK errors, logs them, and returns empty [src/web/src/features/map-room/utils/elkLayout.ts:getLayoutedElements].
- `getLayoutedElements` dispatches trace/impact → `layoutDirectionalGraph` (direction RIGHT for trace, UP for impact), explorer → `layoutExplorerGraph`, otherwise architecture map [src/web/src/features/map-room/utils/elkLayout.ts:getLayoutedElements].
- `computeSelectionHighlights`: trace found → step map from hops; agent focus without selection → highlights agent node ids; node selection in impact mode explores incoming edges depth 1–3 skipping `"defines"`; non-impact explores outgoing up to step 5 plus direct incoming; edge selection highlights endpoints at steps 1/2 [src/web/src/features/map-room/utils/graphTraversal.ts:computeSelectionHighlights].
- `buildFlowElements`: edge id `${from}-${to}-${relation}` deduplicated; edge labels render only when highlighted or `layoutMode === "trace"`; relation colors: calls cyan, routes amber, imports purple dashed, inherits emerald; `activeEdgeTypes` filter skipped when highlighted or in trace [src/web/src/features/map-room/utils/graphTraversal.ts:buildFlowElements].
- `getConnectedComponent` skips `"defines"` edges and only traverses revealed ids [src/web/src/features/map-room/utils/graphTraversal.ts:getConnectedComponent].
- `findRootEntryPoints`: primary names `main`, `cli`, `run`, `app`, `serve`, `handler` plus `*_main`/`*_cli` suffixes; skips `"defines"` edges; falls back to top 5 nodes by out-degree [src/web/src/features/map-room/utils/treeHierarchy.ts:findRootEntryPoints].
- `isAnonymousSymbol` matches `<anonymous` and `<lambda>` [src/web/src/features/map-room/utils/treeHierarchy.ts:isAnonymousSymbol].
- `buildPackageGraph`: package node id `pkg:${dir}`, edge id `pkg-edge:${from}->${to}`, top 3 preview symbols weighted endpoint > class > function [src/web/src/features/map-room/utils/packageGraph.ts:buildPackageGraph].
- `WebGPURenderer.initialize`: tries WebGPU probe, then WebGL2 batch pipeline, then Canvas2D fallback, then `"off"` [src/web/src/features/map-room/utils/webgpuRenderer.ts:WebGPURenderer.initialize].
- `WebGPURenderer.dispose`: destroys GPU device, deletes GL buffers/program, loses context [src/web/src/features/map-room/utils/webgpuRenderer.ts:WebGPURenderer.dispose].
- `hitTest` threshold: `max(radius, 14 / zoom)` [src/web/src/features/map-room/utils/webgpuRenderer.ts:hitTest].

## Side Effects

- `WebGPURenderer.render` writes to canvas via WebGL2 batch buffers or Canvas2D; reads `window.devicePixelRatio` and `performance.now` [src/web/src/features/map-room/utils/webgpuRenderer.ts:WebGPURenderer.render].
- Module-level ELK instance `const elk = new ELK()` created at import [src/web/src/features/map-room/utils/elkLayout.ts:elk].

## Verification

- Run: `cd src/web && bun run build`
