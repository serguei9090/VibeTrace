import { type Edge, MarkerType, type Node } from "@xyflow/react";
import type { RawGraphEdge, RawGraphNode } from "../types";

export interface PackageNodeData extends Record<string, unknown> {
  id: string;
  label: string;
  shortName: string;
  fileCount: number;
  symbolCount: number;
  endpointCount: number;
  classCount: number;
  functionCount: number;
  topSymbols: string[];
  onExplorePackage: (dirPath: string) => void;
}

export const normalizeDirPath = (filePath: string): string => {
  const norm = (filePath || "").replace(/\\/g, "/");
  const parts = norm.split("/");
  if (parts.length > 1) {
    return parts.slice(0, -1).join("/");
  }
  return "root";
};

export const buildPackageGraph = (
  rawNodes: RawGraphNode[],
  rawEdges: RawGraphEdge[],
  onExplorePackage: (dirPath: string) => void
): { packageNodes: Node<PackageNodeData>[]; packageEdges: Edge[] } => {
  const dirMap = new Map<
    string,
    {
      files: Set<string>;
      symbols: RawGraphNode[];
      endpoints: number;
      classes: number;
      functions: number;
    }
  >();

  const nodeToDir = new Map<string, string>();

  // Aggregate symbols into directories
  for (const n of rawNodes) {
    const dir = normalizeDirPath(n.file_path);
    nodeToDir.set(n.id, dir);

    let entry = dirMap.get(dir);
    if (!entry) {
      entry = {
        files: new Set<string>(),
        symbols: [],
        endpoints: 0,
        classes: 0,
        functions: 0,
      };
      dirMap.set(dir, entry);
    }

    entry.files.add(n.file_path);
    entry.symbols.push(n);

    if (n.kind === "endpoint") entry.endpoints++;
    else if (n.kind === "class" || n.kind === "struct") entry.classes++;
    else if (n.kind === "function") entry.functions++;
  }

  // Create Package Nodes
  const packageNodes: Node<PackageNodeData>[] = [];
  for (const [dir, data] of dirMap.entries()) {
    const shortName = dir.split("/").pop() || dir;

    // Pick 3 most important preview symbols
    const sortedSymbols = [...data.symbols].sort((a, b) => {
      const kindWeight = (k: string) => (k === "endpoint" ? 3 : k === "class" ? 2 : 1);
      return kindWeight(b.kind) - kindWeight(a.kind);
    });
    const topSymbols = sortedSymbols.slice(0, 3).map((s) => s.name);

    packageNodes.push({
      id: `pkg:${dir}`,
      type: "packageNode",
      position: { x: 0, y: 0 },
      data: {
        id: `pkg:${dir}`,
        label: dir,
        shortName,
        fileCount: data.files.size,
        symbolCount: data.symbols.length,
        endpointCount: data.endpoints,
        classCount: data.classes,
        functionCount: data.functions,
        topSymbols,
        onExplorePackage,
      },
    });
  }

  // Aggregate Cross-Directory Dependencies
  const edgeAggregates = new Map<
    string,
    {
      fromDir: string;
      toDir: string;
      calls: number;
      imports: number;
      routes: number;
      other: number;
    }
  >();

  for (const edge of rawEdges) {
    const fromDir = nodeToDir.get(edge.from_node_id);
    const toDir = nodeToDir.get(edge.to_node_id);

    if (fromDir && toDir && fromDir !== toDir) {
      const key = `${fromDir}->${toDir}`;
      let agg = edgeAggregates.get(key);
      if (!agg) {
        agg = { fromDir, toDir, calls: 0, imports: 0, routes: 0, other: 0 };
        edgeAggregates.set(key, agg);
      }

      if (edge.relation_type === "calls") agg.calls++;
      else if (edge.relation_type === "imports") agg.imports++;
      else if (edge.relation_type === "routes") agg.routes++;
      else agg.other++;
    }
  }

  // Create Package Edges
  const packageEdges: Edge[] = [];
  for (const [key, agg] of edgeAggregates.entries()) {
    const total = agg.calls + agg.imports + agg.routes + agg.other;
    let stroke = "rgba(148, 163, 184, 0.4)";
    let label = `${total} links`;

    if (agg.routes > 0) {
      stroke = "#f59e0b";
      label = `${agg.routes} routes`;
    } else if (agg.calls > 0) {
      stroke = "#38bdf8";
      label = `${agg.calls} calls`;
    } else if (agg.imports > 0) {
      stroke = "#a78bfa";
      label = `${agg.imports} imports`;
    }

    packageEdges.push({
      id: `pkg-edge:${key}`,
      source: `pkg:${agg.fromDir}`,
      target: `pkg:${agg.toDir}`,
      label,
      type: "smoothstep",
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: stroke,
      },
      style: {
        stroke,
        strokeWidth: Math.min(1.5 + Math.log2(total + 1), 4),
        opacity: 0.75,
      },
      labelStyle: {
        fill: "#f8fafc",
        fontSize: 10,
        fontWeight: 600,
        fontFamily: "JetBrains Mono, monospace",
      },
      labelBgStyle: {
        fill: "rgba(15, 17, 28, 0.95)",
        stroke,
        strokeWidth: 1,
        rx: 4,
        ry: 4,
      },
      labelBgPadding: [6, 4] as [number, number],
    });
  }

  return { packageNodes, packageEdges };
};
