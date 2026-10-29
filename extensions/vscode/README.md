# VibeTrace VS Code Extension

Local AI code graph intelligence, surgical code slicing, blast-radius caller guards, and embedded Map Room for VS Code.

## Features

- **Inline Caller-Guard CodeLens:** Live caller badges (`▲ N Callers (Risk: Low/Med/High) | Safe`) above functions and classes across Python, TypeScript/JavaScript, Go, and Rust. Clicking opens an in-editor QuickPick to navigate directly to calling sites.
- **Pre-Commit Safety Diagnostics:** Alerts in the Problems tab if staged or edited symbols have high blast radius or unpatched callers.
- **1-Click Surgical Context Actions:** Right-click context menu options to "Copy Surgical Slice to Clipboard" and "Assemble Task Context Bundle".
- **Embedded Map Room:** Dedicated sidebar webview with live connection status, project federation cards, and bidirectional navigation.
- **Multi-Repo Federation Support:** Real-time visibility into cross-repository HTTP/RPC contracts.

## Requirements

Requires the local VibeTrace daemon running:
```bash
uv run vibetrace serve --port 8000
```

## Settings

- `vibetrace.daemonUrl`: Base URL of the VibeTrace daemon (default: `http://127.0.0.1:8000`).
- `vibetrace.wsUrl`: WebSocket URL for real-time daemon events (default: `ws://127.0.0.1:8000/ws`).
- `vibetrace.enableCodeLens`: Enable caller guard badges above declarations (default: `true`).
- `vibetrace.enableDiagnostics`: Surface blast-radius warnings in Problems pane (default: `true`).
- `vibetrace.bundleTokenBudget`: Default token budget for AI task context bundles (default: `1500`).
