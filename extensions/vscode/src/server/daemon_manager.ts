import * as cp from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import type { VibeTraceApiClient } from "../client/api_client.js";

export class VibeTraceDaemonManager implements vscode.Disposable {
  private apiClient: VibeTraceApiClient;
  private outputChannel: vscode.OutputChannel;
  private daemonProcess: cp.ChildProcess | null = null;
  private isShuttingDown = false;

  constructor(apiClient: VibeTraceApiClient) {
    this.apiClient = apiClient;
    this.outputChannel = vscode.window.createOutputChannel("VibeTrace Daemon");
  }

  public async ensureDaemonRunning(port = 8000): Promise<boolean> {
    if (await this.apiClient.isAvailable()) {
      this.outputChannel.appendLine(
        `[Info] Connected to existing VibeTrace daemon on port ${port}.`
      );
      return true;
    }

    this.outputChannel.appendLine(
      "[Info] No active VibeTrace daemon detected. Spawning background service..."
    );
    const workspaceRoot = this.getWorkspaceRoot();

    const spawnConfig = this.resolveExecutable(workspaceRoot, port);
    if (!spawnConfig) {
      this.outputChannel.appendLine(
        "[Warning] Could not locate 'vibetrace' or 'uv' executable. Please run 'uv run vibetrace serve' manually."
      );
      return false;
    }

    this.outputChannel.appendLine(
      `[Info] Launching: ${spawnConfig.command} ${spawnConfig.args.join(" ")}`
    );

    try {
      this.daemonProcess = cp.spawn(spawnConfig.command, spawnConfig.args, {
        cwd: workspaceRoot || process.cwd(),
        env: { ...process.env, PYTHONUNBUFFERED: "1" },
        stdio: ["ignore", "pipe", "pipe"],
        shell: process.platform === "win32",
      });

      this.daemonProcess.stdout?.on("data", (chunk: Buffer) => {
        this.outputChannel.append(chunk.toString());
      });

      this.daemonProcess.stderr?.on("data", (chunk: Buffer) => {
        this.outputChannel.append(chunk.toString());
      });

      this.daemonProcess.on("exit", (code, signal) => {
        if (!this.isShuttingDown) {
          this.outputChannel.appendLine(
            `[Warning] Daemon process exited unexpectedly (code: ${code}, signal: ${signal}).`
          );
        }
        this.daemonProcess = null;
      });

      // Poll until ready
      const ready = await this.waitForReady(12000);
      if (ready) {
        this.outputChannel.appendLine(`[Success] VibeTrace daemon active on port ${port}.`);
        this.checkAutoIndex();
        return true;
      }
      this.outputChannel.appendLine("[Error] Timed out waiting for VibeTrace daemon to start.");
      return false;
    } catch (err) {
      this.outputChannel.appendLine(`[Error] Failed to spawn VibeTrace daemon: ${err}`);
      return false;
    }
  }

  private resolveExecutable(
    workspaceRoot: string | undefined,
    port: number
  ): { command: string; args: string[] } | null {
    if (workspaceRoot) {
      // 1. Check workspace .venv (Windows Scripts, Unix bin)
      const venvWin = path.join(workspaceRoot, ".venv", "Scripts", "vibetrace.exe");
      const venvUnix = path.join(workspaceRoot, ".venv", "bin", "vibetrace");
      if (fs.existsSync(venvWin)) {
        return { command: venvWin, args: ["serve", "--port", String(port)] };
      }
      if (fs.existsSync(venvUnix)) {
        return { command: venvUnix, args: ["serve", "--port", String(port)] };
      }
    }

    // 2. Check if uv is available in PATH
    try {
      cp.execSync(process.platform === "win32" ? "where.exe uv" : "which uv", { stdio: "ignore" });
      return { command: "uv", args: ["run", "vibetrace", "serve", "--port", String(port)] };
    } catch {
      // Fall through
    }

    // 3. Check if vibetrace is in PATH directly
    try {
      cp.execSync(process.platform === "win32" ? "where.exe vibetrace" : "which vibetrace", {
        stdio: "ignore",
      });
      return { command: "vibetrace", args: ["serve", "--port", String(port)] };
    } catch {
      // Fall through
    }

    return null;
  }

  private async waitForReady(timeoutMs: number): Promise<boolean> {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (await this.apiClient.isAvailable()) {
        return true;
      }
      await new Promise((r) => setTimeout(r, 600));
    }
    return false;
  }

  private async checkAutoIndex(): Promise<void> {
    const root = this.getWorkspaceRoot();
    if (!root) return;

    const dbPath = path.join(root, ".vibetrace", "vibetrace.db");
    if (!fs.existsSync(dbPath)) {
      this.outputChannel.appendLine(
        "[Info] Workspace not yet indexed. Triggering initial background index..."
      );
      vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Window,
          title: "VibeTrace: Indexing workspace code graph...",
        },
        async () => {
          const res = await this.apiClient.reindex(undefined, true);
          if (res) {
            this.outputChannel.appendLine(
              `[Success] Initial indexing complete. (${res.nodes_indexed ?? 0} nodes indexed)`
            );
          }
        }
      );
    }
  }

  private getWorkspaceRoot(): string | undefined {
    if (vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders.length > 0) {
      return vscode.workspace.workspaceFolders[0].uri.fsPath;
    }
    return undefined;
  }

  public dispose(): void {
    this.isShuttingDown = true;
    if (this.daemonProcess) {
      this.outputChannel.appendLine("[Info] Terminating VibeTrace background daemon...");
      try {
        if (process.platform === "win32" && this.daemonProcess.pid) {
          cp.execSync(`taskkill /F /T /PID ${this.daemonProcess.pid}`, { stdio: "ignore" });
        } else {
          this.daemonProcess.kill("SIGTERM");
        }
      } catch {
        // Safe kill
      }
      this.daemonProcess = null;
    }
    this.outputChannel.dispose();
  }
}
