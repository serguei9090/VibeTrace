import * as vscode from "vscode";
import type { UnifiedReviewReport, VibeTraceApiClient } from "../client/api_client.js";

export type ReviewCategory = "category_security" | "category_splits" | "category_blast";

export class ReviewTreeItem extends vscode.TreeItem {
  constructor(
    public readonly label: string,
    public readonly collapsibleState: vscode.TreeItemCollapsibleState,
    public readonly category?: ReviewCategory,
    public readonly filePath?: string,
    public readonly line?: number
  ) {
    super(label, collapsibleState);

    if (filePath && line !== undefined) {
      this.command = {
        command: "vscode.open",
        title: "Jump to Code Location",
        arguments: [
          vscode.Uri.file(filePath),
          {
            selection: new vscode.Range(Math.max(0, line - 1), 0, Math.max(0, line - 1), 0),
            preview: true,
          },
        ],
      };
    }
  }
}

export class VibeTraceReviewTreeProvider implements vscode.TreeDataProvider<ReviewTreeItem> {
  // biome-ignore lint/suspicious/noConfusingVoidType: standard VS Code TreeDataProvider pattern
  private _onDidChangeTreeData: vscode.EventEmitter<ReviewTreeItem | undefined | void> =
    // biome-ignore lint/suspicious/noConfusingVoidType: standard VS Code TreeDataProvider pattern
    new vscode.EventEmitter<ReviewTreeItem | undefined | void>();
  // biome-ignore lint/suspicious/noConfusingVoidType: standard VS Code TreeDataProvider pattern
  readonly onDidChangeTreeData: vscode.Event<ReviewTreeItem | undefined | void> =
    this._onDidChangeTreeData.event;

  private apiClient: VibeTraceApiClient;
  private cachedReport: UnifiedReviewReport | null = null;
  private isLoading = false;

  constructor(apiClient: VibeTraceApiClient) {
    this.apiClient = apiClient;
  }

  public refresh(): void {
    this.cachedReport = null;
    this._onDidChangeTreeData.fire();
  }

  public getTreeItem(element: ReviewTreeItem): vscode.TreeItem {
    return element;
  }

