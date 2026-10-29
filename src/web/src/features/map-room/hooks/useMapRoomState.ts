import {
  addEdge,
  type Connection,
  type Edge,
  type Node,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import axios from "axios";
import type React from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import type {
  GraphData,
  LayoutMode,
  NodeNeighborsResponse,
  SelectedElement,
  TracePathResponse,
  ViewMode,
} from "../types";
import { getLayoutedElements } from "../utils/elkLayout";
import { buildFlowElements, computeSelectionHighlights } from "../utils/graphTraversal";
import {
  calculateUnrevealedNeighbors,
  findRootEntryPoints,
  isAnonymousSymbol,
} from "../utils/treeHierarchy";
import { useVibeSync } from "./useVibeSync";

const isBrowser = typeof window !== "undefined";
const isViteDev = isBrowser && (window.location.port === "5173" || window.location.port === "3000");

export const API_URL =
  isViteDev || !isBrowser
    ? "http://localhost:8000"
    : `${window.location.protocol}//${window.location.host}`;

export const WS_URL =
  isViteDev || !isBrowser
    ? "ws://localhost:8000/ws"
    : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;

const initialNodes: Node[] = [];
const initialEdges: Edge[] = [];
const DEFAULT_ACTIVE_FILTERS = new Set([
  "function",
  "class",
  "interface",
  "struct",
  "trait",
  "impl",
  "enum",
  "variable",
  "module",
  "endpoint",
]);
const DEFAULT_EDGE_TYPES = new Set(["calls", "routes", "imports", "inherits"]);

export function useMapRoomState() {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [filter, setFilter] = useState("");
  const [activeFilters, setActiveFilters] = useState<Set<string>>(DEFAULT_ACTIVE_FILTERS);
  const [activeEdgeTypes, setActiveEdgeTypes] = useState<Set<string>>(DEFAULT_EDGE_TYPES);
  const [showPrivate, setShowPrivate] = useState(true);
  const [impactMode, setImpactMode] = useState(false);
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("map");
  const [viewMode, setViewMode] = useState<ViewMode>("explorer");
  const [showAnonymous, setShowAnonymous] = useState(false);
  const [revealedNodeIds, setRevealedNodeIds] = useState<Set<string>>(new Set());
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<string>("");

  const [followAgent, setFollowAgent] = useState(true);
  const [activeTrace, setActiveTrace] = useState<TracePathResponse | null>(null);

  const fetchGraphRef = useRef<() => void>(() => {});
  const { lastAgentEvent } = useVibeSync({
    wsUrl: WS_URL,
    onRefresh: useCallback(() => fetchGraphRef.current(), []),
  });

  const rawData = useRef<GraphData>({ nodes: [], edges: [] });
  const [selectedElement, setSelectedElement] = useState<SelectedElement | null>(null);
  const cachedLayout = useRef<{
    key: string;
    nodes: Node[];
    edges: Edge[];
  } | null>(null);

  const handleLayoutModeChange = useCallback((mode: LayoutMode) => {
    setLayoutMode(mode);
    if (mode === "impact") {
      setImpactMode(true);
    } else if (mode === "trace") {
      setImpactMode(false);
    }
  }, []);

  const handleImpactToggle = useCallback((enabled: boolean) => {
    setImpactMode(enabled);
    setLayoutMode((prev) => (enabled ? "impact" : prev === "impact" ? "map" : prev));
  }, []);

  const handleTraceResult = useCallback((res: TracePathResponse | null) => {
    setActiveTrace(res);
    if (res?.found) {
      setLayoutMode("trace");
      setImpactMode(false);
      // Auto-reveal nodes in trace
      setRevealedNodeIds((prev) => new Set([...prev, ...res.node_ids]));
    } else if (!res) {
      setLayoutMode("map");
    }
  }, []);

  const resetTrace = useCallback(() => {
    setActiveTrace(null);
    setLayoutMode("map");
  }, []);

  const onResetToRoots = useCallback(() => {
    const roots = findRootEntryPoints(rawData.current.nodes, rawData.current.edges, showAnonymous);
    setRevealedNodeIds(new Set(roots));
    setSelectedElement(null);
    cachedLayout.current = null;
  }, [showAnonymous]);

  const onToggleNodeVisibility = useCallback((nodeId: string) => {
    setRevealedNodeIds((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
    cachedLayout.current = null;
  }, []);

  const onFocusNode = useCallback((nodeId: string) => {
    setRevealedNodeIds((prev) => new Set([...prev, nodeId]));
    setSelectedElement({ id: nodeId, type: "node" });
    cachedLayout.current = null;
  }, []);

  const onExpandNodeConnections = useCallback((nodeId: string) => {
    const edges = rawData.current.edges;
    const neighborIds: string[] = [];
    for (const e of edges) {
      if (e.relation_type === "defines") continue;
      if (e.from_node_id === nodeId) neighborIds.push(e.to_node_id);
      if (e.to_node_id === nodeId) neighborIds.push(e.from_node_id);
    }
    setRevealedNodeIds((prev) => new Set([...prev, nodeId, ...neighborIds]));
    cachedLayout.current = null;
  }, []);

  const applyLayoutAndFilters = useCallback(async () => {
    const { nodes: rawNodes, edges: rawEdges } = rawData.current;
    if (rawNodes.length === 0) return;

    // Filter out anonymous closures unless explicitly requested
    const nonAnonNodes = showAnonymous ? rawNodes : rawNodes.filter((n) => !isAnonymousSymbol(n));

    // Explorer mode limits visible nodes to revealed set + active filters
    let targetCandidateNodes = nonAnonNodes;
    if (viewMode === "explorer") {
      const term = filter.trim().toLowerCase();
      targetCandidateNodes = nonAnonNodes.filter((n) => {
        if (revealedNodeIds.has(n.id)) return true;
        if (
          term &&
          (n.name.toLowerCase().includes(term) ||
            (n.qualified_name?.toLowerCase().includes(term) ?? false))
        ) {
          return true;
        }
        if (activeTrace?.node_ids.includes(n.id)) return true;
        if (followAgent && lastAgentEvent?.node_ids.includes(n.id)) return true;
        return false;
      });
    }

    const filteredNodes = targetCandidateNodes.filter((n) => {
      const typeMatch = activeFilters.has(n.kind);
      const visibilityMatch = showPrivate || n.visibility !== "private";
      return typeMatch && visibilityMatch;
    });

    const visibleNodeIds = new Set(filteredNodes.map((n) => n.id));
    const filteredEdges = rawEdges.filter(
      (e) => visibleNodeIds.has(e.from_node_id) && visibleNodeIds.has(e.to_node_id)
    );

    const agentNodeIds = followAgent && lastAgentEvent ? lastAgentEvent.node_ids : [];
    const highlights = computeSelectionHighlights(
      selectedElement,
      filteredEdges,
      impactMode || layoutMode === "impact",
      activeTrace,
      agentNodeIds
    );

    const hasActiveHighlight =
      Boolean(selectedElement) ||
      Boolean(activeTrace?.found) ||
      (followAgent && agentNodeIds.length > 0);

    const { flowNodes, flowEdges } = buildFlowElements(
      filteredNodes,
      filteredEdges,
      filter.toLowerCase(),
      hasActiveHighlight,
      selectedElement,
      impactMode,
      layoutMode,
      highlights,
      activeEdgeTypes
    );

    // Compute unrevealed neighbor counts for progressive disclosure
    const neighborMap = calculateUnrevealedNeighbors(
      nonAnonNodes,
      rawEdges,
      visibleNodeIds,
      showAnonymous
    );

    // Enrich flow nodes with on-demand expansion triggers and callbacks
    const interactiveNodes = flowNodes.map((fn) => {
      const info = neighborMap.get(fn.id);
      return {
        ...fn,
        data: {
          ...fn.data,
          id: fn.id,
          viewMode,
          unrevealedCallees: info?.unrevealedCallees || 0,
          unrevealedCallers: info?.unrevealedCallers || 0,
          onExpandCallees: async (id: string) => {
            try {
              // Try surgical lazy neighbors endpoint first
              const res = await axios.get<NodeNeighborsResponse>(
                `${API_URL}/api/node/${id}/neighbors?direction=down`
              );
              if (res.data?.nodes && res.data.nodes.length > 0) {
                // Incorporate fetched nodes/edges into rawData if not already present
                const existingNodeIds = new Set(rawData.current.nodes.map((n) => n.id));
                for (const newNode of res.data.nodes) {
                  if (!existingNodeIds.has(newNode.id)) {
                    rawData.current.nodes.push(newNode);
                  }
                }
                const existingEdgeKeys = new Set(
                  rawData.current.edges.map(
                    (e) => `${e.from_node_id}-${e.to_node_id}-${e.relation_type}`
                  )
                );
                for (const newEdge of res.data.edges) {
                  const key = `${newEdge.from_node_id}-${newEdge.to_node_id}-${newEdge.relation_type}`;
                  if (!existingEdgeKeys.has(key)) {
                    rawData.current.edges.push(newEdge);
                  }
                }
                const newIds = res.data.nodes.map((n) => n.id);
                setRevealedNodeIds((prev) => new Set([...prev, ...newIds]));
                cachedLayout.current = null;
                return;
              }
            } catch {
              // Fallback to local memory graph if offline or error
            }

            const nInfo = neighborMap.get(id);
            const calleeIds =
              nInfo?.calleeIds ??
              rawEdges
                .filter((e) => e.from_node_id === id && e.relation_type !== "defines")
                .map((e) => e.to_node_id);
            if (calleeIds.length > 0) {
              setRevealedNodeIds((prev) => new Set([...prev, ...calleeIds]));
              cachedLayout.current = null;
            }
          },
          onExpandCallers: async (id: string) => {
            try {
              const res = await axios.get<NodeNeighborsResponse>(
                `${API_URL}/api/node/${id}/neighbors?direction=up`
              );
              if (res.data?.nodes && res.data.nodes.length > 0) {
                const existingNodeIds = new Set(rawData.current.nodes.map((n) => n.id));
                for (const newNode of res.data.nodes) {
                  if (!existingNodeIds.has(newNode.id)) {
                    rawData.current.nodes.push(newNode);
                  }
                }
                const existingEdgeKeys = new Set(
                  rawData.current.edges.map(
                    (e) => `${e.from_node_id}-${e.to_node_id}-${e.relation_type}`
                  )
                );
                for (const newEdge of res.data.edges) {
                  const key = `${newEdge.from_node_id}-${newEdge.to_node_id}-${newEdge.relation_type}`;
                  if (!existingEdgeKeys.has(key)) {
                    rawData.current.edges.push(newEdge);
                  }
                }
                const newIds = res.data.nodes.map((n) => n.id);
                setRevealedNodeIds((prev) => new Set([...prev, ...newIds]));
                cachedLayout.current = null;
                return;
              }
            } catch {
              // Fallback to local memory graph
            }

            const nInfo = neighborMap.get(id);
            const callerIds =
              nInfo?.callerIds ??
              rawEdges
                .filter((e) => e.to_node_id === id && e.relation_type !== "defines")
                .map((e) => e.from_node_id);
            if (callerIds.length > 0) {
              setRevealedNodeIds((prev) => new Set([...prev, ...callerIds]));
              cachedLayout.current = null;
            }
          },
          onCollapseNode: (id: string) => {
            setRevealedNodeIds((prev) => {
              const next = new Set(prev);
              next.delete(id);
              return next;
            });
            cachedLayout.current = null;
          },
        },
      };
    });

    const layoutKey = `${layoutMode}_${viewMode}_${showAnonymous}_${showPrivate}_${Array.from(activeFilters).sort().join(",")}_${Array.from(activeEdgeTypes).sort().join(",")}_${filteredNodes.length}_${filteredEdges.length}_${revealedNodeIds.size}`;

    if (
      layoutMode === "map" &&
      viewMode === "full" &&
      cachedLayout.current &&
      cachedLayout.current.key === layoutKey
    ) {
      const flowNodeMap = new Map(interactiveNodes.map((fn) => [fn.id, fn]));
      const updatedNodes = cachedLayout.current.nodes.map((n) => {
        const symbolUpdate = flowNodeMap.get(n.id);
        if (symbolUpdate) {
          return {
            ...n,
            data: symbolUpdate.data,
            className: symbolUpdate.className,
            style: { ...n.style, ...symbolUpdate.style },
          };
        }
        return n;
      });
      setNodes(updatedNodes);
      setEdges(flowEdges);
      return;
    }

    try {
      const { nodes: layoutNodes, edges: layoutEdges } = await getLayoutedElements(
        interactiveNodes,
        flowEdges,
        {
          mode: layoutMode,
          viewMode,
          traceStepMap: highlights.traceStepMap,
          impactDepthMap: highlights.impactDepthMap,
        }
      );
      if (layoutNodes && layoutNodes.length > 0) {
        cachedLayout.current = {
          key: layoutKey,
          nodes: layoutNodes,
          edges: layoutEdges,
        };
        setNodes(layoutNodes);
        setEdges(layoutEdges);
      }
    } catch (layoutError) {
      console.error("Layout Error:", layoutError);
      setError("Layout failed (ELK error). Check console.");
    }
  }, [
    activeFilters,
    activeEdgeTypes,
    showPrivate,
    impactMode,
    layoutMode,
    viewMode,
    showAnonymous,
    revealedNodeIds,
    filter,
    selectedElement,
    activeTrace,
    followAgent,
    lastAgentEvent,
    setNodes,
    setEdges,
  ]);

  const fetchGraph = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(`${API_URL}/graph`);
      if (!res.data?.nodes) {
        setError("Invalid data format received from API");
        return;
      }
      rawData.current = res.data;
      cachedLayout.current = null;
      setLastUpdate(new Date().toLocaleTimeString());

      // Seed initial roots on first load
      setRevealedNodeIds((prev) => {
        if (prev.size === 0) {
          const roots = findRootEntryPoints(res.data.nodes, res.data.edges, false);
          return new Set(roots);
        }
        return prev;
      });

      if (res.data.nodes.length === 0) {
        setError("Database is empty. Did you run the indexer?");
      }
    } catch (err: any) {
      console.error("Failed to fetch graph", err);
      setError(err.message || "Failed to fetch graph");
    } finally {
      setLoading(false);
    }
  }, []);

  fetchGraphRef.current = fetchGraph;

  useEffect(() => {
    applyLayoutAndFilters();
  }, [applyLayoutAndFilters]);

  useEffect(() => {
    fetchGraph();
  }, [fetchGraph]);

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge(params, eds)),
    [setEdges]
  );

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    if (node.type === "vibeNode") {
      setSelectedElement((prev) => (prev?.id === node.id ? null : { id: node.id, type: "node" }));
    }
  }, []);

  const onEdgeClick = useCallback((_: React.MouseEvent, edge: Edge) => {
    setSelectedElement((prev) => (prev?.id === edge.id ? null : { id: edge.id, type: "edge" }));
  }, []);

  const onPaneClick = useCallback(() => {
    setSelectedElement(null);
  }, []);

  const toggleFilter = (kind: string) => {
    const newFilters = new Set(activeFilters);
    if (newFilters.has(kind)) {
      newFilters.delete(kind);
    } else {
      newFilters.add(kind);
    }
    setActiveFilters(newFilters);
  };

  const toggleEdgeType = (relType: string) => {
    const newEdgeTypes = new Set(activeEdgeTypes);
    if (newEdgeTypes.has(relType)) {
      newEdgeTypes.delete(relType);
    } else {
      newEdgeTypes.add(relType);
    }
    setActiveEdgeTypes(newEdgeTypes);
  };

  return {
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,
    onConnect,
    loading,
    error,
    filter,
    setFilter,
    activeFilters,
    toggleFilter,
    activeEdgeTypes,
    toggleEdgeType,
    showPrivate,
    setShowPrivate,
    impactMode,
    handleImpactToggle,
    layoutMode,
    handleLayoutModeChange,
    viewMode,
    setViewMode,
    showAnonymous,
    setShowAnonymous,
    revealedNodeIds,
    isSidebarOpen,
    setIsSidebarOpen,
    onResetToRoots,
    onToggleNodeVisibility,
    onFocusNode,
    onExpandNodeConnections,
    lastUpdate,
    followAgent,
    setFollowAgent,
    lastAgentEvent,
    activeTrace,
    handleTraceResult,
    resetTrace,
    selectedElement,
    setSelectedElement,
    onNodeClick,
    onEdgeClick,
    onPaneClick,
    rawData,
    fetchGraph,
  };
}
