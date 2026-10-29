import * as vscode from "vscode";
import { VibeTraceApiClient } from "./client/api_client.js";
import { VibeTraceWsClient } from "./client/ws_client.js";
import { VibeTraceCodeLensProvider } from "./providers/codelens_provider.js";
import { VibeTraceContextMenuProvider } from "./providers/context_menu.js";
import { VibeTraceDiagnosticsProvider } from "./providers/diagnostics_provider.js";
import { VibeTraceMapRoomWebviewProvider } from "./views/map_room_webview.js";

let wsClient: VibeTraceWsClient | null = null;
let diagnosticsProvider: VibeTraceDiagnosticsProvider | null = null;
let statusBarItem: vscode.StatusBarItem | null = null;

const SUPPORTED_LANGUAGES = [
  "python",
  "typescript",
  "javascript",
  "typescriptreact",
  "javascriptreact",
  "go",
  "rust",
  "csharp",
];

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const config = vscode.workspace.getConfiguration("vibetrace");
  const daemonUrl = config.get<string>("daemonUrl", "http://127.0.0.1:8000");
  const wsUrl = config.get<string>("wsUrl", "ws://127.0.0.1:8000/ws");

  const apiClient = new VibeTraceApiClient(daemonUrl);
  wsClient = new VibeTraceWsClient(wsUrl);

  // Providers
  const codeLensProvider = new VibeTraceCodeLensProvider(apiClient);
  diagnosticsProvider = new VibeTraceDiagnosticsProvider(apiClient);
  const contextMenuProvider = new VibeTraceContextMenuProvider(apiClient, () => {
    codeLensProvider.refresh();
  });
  const mapRoomProvider = new VibeTraceMapRoomWebviewProvider(apiClient, daemonUrl);

  // Register CodeLens for each language selector
  for (const lang of SUPPORTED_LANGUAGES) {
    context.subscriptions.push(
      vscode.languages.registerCodeLensProvider(
        { language: lang, scheme: "file" },
        codeLensProvider
      )
    );
  }

  // Register Webview View
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("vibetrace.mapRoomView", mapRoomProvider)
  );

  // Register Commands
  contextMenuProvider.registerCommands(context);

  context.subscriptions.push(
    vscode.commands.registerCommand("vibetrace.openMapRoom", async () => {
      await vscode.commands.executeCommand("vibetrace.mapRoomView.focus");
    })
  );

  // Status Bar Item
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBarItem.command = "vibetrace.openMapRoom";
  statusBarItem.text = "$(pulse) VibeTrace: Checking...";
  statusBarItem.tooltip = "Click to open VibeTrace Map Room";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // Update Status Bar helper
  const updateStatus = async () => {
    const online = await apiClient.isAvailable();
    if (statusBarItem) {
      if (online) {
        statusBarItem.text = "$(pass) VibeTrace: Online";
        statusBarItem.tooltip = `VibeTrace Daemon active on ${apiClient.getBaseUrl()}`;
      } else {
        statusBarItem.text = "$(circle-slash) VibeTrace: Offline";
        statusBarItem.tooltip = `VibeTrace Daemon unreachable on ${apiClient.getBaseUrl()}. Click to inspect.`;
      }
    }
  };

  // WebSocket Live Events
  wsClient.onRefresh(() => {
    codeLensProvider.refresh();
    mapRoomProvider.refresh();
    if (diagnosticsProvider) {
      diagnosticsProvider.checkStagedSafety();
    }
  });

  wsClient.onAgentFocus((payload) => {
    mapRoomProvider.postFocusEvent(payload.from_symbol || "", payload.node_ids);
  });

  wsClient.onConnectionChange((connected) => {
    if (statusBarItem) {
      statusBarItem.text = connected
        ? "$(pass) VibeTrace: Online"
        : "$(circle-slash) VibeTrace: Offline";
    }
  });

  wsClient.connect();
  await updateStatus();

  // Watch for configuration changes
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("vibetrace.daemonUrl")) {
        const newDaemon = vscode.workspace
          .getConfiguration("vibetrace")
          .get<string>("daemonUrl", "http://127.0.0.1:8000");
        apiClient.updateBaseUrl(newDaemon);
        mapRoomProvider.updateDaemonUrl(newDaemon);
        updateStatus();
      }
      if (e.affectsConfiguration("vibetrace.wsUrl")) {
        const newWs = vscode.workspace
          .getConfiguration("vibetrace")
          .get<string>("wsUrl", "ws://127.0.0.1:8000/ws");
        if (wsClient) {
          wsClient.updateUrl(newWs);
        }
      }
    })
  );
}

export function deactivate(): void {
  if (wsClient) {
    wsClient.dispose();
    wsClient = null;
  }
  if (diagnosticsProvider) {
    diagnosticsProvider.dispose();
    diagnosticsProvider = null;
  }
  if (statusBarItem) {
    statusBarItem.dispose();
    statusBarItem = null;
  }
}
