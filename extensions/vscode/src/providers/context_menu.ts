import * as vscode from "vscode";
import type { CallerGuardReport, VibeTraceApiClient } from "../client/api_client.js";

export class VibeTraceContextMenuProvider {
  private apiClient: VibeTraceApiClient;
  private onRefreshRequested: () => void;

  constructor(apiClient: VibeTraceApiClient, onRefreshRequested: () => void) {
    this.apiClient = apiClient;
    this.onRefreshRequested = onRefreshRequested;
  }

  public registerCommands(context: vscode.ExtensionContext): void {
    context.subscriptions.push(
      vscode.commands.registerCommand(
        "vibetrace.copySlice",
        async (uri?: vscode.Uri, ..._args: unknown[]) => {
          await this.handleCopySlice(uri);
        }
      )
    );

    context.subscriptions.push(
      vscode.commands.registerCommand("vibetrace.bundleContext", async () => {
        await this.handleBundleContext();
      })
    );

    context.subscriptions.push(
      vscode.commands.registerCommand(
        "vibetrace.inspectCallers",
        async (symbol?: string, filePath?: string, report?: CallerGuardReport) => {
          await this.handleInspectCallers(symbol, filePath, report);
        }
      )
    );

    context.subscriptions.push(
      vscode.commands.registerCommand("vibetrace.refreshProject", async () => {
        await this.handleRefreshProject();
      })
    );

    context.subscriptions.push(
      vscode.commands.registerCommand(
        "vibetrace.reindex",
        async (targetPath?: string | vscode.Uri) => {
          await this.handleReindex(targetPath);
        }
      )
    );
  }

  private async handleCopySlice(uri?: vscode.Uri): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    const document = editor?.document;
    const filePath = uri?.fsPath || document?.uri.fsPath;

    let symbol = this.getSymbolUnderCursor(editor);
    if (!symbol) {
      symbol = await vscode.window.showInputBox({
        title: "VibeTrace: Copy Surgical Code Slice",
        prompt: "Enter the function or class symbol to extract",
        placeHolder: "e.g. process_payment, handle_request",
      });
    }

    if (!symbol) {
      return;
    }

    const slice = await this.apiClient.getCodeSlice(symbol, filePath);
    if (!slice?.found) {
      vscode.window.showWarningMessage(
        `VibeTrace: Symbol '${symbol}' was not found in the indexed call graph.`
      );
      return;
    }

    let formatted = `### VibeTrace Surgical Slice: \`${slice.qualified_name || slice.name}\`\n`;
    formatted += `**File:** \`${slice.file_path}\` (Lines ${slice.start_line}–${slice.end_line})\n\n`;
    formatted += "```\n" + slice.source_code + "\n```\n";

    if (slice.callees && slice.callees.length > 0) {
      formatted += `\n#### Callee Contracts (${slice.callees.length})\n`;
      for (const callee of slice.callees) {
        formatted += `- \`${callee.name}\` in \`${callee.file_path}:${callee.call_line}\``;
        if (callee.signature) {
          formatted += ` -> \`${callee.signature}\``;
        }
        formatted += "\n";
      }
    }

