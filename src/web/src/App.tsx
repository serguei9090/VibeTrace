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
import type {
  AgentFocusEvent,
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

export default function App() {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [filter, setFilter] = useState("");
  const [activeFilters, setActiveFilters] = useState<Set<string>>(
    new Set(["function", "class", "interface", "module"])
  );
  const [showPrivate, setShowPrivate] = useState(true);
  const [impactMode, setImpactMode] = useState(false);
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("map");
  const [lastUpdate, setLastUpdate] = useState<string>("");

  const [followAgent, setFollowAgent] = useState(true);
  const [lastAgentEvent, setLastAgentEvent] = useState<AgentFocusEvent | null>(null);
  const [activeTrace, setActiveTrace] = useState<TracePathResponse | null>(null);

  const rawData = useRef<GraphData>({ nodes: [], edges: [] });
  const ws = useRef<WebSocket | null>(null);
  const [selectedElement, setSelectedElement] = useState<SelectedElement | null>(null);

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
      highlights
    );

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
        setNodes(layoutNodes);
        setEdges(layoutEdges);
      }
    } catch (layoutError) {
      console.error("Layout Error:", layoutError);
      setError("Layout failed (ELK error). Check console.");
    }
  }, [
    activeFilters,
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

  useEffect(() => {
    applyLayoutAndFilters();
  }, [applyLayoutAndFilters]);

  useEffect(() => {
    fetchGraph();
    const connectWS = () => {
      ws.current = new WebSocket(WS_URL);
      ws.current.onopen = () => console.log("Vibe-Sync Active");
      ws.current.onmessage = (event) => {
        if (event.data === "refresh") {
          fetchGraph();
          return;
        }
        try {
          const parsed = JSON.parse(event.data);
          if (parsed?.type === "agent_focus") {
            setLastAgentEvent(parsed as AgentFocusEvent);
          }
        } catch {
          // ignore non-json frames
        }
      };
      ws.current.onclose = () => {
        setTimeout(connectWS, 3000);
      };
    };

    connectWS();
    return () => {
      ws.current?.close();
    };
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
