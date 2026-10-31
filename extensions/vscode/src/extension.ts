import * as vscode from "vscode";
import { VibeTraceApiClient } from "./client/api_client.js";
import { VibeTraceWsClient } from "./client/ws_client.js";
import { VibeTraceCodeLensProvider } from "./providers/codelens_provider.js";
import { VibeTraceContextMenuProvider } from "./providers/context_menu.js";
import { VibeTraceDiagnosticsProvider } from "./providers/diagnostics_provider.js";
import { VibeTraceDaemonManager } from "./server/daemon_manager.js";
import { openMapRoomTab } from "./views/map_room_tab.js";
import { VibeTraceMapRoomWebviewProvider } from "./views/map_room_webview.js";
import { VibeTraceReviewTreeProvider } from "./views/review_tree_provider.js";
import { VibeTraceTraceTreeProvider } from "./views/trace_tree_provider.js";

let wsClient: VibeTraceWsClient | null = null;
let diagnosticsProvider: VibeTraceDiagnosticsProvider | null = null;
let statusBarItem: vscode.StatusBarItem | null = null;
let daemonManager: VibeTraceDaemonManager | null = null;

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
  const configuredPort = config.get<number>("port", 8000);
  const daemonUrl = config.get<string>("daemonUrl", `http://127.0.0.1:${configuredPort}`);
  const wsUrl = config.get<string>("wsUrl", `ws://127.0.0.1:${configuredPort}/ws`);
  const autoStart = config.get<boolean>("autoStartDaemon", true);

  const apiClient = new VibeTraceApiClient(daemonUrl);
  wsClient = new VibeTraceWsClient(wsUrl);

  // Managed Daemon Lifecycle
  daemonManager = new VibeTraceDaemonManager(apiClient);
  context.subscriptions.push(daemonManager);

  // Native Tree View Providers
  const traceTreeProvider = new VibeTraceTraceTreeProvider();
  const reviewTreeProvider = new VibeTraceReviewTreeProvider(apiClient);

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("vibetrace.traceView", traceTreeProvider)
  );
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("vibetrace.reviewView", reviewTreeProvider)
  );

  // In-Editor & CodeLens Providers
  const codeLensProvider = new VibeTraceCodeLensProvider(apiClient);
  diagnosticsProvider = new VibeTraceDiagnosticsProvider(apiClient);
  const contextMenuProvider = new VibeTraceContextMenuProvider(
    apiClient,
    () => {
      codeLensProvider.refresh();
      reviewTreeProvider.refresh();
    },
    (trace) => {
      traceTreeProvider.setTrace(trace);
      vscode.commands.executeCommand("vibetrace.traceView.focus");
    }
  );

  const mapRoomProvider = new VibeTraceMapRoomWebviewProvider(apiClient, daemonUrl, () =>
    openMapRoomTab(context, apiClient.getBaseUrl())
  );

  // Register CodeLens for each language selector
  for (const lang of SUPPORTED_LANGUAGES) {
    context.subscriptions.push(
      vscode.languages.registerCodeLensProvider(
        { language: lang, scheme: "file" },
        codeLensProvider
      )
    );
  }

  // Register Sidebar Webview View
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("vibetrace.mapRoomView", mapRoomProvider)
  );

  // Register Commands
  contextMenuProvider.registerCommands(context);

  context.subscriptions.push(
    vscode.commands.registerCommand("vibetrace.openMapRoom", () => {
      openMapRoomTab(context, apiClient.getBaseUrl());
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("vibetrace.openMapRoomTab", () => {
      openMapRoomTab(context, apiClient.getBaseUrl(), "VibeTrace: Map Room");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("vibetrace.openQualityGateTab", () => {
      openMapRoomTab(context, apiClient.getBaseUrl(), "VibeTrace: Quality Gate");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("vibetrace.refreshReview", () => {
      reviewTreeProvider.refresh();
      diagnosticsProvider?.invalidateReviewCache();
      if (vscode.window.activeTextEditor?.document) {
        diagnosticsProvider?.checkDocument(vscode.window.activeTextEditor.document);
      }
      vscode.window.showInformationMessage("VibeTrace: Refreshed scan issues & quality gate.");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("vibetrace.clearTrace", () => {
      traceTreeProvider.setTrace(null);
    })
  );

  // Status Bar Item
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBarItem.command = "vibetrace.openMapRoomTab";
  statusBarItem.text = "$(pulse) VibeTrace: Starting...";
  statusBarItem.tooltip = "Click to open VibeTrace Architecture Map";
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

  // Auto-start daemon in background with dynamic port fallback
  if (autoStart) {
    try {
      const res = await daemonManager.ensureDaemonRunning(configuredPort);
      if (res.success && res.port !== configuredPort) {
        const newUrl = `http://127.0.0.1:${res.port}`;
        const newWs = `ws://127.0.0.1:${res.port}/ws`;
        apiClient.updateBaseUrl(newUrl);
        mapRoomProvider.updateDaemonUrl(newUrl);
        wsClient.updateUrl(newWs);
      }
    } catch {
      // Safe fallback
    }
  }

  // Auto-refresh sidebar symbols on active editor change
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(() => {
      mapRoomProvider.refresh();
    })
  );

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
    vscode.workspace.onDidChangeConfiguration(async (e) => {
      if (
        e.affectsConfiguration("vibetrace.port") ||
        e.affectsConfiguration("vibetrace.daemonUrl")
      ) {
        const newPort = vscode.workspace.getConfiguration("vibetrace").get<number>("port", 8000);
        const newDaemon = vscode.workspace
          .getConfiguration("vibetrace")
          .get<string>("daemonUrl", `http://127.0.0.1:${newPort}`);
        apiClient.updateBaseUrl(newDaemon);
        mapRoomProvider.updateDaemonUrl(newDaemon);
        await updateStatus();
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
  if (daemonManager) {
    daemonManager.dispose();
    daemonManager = null;
  }
}
