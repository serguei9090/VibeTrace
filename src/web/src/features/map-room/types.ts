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
