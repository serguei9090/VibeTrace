import * as vscode from "vscode";
import type {
  CallerGuardReport,
  TracePathResponse,
  VibeTraceApiClient,
} from "../client/api_client.js";

export class VibeTraceContextMenuProvider {
  private apiClient: VibeTraceApiClient;
  private onRefreshRequested: () => void;
  private onTraceDiscovered?: (trace: TracePathResponse) => void;

  constructor(
    apiClient: VibeTraceApiClient,
    onRefreshRequested: () => void,
    onTraceDiscovered?: (trace: TracePathResponse) => void
  ) {
    this.apiClient = apiClient;
    this.onRefreshRequested = onRefreshRequested;
    this.onTraceDiscovered = onTraceDiscovered;
  }

  public registerCommands(context: vscode.ExtensionContext): void {
    context.subscriptions.push(
      vscode.commands.registerCommand(
        "vibetrace.tracePath",
        async (fromSymbol?: string, toSymbol?: string) => {
          await this.handleTracePath(fromSymbol, toSymbol);
        }
      )
    );

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

    context.subscriptions.push(
      vscode.commands.registerCommand("vibetrace.copyRepoMap", async () => {
        await this.handleCopyRepoMap();
      })
    );

    context.subscriptions.push(
      vscode.commands.registerCommand(
        "vibetrace.investigateWithAi",
        async (diagnostic: vscode.Diagnostic, documentUri?: vscode.Uri, kind?: string) => {
          await this.handleInvestigateWithAi(diagnostic, documentUri, kind);
        }
      )
    );

    context.subscriptions.push(
      vscode.commands.registerCommand(
        "vibetrace.copyRemediationPrompt",
        async (diagnostic: vscode.Diagnostic, documentUri?: vscode.Uri) => {
          await this.handleCopyRemediationPrompt(diagnostic, documentUri);
        }
      )
    );

    context.subscriptions.push(
      vscode.commands.registerCommand(
        "vibetrace.approveSplit",
        async (diagnostic?: vscode.Diagnostic, documentUri?: vscode.Uri) => {
          await this.handleApproveSplit(diagnostic, documentUri);
        }
      )
    );
  }

  public async handleTracePath(fromSymbol?: string, toSymbol?: string): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    let from = fromSymbol || this.getSymbolUnderCursor(editor);

    if (!from) {
      from = await this.promptSymbolWithAutocomplete(
        "VibeTrace: Trace Execution Path (Source)",
        "Search source symbol or route (e.g. 'handle_request', 'GET /api')..."
      );
    }

    if (!from) {
      return;
    }

    let to = toSymbol;
    if (!to) {
      to = await this.promptSymbolWithAutocomplete(
        `VibeTrace: Trace Destination from '${from}'`,
        "Search target symbol or route (type to autocomplete)..."
      );
    }

    if (!to) {
      return;
    }

