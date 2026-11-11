# Web Client Source

## Overview

React application entry point and root shell. `main.tsx` mounts `App` into the `#root` element; `App.tsx` wires the map room canvas, dashboard, and docs workspaces to shared state from `useMapRoomState`.

## Interface

- [`main.tsx`](./main.tsx) — `createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>)`; imports `./index.css` and `./App.tsx`.
- [`App.tsx`](./App.tsx) — default export `App`; module-level `nodeTypes` map: `vibeNode` → `VibeNode`, `packageNode` → `PackageNode`, `districtGroup` → `DistrictNode`, `fileGroup` → `FileGroupNode`.
- [`App.css`](./App.css), [`index.css`](./index.css) — styling only.
- [`./features`](./features/README.md) - Feature contracts: dashboard, docs, map-room.

## Invariants

- Workspace mode state is `"map" | "dashboard" | "docs"`, default `"map"`; dashboard and docs views render only when their mode is active [src/web/src/App.tsx:App].
- `handleViewModeChange` closes the sidebar when view mode becomes `"full"` [src/web/src/App.tsx:App].
- `visibleSymbols` counts nodes excluding `districtGroup` and `fileGroup` container types [src/web/src/App.tsx:App].
- `handleSwitchToMapAndFocus` switches to map mode, then focuses the node after a 60 ms timeout [src/web/src/App.tsx:App].
- React Flow viewport is bounded to `minZoom={0.05}` and `maxZoom={2.0}`, `fitView` padding 0.35, `onlyRenderVisibleElements` [src/web/src/App.tsx:App].
- `WebGPUCanvas` mounts only when `webGpuTurbo` is enabled [src/web/src/App.tsx:App].

## Side Effects

- `main.tsx` mounts the React tree into the DOM `#root` element and loads global CSS [src/web/src/main.tsx].
- `App.tsx` starts graph fetching, layout application, and WebSocket sync through `useMapRoomState` [src/web/src/App.tsx:App].

## Verification

- Run: `cd src/web && bun run build`
