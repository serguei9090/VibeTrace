import type { RawGraphEdge, RawGraphNode } from "../types";

export interface SymbolTreeNode {
  id: string;
  name: string;
  qualified_name?: string | null;
  kind: string;
  file_path: string;
  start_line: number;
  inDegree: number;
  outDegree: number;
  isVisible: boolean;
}

export interface FileTreeItem {
  filePath: string;
  fileName: string;
  symbols: SymbolTreeNode[];
}

export interface DirectoryGroup {
  dirPath: string;
  files: FileTreeItem[];
}

export interface NeighborInfo {
  unrevealedCallees: number;
  unrevealedCallers: number;
  calleeIds: string[];
  callerIds: string[];
}

/**
 * Checks if a symbol is an anonymous closure or lambda callback.
 */
export const isAnonymousSymbol = (node: RawGraphNode): boolean => {
  const name = node.name || "";
  const qname = node.qualified_name || "";
  return (
    name.startsWith("<anonymous") ||
    qname.includes("<anonymous") ||
    name.includes("<lambda>") ||
    qname.includes("<lambda>")
  );
};

/**
 * Finds high-level entry points for initial hierarchical progressive exploration.
 */
export const findRootEntryPoints = (
  nodes: RawGraphNode[],
  edges: RawGraphEdge[],
  showAnonymous = false
): string[] => {
  const candidateNodes = showAnonymous ? nodes : nodes.filter((n) => !isAnonymousSymbol(n));
  const nodeMap = new Map(candidateNodes.map((n) => [n.id, n]));

  // In-degree & out-degree maps
  const inDegreeMap = new Map<string, number>();
  const outDegreeMap = new Map<string, number>();

  for (const e of edges) {
    if (e.relation_type === "defines") continue;
    if (nodeMap.has(e.from_node_id) && nodeMap.has(e.to_node_id)) {
      outDegreeMap.set(e.from_node_id, (outDegreeMap.get(e.from_node_id) || 0) + 1);
      inDegreeMap.set(e.to_node_id, (inDegreeMap.get(e.to_node_id) || 0) + 1);
    }
  }

  // 1. All API endpoints are primary entry points
  const endpoints = candidateNodes.filter((n) => n.kind === "endpoint");
  if (endpoints.length > 0 && endpoints.length <= 50) {
    return endpoints.map((n) => n.id);
  }

  // 2. High-level entrypoints: main, cli, run, app, or root handlers
  const primaryNames = new Set(["main", "cli", "run", "app", "serve", "handler"]);
  const mainEntrypoints = candidateNodes.filter(
    (n) =>
      primaryNames.has(n.name.toLowerCase()) ||
      n.name.toLowerCase().endsWith("_main") ||
      n.name.toLowerCase().endsWith("_cli")
  );

  // 3. Roots with 0 callers and >0 callees
  const naturalRoots = candidateNodes.filter((n) => {
    const inDeg = inDegreeMap.get(n.id) || 0;
    const outDeg = outDegreeMap.get(n.id) || 0;
    return inDeg === 0 && outDeg > 0 && n.kind !== "variable" && n.kind !== "module";
  });

  const combined = new Set<string>();
  for (const n of endpoints) combined.add(n.id);
  for (const n of mainEntrypoints) combined.add(n.id);
  for (const n of naturalRoots) combined.add(n.id);

  if (combined.size > 0) {
    // Cap initial roots to top 30 to guarantee instantaneous rendering and high readability
    return Array.from(combined).slice(0, 30);
  }

  // Fallback: Top 15 nodes by out-degree
  const sorted = [...candidateNodes].sort(
    (a, b) => (outDegreeMap.get(b.id) || 0) - (outDegreeMap.get(a.id) || 0)
  );
  return sorted.slice(0, 15).map((n) => n.id);
};

/**
 * Computes unrevealed callee and caller counts for every node.
 */
