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
import type { GraphData, LayoutMode, SelectedElement, TracePathResponse } from "../types";
import { getLayoutedElements } from "../utils/elkLayout";
import { buildFlowElements, computeSelectionHighlights } from "../utils/graphTraversal";
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
    } else if (!res) {
      setLayoutMode("map");
    }
  }, []);

  const resetTrace = useCallback(() => {
    setActiveTrace(null);
    setLayoutMode("map");
  }, []);

  const applyLayoutAndFilters = useCallback(async () => {
    const { nodes: rawNodes, edges: rawEdges } = rawData.current;
    if (rawNodes.length === 0) return;

    const filteredNodes = rawNodes.filter((n) => {
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

    const layoutKey = `${layoutMode}_${showPrivate}_${Array.from(activeFilters).sort().join(",")}_${Array.from(activeEdgeTypes).sort().join(",")}_${filteredNodes.length}_${filteredEdges.length}`;

    if (layoutMode === "map" && cachedLayout.current && cachedLayout.current.key === layoutKey) {
      const flowNodeMap = new Map(flowNodes.map((fn) => [fn.id, fn]));
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
        flowNodes,
        flowEdges,
        {
          mode: layoutMode,
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

      if (res.data.nodes.length === 0) {
        setError("Database is empty. Did you run the indexer?");
      } else {
        await applyLayoutAndFilters();
      }
    } catch (err: any) {
      console.error("Failed to fetch graph", err);
      setError(err.message || "Failed to fetch graph");
    } finally {
      setLoading(false);
    }
  }, [applyLayoutAndFilters]);

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
