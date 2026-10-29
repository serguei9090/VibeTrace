import * as vscode from "vscode";
import type { ProjectStatus, VibeTraceApiClient } from "../client/api_client.js";

interface ActiveFileSymbol {
  name: string;
  line: number;
  totalCallers: number;
  riskLevel: "LOW" | "MEDIUM" | "HIGH";
}

export class VibeTraceMapRoomWebviewProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private apiClient: VibeTraceApiClient;
  private daemonUrl: string;
  private onOpenTabRequested: () => void;

  constructor(
    apiClient: VibeTraceApiClient,
    daemonUrl = "http://127.0.0.1:8000",
    onOpenTabRequested: () => void = () => {}
  ) {
    this.apiClient = apiClient;
    this.daemonUrl = daemonUrl;
    this.onOpenTabRequested = onOpenTabRequested;
  }

  public updateDaemonUrl(daemonUrl: string): void {
    this.daemonUrl = daemonUrl;
    if (this.view) {
      this.refresh();
    }
  }

  public resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      enableForms: true,
    };

    webviewView.webview.onDidReceiveMessage(async (message) => {
      switch (message.command) {
        case "openMapRoomTab": {
          this.onOpenTabRequested();
          break;
        }
        case "openMapRoomExternal": {
          await vscode.env.openExternal(vscode.Uri.parse(this.daemonUrl));
          break;
        }
        case "reindex": {
          await vscode.commands.executeCommand("vibetrace.reindex");
          break;
        }
        case "inspectCallers": {
          await vscode.commands.executeCommand("vibetrace.inspectCallers", message.symbol);
          break;
        }
        case "copySlice": {
          await vscode.commands.executeCommand("vibetrace.copySlice");
          break;
        }
        case "jumpToLine": {
          if (message.line !== undefined) {
            this.jumpToActiveEditorLine(message.line);
          }
          break;
        }
        case "refresh": {
          await this.refresh();
          break;
        }
      }
    });

    this.refresh();
  }

  public async refresh(): Promise<void> {
    if (!this.view) return;

    const isConnected = await this.apiClient.isAvailable();
    let projectStatus: ProjectStatus | null = null;
    let topologyStats = { nodes: 0, edges: 0 };

    if (isConnected) {
      projectStatus = await this.apiClient.getProjectStatus();
      try {
        const resp = await fetch(`${this.daemonUrl}/api/graph`, {
          signal: AbortSignal.timeout(2500),
        });
        if (resp.ok) {
          const g = await resp.json();
          topologyStats = {
            nodes: (g.nodes || []).length,
            edges: (g.edges || []).length,
          };
        }
      } catch {
        // Fallback
      }
    }

    const activeSymbols = await this.extractActiveFileSymbols();
    this.view.webview.html = this.renderHtml(
      isConnected,
      projectStatus,
      topologyStats,
      activeSymbols
    );
  }

  public postFocusEvent(symbol: string, nodeIds: string[]): void {
    if (this.view) {
      this.view.webview.postMessage({
        type: "agent_focus",
        symbol,
        nodeIds,
      });
    }
  }

  private async extractActiveFileSymbols(): Promise<{
    fileName: string;
    filePath: string;
    symbols: ActiveFileSymbol[];
  } | null> {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.uri.scheme !== "file") {
      return null;
    }

    const doc = editor.document;
    const text = doc.getText();
    const lines = text.split(/\r?\n/);
    const lang = doc.languageId;
    const pat =
      lang === "python"
        ? /^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)/
        : /^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const|func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)/;

    const rawSymbols: Array<{ name: string; line: number }> = [];
    for (let i = 0; i < Math.min(lines.length, 400); i++) {
      const m = pat.exec(lines[i]);
      if (m?.[1]) {
        rawSymbols.push({ name: m[1], line: i });
      }
    }

    const results: ActiveFileSymbol[] = [];
    for (const sym of rawSymbols.slice(0, 12)) {
      const guard = await this.apiClient.getCallerGuard(sym.name, doc.uri.fsPath);
      results.push({
        name: sym.name,
        line: sym.line,
        totalCallers: guard?.total_callers ?? 0,
        riskLevel: guard?.risk_level ?? "LOW",
      });
    }

    return {
      fileName: vscode.workspace.asRelativePath(doc.uri),
      filePath: doc.uri.fsPath,
      symbols: results,
    };
  }

  private jumpToActiveEditorLine(line: number): void {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return;
    const pos = new vscode.Position(Math.max(0, line), 0);
    editor.selection = new vscode.Selection(pos, pos);
    editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
  }

  private renderHtml(
    isConnected: boolean,
    project: (ProjectStatus & { is_federated?: boolean }) | null,
    stats: { nodes: number; edges: number },
    activeFile: { fileName: string; filePath: string; symbols: ActiveFileSymbol[] } | null
  ): string {
    const statusColor = isConnected ? "#4ec9b0" : "#f14c4c";
    const statusText = isConnected ? "Online" : "Offline";
    const isFederated = project?.is_federated === true;

    let symbolRows = "";
    if (activeFile && activeFile.symbols.length > 0) {
      symbolRows = activeFile.symbols
        .map((s) => {
          const badgeClass =
            s.totalCallers === 0
              ? "badge-leaf"
              : s.riskLevel === "HIGH"
                ? "badge-high"
                : "badge-med";
          const label = s.totalCallers === 0 ? "0 Callers (Leaf)" : `▲ ${s.totalCallers} Callers`;
          return `
          <div class="symbol-row">
            <div class="symbol-name" onclick="send('jumpToLine', { line: ${s.line} })" title="Jump to line ${s.line + 1}">
              <code>${s.name}</code>
            </div>
            <div class="symbol-actions">
              <span class="badge ${badgeClass}">${label}</span>
              <button class="btn-icon" onclick="send('inspectCallers', { symbol: '${s.name}' })" title="Inspect Callers">🔍</button>
            </div>
          </div>`;
        })
        .join("");
    } else {
      symbolRows = `<div class="empty-state">${
        activeFile
          ? "No top-level functions found in this file."
          : "Open any source file in editor to inspect functions & blast radius."
      }</div>`;
    }

    let workspaceHtml = "";
    if (isFederated && project) {
      const pills = project.members.map((m) => `<span class="pill">📁 ${m.name}</span>`).join(" ");
      workspaceHtml = `
        <div class="card">
          <div class="card-title">🌐 Federated Workspace: <strong>${project.project_name}</strong></div>
          <div class="pill-group">${pills}</div>
        </div>`;
    } else {
      workspaceHtml = `
        <div class="card">
          <div class="card-title">📦 Workspace Code Graph</div>
          <div class="stats-grid">
            <div class="stat-box"><span class="stat-num">${stats.nodes}</span><span class="stat-label">Symbols</span></div>
            <div class="stat-box"><span class="stat-num">${stats.edges}</span><span class="stat-label">Relations</span></div>
          </div>
        </div>`;
    }

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>VibeTrace Control Panel</title>
  <style>
    body {
      font-family: var(--vscode-font-family);
      font-size: 11px;
      color: var(--vscode-foreground);
      background-color: var(--vscode-sideBar-background);
      padding: 10px;
      margin: 0;
      line-height: 1.4;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 10px;
      padding-bottom: 6px;
      border-bottom: 1px solid var(--vscode-panel-border, #333);
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-weight: 600;
    }
    .status-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background-color: ${statusColor};
      box-shadow: 0 0 4px ${statusColor};
    }
    .primary-btn {
      width: 100%;
      background-color: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      border: none;
      padding: 7px 10px;
      border-radius: 3px;
      cursor: pointer;
      font-weight: 600;
      font-size: 11px;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      margin-bottom: 8px;
    }
    .primary-btn:hover {
      background-color: var(--vscode-button-hoverBackground);
    }
    .btn-row {
      display: flex;
      gap: 6px;
      margin-bottom: 12px;
    }
    .btn-row button {
      flex: 1;
      background: var(--vscode-button-secondaryBackground, #3a3d41);
      color: var(--vscode-button-secondaryForeground, #ffffff);
      border: none;
      padding: 5px;
      border-radius: 2px;
      cursor: pointer;
      font-size: 10px;
    }
    .btn-row button:hover {
      background: var(--vscode-button-secondaryHoverBackground, #45494e);
    }
    .card {
      background: var(--vscode-editor-background);
      border: 1px solid var(--vscode-panel-border, #2d2d2d);
      border-radius: 4px;
      padding: 8px;
      margin-bottom: 12px;
    }
    .card-title {
      font-weight: 600;
      margin-bottom: 6px;
      font-size: 11px;
    }
    .stats-grid {
      display: flex;
      gap: 8px;
    }
    .stat-box {
      flex: 1;
      background: var(--vscode-sideBar-background);
      border: 1px solid var(--vscode-panel-border, #333);
      border-radius: 3px;
      padding: 6px;
      text-align: center;
    }
    .stat-num {
      display: block;
      font-size: 14px;
      font-weight: 700;
      color: var(--vscode-button-background, #0e70c0);
    }
    .stat-label {
      font-size: 9px;
      opacity: 0.75;
    }
    .section-header {
      font-weight: 600;
      font-size: 11px;
      margin-bottom: 6px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .file-tag {
      font-size: 10px;
      color: var(--vscode-textLink-foreground);
      max-width: 140px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .symbol-list {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .symbol-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 4px 6px;
      background: var(--vscode-editor-background);
      border: 1px solid var(--vscode-panel-border, #2d2d2d);
      border-radius: 3px;
    }
    .symbol-name {
      cursor: pointer;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
    }
    .symbol-name:hover {
      color: var(--vscode-textLink-activeForeground);
    }
    .symbol-actions {
      display: flex;
      align-items: center;
      gap: 4px;
    }
    .badge {
      font-size: 9px;
      padding: 1px 5px;
      border-radius: 3px;
      font-weight: 600;
    }
    .badge-leaf { background: #234d38; color: #4ec9b0; }
    .badge-med { background: #4d4423; color: #dcdcaa; }
    .badge-high { background: #5a2424; color: #f48771; }
    .btn-icon {
      background: transparent;
      border: none;
      cursor: pointer;
      padding: 2px;
      font-size: 10px;
    }
    .empty-state {
      padding: 12px;
      text-align: center;
      opacity: 0.65;
      font-style: italic;
      border: 1px dashed var(--vscode-panel-border, #333);
      border-radius: 3px;
    }
    .pill-group {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      margin-top: 4px;
    }
    .pill {
      background: var(--vscode-badge-background, #333);
      color: var(--vscode-badge-foreground, #fff);
      padding: 2px 6px;
      border-radius: 3px;
      font-size: 9px;
    }
  </style>
</head>
<body>
  <div class="header">
    <div class="status-badge">
      <div class="status-dot"></div>
      <span>${statusText}</span>
    </div>
    <span style="opacity: 0.7;">${this.daemonUrl.replace("http://", "")}</span>
  </div>

  <button class="primary-btn" onclick="send('openMapRoomTab')">
    🗺️ Open Architecture Map (Full Tab)
  </button>

  <div class="btn-row">
    <button onclick="send('reindex')">🔄 Reindex AST...</button>
    <button onclick="send('openMapRoomExternal')">🌐 In Browser</button>
  </div>

  <div class="section-header">
    <span>Active File Symbols</span>
    <span class="file-tag">${activeFile ? activeFile.fileName : ""}</span>
  </div>
  <div class="symbol-list">
    ${symbolRows}
  </div>

  <div style="margin-top: 12px;"></div>
  ${workspaceHtml}

  <script>
    const vscode = acquireVsCodeApi();
    function send(cmd, data = {}) {
      vscode.postMessage({ command: cmd, ...data });
    }
  </script>
</body>
</html>`;
  }
}
