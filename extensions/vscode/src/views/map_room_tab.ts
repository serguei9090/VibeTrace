import * as vscode from "vscode";

let activePanel: vscode.WebviewPanel | null = null;

export function openMapRoomTab(
  context: vscode.ExtensionContext,
  daemonUrl: string,
  title = "VibeTrace: Map Room & Quality Gate"
): vscode.WebviewPanel {
  if (activePanel) {
    activePanel.title = title;
    activePanel.reveal(vscode.ViewColumn.One);
    return activePanel;
  }

  activePanel = vscode.window.createWebviewPanel(
    "vibetrace.mapRoomTab",
    title,
    vscode.ViewColumn.One,
    {
      enableScripts: true,
      retainContextWhenHidden: true,
    }
  );

  activePanel.iconPath = vscode.Uri.joinPath(context.extensionUri, "resources", "icon.svg");

  let safeUrl = "http://localhost:4000";
  try {
    const parsed = new URL(daemonUrl);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      safeUrl = parsed.toString();
    }
  } catch {
    safeUrl = "http://localhost:4000";
  }

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
  <iframe src="${encodeURI(safeUrl)}" allow="cross-origin-isolated; fullscreen"></iframe>
</body>
</html>`;

  activePanel.onDidDispose(() => {
    activePanel = null;
  });

  return activePanel;
}