    await vscode.env.clipboard.writeText(formatted);
    const lineCount = slice.end_line - slice.start_line + 1;
    vscode.window.showInformationMessage(
      `VibeTrace: Copied slice for '${symbol}' (${lineCount} lines, ${slice.callees.length} callees) to clipboard!`
    );
  }

  private async handleBundleContext(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    const currentSymbol = this.getSymbolUnderCursor(editor);

    const intent = await vscode.window.showInputBox({
      title: "VibeTrace: Assemble Task Context Bundle",
      prompt: "Describe your coding task or prompt intent for AI context assembly",
      placeHolder: "e.g. Add idempotency retry logic to checkout payment pipeline",
    });

    if (!intent) {
      return;
    }

    const config = vscode.workspace.getConfiguration("vibetrace");
    const budget = config.get<number>("bundleTokenBudget", 1500);

    await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: "VibeTrace: Assembling Task Context Bundle...",
        cancellable: false,
      },
      async () => {
        const bundle = await this.apiClient.getTaskBundle(intent, currentSymbol, budget);
        if (!bundle?.formatted_bundle) {
          vscode.window.showErrorMessage(
            "VibeTrace: Failed to assemble task context bundle. Is the daemon running?"
          );
          return;
        }

        await vscode.env.clipboard.writeText(bundle.formatted_bundle);
        vscode.window.showInformationMessage(
          `VibeTrace: Context bundle ready! (~${bundle.estimated_tokens} tokens copied to clipboard)`
        );
      }
    );
  }

  public async handleInspectCallers(
    symbol?: string,
    filePath?: string,
    report?: CallerGuardReport
  ): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    const sym = symbol || this.getSymbolUnderCursor(editor);

    if (!sym) {
      const input = await vscode.window.showInputBox({
        title: "VibeTrace: Inspect Blast Radius",
        prompt: "Enter symbol name to inspect callers",
      });
      if (!input) return;
      return this.handleInspectCallers(input, filePath);
    }

    let guardReport = report;
    if (!guardReport) {
      guardReport = (await this.apiClient.getCallerGuard(sym, filePath)) || undefined;
    }

    if (!guardReport?.found) {
      vscode.window.showWarningMessage(
        `VibeTrace: No blast radius information found for '${sym}'.`
      );
      return;
    }

    if (guardReport.total_callers === 0) {
      vscode.window.showInformationMessage(
        `VibeTrace: Symbol '${sym}' is a Leaf Node (0 callers). Safe to modify with 0 blast radius.`
      );
      return;
    }

    const items: Array<vscode.QuickPickItem & { file?: string; line?: number }> = [];

    // Header info
    items.push({
      label: `$(shield) Blast Radius: ${guardReport.risk_level} (${guardReport.total_callers} total callers)`,
      detail: guardReport.safety_advisory,
      alwaysShow: true,
    });

    for (const caller of guardReport.callers) {
      const crossTag = caller.is_cross_repo
        ? ` [Cross-Repo: ${caller.repo_name || "Federated"}]`
        : "";
      items.push({
        label: `$(arrow-right) ${caller.caller_symbol}`,
        description: `${caller.file_path}:${caller.line}${crossTag}`,
        detail: `Relation: ${caller.caller_type}`,
        file: caller.file_path,
        line: caller.line,
      });
    }

    const selected = await vscode.window.showQuickPick(items, {
      title: `VibeTrace Callers for '${sym}'`,
      placeHolder: "Select a caller to navigate to its call site in the editor",
    });

    if (selected?.file && selected.line !== undefined) {
      await this.navigateToFile(selected.file, selected.line);
    }
  }

  private async handleRefreshProject(): Promise<void> {
    const status = await this.apiClient.getProjectStatus();
    if (!status) {
      vscode.window.showInformationMessage(
        "VibeTrace: No multi-repo .vibetrace-project.toml workspace active. Operating in single-repo mode."
      );
      return;
    }

    const links = await this.apiClient.getProjectLinks();

    const items: vscode.QuickPickItem[] = [
      {
        label: `$(project) Workspace: ${status.project_name}`,
        description: `Storage: ${status.storage} | Members: ${status.member_count} | Cross-Repo Links: ${links.length}`,
      },
    ];

    for (const member of status.members) {
      items.push({
        label: `$(repo) ${member.name}`,
        description: `${member.path} (${member.node_count} nodes)`,
        detail: `DB: ${member.database_path}`,
      });
    }

    if (links.length > 0) {
      items.push({
        label: `$(link) Cross-Repo Contracts (${links.length} total)`,
        description: "HTTP/RPC routes connecting frontend callers to backend handlers",
      });
      for (const link of links.slice(0, 10)) {
        items.push({
          label: `  ${link.http_method} ${link.route_path}`,
          description: `${link.client_repo} -> ${link.server_repo}:${link.server_handler}`,
        });
      }
    }

    await vscode.window.showQuickPick(items, {
      title: `VibeTrace Project Federation: ${status.project_name}`,
      placeHolder: "Federated Member Repositories & Cross-Repo Contract Links",
    });
  }

  private async handleReindex(targetPath?: string | vscode.Uri): Promise<void> {
    const pathStr =
      typeof targetPath === "string"
        ? targetPath
        : targetPath instanceof vscode.Uri
          ? targetPath.fsPath
          : undefined;

    await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: "VibeTrace: Indexing AST call graph & documentation...",
        cancellable: false,
      },
      async () => {
        const res = await this.apiClient.reindex(pathStr, true);
        if (res) {
          vscode.window.showInformationMessage(
            `VibeTrace: Reindexing completed successfully. (${res.nodes_indexed ?? 0} nodes updated)`
          );
          this.onRefreshRequested();
        } else {
          vscode.window.showErrorMessage(
            "VibeTrace: Reindexing failed. Check daemon connection or terminal logs."
          );
        }
      }
    );
  }

  private getSymbolUnderCursor(editor?: vscode.TextEditor): string | undefined {
    if (!editor) return undefined;
    const document = editor.document;
    const selection = editor.selection;

    if (!selection.isEmpty) {
      return document.getText(selection).trim();
    }

    const range = document.getWordRangeAtPosition(selection.active);
    if (range) {
      return document.getText(range).trim();
    }

    return undefined;
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
    } catch (err) {
      vscode.window.showErrorMessage(`VibeTrace: Unable to open ${filePath}: ${err}`);
    }
  }
}