  public async getChildren(element?: ReviewTreeItem): Promise<ReviewTreeItem[]> {
    if (!element) {
      if (!this.cachedReport && !this.isLoading) {
        this.isLoading = true;
        try {
          this.cachedReport = await this.apiClient.getReview("changed");
        } catch (err) {
          console.error("VibeTrace Review Tree fetch failed:", err);
          this.cachedReport = null;
        } finally {
          this.isLoading = false;
        }
      }

      if (!this.cachedReport) {
        const item = new ReviewTreeItem(
          "Quality Gate: Ready (Click refresh to scan)",
          vscode.TreeItemCollapsibleState.None
        );
        item.iconPath = new vscode.ThemeIcon("shield");
        return [item];
      }

      const secCount = (this.cachedReport.security_findings || []).length;
      const splitCount = (this.cachedReport.actionable_splits || []).length;
      const blastCount = (this.cachedReport.caller_guards || []).filter(
        (g) => g.risk_level === "HIGH"
      ).length;

      const secCat = new ReviewTreeItem(
        `🛡️ Security & Taint Findings (${secCount})`,
        secCount > 0
          ? vscode.TreeItemCollapsibleState.Expanded
          : vscode.TreeItemCollapsibleState.Collapsed,
        "category_security"
      );
      secCat.iconPath = new vscode.ThemeIcon(
        secCount > 0 ? "error" : "pass",
        secCount > 0
          ? new vscode.ThemeColor("errorForeground")
          : new vscode.ThemeColor("charts.green")
      );

      const splitCat = new ReviewTreeItem(
        `🔀 Architectural Split Tripwires (${splitCount})`,
        splitCount > 0
          ? vscode.TreeItemCollapsibleState.Expanded
          : vscode.TreeItemCollapsibleState.Collapsed,
        "category_splits"
      );
      splitCat.iconPath = new vscode.ThemeIcon(
        splitCount > 0 ? "warning" : "pass",
        splitCount > 0
          ? new vscode.ThemeColor("charts.orange")
          : new vscode.ThemeColor("charts.green")
      );

      const blastCat = new ReviewTreeItem(
        `⚠️ Caller Blast Radius Alerts (${blastCount})`,
        blastCount > 0
          ? vscode.TreeItemCollapsibleState.Expanded
          : vscode.TreeItemCollapsibleState.Collapsed,
        "category_blast"
      );
      blastCat.iconPath = new vscode.ThemeIcon(
        blastCount > 0 ? "shield" : "pass",
        blastCount > 0
          ? new vscode.ThemeColor("charts.yellow")
          : new vscode.ThemeColor("charts.green")
      );

      return [secCat, splitCat, blastCat];
    }

    if (!this.cachedReport) {
      return [];
    }

    if (element.category === "category_security") {
      const findings = this.cachedReport.security_findings || [];
      if (findings.length === 0) {
        const empty = new ReviewTreeItem(
          "Clean: No security vulnerabilities or taint sinks found",
          vscode.TreeItemCollapsibleState.None
        );
        empty.iconPath = new vscode.ThemeIcon("check");
        return [empty];
      }

      return findings.map((f) => {
        const cwe = f.cwe ? `[${f.cwe}] ` : "";
        const item = new ReviewTreeItem(
          `${cwe}${f.title}`,
          vscode.TreeItemCollapsibleState.None,
          undefined,
          this.resolveFilePath(f.file_path),
          f.line_number
        );
        item.description = `${this.basename(f.file_path)}:${f.line_number}`;
        item.tooltip = `${f.severity}: ${f.description}\nFile: ${f.file_path}:${f.line_number}`;
        item.iconPath = new vscode.ThemeIcon(
          f.severity === "CRITICAL" || f.severity === "HIGH" ? "error" : "warning",
          f.severity === "CRITICAL" || f.severity === "HIGH"
            ? new vscode.ThemeColor("errorForeground")
            : new vscode.ThemeColor("charts.orange")
        );
        return item;
      });
    }

    if (element.category === "category_splits") {
      const splits = this.cachedReport.actionable_splits || [];
      if (splits.length === 0) {
        const empty = new ReviewTreeItem(
          "Clean: 0 unapproved architectural splits",
          vscode.TreeItemCollapsibleState.None
        );
        empty.iconPath = new vscode.ThemeIcon("check");
        return [empty];
      }

      return splits.map((s) => {
        const item = new ReviewTreeItem(
          `${s.symbol_name} (${s.branch_count} branches) [${s.status}]`,
          vscode.TreeItemCollapsibleState.None,
          undefined,
          this.resolveFilePath(s.file_path),
          s.line_number
        );
        item.description = `${this.basename(s.file_path)}:${s.line_number}`;
        item.tooltip = `Split Tripwire: ${s.symbol_name} in ${s.file_path}:${s.line_number}. Requires approval in .vibetrace/splits.toml.`;
        item.iconPath = new vscode.ThemeIcon("split-horizontal");
        return item;
      });
    }

    if (element.category === "category_blast") {
      const highGuards = (this.cachedReport.caller_guards || []).filter(
        (g) => g.risk_level === "HIGH"
      );
      if (highGuards.length === 0) {
        const empty = new ReviewTreeItem(
          "Safe: 0 modified symbols with critical caller blast radius",
          vscode.TreeItemCollapsibleState.None
        );
        empty.iconPath = new vscode.ThemeIcon("check");
        return [empty];
      }

      return highGuards.map((g) => {
        const item = new ReviewTreeItem(
          `${g.symbol_name} (${g.total_callers} callers)`,
          vscode.TreeItemCollapsibleState.None,
          undefined,
          this.resolveFilePath(g.file_path),
          1
        );
        item.description = `${this.basename(g.file_path)} - HIGH BLAST`;
        item.tooltip = `Caller Guard: ${g.symbol_name} has ${g.total_callers} callers. ${g.safety_advisory}`;
        item.iconPath = new vscode.ThemeIcon("alert");
        return item;
      });
    }

    return [];
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
