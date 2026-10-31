import * as vscode from "vscode";
import type { TraceHopData, TracePathResponse } from "../client/api_client.js";

export type TraceItemType = "header" | "route" | "hop" | "empty";

export class TraceTreeItem extends vscode.TreeItem {
  constructor(
    public readonly label: string,
    public readonly collapsibleState: vscode.TreeItemCollapsibleState,
    public readonly itemType: TraceItemType,
    public readonly hopData?: TraceHopData,
    public readonly filePath?: string,
    public readonly line?: number,
    public readonly routeIndex?: number
  ) {
    super(label, collapsibleState);

    if (itemType === "header") {
      this.iconPath = new vscode.ThemeIcon("arrow-swap");
      this.contextValue = "traceHeader";
    } else if (itemType === "route") {
      this.contextValue = "traceRoute";
      if (routeIndex !== undefined) {
        this.command = {
          command: "vibetrace.selectRoute",
          title: "Select Route",
          arguments: [routeIndex],
        };
      }
    } else if (itemType === "hop") {
      this.iconPath = new vscode.ThemeIcon("debug-step-over");
      this.contextValue = "traceHop";
      if (filePath && line !== undefined) {
        this.command = {
          command: "vscode.open",
          title: "Jump to Call Site",
          arguments: [
            vscode.Uri.file(filePath),
            {
              selection: new vscode.Range(Math.max(0, line - 1), 0, Math.max(0, line - 1), 0),
              preview: true,
            },
          ],
        };
      }
    } else {
      this.iconPath = new vscode.ThemeIcon("info");
      this.contextValue = "traceEmpty";
    }
  }
}

export class VibeTraceTraceTreeProvider implements vscode.TreeDataProvider<TraceTreeItem> {
  // biome-ignore lint/suspicious/noConfusingVoidType: standard VS Code TreeDataProvider pattern
  private _onDidChangeTreeData: vscode.EventEmitter<TraceTreeItem | undefined | void> =
    new vscode.EventEmitter<TraceTreeItem | undefined | void>();
  // biome-ignore lint/suspicious/noConfusingVoidType: standard VS Code TreeDataProvider pattern
  readonly onDidChangeTreeData: vscode.Event<TraceTreeItem | undefined | void> =
    this._onDidChangeTreeData.event;

  private currentTrace: TracePathResponse | null = null;

  public setTrace(trace: TracePathResponse | null): void {
    this.currentTrace = trace;
    if (this.currentTrace?.paths && this.currentTrace.paths.length > 0) {
      if (this.currentTrace.activeRouteIndex === undefined) {
        this.currentTrace.activeRouteIndex = 0;
      }
      this.currentTrace.hops =
        this.currentTrace.paths[this.currentTrace.activeRouteIndex] || this.currentTrace.hops;
    }
    this._onDidChangeTreeData.fire();
  }

  public selectRoute(routeIndex: number): void {
    if (!this.currentTrace?.paths?.[routeIndex]) {
      return;
    }
    this.currentTrace.activeRouteIndex = routeIndex;
    this.currentTrace.hops = this.currentTrace.paths[routeIndex];
    this._onDidChangeTreeData.fire();
  }

  public getTrace(): TracePathResponse | null {
    return this.currentTrace;
  }

  public refresh(): void {
    this._onDidChangeTreeData.fire();
  }

  public getTreeItem(element: TraceTreeItem): vscode.TreeItem {
    return element;
  }

