import type { RawGraphEdge, SelectedElement } from "../types";

export const computeSelectionHighlights = (
  selectedElement: SelectedElement | null,
  filteredEdges: RawGraphEdge[],
  impactMode: boolean
): { highlightedNodes: Set<string>; highlightedEdges: Set<string> } => {
  const highlightedNodes = new Set<string>();
  const highlightedEdges = new Set<string>();

  if (!selectedElement) {
    return { highlightedNodes, highlightedEdges };
  }

  if (selectedElement.type === "node") {
    highlightedNodes.add(selectedElement.id);

    if (impactMode) {
      let currentLevel = new Set([selectedElement.id]);

      for (let depth = 1; depth <= 3; depth++) {
        const nextLevel = new Set<string>();
        filteredEdges.forEach((e) => {
          if (currentLevel.has(e.to_node_id)) {
            if (!highlightedNodes.has(e.from_node_id)) {
              highlightedNodes.add(e.from_node_id);
              nextLevel.add(e.from_node_id);
            }
            highlightedEdges.add(`${e.from_node_id}-${e.to_node_id}-${e.relation_type}`);
          }
        });
        currentLevel = nextLevel;
        if (currentLevel.size === 0) break;
      }
    } else {
      filteredEdges.forEach((e) => {
        if (e.from_node_id === selectedElement.id) {
          highlightedNodes.add(e.to_node_id);
          highlightedEdges.add(`${e.from_node_id}-${e.to_node_id}-${e.relation_type}`);
        }
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
  }

  return { highlightedNodes, highlightedEdges };
};
