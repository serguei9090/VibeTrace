# Hooks

## Overview

React state and sync hooks powering the map room canvas graph, distributed graph layout, tracepath expansion, and agent-focused WebSocket updates.

## Interface

- [`useMapRoomState()`](useMapRoomState.ts) — graph state hook returning nodes/edges/canvas controls, trace handling, neighbor expansion, project fetch, and layout filtering.
- [`API_URL`](useMapRoomState.ts) — `http://localhost:8000` when Vite dev port `5173`/`3000` or non-browser, otherwise same-origin URL [src/web/src/features/map-room/hooks/useMapRoomState.ts:API_URL].
- [`WS_URL`](useMapRoomState.ts) — `ws://localhost:8000/ws` following the same dev/same-origin rule [src/web/src/features/map-room/hooks/useMapRoomState.ts:WS_URL].
- [`useVibeSync(options)`](useVibeSync.ts) — WebSocket hook; options typed by `UseVibeSyncOptions` (`{ wsUrl: string; onRefresh: () => void }`); returns `{ lastAgentEvent, setLastAgentEvent, wsRef }`.

## Invariants

- `handleImpactToggle`: enabling impact sets `layoutMode = "impact"`; disabling reverts `"impact"` → `"map"` [src/web/src/features/map-room/hooks/useMapRoomState.ts:handleImpactToggle].
- `handleTraceResult`: a found trace sets `layoutMode = "trace"`, `impactMode = false`, and auto-reveals `res.node_ids`; `null` resets to `"map"` [src/web/src/features/map-room/hooks/useMapRoomState.ts:handleTraceResult].
- `onExpandCallees`/`onExpandCallers`: merge API neighbors into `rawData.current`, deduped by node id and edge key `${from}-${to}-${relation}`; fall back to local edges on error [src/web/src/features/map-room/hooks/useMapRoomState.ts:onExpandCallees].
- `onExpandNodeConnections` skips relation type `"defines"` [src/web/src/features/map-room/hooks/useMapRoomState.ts:onExpandNodeConnections].
- `getConnectedComponent` skips `"defines"` edges and traverses only revealed ids [src/web/src/features/map-room/utils/graphTraversal.ts:getConnectedComponent].
- `findRootEntryPoints` caps at 8 roots (4 main/CLI, 4 endpoints, fill from natural roots with out-degree > 0 and kind not `variable`/`module`), adding up to 3 immediate callees per root; fallback top 5 by out-degree [src/web/src/features/map-room/utils/treeHierarchy.ts:findRootEntryPoints].
- `fetchGraph`: GET `/graph`; seeds `revealedNodeIds` via `findRootEntryPoints` only on first load (`prev.size === 0`); GET `/api/security/audit?skip_deps=true` in background, keeping `status !== "APPROVED"` findings [src/web/src/features/map-room/hooks/useMapRoomState.ts:fetchGraph].
- `showTracePath`: GET `/trace` with `from_symbol`, `to_symbol`, `max_depth=8`, `detect_splits=true` [src/web/src/features/map-room/hooks/useMapRoomState.ts:showTracePath].

## Side Effects

- `useMapRoomState`: axios GET `/graph`, GET `/api/security/audit`, GET `/trace`, GET `/api/node/:id/neighbors?direction=up|down`; `console.error`/`console.log`; `rawData.current` mutation; React state setter calls (`setNodes`, `setEdges`, `setRevealedNodeIds`, `setSelectedElement`, `setLayoutMode`, and the other `useState` setters).
- `useVibeSync`: WebSocket connection to `WS_URL` with auto-reconnect (`min(1000 * 2^retry, 15000)`); "refresh" text frames trigger `onRefresh`; JSON frames of `type === "agent_focus"` set `lastAgentEvent`; cleanup closes socket and clears timers [src/web/src/features/map-room/hooks/useVibeSync.ts:useVibeSync].

## Verification

- Run: `cd src/web && bun run build`