  public getChildren(element?: TraceTreeItem): Thenable<TraceTreeItem[]> {
    if (!this.currentTrace) {
      return Promise.resolve([
        new TraceTreeItem(
          "No active trace. Right-click symbol -> 'Trace Execution Path'",
          vscode.TreeItemCollapsibleState.None,
          "empty"
        ),
      ]);
    }

    if (!element) {
      const hops = this.currentTrace.hops || [];
      const hopCount = hops.length;
      const routeIdx = (this.currentTrace.activeRouteIndex ?? 0) + 1;
      const totalRoutes = this.currentTrace.paths?.length || 1;
      const routeTag = totalRoutes > 1 ? `[Route ${routeIdx}/${totalRoutes}] ` : "";

      const header = new TraceTreeItem(
        `${routeTag}${this.currentTrace.from_symbol} -> ${this.currentTrace.to_symbol} (${hopCount} hops)`,
        vscode.TreeItemCollapsibleState.Expanded,
        "header"
      );
      header.description = this.currentTrace.found ? "Path Found" : "No Path";

      return Promise.resolve([header]);
    }

    if (element.itemType === "header" && this.currentTrace) {
      const hops = this.currentTrace.hops || [];
      const items: TraceTreeItem[] = [];

      // If multiple routes exist, display route switcher items first
      if (this.currentTrace.paths && this.currentTrace.paths.length > 1) {
        const activeIdx = this.currentTrace.activeRouteIndex ?? 0;
        for (let r = 0; r < this.currentTrace.paths.length; r++) {
          const routeHops = this.currentTrace.paths[r];
          const isActive = r === activeIdx;
          const tag = r === 0 ? "Direct / Shortest" : r === 1 ? "Alternative" : "Secondary";
          const routeItem = new TraceTreeItem(
            `${isActive ? "▶ " : ""}Route ${r + 1}: ${tag} (${routeHops.length} hops)`,
            vscode.TreeItemCollapsibleState.None,
            "route",
            undefined,
            undefined,
            undefined,
            r
          );
          routeItem.iconPath = new vscode.ThemeIcon(
            isActive ? "pass-filled" : "circle-outline",
            r === 0
              ? new vscode.ThemeColor("charts.blue")
              : r === 1
                ? new vscode.ThemeColor("charts.orange")
                : new vscode.ThemeColor("charts.purple")
          );
          routeItem.tooltip = `Click to switch to Route ${r + 1} (${tag}, ${routeHops.length} hops)`;
          items.push(routeItem);
        }
      }

      for (let i = 0; i < hops.length; i++) {
        const hop = hops[i];
        const stepNum = i + 1;
        const fromName = hop.from_node?.name || "anonymous";
        const toName = hop.to_node?.name || "anonymous";
        const relation = hop.relation_type || "calls";
        const file = hop.from_node?.file_path || "";
        const callLine = hop.call_line || hop.from_node?.start_line || 1;

        const label = `[${stepNum}] ${fromName} --(${relation})--> ${toName}`;
        const item = new TraceTreeItem(
          label,
          vscode.TreeItemCollapsibleState.None,
          "hop",
          hop,
          this.resolveFilePath(file),
          callLine
        );
        item.description = `${this.basename(file)}:${callLine}`;
        item.tooltip = `${fromName} calls ${toName} at ${file}:${callLine}`;
        items.push(item);
      }

      // Add final target node destination
      if (hops.length > 0) {
        const lastHop = hops[hops.length - 1];
        const targetNode = lastHop.to_node;
        const targetLabel = `[Goal] ${targetNode.qualified_name || targetNode.name}`;
        const targetItem = new TraceTreeItem(
          targetLabel,
          vscode.TreeItemCollapsibleState.None,
          "hop",
          undefined,
          this.resolveFilePath(targetNode.file_path),
          targetNode.start_line
        );
        targetItem.iconPath = new vscode.ThemeIcon("target");
        targetItem.description = `${this.basename(targetNode.file_path)}:${targetNode.start_line}`;
        targetItem.tooltip = `Destination reached: ${targetNode.file_path}:${targetNode.start_line}`;
        items.push(targetItem);
      }

      return Promise.resolve(items);
    }

    return Promise.resolve([]);
  }

  private basename(filePath: string): string {
    const parts = filePath.replace(/\\/g, "/").split("/");
    return parts[parts.length - 1] || filePath;
  }

  private resolveFilePath(filePath: string): string {
    if (vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders.length > 0) {
      for (const folder of vscode.workspace.workspaceFolders) {
        const abs = vscode.Uri.joinPath(folder.uri, filePath).fsPath;
        return abs;
      }
    }
    return filePath;
  }
}
