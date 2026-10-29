import * as vscode from "vscode";
import type { CallerGuardReport, VibeTraceApiClient } from "../client/api_client.js";

export class VibeTraceDiagnosticsProvider implements vscode.Disposable {
  private collection: vscode.DiagnosticCollection;
  private apiClient: VibeTraceApiClient;
  private disposables: vscode.Disposable[] = [];

  constructor(apiClient: VibeTraceApiClient) {
    this.apiClient = apiClient;
    this.collection = vscode.languages.createDiagnosticCollection("vibetrace");
    this.disposables.push(this.collection);

    this.disposables.push(
      vscode.workspace.onDidSaveTextDocument((doc) => {
        this.checkDocument(doc);
      })
    );

    this.disposables.push(
      vscode.window.onDidChangeActiveTextEditor((editor) => {
        if (editor?.document) {
          this.checkDocument(editor.document);
        }
      })
    );

    // Initial check for currently active editor
    if (vscode.window.activeTextEditor?.document) {
      this.checkDocument(vscode.window.activeTextEditor.document);
    }
  }

  public async checkStagedSafety(): Promise<void> {
    const config = vscode.workspace.getConfiguration("vibetrace");
    if (!config.get<boolean>("enableDiagnostics", true)) {
      return;
    }

    const staged = await this.apiClient.getStagedCallerGuard(false);
    if (!staged?.reports || staged.reports.length === 0) {
      return;
    }

    const diagnosticsByUri: Map<string, vscode.Diagnostic[]> = new Map();

    for (const report of staged.reports) {
      if (report.total_callers === 0) {
        continue;
      }

      const uri = this.resolveFileUri(report.file_path);
      if (!uri) {
        continue;
      }

      const line = 0; // Top of file or symbol line
      const range = new vscode.Range(line, 0, line, 80);
      const severity =
        report.risk_level === "HIGH"
          ? vscode.DiagnosticSeverity.Warning
          : vscode.DiagnosticSeverity.Information;

      const crossMsg =
        report.cross_repo_callers && report.cross_repo_callers.length > 0
          ? ` (${report.cross_repo_callers.length} cross-repo callers)`
          : "";

      const diag = new vscode.Diagnostic(
        range,
        `VibeTrace Pre-Commit Guard: Staged changes affect '${report.symbol_name}' with ${report.total_callers} callers${crossMsg}. Advisory: ${report.safety_advisory}`,
        severity
      );
      diag.source = "VibeTrace";
      diag.code = "blast-radius-warning";

      const existing = diagnosticsByUri.get(uri.toString()) || [];
      existing.push(diag);
      diagnosticsByUri.set(uri.toString(), existing);
    }

    for (const [uriStr, diags] of diagnosticsByUri.entries()) {
      this.collection.set(vscode.Uri.parse(uriStr), diags);
    }
  }

  public async checkDocument(document: vscode.TextDocument): Promise<void> {
    const config = vscode.workspace.getConfiguration("vibetrace");
    if (!config.get<boolean>("enableDiagnostics", true)) {
      this.collection.delete(document.uri);
      return;
    }

    if (document.uri.scheme !== "file") {
      return;
    }

    const text = document.getText();
    const symbols = this.extractTopSymbols(text, document.languageId);
    if (symbols.length === 0) {
      this.collection.delete(document.uri);
      return;
    }

    const diagnostics: vscode.Diagnostic[] = [];
    const filePath = document.uri.fsPath;

    for (const sym of symbols) {
      const report: CallerGuardReport | null = await this.apiClient.getCallerGuard(
        sym.name,
        filePath
      );
      if (!report?.found || report.risk_level !== "HIGH") {
        continue;
      }

      const range = new vscode.Range(sym.line, 0, sym.line, sym.length);
      const crossMsg =
        report.cross_repo_callers && report.cross_repo_callers.length > 0
          ? ` (including ${report.cross_repo_callers.length} cross-repo callers)`
          : "";

      const diag = new vscode.Diagnostic(
        range,
        `VibeTrace High Blast Radius: '${sym.name}' has ${report.total_callers} callers${crossMsg}. Modifying its signature may break callers. ${report.safety_advisory}`,
        vscode.DiagnosticSeverity.Warning
      );
      diag.source = "VibeTrace";
      diag.code = "high-blast-radius";
      diagnostics.push(diag);
    }

    this.collection.set(document.uri, diagnostics);
  }

  public clear(): void {
    this.collection.clear();
  }

  public dispose(): void {
    this.collection.clear();
    for (const d of this.disposables) {
      d.dispose();
    }
  }

  private resolveFileUri(filePath: string): vscode.Uri | null {
    if (vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders.length > 0) {
      for (const folder of vscode.workspace.workspaceFolders) {
        const full = vscode.Uri.joinPath(folder.uri, filePath);
        return full;
      }
    }
    return null;
  }

  private extractTopSymbols(
    text: string,
    languageId: string
  ): Array<{ name: string; line: number; length: number }> {
    const results: Array<{ name: string; line: number; length: number }> = [];
    const lines = text.split(/\r?\n/);
    let pat: RegExp;

    if (languageId === "python") {
      pat = /^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)/;
    } else if (languageId.includes("javascript") || languageId.includes("typescript")) {
      pat = /^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)/;
    } else {
      pat = /^\s*(?:pub\s+)?(?:async\s+)?(?:func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)/;
    }

    for (let i = 0; i < Math.min(lines.length, 300); i++) {
      const line = lines[i];
      const m = pat.exec(line);
      if (m?.[1]) {
        results.push({ name: m[1], line: i, length: line.length });
      }
    }

    return results;
  }
}
