import type { RawGraphEdge, SelectedElement } from "../types";

export interface SelectionHighlightResult {
  highlightedNodes: Set<string>;
  highlightedEdges: Set<string>;
  impactDepthMap: Map<string, number>;
  traceStepMap: Map<string, number>;
}

export const computeSelectionHighlights = (
  selectedElement: SelectedElement | null,
  filteredEdges: RawGraphEdge[],
  impactMode: boolean
): SelectionHighlightResult => {
  const highlightedNodes = new Set<string>();
  const highlightedEdges = new Set<string>();
  const impactDepthMap = new Map<string, number>();
  const traceStepMap = new Map<string, number>();

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
      // Forward downstream trace steps (#1 -> #2 -> #3) + immediate callers
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

      // Direct incoming callers
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
