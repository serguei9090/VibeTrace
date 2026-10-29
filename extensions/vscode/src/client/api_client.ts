export interface CallerInfo {
  caller_symbol: string;
  file_path: string;
  line: number;
  caller_type: string;
  caller_node_id?: string;
  is_cross_repo?: boolean;
  repo_name?: string;
}

export interface CallerGuardReport {
  found: boolean;
  symbol_name: string;
  qualified_name: string;
  file_path: string;
  total_callers: number;
  risk_level: "LOW" | "MEDIUM" | "HIGH";
  safety_advisory: string;
  callers: CallerInfo[];
  cross_repo_callers?: CallerInfo[];
}

export interface CalleeContract {
  name: string;
  qualified_name: string;
  file_path: string;
  call_line: number;
  signature?: string;
  docstring?: string;
}

export interface CodeSliceResult {
  found: boolean;
  name: string;
  qualified_name: string;
  file_path: string;
  start_line: number;
  end_line: number;
  source_code: string;
  callees: CalleeContract[];
}

export interface VibeBundleResult {
  target_symbol?: string;
  intent: string;
  formatted_bundle: string;
  estimated_tokens: number;
  doc_chunks?: Array<{ title: string; file_path: string; content: string }>;
  code_slice?: CodeSliceResult;
  caller_guard?: CallerGuardReport;
  test_files?: string[];
}

export interface RepoMemberStatus {
  name: string;
  path: string;
  database_path: string;
  exists: boolean;
  node_count: number;
}

export interface ProjectStatus {
  project_name: string;
  storage: string;
  manifest_path: string;
  member_count: number;
  members: RepoMemberStatus[];
  cross_repo_link_count: number;
}

export interface CrossRepoLink {
  id: string;
  client_repo: string;
  client_file: string;
  client_line: number;
  http_method: string;
  route_path: string;
  server_repo: string;
  server_file: string;
  server_line: number;
  server_handler: string;
}

export interface ReindexResponse {
  status: string;
  nodes_indexed?: number;
  files_indexed?: number;
}

export class VibeTraceApiClient {
  private baseUrl: string;

  constructor(baseUrl = "http://127.0.0.1:8000") {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  public updateBaseUrl(baseUrl: string): void {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  public getBaseUrl(): string {
    return this.baseUrl;
  }

  public async isAvailable(): Promise<boolean> {
    try {
      const resp = await fetch(`${this.baseUrl}/api/graph`, {
        signal: AbortSignal.timeout(2000),
      });
      return resp.ok;
    } catch {
      return false;
    }
  }

  public async getCallerGuard(
    symbol: string,
    filePath?: string
  ): Promise<CallerGuardReport | null> {
    try {
      const params = new URLSearchParams({ symbol });
      if (filePath) {
        params.set("file_path", filePath);
      }
      const resp = await fetch(`${this.baseUrl}/api/vibe/guard?${params.toString()}`, {
        signal: AbortSignal.timeout(4000),
      });
      if (!resp.ok) {
        return null;
      }
      return (await resp.json()) as CallerGuardReport;
    } catch {
      return null;
    }
  }

  public async getStagedCallerGuard(strict = false): Promise<{
    staged: boolean;
    strict: boolean;
    violation: boolean;
    reports: CallerGuardReport[];
  } | null> {
    try {
      const params = new URLSearchParams({
        staged: "true",
        strict: strict ? "true" : "false",
      });
      const resp = await fetch(`${this.baseUrl}/api/vibe/guard?${params.toString()}`, {
        signal: AbortSignal.timeout(6000),
      });
      if (!resp.ok) {
        return null;
      }
      const data = await resp.json();
      if (Array.isArray(data)) {
        return {
          staged: true,
          strict,
          violation: data.some((r: CallerGuardReport) => r.total_callers > 0),
          reports: data,
        };
      }
      return data;
    } catch {
      return null;
    }
  }

  public async getCodeSlice(
    symbol: string,
    filePath?: string,
    noCallees = false
  ): Promise<CodeSliceResult | null> {
    try {
      const params = new URLSearchParams({ symbol });
      if (filePath) {
        params.set("file_path", filePath);
      }
      if (noCallees) {
        params.set("no_callees", "true");
      }
      const resp = await fetch(`${this.baseUrl}/api/slice?${params.toString()}`, {
        signal: AbortSignal.timeout(5000),
      });
      if (!resp.ok) {
        return null;
      }
      return (await resp.json()) as CodeSliceResult;
    } catch {
      return null;
    }
  }

  public async getTaskBundle(
    intent: string,
    symbol?: string,
    budget = 1500
  ): Promise<VibeBundleResult | null> {
    try {
      const params = new URLSearchParams({
        intent,
        budget: String(budget),
      });
      if (symbol) {
        params.set("symbol", symbol);
      }
      const resp = await fetch(`${this.baseUrl}/api/vibe/bundle?${params.toString()}`, {
        signal: AbortSignal.timeout(8000),
      });
      if (!resp.ok) {
        return null;
      }
      return (await resp.json()) as VibeBundleResult;
    } catch {
      return null;
    }
  }

  public async getProjectStatus(): Promise<ProjectStatus | null> {
    try {
      const resp = await fetch(`${this.baseUrl}/api/project`, {
        signal: AbortSignal.timeout(4000),
      });
      if (!resp.ok) {
        return null;
      }
      return (await resp.json()) as ProjectStatus;
    } catch {
      return null;
    }
  }

  public async getProjectLinks(): Promise<CrossRepoLink[]> {
    try {
      const resp = await fetch(`${this.baseUrl}/api/project/links`, {
        signal: AbortSignal.timeout(5000),
      });
      if (!resp.ok) {
        return [];
      }
      const data = await resp.json();
      if (Array.isArray(data)) {
        return data as CrossRepoLink[];
      }
      return (data.links || []) as CrossRepoLink[];
    } catch {
      return [];
    }
  }

  public async reindex(filePath?: string, force = false): Promise<ReindexResponse | null> {
    try {
      const resp = await fetch(`${this.baseUrl}/api/reindex`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path: filePath, force, docs: true }),
        signal: AbortSignal.timeout(30000),
      });
      if (!resp.ok) {
        return null;
      }
      return (await resp.json()) as ReindexResponse;
    } catch {
      return null;
    }
  }
}
