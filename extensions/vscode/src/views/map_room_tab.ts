import * as vscode from "vscode";

let activePanel: vscode.WebviewPanel | null = null;

export function openMapRoomTab(
  context: vscode.ExtensionContext,
  daemonUrl: string
): vscode.WebviewPanel {
  if (activePanel) {
    activePanel.reveal(vscode.ViewColumn.One);
    return activePanel;
  }

  activePanel = vscode.window.createWebviewPanel(
    "vibetrace.mapRoomTab",
    "VibeTrace: Architecture Map",
    vscode.ViewColumn.One,
    {
      enableScripts: true,
      retainContextWhenHidden: true,
    }
  );

  activePanel.iconPath = vscode.Uri.joinPath(context.extensionUri, "resources", "icon.svg");

  activePanel.webview.html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>VibeTrace Architecture Map</title>
  <style>
    body, html {
      margin: 0;
      padding: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background-color: var(--vscode-editor-background, #1e1e1e);
    }
    iframe {
      width: 100%;
      height: 100%;
      border: none;
    }
  </style>
</head>
<body>
  <iframe src="${daemonUrl}" allow="cross-origin-isolated; fullscreen"></iframe>
</body>
</html>`;

  activePanel.onDidDispose(() => {
    activePanel = null;
  });

  return activePanel;
}
