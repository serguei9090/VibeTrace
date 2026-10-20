import {
  addEdge,
  Background,
  type Connection,
  Controls,
  type Edge,
  MiniMap,
  type Node,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import axios from "axios";
import type React from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";

import { CanvasAutoFitter } from "./features/map-room/components/CanvasAutoFitter";
import { CodeInspectorDrawer } from "./features/map-room/components/CodeInspectorDrawer";
import { FilterPanel } from "./features/map-room/components/FilterPanel";
import { MapHeader } from "./features/map-room/components/MapHeader";
import { DistrictNode } from "./features/map-room/components/nodes/DistrictNode";
import { FileGroupNode } from "./features/map-room/components/nodes/FileGroupNode";
import { VibeNode } from "./features/map-room/components/nodes/VibeNode";
import { StatusOverlays } from "./features/map-room/components/StatusOverlays";
import { TracePathBar } from "./features/map-room/components/TracePathBar";
import { useVibeSync } from "./features/map-room/hooks/useVibeSync";
import type {
  GraphData,
  LayoutMode,
  SelectedElement,
  TracePathResponse,
} from "./features/map-room/types";
import { getGraphBounds, getLayoutedElements } from "./features/map-room/utils/elkLayout";
import {
  buildFlowElements,
  computeSelectionHighlights,
} from "./features/map-room/utils/graphTraversal";

const API_URL = "http://localhost:8000";
const WS_URL = "ws://localhost:8000/ws";

const nodeTypes = {
  vibeNode: VibeNode,
  districtGroup: DistrictNode,
  fileGroup: FileGroupNode,
};

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

export default function App() {
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

    // Fast-path: In "map" mode, if layout structure has not changed, reuse cached node coordinates
    // and update only visual highlights and edge styling in place without re-invoking ELK.
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

  return (
    <div className="graph-container">
      <MapHeader
        filter={filter}
        onFilterChange={setFilter}
        loading={loading}
        onRefresh={() => fetchGraph()}
        totalSymbols={rawData.current.nodes.length}
        totalEdges={rawData.current.edges.length}
        visibleSymbols={
          nodes.filter((n) => n.type !== "districtGroup" && n.type !== "fileGroup").length
        }
        layoutMode={layoutMode}
        activeTrace={activeTrace}
        onResetTrace={() => {
          setActiveTrace(null);
          setLayoutMode("map");
        }}
        lastUpdate={lastUpdate}
        followAgent={followAgent}
        onToggleFollowAgent={setFollowAgent}
        lastAgentEvent={lastAgentEvent}
      />

      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        onPaneClick={onPaneClick}
        fitView
        fitViewOptions={{ padding: 0.35 }}
        translateExtent={getGraphBounds(nodes)}
      >
        <Controls />
        <MiniMap
          nodeStrokeWidth={3}
          zoomable
          pannable
          style={{
            backgroundColor: "#0d0d10",
            border: "1px solid rgba(99, 102, 241, 0.3)",
            width: 220,
            height: 160,
            bottom: 60,
          }}
          nodeColor={(n: any) => {
            if (n.data?.kind === "class") return "#a855f7";
            if (n.data?.kind === "interface") return "#10b981";
            return "#6366f1";
          }}
          maskColor="rgba(0, 0, 0, 0.7)"
        />
        <Background color="#1a1b23" gap={20} size={1} />

        <StatusOverlays loading={loading} error={error} onRetry={() => fetchGraph()} />
        <CanvasAutoFitter layoutMode={layoutMode} nodeCount={nodes.length} />

        <TracePathBar
          apiUrl={API_URL}
          nodes={rawData.current.nodes}
          activeTrace={activeTrace}
          onTraceResult={handleTraceResult}
        />

        <CodeInspectorDrawer
          apiUrl={API_URL}
          selectedElement={selectedElement}
          rawNodes={rawData.current.nodes}
          rawEdges={rawData.current.edges}
          onClose={() => setSelectedElement(null)}
        />

        <FilterPanel
          activeFilters={activeFilters}
          onToggleFilter={toggleFilter}
          activeEdgeTypes={activeEdgeTypes}
          onToggleEdgeType={toggleEdgeType}
          showPrivate={showPrivate}
          onTogglePrivate={setShowPrivate}
          impactMode={impactMode}
          onToggleImpact={handleImpactToggle}
          layoutMode={layoutMode}
          onLayoutModeChange={handleLayoutModeChange}
        />
      </ReactFlow>
    </div>
  );
}
