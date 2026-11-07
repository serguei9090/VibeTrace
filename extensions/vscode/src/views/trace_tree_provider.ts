import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import type { TraceHopData, TracePathResponse } from "../client/api_client.js";

const ROUTE_THEME_COLORS = ["charts.blue", "charts.orange", "charts.purple"];

function getRouteThemeColor(index: number): vscode.ThemeColor {
  const colorId = ROUTE_THEME_COLORS[index % ROUTE_THEME_COLORS.length];
  return new vscode.ThemeColor(colorId);
}

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
    // biome-ignore lint/suspicious/noConfusingVoidType: standard VS Code TreeDataProvider pattern
    new vscode.EventEmitter<TraceTreeItem | undefined | void>();
  // biome-ignore lint/suspicious/noConfusingVoidType: standard VS Code TreeDataProvider pattern
  readonly onDidChangeTreeData: vscode.Event<TraceTreeItem | undefined | void> =
    this._onDidChangeTreeData.event;

  private currentTrace: TracePathResponse | null = null;
  private activeRouteIndex = 0;

  public setTrace(trace: TracePathResponse | null): void {
    if (!trace) {
      this.currentTrace = null;
      this.activeRouteIndex = 0;
      this._onDidChangeTreeData.fire();
      return;
    }

    // Clone to prevent mutating caller-owned objects
    this.currentTrace = {
      ...trace,
      hops: trace.hops ? [...trace.hops] : [],
      paths: trace.paths ? trace.paths.map((p) => [...p]) : undefined,
    };

    if (this.currentTrace.paths && this.currentTrace.paths.length > 0) {
      const candidateIdx = trace.activeRouteIndex ?? 0;
      const clampedIdx =
        candidateIdx >= 0 && candidateIdx < this.currentTrace.paths.length ? candidateIdx : 0;
      this.activeRouteIndex = clampedIdx;
      this.currentTrace.activeRouteIndex = clampedIdx;
      this.currentTrace.hops = this.currentTrace.paths[clampedIdx] || this.currentTrace.hops;
    } else {
      this.activeRouteIndex = 0;
    }

    this._onDidChangeTreeData.fire();
  }

  public selectRoute(routeIndex: number): void {
    if (!this.currentTrace?.paths?.[routeIndex]) {
      return;
    }
    this.activeRouteIndex = routeIndex;
    this.currentTrace.activeRouteIndex = routeIndex;
    this.currentTrace.hops = this.currentTrace.paths[routeIndex];
    this._onDidChangeTreeData.fire();
  }

  public getTrace(): TracePathResponse | null {
    if (!this.currentTrace) {
      return null;
    }
    return {
      ...this.currentTrace,
      activeRouteIndex: this.activeRouteIndex,
      hops: [...(this.currentTrace.hops || [])],
    };
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
            getRouteThemeColor(r)
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
        const callLine = hop.call_line ?? hop.from_node?.start_line ?? 1;

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
        if (!targetNode) {
          return Promise.resolve(items);
        }
        const targetLabel = `[Goal] ${targetNode.qualified_name || targetNode.name || "unknown"}`;
        const targetFilePath = targetNode.file_path || "";
        const targetStartLine = targetNode.start_line ?? 1;
        const targetItem = new TraceTreeItem(
          targetLabel,
          vscode.TreeItemCollapsibleState.None,
          "hop",
          undefined,
          this.resolveFilePath(targetFilePath),
          targetStartLine
        );
        targetItem.iconPath = new vscode.ThemeIcon("target");
        targetItem.description = `${this.basename(targetFilePath)}:${targetStartLine}`;
        targetItem.tooltip = `Destination reached: ${targetFilePath}:${targetStartLine}`;
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
    if (!filePath) {
      return filePath;
    }
    if (path.isAbsolute(filePath)) {
      return filePath;
    }
    const folders = vscode.workspace.workspaceFolders || [];
    for (const folder of folders) {
      const abs = vscode.Uri.joinPath(folder.uri, filePath).fsPath;
      if (fs.existsSync(abs)) {
        return abs;
      }
    }
    return folders.length > 0 ? vscode.Uri.joinPath(folders[0].uri, filePath).fsPath : filePath;
  }
}
