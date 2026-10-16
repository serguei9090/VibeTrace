export type LayoutMode = "map" | "trace" | "impact";

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
  layoutMode?: LayoutMode;
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

export interface TracePathResponse {
  from_symbol: string;
  to_symbol: string;
  found: boolean;
  hops: TraceHopData[];
  paths?: TraceHopData[][];
  node_ids: string[];
}

export interface AgentFocusEvent {
  type: "agent_focus";
  event_type: string;
  node_ids: string[];
  summary: string;
  from_symbol?: string | null;
  to_symbol?: string | null;
}
