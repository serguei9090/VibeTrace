import { type Edge, type Node, Position } from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";
import type { LayoutMode, VibeNodeData } from "../types";

const elk = new ELK();

export const NODE_WIDTH = 250;
export const NODE_HEIGHT = 150;

export interface LayoutConfig {
  mode?: LayoutMode;
  traceStepMap?: Map<string, number>;
  impactDepthMap?: Map<string, number>;
}

const layoutArchitectureMap = async (
  contentNodes: Node<VibeNodeData>[],
  edges: Edge[]
): Promise<{ nodes: Node[]; edges: Edge[] }> => {
  const tree: Record<string, Record<string, Node<VibeNodeData>[]>> = {};

  for (const node of contentNodes) {
    const filePath = String(node.data?.file_path || "unknown");
    const parts = filePath.split(/[\\/]/);
    const districtName = parts.length > 1 ? parts.slice(0, -1).join("/") : "root";

    if (!tree[districtName]) tree[districtName] = {};
    if (!tree[districtName][filePath]) tree[districtName][filePath] = [];
    tree[districtName][filePath].push(node);
  }

  const elkDistricts = [];

  for (const [districtPath, files] of Object.entries(tree)) {
    const elkFiles = [];

    for (const [filePath, fileNodes] of Object.entries(files)) {
      const elkNodes = fileNodes.map((n) => ({
        id: n.id,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
      }));

      elkFiles.push({
        id: `group-${filePath}`,
        children: elkNodes,
        layoutOptions: {
          "elk.algorithm": "rectpacking",
          "org.eclipse.elk.padding": "[top=80,left=20,bottom=20,right=20]",
          "elk.padding": "[top=80,left=20,bottom=20,right=20]",
          "elk.spacing.nodeNode": "40",
          "elk.aspectRatio": "1.5",
        },
        labels: [{ text: filePath }],
      });
    }

    elkDistricts.push({
      id: `district-${districtPath}`,
      children: elkFiles,
      layoutOptions: {
        "elk.algorithm": "rectpacking",
        "elk.padding": "[top=80,left=40,bottom=40,right=40]",
        "elk.spacing.nodeNode": "80",
        "elk.aspectRatio": "1.2",
      },
      labels: [{ text: districtPath }],
    });
  }

  const rootGraph = {
    id: "root",
    children: elkDistricts,
    edges: edges.map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
    layoutOptions: {
      "elk.algorithm": "rectpacking",
      "elk.padding": "[top=50,left=50,bottom=50,right=50]",
      "elk.spacing.nodeNode": "150",
      "elk.aspectRatio": "1.4",
    },
  };

  const layoutedGraph = await elk.layout(rootGraph);
  const finalNodes: Node[] = [];
  const nodeMap = new Map(contentNodes.map((n) => [n.id, n]));

  layoutedGraph.children?.forEach((district: any) => {
    finalNodes.push({
      id: district.id,
      type: "districtGroup",
      data: { label: district.labels?.[0]?.text || "District" },
      position: { x: district.x ?? 0, y: district.y ?? 0 },
      style: {
        width: district.width,
        height: district.height,
        backgroundColor: "rgba(15, 17, 28, 0.45)",
        border: "1.5px solid rgba(99, 102, 241, 0.3)",
        borderRadius: "16px",
        paddingTop: "80px",
        boxShadow: "0 8px 32px rgba(0, 0, 0, 0.3)",
      },
    });

    district.children?.forEach((file: any) => {
      finalNodes.push({
        id: file.id,
        type: "fileGroup",
        data: {
          label: file.labels?.[0]?.text || "File",
          stats: `${file.children?.length ?? 0} symbols`,
        },
        position: { x: file.x ?? 0, y: file.y ?? 0 },
        parentId: district.id,
        extent: "parent",
        style: {
          width: file.width,
          height: file.height,
          backgroundColor: "rgba(20, 23, 36, 0.65)",
          border: "1px solid rgba(255, 255, 255, 0.08)",
          borderRadius: "10px",
          paddingTop: "5px",
          boxShadow: "0 4px 16px rgba(0, 0, 0, 0.2)",
        },
      });

      file.children?.forEach((symbol: any) => {
        const original = nodeMap.get(symbol.id);
        if (original) {
          finalNodes.push({
            ...original,
            position: { x: symbol.x ?? 0, y: symbol.y ?? 0 },
            parentId: file.id,
            extent: "parent",
            width: symbol.width,
            height: symbol.height,
            sourcePosition: Position.Bottom,
            targetPosition: Position.Top,
          });
        }
      });
    });
  });

  return { nodes: finalNodes, edges };
};

