import * as vscode from "vscode";
import type { VibeTraceApiClient } from "../client/api_client.js";

export class VibeTraceCodeActionProvider implements vscode.CodeActionProvider {
  public static readonly providedCodeActionKinds = [
    vscode.CodeActionKind.QuickFix,
    vscode.CodeActionKind.Refactor,
  ];

  public provideCodeActions(
    document: vscode.TextDocument,
    _range: vscode.Range | vscode.Selection,
    context: vscode.CodeActionContext,
    token: vscode.CancellationToken
  ): vscode.CodeAction[] {
    const actions: vscode.CodeAction[] = [];

    for (const diagnostic of context.diagnostics) {
      if (token.isCancellationRequested) break;
      const source = diagnostic.source || "";
      if (!source.startsWith("VibeTrace")) {
        continue;
      }

      // Security & Taint Findings
      if (
        source === "VibeTrace Security" ||
        diagnostic.code === "security-finding" ||
        String(diagnostic.code).toLowerCase().includes("cwe")
      ) {
        const askAiAction = new vscode.CodeAction(
          "$(sparkle) VibeTrace: Ask AI to Remediate Security Finding",
          vscode.CodeActionKind.QuickFix
        );
        askAiAction.isPreferred = true;
        askAiAction.diagnostics = [diagnostic];
        askAiAction.command = {
          command: "vibetrace.investigateWithAi",
          title: "Ask AI to Remediate",
          arguments: [diagnostic, document.uri, "security"],
        };
        actions.push(askAiAction);

        const copyPromptAction = new vscode.CodeAction(
          "$(clippy) VibeTrace: Copy Remediation Context for AI Chat",
          vscode.CodeActionKind.QuickFix
        );
        copyPromptAction.diagnostics = [diagnostic];
        copyPromptAction.command = {
          command: "vibetrace.copyRemediationPrompt",
          title: "Copy Remediation Context",
          arguments: [diagnostic, document.uri],
        };
        actions.push(copyPromptAction);
      }

      // Architectural Split Tripwires
      if (source === "VibeTrace Architecture" || diagnostic.code === "architectural-split") {
        const approveAction = new vscode.CodeAction(
          "$(shield) VibeTrace: Approve Split in .vibetrace/splits.toml",
          vscode.CodeActionKind.QuickFix
        );
        approveAction.isPreferred = true;
        approveAction.diagnostics = [diagnostic];
        approveAction.command = {
          command: "vibetrace.approveSplit",
          title: "Approve Split",
          arguments: [diagnostic, document.uri],
        };
        actions.push(approveAction);

        const askAiSplitAction = new vscode.CodeAction(
          "$(sparkle) VibeTrace: Ask AI to Refactor & Decompose Split",
          vscode.CodeActionKind.Refactor
        );
        askAiSplitAction.diagnostics = [diagnostic];
        askAiSplitAction.command = {
          command: "vibetrace.investigateWithAi",
          title: "Ask AI to Refactor",
          arguments: [diagnostic, document.uri, "split"],
        };
        actions.push(askAiSplitAction);
      }

      // High Caller Blast Radius
      if (
        source === "VibeTrace Blast" ||
        diagnostic.code === "high-blast-radius" ||
        diagnostic.code === "blast-radius-warning"
      ) {
        const inspectAction = new vscode.CodeAction(
          "$(search-fuzzy) VibeTrace: Inspect All Active Callers",
          vscode.CodeActionKind.QuickFix
        );
        inspectAction.isPreferred = true;
        inspectAction.diagnostics = [diagnostic];
        inspectAction.command = {
          command: "vibetrace.inspectCallers",
          title: "Inspect Callers",
        };
        actions.push(inspectAction);

        const askAiBlastAction = new vscode.CodeAction(
          "$(sparkle) VibeTrace: Ask AI to Safely Modify with Caller Guard",
          vscode.CodeActionKind.Refactor
        );
        askAiBlastAction.diagnostics = [diagnostic];
        askAiBlastAction.command = {
          command: "vibetrace.investigateWithAi",
          title: "Ask AI to Modify",
          arguments: [diagnostic, document.uri, "blast"],
        };
        actions.push(askAiBlastAction);
      }
    }

    return actions;
  }
}
