import { type Edge, MarkerType, type Node } from "@xyflow/react";
import type {
  LayoutMode,
  RawGraphEdge,
  RawGraphNode,
  SelectedElement,
  TracePathResponse,
  VibeNodeData,
} from "../types";

export interface SelectionHighlightResult {
  highlightedNodes: Set<string>;
  highlightedEdges: Set<string>;
  impactDepthMap: Map<string, number>;
  traceStepMap: Map<string, number>;
}

export const computeSelectionHighlights = (
  selectedElement: SelectedElement | null,
  filteredEdges: RawGraphEdge[],
  impactMode: boolean,
  activeTrace?: TracePathResponse | null,
  agentFocusNodeIds?: string[]
): SelectionHighlightResult => {
  const highlightedNodes = new Set<string>();
  const highlightedEdges = new Set<string>();
  const impactDepthMap = new Map<string, number>();
  const traceStepMap = new Map<string, number>();

  if (activeTrace?.found && activeTrace.node_ids.length > 0) {
    activeTrace.node_ids.forEach((nid, idx) => {
      highlightedNodes.add(nid);
      traceStepMap.set(nid, idx + 1);
    });
    activeTrace.hops.forEach((hop) => {
      highlightedNodes.add(hop.from_node.id);
      highlightedNodes.add(hop.to_node.id);
      traceStepMap.set(hop.from_node.id, hop.step_index);
      traceStepMap.set(hop.to_node.id, hop.step_index + 1);
      highlightedEdges.add(`${hop.from_node.id}-${hop.to_node.id}-${hop.relation_type}`);
    });
    return { highlightedNodes, highlightedEdges, impactDepthMap, traceStepMap };
  }

  if (agentFocusNodeIds && agentFocusNodeIds.length > 0 && !selectedElement) {
    agentFocusNodeIds.forEach((nid, idx) => {
      highlightedNodes.add(nid);
      traceStepMap.set(nid, idx + 1);
    });
    filteredEdges.forEach((e) => {
      if (highlightedNodes.has(e.from_node_id) && highlightedNodes.has(e.to_node_id)) {
        highlightedEdges.add(`${e.from_node_id}-${e.to_node_id}-${e.relation_type}`);
      }
    });
    return { highlightedNodes, highlightedEdges, impactDepthMap, traceStepMap };
  }

  if (!selectedElement) {
    return { highlightedNodes, highlightedEdges, impactDepthMap, traceStepMap };
  }

  if (selectedElement.type === "node") {
    highlightedNodes.add(selectedElement.id);
    impactDepthMap.set(selectedElement.id, 0);
    traceStepMap.set(selectedElement.id, 1);

    if (impactMode) {
      let currentLevel = new Set([selectedElement.id]);

      for (let depth = 1; depth <= 3; depth++) {
        const nextLevel = new Set<string>();
        filteredEdges.forEach((e) => {
          if (currentLevel.has(e.to_node_id)) {
            if (!highlightedNodes.has(e.from_node_id)) {
              highlightedNodes.add(e.from_node_id);
              impactDepthMap.set(e.from_node_id, depth);
              nextLevel.add(e.from_node_id);
            }
            highlightedEdges.add(`${e.from_node_id}-${e.to_node_id}-${e.relation_type}`);
          }
        });
        currentLevel = nextLevel;
        if (currentLevel.size === 0) break;
      }
    } else {
      let frontier = new Set([selectedElement.id]);
      for (let step = 2; step <= 5; step++) {
        const nextFrontier = new Set<string>();
        filteredEdges.forEach((e) => {
          if (e.relation_type === "defines") return;
          if (frontier.has(e.from_node_id)) {
            highlightedEdges.add(`${e.from_node_id}-${e.to_node_id}-${e.relation_type}`);
            if (!highlightedNodes.has(e.to_node_id)) {
              highlightedNodes.add(e.to_node_id);
              traceStepMap.set(e.to_node_id, step);
              nextFrontier.add(e.to_node_id);
            }
          }
        });
        frontier = nextFrontier;
        if (frontier.size === 0) break;
      }

      filteredEdges.forEach((e) => {
        if (e.to_node_id === selectedElement.id) {
          highlightedNodes.add(e.from_node_id);
          highlightedEdges.add(`${e.from_node_id}-${e.to_node_id}-${e.relation_type}`);
        }
      });
    }
  } else if (selectedElement.type === "edge") {
    const [from, to] = selectedElement.id.split("-");
    highlightedEdges.add(selectedElement.id);
    highlightedNodes.add(from);
    highlightedNodes.add(to);
    traceStepMap.set(from, 1);
    traceStepMap.set(to, 2);
  }

  return { highlightedNodes, highlightedEdges, impactDepthMap, traceStepMap };
};

