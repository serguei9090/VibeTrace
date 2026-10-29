import * as vscode from "vscode";
import type { ProjectStatus, VibeTraceApiClient } from "../client/api_client.js";

export class VibeTraceMapRoomWebviewProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private apiClient: VibeTraceApiClient;
  private daemonUrl: string;

  constructor(apiClient: VibeTraceApiClient, daemonUrl = "http://127.0.0.1:8000") {
    this.apiClient = apiClient;
    this.daemonUrl = daemonUrl;
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
        case "openMapRoomExternal": {
          await vscode.env.openExternal(vscode.Uri.parse(this.daemonUrl));
          break;
        }
        case "reindex": {
          await vscode.commands.executeCommand("vibetrace.reindex");
          break;
        }
        case "refreshFederation": {
          await vscode.commands.executeCommand("vibetrace.refreshProject");
          break;
        }
        case "inspectCallers": {
          await vscode.commands.executeCommand("vibetrace.inspectCallers", message.symbol);
          break;
        }
        case "navigate": {
          if (message.filePath && message.line !== undefined) {
            await this.navigateToFile(message.filePath, message.line);
          }
          break;
        }
      }
    });

    this.refresh();
  }

  public async refresh(): Promise<void> {
    if (!this.view) return;

    const isConnected = await this.apiClient.isAvailable();
    const projectStatus = isConnected ? await this.apiClient.getProjectStatus() : null;
    const links = isConnected && projectStatus ? await this.apiClient.getProjectLinks() : [];

    this.view.webview.html = this.renderHtml(isConnected, projectStatus, links);
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

  private renderHtml(
    isConnected: boolean,
    project: ProjectStatus | null,
    links: Array<{
      client_repo: string;
      server_repo: string;
      http_method: string;
      route_path: string;
      server_handler: string;
    }>
  ): string {
    const statusColor = isConnected ? "#4ec9b0" : "#f14c4c";
    const statusText = isConnected ? "Daemon Connected" : "Daemon Offline";

    let projectSection = "";
    if (project) {
      const memberPills = project.members
        .map(
          (m) =>
            `<span class="pill" title="${m.path}">📁 <strong>${m.name}</strong> (${m.node_count} nodes)</span>`
        )
        .join("");

      const linkRows = links
        .slice(0, 5)
        .map(
          (lnk) =>
            `<li class="link-item"><span class="badge ${lnk.http_method.toLowerCase()}">${lnk.http_method}</span> <code>${lnk.route_path}</code><div class="link-sub">${lnk.client_repo} ➔ ${lnk.server_repo}:${lnk.server_handler}</div></li>`
        )
        .join("");

      projectSection = `
        <div class="card">
          <div class="card-header">
            <span>🌐 Federated Workspace: <strong>${project.project_name}</strong></span>
            <span class="subtext">(${project.storage})</span>
          </div>
          <div class="pill-container">
            ${memberPills}
          </div>
          ${
            links.length > 0
              ? `<div class="links-header">🔗 Cross-Repo Contracts (${links.length} total)</div>
                 <ul class="links-list">${linkRows}</ul>`
              : ""
          }
        </div>
      `;
    }

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>VibeTrace Map Room</title>
  <style>
    body {
      font-family: var(--vscode-font-family, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto);
      font-size: 12px;
      color: var(--vscode-foreground);
      background-color: var(--vscode-sideBar-background);
      padding: 10px;
      margin: 0;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--vscode-panel-border, #333);
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      font-weight: 500;
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background-color: ${statusColor};
      box-shadow: 0 0 4px ${statusColor};
    }
    .btn-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-bottom: 12px;
    }
    button {
      background-color: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      border: none;
      padding: 6px 10px;
      border-radius: 2px;
      cursor: pointer;
      font-size: 11px;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      transition: background-color 0.15s;
    }
    button:hover {
      background-color: var(--vscode-button-hoverBackground);
    }
    button.secondary {
      background-color: var(--vscode-button-secondaryBackground, #3a3d41);
      color: var(--vscode-button-secondaryForeground, #ffffff);
    }
    button.secondary:hover {
      background-color: var(--vscode-button-secondaryHoverBackground, #45494e);
    }
    .card {
      background: var(--vscode-editor-background);
      border: 1px solid var(--vscode-panel-border, #2d2d2d);
      border-radius: 4px;
      padding: 8px;
      margin-bottom: 12px;
    }
    .card-header {
      font-size: 11px;
      display: flex;
      justify-content: space-between;
      margin-bottom: 6px;
    }
    .subtext {
      opacity: 0.7;
    }
    .pill-container {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      margin-top: 4px;
    }
    .pill {
      background: var(--vscode-badge-background, #2c2d30);
      color: var(--vscode-badge-foreground, #fff);
      padding: 2px 6px;
      border-radius: 3px;
      font-size: 10px;
    }
    .links-header {
      font-size: 11px;
      font-weight: 600;
      margin-top: 8px;
      margin-bottom: 4px;
    }
    .links-list {
      list-style: none;
      padding: 0;
      margin: 0;
    }
    .link-item {
      padding: 4px 0;
      border-bottom: 1px solid var(--vscode-panel-border, #222);
      font-size: 10px;
    }
    .badge {
      font-weight: bold;
      padding: 1px 4px;
      border-radius: 2px;
      font-size: 9px;
    }
    .badge.get { background: #0e70c0; color: #fff; }
    .badge.post { background: #28a745; color: #fff; }
    .badge.put, .badge.patch { background: #d39e00; color: #fff; }
    .badge.delete { background: #dc3545; color: #fff; }
    .link-sub {
      opacity: 0.75;
      font-size: 9px;
      margin-top: 2px;
    }
    .frame-container {
      margin-top: 8px;
      border: 1px solid var(--vscode-panel-border, #333);
      border-radius: 4px;
      height: 320px;
      overflow: hidden;
      position: relative;
    }
    iframe {
      width: 100%;
      height: 100%;
      border: none;
    }
  </style>
</head>
<body>
  <div class="header">
    <div class="status-badge">
      <div class="status-dot"></div>
      <span>${statusText}</span>
    </div>
    <span class="subtext">${this.daemonUrl}</span>
  </div>

  <div class="btn-group">
    <button onclick="send('openMapRoomExternal')">🚀 Open Full Map Room</button>
    <button class="secondary" onclick="send('reindex')">🔄 Reindex AST & Graph</button>
    <button class="secondary" onclick="send('refreshFederation')">🌐 Project Federation Status</button>
  </div>

  ${projectSection}

  <div class="frame-container">
    <iframe src="${this.daemonUrl}" title="VibeTrace Map Room"></iframe>
  </div>

  <script>
    const vscode = acquireVsCodeApi();
    function send(cmd, data = {}) {
      vscode.postMessage({ command: cmd, ...data });
    }
    window.addEventListener('message', event => {
      const msg = event.data;
      if (msg.type === 'agent_focus') {
        console.log('VibeTrace agent focus:', msg);
      }
    });
  </script>
</body>
</html>`;
  }

  private async navigateToFile(filePath: string, line: number): Promise<void> {
    let targetUri: vscode.Uri | null = null;
    if (vscode.workspace.workspaceFolders) {
      for (const f of vscode.workspace.workspaceFolders) {
        const candidate = vscode.Uri.joinPath(f.uri, filePath);
        try {
          await vscode.workspace.fs.stat(candidate);
          targetUri = candidate;
          break;
        } catch {
          // Keep searching
        }
      }
    }
    if (!targetUri) {
      targetUri = vscode.Uri.file(filePath);
    }
    try {
      const doc = await vscode.workspace.openTextDocument(targetUri);
      const editor = await vscode.window.showTextDocument(doc);
      const pos = new vscode.Position(Math.max(0, line - 1), 0);
      editor.selection = new vscode.Selection(pos, pos);
      editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
    } catch {
      // Ignored
    }
  }
}
