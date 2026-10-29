import * as vscode from "vscode";
import type { CallerGuardReport, VibeTraceApiClient } from "../client/api_client.js";

interface SymbolLocation {
  name: string;
  line: number;
}

export class VibeTraceCodeLensProvider implements vscode.CodeLensProvider {
  private onDidChangeCodeLensesEmitter = new vscode.EventEmitter<void>();
  public readonly onDidChangeCodeLenses = this.onDidChangeCodeLensesEmitter.event;

  private apiClient: VibeTraceApiClient;
  private cache: Map<string, { report: CallerGuardReport | null; timestamp: number }> = new Map();
  private cacheTtlMs = 15000;

  constructor(apiClient: VibeTraceApiClient) {
    this.apiClient = apiClient;
  }

  public refresh(): void {
    this.cache.clear();
    this.onDidChangeCodeLensesEmitter.fire();
  }

  public async provideCodeLenses(
    document: vscode.TextDocument,
    token: vscode.CancellationToken
  ): Promise<vscode.CodeLens[]> {
    const config = vscode.workspace.getConfiguration("vibetrace");
    if (!config.get<boolean>("enableCodeLens", true)) {
      return [];
    }

    const symbols = this.findDeclarations(document);
    if (symbols.length === 0) {
      return [];
    }

    const lenses: vscode.CodeLens[] = [];
    const filePath = document.uri.fsPath;

    for (const sym of symbols) {
      if (token.isCancellationRequested) {
        break;
      }

      const range = new vscode.Range(sym.line, 0, sym.line, 0);
      const cacheKey = `${filePath}:${sym.name}`;
      const cached = this.cache.get(cacheKey);

      let report: CallerGuardReport | null = null;
      const now = Date.now();

      if (cached && now - cached.timestamp < this.cacheTtlMs) {
        report = cached.report;
      } else {
        report = await this.apiClient.getCallerGuard(sym.name, filePath);
        this.cache.set(cacheKey, { report, timestamp: now });
      }

      const lens = new vscode.CodeLens(range);
      if (report?.found) {
        const total = report.total_callers;
        const cross = report.cross_repo_callers ? report.cross_repo_callers.length : 0;
        let title: string;

        if (total === 0) {
          title = "$(check) 0 Callers (Leaf)";
        } else {
          const crossTag = cross > 0 ? ` [${cross} Cross-Repo]` : "";
          const icon =
            report.risk_level === "HIGH"
              ? "$(error)"
              : report.risk_level === "MEDIUM"
                ? "$(warning)"
                : "$(info)";
          title = `${icon} ▲ ${total} Caller${total > 1 ? "s" : ""}${crossTag} (${report.risk_level} Risk)`;
        }

        lens.command = {
          title,
          tooltip: `Click to inspect all ${total} blast-radius callers for '${sym.name}'\nSafety Advisory: ${report.safety_advisory}`,
          command: "vibetrace.inspectCallers",
          arguments: [sym.name, filePath, report],
        };
      } else {
        lens.command = {
          title: `$(symbol-function) VibeTrace: ${sym.name}`,
          tooltip: `Symbol '${sym.name}' is not currently in the index. Click to reindex.`,
          command: "vibetrace.reindex",
          arguments: [filePath],
        };
      }

      lenses.push(lens);
    }

    return lenses;
  }

  private findDeclarations(document: vscode.TextDocument): SymbolLocation[] {
    const symbols: SymbolLocation[] = [];
    const languageId = document.languageId;
    const text = document.getText();
    const lines = text.split(/\r?\n/);

    const patterns: RegExp[] = [];

    if (languageId === "python") {
      patterns.push(
        /^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(/,
        /^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*\(|:)/
      );
    } else if (
      languageId === "typescript" ||
      languageId === "javascript" ||
      languageId === "typescriptreact" ||
      languageId === "javascriptreact"
    ) {
      patterns.push(
        /^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(/,
        /^\s*(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)/,
        /^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\(/,
        /^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?(?:function|\([^)]*\)\s*=>)/
      );
    } else if (languageId === "go") {
      patterns.push(/^\s*func\s+(?:\([^)]+\)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(/);
    } else if (languageId === "rust") {
      patterns.push(
        /^\s*(?:pub(?:\s*\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*[(<]/,
        /^\s*(?:pub(?:\s*\([^)]*\))?\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)/
      );
    } else {
      patterns.push(
        /^\s*(?:public|private|protected|static|\s)+[A-Za-z0-9_<>[\]]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(/
      );
    }

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (!line || line.trim().startsWith("//") || line.trim().startsWith("#")) {
        continue;
      }
      for (const pat of patterns) {
        const m = pat.exec(line);
        if (m?.[1]) {
          symbols.push({ name: m[1], line: i });
          break;
        }
      }
    }

    return symbols;
  }
}
