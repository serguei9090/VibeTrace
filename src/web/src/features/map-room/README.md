# Map Room

## Overview

Map room feature: symbol graph domain types plus the state hooks, layout utilities, and React Flow components that render the architecture map, tracepaths, and audit panels.

## Interface

- [`types.ts`](./types.ts) — shared domain types:
  - `SplitUI`, `LayoutMode` (`"map" | "trace" | "impact"`), `ViewMode` (`"explorer" | "full"`), `WorkspaceMode` (`"map" | "dashboard" | "docs"`).
  - `RawGraphNode`, `RawGraphEdge`, `GraphData`, `VibeNodeData`, `SelectedElement`.
  - `TraceHopNode`, `TraceHopData`, `SplitPointInfo`, `ReachableSymbolsResponse`, `TracePathResponse`, `AgentFocusEvent`, `SavedTrace`, `NodeNeighborsResponse`.
  - `SecurityFindingUI`, `SecurityAuditReportUI`, `UntestedSymbolUI`, `CoverageGateReportUI`, `UnifiedReviewReportUI`.
  - `ProjectMemberUI`, `CrossRepoLinkUI`, `ProjectStatusUI`.
  - `FunctionComplexityUI`, `QualityFindingUI`, `QualityAuditReportUI`.
- [`./components`](./components/README.md) — map room UI components.
- [`./hooks`](./hooks/README.md) — `useMapRoomState`, `useVibeSync`.
- [`./utils`](./utils/README.md) — ELK layout, traversal, package graph, tree hierarchy, WebGPU renderer.

## Invariants

- `SecurityFindingUI.category` is limited to `ast_pattern | taint_reachability | package_cve | iac_misconfig | git_secret`; `severity` to `CRITICAL | HIGH | MEDIUM | LOW | INFO`; `status` to `UNAPPROVED | APPROVED | MODIFIED_CODE | RISK` [src/web/src/features/map-room/types.ts:SecurityFindingUI].
- `VibeNodeData.securityBadge` accepts only `CRITICAL | HIGH | MEDIUM | LOW` [src/web/src/features/map-room/types.ts:VibeNodeData].
- `TracePathResponse.found` gates whether hops/node_ids are meaningful; `activeRouteIndex` selects one of `paths` [src/web/src/features/map-room/types.ts:TracePathResponse].
- `SelectedElement.type` is `"node"` or `"edge"`; edge IDs follow `${from_node_id}-${to_node_id}-${relation_type}` [src/web/src/features/map-room/types.ts:SelectedElement].

## Side Effects

- None in this directory (type declarations only).

## Verification

- Run: `cd src/web && bun run build`
