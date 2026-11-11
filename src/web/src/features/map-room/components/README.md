# Components

## Overview

Map room UI components: canvas header, tracepath bar, tree sidebar, filter panel, code inspector drawer, audit cards (security/quality/split), agent radar badge, canvas auto-fitter, WebGPU canvas, and bundle copy button.

## Interface

- [`AgentRadarBadge`](AgentRadarBadge.tsx) — props `{ followAgent, onToggleFollow, lastAgentEvent }`.
- [`CallerGuardBadge`](CallerGuardBadge.tsx) — props `{ apiUrl, symbolName, filePath? }`; renders `null` when guard reports no callers.
- [`CanvasAutoFitter`](CanvasAutoFitter.tsx) — props `{ layoutMode, nodeCount }`; calls `fitView` via `useReactFlow` after 120ms.
- [`CodeInspectorDrawer`](CodeInspectorDrawer.tsx) — props `{ apiUrl, selectedElement, rawNodes, rawEdges, revealedNodeIds?, onClose, onFocusNode?, onExpandCallees?, onExpandCallers?, onExpandNodeConnections?, onShowTracePath? }`; renders CallerGuardBadge, VibeBundleButton, SplitAuditCard, SecurityAuditCard, QualityAuditCard, source slice.
- [`FilterPanel`](FilterPanel.tsx) — props `{ activeFilters, onToggleFilter, activeEdgeTypes, onToggleEdgeType, showPrivate, onTogglePrivate, impactMode, onToggleImpact, layoutMode?, onLayoutModeChange?, isSidebarOpen? }`.
- [`MapHeader`](MapHeader.tsx) — props `{ apiUrl?, workspaceMode, ... }`; default `apiUrl = "http://127.0.0.1:8000"`, `webGpuTurbo = false`, `rendererMode = "canvas2d"`, `liveFps = 60`.
- [`QualityAuditCard`](QualityAuditCard.tsx) — props `{ apiUrl, filePath?, symbolName?, startLine?, endLine? }`.
- [`SecurityAuditCard`](SecurityAuditCard.tsx) — props `{ apiUrl, filePath?, startLine?, endLine?, onShowPath? }`.
- [`SplitAuditCard`](SplitAuditCard.tsx) — props `{ apiUrl, symbolName, qualifiedName?, filePath? }`.
- [`StatusOverlays`](StatusOverlays.tsx) — props `{ loading, error, onRetry }`.
- [`TracePathBar`](TracePathBar.tsx) — props `{ apiUrl, nodes, activeTrace, onTraceResult, isSidebarOpen? }`; symbol options capped at 300; reachable fetch debounced 250ms, `max_depth=8`; trace `max_depth=6`.
- [`TreeSidebar`](TreeSidebar.tsx) — props `{ isOpen, onClose, rawNodes, rawEdges, revealedNodeIds, onToggleNodeVisibility, onFocusNode, onExpandNodeConnections, onResetToRoots, showAnonymous, onToggleAnonymous }`; Escape closes sidebar via `window` keydown listener.
- [`VibeBundleButton`](VibeBundleButton.tsx) — props `{ apiUrl, symbolName }`; `VIBE_BUNDLE_BUDGET = 1500`.
- [`WebGPUCanvas`](WebGPUCanvas.tsx) — props `{ rawNodes, rawEdges, nodePositions?, activeTrace, onSelectNode?, onRendererStatusChange? }`; `KIND_COLORS` for class/interface/endpoint/module/function/method.
- [`./nodes`](./nodes/README.md) — DistrictNode, FileGroupNode, PackageNode, VibeNode.

## Invariants

- `SecurityAuditCard` and `QualityAuditCard` skip fetching when `filePath` is missing or `"external"`; SecurityAuditCard filters findings overlapping `[startLine, endLine]` [src/web/src/features/map-room/components/SecurityAuditCard.tsx:SecurityAuditCard], [src/web/src/features/map-room/components/QualityAuditCard.tsx:QualityAuditCard].
- Security badge severity rank used by graph enrichment: `LOW < MEDIUM < HIGH < CRITICAL` [src/web/src/features/map-room/hooks/useMapRoomState.ts:useMapRoomState].
- `TracePathBar` caps `symbolOptions` at 300 and fetches reachable symbols at `max_depth=8`; trace uses `max_depth=6` [src/web/src/features/map-room/components/TracePathBar.tsx:TracePathBar].
- `CanvasAutoFitter` only runs `fitView` when `nodeCount > 0` and `layoutMode` is set [src/web/src/features/map-room/components/CanvasAutoFitter.tsx:CanvasAutoFitter].

## Side Effects

- `MapHeader`: GET `/api/project` [src/web/src/features/map-room/components/MapHeader.tsx:MapHeader].
- `CallerGuardBadge`: GET `/api/vibe/guard` [src/web/src/features/map-room/components/CallerGuardBadge.tsx:CallerGuardBadge].
- `SecurityAuditCard`: GET `/api/security/audit`, POST `/api/security/approve`, `navigator.clipboard.writeText` [src/web/src/features/map-room/components/SecurityAuditCard.tsx:SecurityAuditCard].
- `SplitAuditCard`: GET `/api/splits/audit`, POST `/api/splits/approve` [src/web/src/features/map-room/components/SplitAuditCard.tsx:SplitAuditCard].
- `QualityAuditCard`: GET `/api/quality/audit`, POST `/api/quality/approve`, `navigator.clipboard.writeText` [src/web/src/features/map-room/components/QualityAuditCard.tsx:QualityAuditCard].
- `VibeBundleButton`: GET `/api/vibe/bundle`, `navigator.clipboard.writeText` [src/web/src/features/map-room/components/VibeBundleButton.tsx:VibeBundleButton].
- `CodeInspectorDrawer`: GET `/source` [src/web/src/features/map-room/components/CodeInspectorDrawer.tsx:CodeInspectorDrawer].
- `TracePathBar`: GET `/api/reachable`, GET `/trace`, GET/POST/DELETE `/api/saved-traces` [src/web/src/features/map-room/components/TracePathBar.tsx:TracePathBar].
- `WebGPUCanvas`: canvas drawing, `window` resize listener, requestAnimationFrame loop [src/web/src/features/map-room/components/WebGPUCanvas.tsx:WebGPUCanvas].
- `TreeSidebar`: `window` keydown listener (Escape → `onClose`) [src/web/src/features/map-room/components/TreeSidebar.tsx:TreeSidebar].

## Verification

- Run: `cd src/web && bun run build`
