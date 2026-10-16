export interface RawGraphNode {
  id: string;
  name: string;
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
}

export interface GraphData {
  nodes: RawGraphNode[];
  edges: RawGraphEdge[];
}

export interface VibeNodeData {
  label: string;
  kind: string;
  signature?: string | null;
  docstring?: string | null;
  file_path: string;
  visibility?: string | null;
  decorators?: string | null;
  isHighlighted?: boolean;
  isDimmed?: boolean;
  isImpact?: boolean;
}

export interface SelectedElement {
  id: string;
  type: "node" | "edge";
}
