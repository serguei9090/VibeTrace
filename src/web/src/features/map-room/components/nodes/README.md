# Nodes

## Overview

Leaf renderers for React Flow graph nodes in the VibeMap architecture map. Each node consumes graph data (`RawGraphNode`/`VibeNodeData` shapes defined in `../types`) and paints kind-scoped badges, expand pills, and connection handles.

## Interface

- [`DistrictNode`](./DistrictNode.tsx) — memoized district container label node.
- [`FileGroupNode`](./FileGroupNode.tsx) — memoized file-group header node with optional `stats` badge.
- [`PackageNode`](./PackageNode.tsx) — package node with `target-top`/`target-left`/`source-bottom`/`source-right` handles; renders `onExplorePackage(label)` explore button; memo compares `id`, `label`, `fileCount`, `symbolCount`, `shortName`, `topSymbols` length.
- [`VibeNode`](./VibeNode.tsx) — symbol node rendering kind badges (`stepBadge`, `splitBadge`, `securityBadge`, `impactBadge`) and expand pills for `onExpandCallers`/`onExpandCallees`.

## Invariants

- `VibeNode.KIND_COLOR_MAP` covers endpoint/class/interface/trait/struct/impl/enum/variable/module; unknown kinds fall back to `#818cf8` [src/web/src/features/map-room/components/nodes/VibeNode.tsx:VibeNode].
- `VibeNode` target handle is Left and source handle is Right when `data.layoutMode === "trace"`, otherwise Top/Bottom [src/web/src/features/map-room/components/nodes/VibeNode.tsx:VibeNode].
- Expand pills render only when `unrevealedCallers > 0` or `unrevealedCallees > 0` [src/web/src/features/map-room/components/nodes/VibeNode.tsx:VibeNode].

## Side Effects

- None (pure rendering; callbacks invoked through props).

## Verification

- Run: `cd src/web && bun run build`