export const buildFlowElements = (
  filteredNodes: RawGraphNode[],
  filteredEdges: RawGraphEdge[],
  searchTerm: string,
  hasActiveHighlight: boolean,
  selectedElement: SelectedElement | null,
  impactMode: boolean,
  layoutMode: LayoutMode,
  highlights: SelectionHighlightResult,
  activeEdgeTypes?: Set<string>
): { flowNodes: Node<VibeNodeData>[]; flowEdges: Edge[] } => {
  const { highlightedNodes, highlightedEdges } = highlights;
  const searchActive = searchTerm.length > 0;

  const flowNodes: Node<VibeNodeData>[] = filteredNodes.map((n) => {
    const qname = n.qualified_name || n.name;
    const isSearchMatch =
      searchActive &&
      (n.name.toLowerCase().includes(searchTerm) ||
        qname.toLowerCase().includes(searchTerm) ||
        n.file_path.toLowerCase().includes(searchTerm));

    let isHighlighted = false;
    let isDimmed = false;

    if (hasActiveHighlight) {
      isHighlighted = highlightedNodes.has(n.id);
      isDimmed = !isHighlighted;
    } else if (searchActive) {
      isHighlighted = isSearchMatch;
      isDimmed = !isHighlighted;
    }

    return {
      id: n.id,
      type: "vibeNode",
      position: { x: 0, y: 0 },
      data: {
        label: n.name,
        qualified_name: n.qualified_name,
        kind: n.kind,
        signature: n.signature,
        docstring: n.docstring,
        file_path: n.file_path,
        start_line: n.start_line,
        end_line: n.end_line,
        visibility: n.visibility,
        decorators: n.decorators,
        isHighlighted,
        isDimmed,
        isImpact:
          (impactMode || layoutMode === "impact") &&
          highlightedNodes.has(n.id) &&
          n.id !== selectedElement?.id,
        layoutMode,
      },
    };
  });

  const uniqueEdgeIds = new Set<string>();
  const flowEdges: Edge[] = [];

  filteredEdges.forEach((e) => {
    const edgeId = `${e.from_node_id}-${e.to_node_id}-${e.relation_type}`;
    if (!uniqueEdgeIds.has(edgeId)) {
      uniqueEdgeIds.add(edgeId);

      let isHighlighted = false;
      let isDimmed = false;

      if (hasActiveHighlight) {
        isHighlighted = highlightedEdges.has(edgeId);
        isDimmed = !isHighlighted;
      } else if (searchActive) {
        isDimmed = true;
      }

      // Check relation filter when not actively highlighted or in trace
      if (
        activeEdgeTypes &&
        !activeEdgeTypes.has(e.relation_type) &&
        !isHighlighted &&
        layoutMode !== "trace"
      ) {
        return;
      }

      // Hide labels on unselected static edges to eliminate visual clutter
      const showLabel = isHighlighted || layoutMode === "trace";
      const lineSuffix = e.call_line ? ` :L${e.call_line}` : "";
      const label = showLabel ? `${e.relation_type}${lineSuffix}` : "";

      // Differentiate colors and line patterns by relation type
      let stroke = "rgba(148, 163, 184, 0.25)";
      let strokeWidth = 1.2;
      let strokeDasharray: string | undefined;
      let markerColor = "rgba(148, 163, 184, 0.4)";

      if (isHighlighted) {
        stroke = layoutMode === "trace" ? "#10b981" : "#818cf8";
        strokeWidth = 3;
        markerColor = stroke;
      } else {
        switch (e.relation_type) {
          case "calls":
            stroke = "rgba(56, 189, 248, 0.65)"; // bright cyan
            strokeWidth = 1.5;
            markerColor = "rgba(56, 189, 248, 0.75)";
            break;
          case "imports":
            stroke = "rgba(167, 139, 250, 0.55)"; // purple
            strokeWidth = 1.3;
            strokeDasharray = "5 5";
            markerColor = "rgba(167, 139, 250, 0.65)";
            break;
          case "inherits":
          case "implements":
          case "extends":
            stroke = "rgba(52, 211, 153, 0.65)"; // emerald
            strokeWidth = 1.5;
            markerColor = "rgba(52, 211, 153, 0.75)";
            break;
          default:
            stroke = "rgba(148, 163, 184, 0.2)"; // subtle muted slate
            strokeWidth = 1;
            strokeDasharray = "2 3";
            markerColor = "rgba(148, 163, 184, 0.3)";
            break;
        }
      }

      flowEdges.push({
        id: edgeId,
        source: e.from_node_id,
        target: e.to_node_id,
        label,
        type: "smoothstep",
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: markerColor,
        },
        animated: isHighlighted || layoutMode === "trace",
        labelStyle: showLabel
          ? {
              fill: "#f8fafc",
              fontSize: 10,
              fontWeight: 600,
              fontFamily: "JetBrains Mono, monospace",
            }
          : undefined,
        labelBgStyle: showLabel
          ? {
              fill: "rgba(15, 17, 28, 0.95)",
              stroke: stroke,
              strokeWidth: 1,
              rx: 4,
              ry: 4,
            }
          : undefined,
        labelBgPadding: [6, 4] as [number, number],
        style: {
          stroke,
          strokeWidth,
          strokeDasharray,
          opacity: isDimmed ? 0.04 : 0.8,
          transition: "opacity 0.25s ease, stroke 0.25s ease",
        },
      });
    }
  });

  return { flowNodes, flowEdges };
};