    await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: `VibeTrace: Tracing path '${from}' -> '${to}'...`,
        cancellable: false,
      },
      async () => {
        const trace = await this.apiClient.getTrace(from, to);
        if (!trace?.found || !trace.hops || trace.hops.length === 0) {
          vscode.window.showWarningMessage(
            `VibeTrace: No execution path found between '${from}' and '${to}'.`
          );
          return;
        }

        if (this.onTraceDiscovered) {
          this.onTraceDiscovered(trace);
        }

        const hopCount = trace.hops.length;
        const choice = await vscode.window.showInformationMessage(
          `VibeTrace: Discovered ${hopCount}-hop path between '${from}' and '${to}'! (Inspected in Active Trace sidebar)`,
          "Jump to First Call Site",
          "Open Map Room Tab"
        );

        if (choice === "Jump to First Call Site") {
          const firstHop = trace.hops[0];
          const line = firstHop.call_line || firstHop.from_node?.start_line || 1;
          await this.navigateToFile(firstHop.from_node.file_path, line);
        } else if (choice === "Open Map Room Tab") {
          vscode.commands.executeCommand("vibetrace.openMapRoomTab");
        }
      }
    );
  }

  private async promptSymbolWithAutocomplete(
    title: string,
    placeholder: string
  ): Promise<string | undefined> {
    return new Promise((resolve) => {
      const qp = vscode.window.createQuickPick<vscode.QuickPickItem & { symbol?: string }>();
      qp.title = title;
      qp.placeholder = placeholder;
      qp.matchOnDescription = true;
      qp.matchOnDetail = true;

      let debounceTimer: ReturnType<typeof setTimeout> | undefined;

      // Populate initial top symbols
      this.apiClient.searchSymbols("", 15).then((results) => {
        qp.items = results.map((r) => ({
          label: `$(${r.kind === "class" ? "symbol-class" : "symbol-function"}) ${r.name}`,
          description: `${r.file_path}:${r.start_line}`,
          detail: r.signature || r.qualified_name,
          symbol: r.name,
        }));
      });

      qp.onDidChangeValue((val) => {
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(async () => {
          qp.busy = true;
          try {
            const results = await this.apiClient.searchSymbols(val.trim(), 20);
            const items: Array<vscode.QuickPickItem & { symbol?: string }> = results.map((r) => ({
              label: `$(${r.kind === "class" ? "symbol-class" : "symbol-function"}) ${r.name}`,
              description: `${r.file_path}:${r.start_line}`,
              detail: r.signature || r.qualified_name,
              symbol: r.name,
            }));

            if (
              val.trim() &&
              !results.some((r) => r.name.toLowerCase() === val.trim().toLowerCase())
            ) {
              items.unshift({
                label: `$(search) Trace to '${val.trim()}'`,
                description: "Custom target symbol or route",
                symbol: val.trim(),
              });
            }

            qp.items = items;
          } finally {
            qp.busy = false;
          }
        }, 150);
      });

      qp.onDidAccept(() => {
        const selected = qp.selectedItems[0];
        const res = selected?.symbol || qp.value.trim();
        qp.hide();
        resolve(res || undefined);
      });

      qp.onDidHide(() => {
        qp.dispose();
        resolve(undefined);
      });

      qp.show();
    });
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

    const confirm = await vscode.window.showWarningMessage(
      pathStr
        ? `Reindex AST and call graph for '${pathStr}'?`
        : "Reindex entire workspace AST and code graph? This will rescan all project files.",
      { modal: true },
      "Reindex",
      "Cancel"
    );
    if (confirm !== "Reindex") {
      return;
    }

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

  public async handleCopyRepoMap(): Promise<void> {
    const items = [
      {
        label: "$(zap) 250 Tokens",
        description: "Tight context for small edits / bug fixes",
        budget: 250,
      },
      {
        label: "$(layers) 500 Tokens",
        description: "Standard balanced architecture overview (Default)",
        budget: 500,
      },
      {
        label: "$(list-tree) 1,000 Tokens",
        description: "Detailed outline with key classes & handlers",
        budget: 1000,
      },
      {
        label: "$(database) 2,000 Tokens",
        description: "Comprehensive workspace repository map",
        budget: 2000,
      },
    ];

    const pick = await vscode.window.showQuickPick(items, {
      title: "VibeTrace: Copy Token-Budgeted Repo Map",
      placeHolder: "Select maximum token budget for PageRank centrality pruning",
    });

    if (!pick) {
      return;
    }

    await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: `VibeTrace: Generating ${pick.budget}-token PageRank repo map...`,
        cancellable: false,
      },
      async () => {
        const res = await this.apiClient.getBudgetedRepoMap(pick.budget);
        if (!res?.formatted_map) {
          vscode.window.showErrorMessage("VibeTrace: Failed to generate budgeted repo map.");
          return;
        }

        await vscode.env.clipboard.writeText(res.formatted_map);
        vscode.window.showInformationMessage(
          `VibeTrace: Repo map (~${res.estimated_tokens} tokens, ${res.pruned_symbols} symbols pruned) copied to clipboard! Ready to paste into AI chat.`
        );
      }
    );
  }

  public async handleInvestigateWithAi(
    diagnostic: vscode.Diagnostic,
    documentUri?: vscode.Uri,
    kind = "security"
  ): Promise<void> {
    const uri = documentUri || vscode.window.activeTextEditor?.document.uri;
    const filePath = uri?.fsPath || "unknown_file";
    const line = diagnostic.range ? diagnostic.range.start.line + 1 : 1;

    let codeSnippet = "";
    if (vscode.window.activeTextEditor?.document) {
      const doc = vscode.window.activeTextEditor.document;
      const start = Math.max(0, line - 5);
      const end = Math.min(doc.lineCount - 1, line + 5);
      const lines: string[] = [];
      for (let l = start; l <= end; l++) {
        const prefix = l + 1 === line ? ">>> " : "    ";
        lines.push(`${prefix}${l + 1}: ${doc.lineAt(l).text}`);
      }
      codeSnippet = lines.join("\n");
    }

    const prompt = [
      `### VibeTrace Code Intelligence Finding (${kind.toUpperCase()})`,
      `- **File:** \`${filePath}\` (Line ${line})`,
      `- **Rule / Code:** \`${diagnostic.code || "VibeTrace Finding"}\``,
      `- **Diagnostic Message:** ${diagnostic.message}`,
      "",
      "```code",
      codeSnippet,
      "```",
      "",
      "**Task for AI Assistant:**",
      "1. Analyze this finding and trace the root cause.",
      "2. Check if this is an authentic vulnerability, taint sink, or architectural split tripwire.",
      "3. Provide the exact minimal, edge-case-correct code diff to fix it safely without breaking callers.",
    ].join("\n");

    await vscode.env.clipboard.writeText(prompt);

    // Attempt native AI chat integration
    try {
      await vscode.commands.executeCommand("workbench.action.chat.open", { query: prompt });
    } catch {
      // Fallback
    }

    vscode.window
      .showInformationMessage(
        "VibeTrace: Ground-truth AI investigation prompt copied to clipboard! (Pasted to AI Chat if available)",
        "View in Problems"
      )
      .then((choice) => {
        if (choice === "View in Problems") {
          vscode.commands.executeCommand("workbench.actions.view.problems");
        }
      });
  }

  public async handleCopyRemediationPrompt(
    diagnostic: vscode.Diagnostic,
    documentUri?: vscode.Uri
  ): Promise<void> {
    await this.handleInvestigateWithAi(diagnostic, documentUri, "security");
  }

  public async handleApproveSplit(
    diagnostic?: vscode.Diagnostic,
    documentUri?: vscode.Uri
  ): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    const uri = documentUri || editor?.document.uri;
    const filePath = uri?.fsPath || "";

    let symbol: string | undefined;
    if (diagnostic?.message) {
      const m = /'([^']+)'/.exec(diagnostic.message);
      if (m?.[1]) {
        symbol = m[1];
      }
    }
    if (!symbol) {
      symbol = this.getSymbolUnderCursor(editor);
    }

    if (!symbol) {
      symbol = await vscode.window.showInputBox({
        title: "VibeTrace: Approve Architectural Split",
        prompt: "Enter symbol name of the split to record in .vibetrace/splits.toml",
      });
    }

    if (!symbol) return;

    const ok = await this.apiClient.approveSplit(symbol, filePath);
    if (ok) {
      this.onRefreshRequested();
      vscode.window.showInformationMessage(
        `VibeTrace: Split for '${symbol}' recorded as approved in .vibetrace/splits.toml.`
      );
    } else {
      vscode.window.showErrorMessage(
        `VibeTrace: Failed to record split approval for '${symbol}'. Is the daemon running?`
      );
    }
  }
}