export const calculateUnrevealedNeighbors = (
  allNodes: RawGraphNode[],
  edges: RawGraphEdge[],
  visibleIds: Set<string>,
  showAnonymous = false
): Map<string, NeighborInfo> => {
  const result = new Map<string, NeighborInfo>();
  const validNodeIds = new Set(
    (showAnonymous ? allNodes : allNodes.filter((n) => !isAnonymousSymbol(n))).map((n) => n.id)
  );

  const outEdges = new Map<string, string[]>();
  const inEdges = new Map<string, string[]>();

  for (const e of edges) {
    if (e.relation_type === "defines") continue;
    if (!validNodeIds.has(e.from_node_id) || !validNodeIds.has(e.to_node_id)) continue;

    const outs = outEdges.get(e.from_node_id);
    if (outs) outs.push(e.to_node_id);
    else outEdges.set(e.from_node_id, [e.to_node_id]);

    const ins = inEdges.get(e.to_node_id);
    if (ins) ins.push(e.from_node_id);
    else inEdges.set(e.to_node_id, [e.from_node_id]);
  }

  for (const nodeId of visibleIds) {
    const rawCallees = outEdges.get(nodeId) || [];
    const rawCallers = inEdges.get(nodeId) || [];

    const calleeIds = Array.from(new Set(rawCallees)).filter((id) => !visibleIds.has(id));
    const callerIds = Array.from(new Set(rawCallers)).filter((id) => !visibleIds.has(id));

    result.set(nodeId, {
      unrevealedCallees: calleeIds.length,
      unrevealedCallers: callerIds.length,
      calleeIds,
      callerIds,
    });
  }

  return result;
};

/**
 * Builds a hierarchical directory -> file -> symbol structure for the Tree Sidebar.
 */
export const buildFileTree = (
  nodes: RawGraphNode[],
  edges: RawGraphEdge[],
  visibleIds: Set<string>,
  searchTerm = "",
  kindFilter?: string,
  showAnonymous = false
): DirectoryGroup[] => {
  const term = searchTerm.trim().toLowerCase();
  const inDegreeMap = new Map<string, number>();
  const outDegreeMap = new Map<string, number>();

  for (const e of edges) {
    if (e.relation_type === "defines") continue;
    outDegreeMap.set(e.from_node_id, (outDegreeMap.get(e.from_node_id) || 0) + 1);
    inDegreeMap.set(e.to_node_id, (inDegreeMap.get(e.to_node_id) || 0) + 1);
  }

  const dirMap = new Map<string, Map<string, SymbolTreeNode[]>>();

  for (const n of nodes) {
    if (!showAnonymous && isAnonymousSymbol(n)) continue;
    if (kindFilter && kindFilter !== "all" && n.kind !== kindFilter) continue;

    const matchesSearch =
      !term ||
      n.name.toLowerCase().includes(term) ||
      (n.qualified_name?.toLowerCase().includes(term) ?? false) ||
      n.file_path.toLowerCase().includes(term);

    if (!matchesSearch) continue;

    const filePath = n.file_path.replace(/\\/g, "/");
    const lastSlash = filePath.lastIndexOf("/");
    const dirPath = lastSlash !== -1 ? filePath.substring(0, lastSlash) : "root";
    const fileName = lastSlash !== -1 ? filePath.substring(lastSlash + 1) : filePath;

    let dirFiles = dirMap.get(dirPath);
    if (!dirFiles) {
      dirFiles = new Map<string, SymbolTreeNode[]>();
      dirMap.set(dirPath, dirFiles);
    }

    let fileSymbols = dirFiles.get(fileName);
    if (!fileSymbols) {
      fileSymbols = [];
      dirFiles.set(fileName, fileSymbols);
    }

    fileSymbols.push({
      id: n.id,
      name: n.name,
      qualified_name: n.qualified_name,
      kind: n.kind,
      file_path: filePath,
      start_line: n.start_line,
      inDegree: inDegreeMap.get(n.id) || 0,
      outDegree: outDegreeMap.get(n.id) || 0,
      isVisible: visibleIds.has(n.id),
    });
  }

  const result: DirectoryGroup[] = [];
  const sortedDirs = Array.from(dirMap.keys()).sort();

  for (const dirPath of sortedDirs) {
    const fileMap = dirMap.get(dirPath)!;
    const sortedFileNames = Array.from(fileMap.keys()).sort();
    const files: FileTreeItem[] = [];

    for (const fileName of sortedFileNames) {
      const symbols = fileMap.get(fileName)!;
      symbols.sort((a, b) => a.start_line - b.start_line);
      files.push({
        filePath: `${dirPath}/${fileName}`,
        fileName,
        symbols,
      });
    }

    result.push({ dirPath, files });
  }

  return result;
};