const layoutDirectionalGraph = async (
  contentNodes: Node<VibeNodeData>[],
  edges: Edge[],
  mode: "trace" | "impact",
  config?: LayoutConfig
): Promise<{ nodes: Node[]; edges: Edge[] }> => {
  // Filter to highlighted subgraph when a selection or trace is active
  const highlighted = contentNodes.filter((n) => n.data?.isHighlighted);
  const activeNodes = highlighted.length > 0 ? highlighted : contentNodes;
  const activeIds = new Set(activeNodes.map((n) => n.id));
  const activeEdges = edges.filter(
    (e) => activeIds.has(e.source) && activeIds.has(e.target) && e.label !== "defines"
  );

  const isTrace = mode === "trace";
  const direction = isTrace ? "RIGHT" : "UP";

  const rootGraph = {
    id: "root",
    children: activeNodes.map((n) => ({
      id: n.id,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    })),
    edges: activeEdges.map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": direction,
      "elk.layered.spacing.nodeNodeBetweenLayers": isTrace ? "140" : "120",
      "elk.spacing.nodeNode": "60",
      "elk.padding": "[top=60,left=60,bottom=60,right=60]",
    },
  };

  const layouted = await elk.layout(rootGraph);
  const posMap = new Map<string, { x: number; y: number }>();
  layouted.children?.forEach((c: any) => {
    posMap.set(c.id, { x: c.x ?? 0, y: c.y ?? 0 });
  });

  // Sort by X in trace mode to assign fallback sequential step badges (#1, #2, ...)
  const sortedByX = [...activeNodes].sort(
    (a, b) => (posMap.get(a.id)?.x ?? 0) - (posMap.get(b.id)?.x ?? 0)
  );
  const fallbackOrder = new Map<string, number>();
  sortedByX.forEach((n, idx) => {
    fallbackOrder.set(n.id, idx + 1);
  });

  const finalNodes: Node[] = activeNodes.map((n) => {
    const pos = posMap.get(n.id) || { x: 0, y: 0 };
    const stepNum = config?.traceStepMap?.get(n.id) ?? fallbackOrder.get(n.id);
    const impactDepth = config?.impactDepthMap?.get(n.id);

    return {
      ...n,
      position: pos,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      sourcePosition: isTrace ? Position.Right : Position.Top,
      targetPosition: isTrace ? Position.Left : Position.Bottom,
      data: {
        ...n.data,
        layoutMode: mode,
        stepBadge: isTrace && stepNum ? `#${stepNum}` : undefined,
        impactBadge:
          !isTrace && impactDepth !== undefined
            ? impactDepth === 0
              ? "TARGET"
              : `L${impactDepth}`
            : undefined,
      },
    };
  });

  return { nodes: finalNodes, edges: activeEdges };
};

export const getLayoutedElements = async (
  nodes: Node<VibeNodeData>[],
  edges: Edge[],
  config?: LayoutConfig
): Promise<{ nodes: Node[]; edges: Edge[] }> => {
  const contentNodes = nodes.filter((n) => n.type === "vibeNode");
  if (contentNodes.length === 0) return { nodes: [], edges };

  const mode = config?.mode || "map";

  try {
    if (mode === "trace" || mode === "impact") {
      return await layoutDirectionalGraph(contentNodes, edges, mode, config);
    }
    return await layoutArchitectureMap(contentNodes, edges);
  } catch (err) {
    console.error("ELK Layout Failed", err);
    return { nodes: [], edges: [] };
  }
};

export const getGraphBounds = (nodes: Node[]): [[number, number], [number, number]] | undefined => {
  if (nodes.length === 0) return undefined;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  nodes.forEach((n) => {
    minX = Math.min(minX, n.position.x);
    minY = Math.min(minY, n.position.y);
    const w = n.style?.width ? (n.style.width as number) : NODE_WIDTH;
    const h = n.style?.height ? (n.style.height as number) : NODE_HEIGHT;
    maxX = Math.max(maxX, n.position.x + w);
    maxY = Math.max(maxY, n.position.y + h);
  });

  const padding = 500;
  return [
    [minX - padding, minY - padding],
    [maxX + padding, maxY + padding],
  ];
};
