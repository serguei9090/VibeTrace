export type LayoutMode = "map" | "trace" | "impact";
export type ViewMode = "explorer" | "full";

export interface RawGraphNode {
  id: string;
  name: string;
  qualified_name?: string | null;
  parent_id?: string | null;
  kind: string;
  file_path: string;
  start_line: number;
  end_line: number;
  signature?: string | null;
  docstring?: string | null;
  decorators?: string | null;
  visibility?: string | null;
}

export interface RawGraphEdge {
  id?: number;
  from_node_id: string;
  to_node_id: string;
  relation_type: string;
  call_line?: number | null;
}

export interface GraphData {
  nodes: RawGraphNode[];
  edges: RawGraphEdge[];
}

export interface VibeNodeData extends Record<string, unknown> {
  id?: string;
  label: string;
  qualified_name?: string | null;
  kind: string;
  signature?: string | null;
  docstring?: string | null;
  file_path: string;
  start_line?: number;
  end_line?: number;
  visibility?: string | null;
  decorators?: string | null;
  isHighlighted?: boolean;
  isDimmed?: boolean;
  isImpact?: boolean;
  stepBadge?: string;
  impactBadge?: string;
  splitBadge?: string;
  splitBranches?: string[];
  securityBadge?: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
  securityFindingId?: string;
  layoutMode?: LayoutMode;
  viewMode?: ViewMode;
  unrevealedCallees?: number;
  unrevealedCallers?: number;
  onExpandCallees?: (id: string) => void;
  onExpandCallers?: (id: string) => void;
  onCollapseNode?: (id: string) => void;
}

export interface SelectedElement {
  id: string;
  type: "node" | "edge";
}

export interface TraceHopNode {
  id: string;
  name: string;
  qualified_name: string;
  kind: string;
  file_path: string;
  start_line?: number | null;
  end_line?: number | null;
  signature?: string | null;
}

export interface TraceHopData {
  step_index: number;
  from_node: TraceHopNode;
  to_node: TraceHopNode;
  relation_type: string;
  call_line?: number | null;
}

export interface SplitPointInfo {
  node_id: string;
  symbol_name: string;
  total_outgoing_branches: number;
  branch_names: string[];
}

export interface ReachableSymbolsResponse {
  from_symbol: string;
  count: number;
  reachable_symbols: string[];
}

export interface TracePathResponse {
  from_symbol: string;
  to_symbol: string;
  found: boolean;
  hops: TraceHopData[];
  paths?: TraceHopData[][];
  node_ids: string[];
  split_points?: SplitPointInfo[];
}

export interface AgentFocusEvent {
  type: "agent_focus";
  event_type: string;
  node_ids: string[];
  summary: string;
  from_symbol?: string | null;
  to_symbol?: string | null;
}

export interface SavedTrace {
  id: number;
  name: string;
  description?: string;
  from_symbol: string;
  to_symbol: string;
  max_depth: number;
  created_at?: string | null;
}

export interface NodeNeighborsResponse {
  center_node: RawGraphNode;
  nodes: RawGraphNode[];
  edges: RawGraphEdge[];
  caller_count: number;
  callee_count: number;
}

export interface SecurityFindingUI {
  id: string;
  rule_id: string;
  cwe_id: string;
  category: "ast_pattern" | "taint_reachability" | "package_cve" | "iac_misconfig" | "git_secret";
  severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFO";
  title: string;
  message: string;
  file_path: string;
  start_line: number;
  end_line: number;
  symbol_name?: string | null;
  code_hash: string;
  non_compliant_snippet?: string | null;
  compliant_snippet?: string | null;
  remediation_guidance?: string | null;
  status: "UNAPPROVED" | "APPROVED" | "MODIFIED_CODE" | "RISK";
  tag?: string | null;
  rationale?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  metadata?: { taint_path?: string[]; tainted_variables?: Record<string, string[]> } & Record<
    string,
    unknown
  >;
}

export interface SecurityAuditReportUI {
  total_findings: number;
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  unapproved_count: number;
  modified_count: number;
  approved_count: number;
  findings: SecurityFindingUI[];
}

export interface UntestedSymbolUI {
  symbol: string;
  file_path: string;
  start_line: number;
}

export interface CoverageGateReportUI {
  threshold_pct: number;
  enforced: boolean;
  passed: boolean;
  total_target_symbols: number;
  tested_symbols_count: number;
  ast_symbol_coverage_pct: number;
  line_coverage_pct?: number | null;
  effective_coverage_pct: number;
  coverage_source: string;
  untested_symbols: UntestedSymbolUI[];
}

export interface UnifiedReviewReportUI {
  scope: "changed" | "full";
  passed: boolean;
  changed_files: string[];
  unapproved_security_count: number;
  modified_security_count: number;
  new_splits_count: number;
  modified_splits_count: number;
  modified_symbols_with_callers: number;
  coverage_gate: CoverageGateReportUI;
  security_findings: SecurityFindingUI[];
  actionable_splits: Array<{
    node_id: string;
    symbol: string;
    qualified_name: string;
    file_path: string;
    start_line: number;
    end_line: number;
    code_hash: string;
    branches_count: number;
    status: string;
  }>;
  actions_required: string[];
}
