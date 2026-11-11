# React + TypeScript + Vite

## System Overview

VibeGraph web client. Single-page React 19 + TypeScript app built with Vite, rendered as a canvas architecture map (map room), a unified quality gate dashboard, and a documentation RAG workspace. Backend data comes from the VibeTrace HTTP API and WebSocket sync endpoint; no local persistence beyond in-memory caches.

## Local Interface & Invariants

- [`package.json`](./package.json) scripts:
  - `dev` - `vite`
  - `build` - `tsc -b && vite build`
  - `lint` - `biome check --write src`
  - `test:e2e` - `playwright test`
  - `preview` - `vite preview`
- [`vite.config.ts`](./vite.config.ts) - `defineConfig` with `@vitejs/plugin-react`; manual Rollup chunks: `elkjs` → `vendor_elk`, `@xyflow` → `vendor_flow`, `lucide-react` → `vendor_icons`.
- [`playwright.config.ts`](./playwright.config.ts) - `testDir: "./e2e"`, timeout 30000 ms, retries 0, workers 1, `baseURL: "http://localhost:5173"`, headless, screenshot `only-on-failure`; web servers: `uv run python -m vibetrace.server_api` on port 8000 with `cwd: "../.."`, and `bun run dev --port 5173`.
- [`index.html`](./index.html) - mounts `/src/main.tsx` into `<div id="root">`, title `VibeTrace Map Room`.
- [`src`](./src/README.md) - application source: entry point, root shell, and feature contracts.

## Subdirectories & System Boundaries

- [`src`](./src/README.md) - React entry point (`main.tsx`), root shell (`App.tsx`), and feature packages for dashboard, docs, and map-room.

## Global Invariants

- Runtime API base is `http://localhost:8000` when the app runs on Vite dev port `5173`/`3000`, otherwise the current origin [src/web/src/features/map-room/hooks/useMapRoomState.ts:API_URL].
- WebSocket sync endpoint follows the same rule: `ws://localhost:8000/ws` in dev, otherwise `ws(s)://<current host>/ws` [src/web/src/features/map-room/hooks/useMapRoomState.ts:WS_URL].
- Excluded from this contract: `node_modules`, `dist`, `test-results`, `.vibetrace`, `vibetrace_context`, `e2e`, `public`, `src/assets`.

## Global Side Effects

- Network calls to the VibeTrace API: `/graph`, `/source`, `/trace`, `/api/project`, `/api/security/audit`, `/api/security/approve`, `/api/splits/audit`, `/api/splits/approve`, `/api/quality/audit`, `/api/quality/approve`, `/api/vibe/guard`, `/api/vibe/bundle`, `/api/reachable`, `/api/saved-traces`, `/api/node/:id/neighbors`, `/api/review`, `/api/docs/catalogs`, `/api/docs/search`, `/api/docs/index`.
- Persistent WebSocket connection to `/ws` with automatic reconnect.
- Clipboard writes for AI fix prompts and Vibe bundles.
- Canvas/WebGL/WebGPU drawing in the accelerated map canvas.

## System Verification

- Run: `cd src/web && bun install && bun run build`
- Run: `cd src/web && bun run test:e2e`

## Developer Notes

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) (or [oxc](https://oxc.rs) when used in [rolldown-vite](https://vite.dev/guide/rolldown)) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react-swc/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```
