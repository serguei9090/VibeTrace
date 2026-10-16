import axios from "axios";
import type React from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactFlow, {
  addEdge,
  Background,
  type Connection,
  Controls,
  type Edge,
  MarkerType,
  MiniMap,
  type Node,
  useEdgesState,
  useNodesState,
} from "reactflow";
import "reactflow/dist/style.css";
import "./App.css";

import { FilterPanel } from "./features/map-room/components/FilterPanel";
import { MapHeader } from "./features/map-room/components/MapHeader";
import { DistrictNode } from "./features/map-room/components/nodes/DistrictNode";
import { FileGroupNode } from "./features/map-room/components/nodes/FileGroupNode";
import { VibeNode } from "./features/map-room/components/nodes/VibeNode";
import { StatusOverlays } from "./features/map-room/components/StatusOverlays";
import type { GraphData, SelectedElement } from "./features/map-room/types";
import { getGraphBounds, getLayoutedElements } from "./features/map-room/utils/elkLayout";
import { computeSelectionHighlights } from "./features/map-room/utils/graphTraversal";

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
  const [lastUpdate, setLastUpdate] = useState<string>("");

  const rawData = useRef<GraphData>({ nodes: [], edges: [] });
  const ws = useRef<WebSocket | null>(null);
  const [selectedElement, setSelectedElement] = useState<SelectedElement | null>(null);

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

    const searchTerm = filter.toLowerCase();
    const searchActive = searchTerm.length > 0;

    const { highlightedNodes, highlightedEdges } = computeSelectionHighlights(
      selectedElement,
      filteredEdges,
      impactMode
    );

    const flowNodes: Node[] = filteredNodes.map((n) => {
      const isSearchMatch =
        searchActive &&
        (n.name.toLowerCase().includes(searchTerm) ||
          n.file_path.toLowerCase().includes(searchTerm));

      let isHighlighted = false;
      let isDimmed = false;

      if (selectedElement) {
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
          kind: n.kind,
          signature: n.signature,
          docstring: n.docstring,
          file_path: n.file_path,
          visibility: n.visibility,
          decorators: n.decorators,
          isHighlighted,
          isDimmed,
          isImpact: impactMode && highlightedNodes.has(n.id) && n.id !== selectedElement?.id,
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

        if (selectedElement) {
          isHighlighted = highlightedEdges.has(edgeId);
          isDimmed = !isHighlighted;
        } else if (searchActive) {
          isDimmed = true;
        }

        let label = e.relation_type;
        if (label === "defines") label = "";

        flowEdges.push({
          id: edgeId,
          source: e.from_node_id,
          target: e.to_node_id,
          label: isHighlighted ? e.relation_type : label,
          type: "smoothstep",
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: isHighlighted ? "#fff" : "rgba(255, 255, 255, 0.3)",
          },
          animated: isHighlighted,
          style: {
            stroke: isHighlighted ? "#6366f1" : "rgba(255, 255, 255, 0.2)",
            strokeWidth: isHighlighted ? 3 : 1,
            opacity: isDimmed ? 0.05 : 0.6,
            transition: "opacity 0.3s ease, stroke 0.3s ease",
          },
        });
      }
    });

    try {
      const { nodes: layoutNodes, edges: layoutEdges } = await getLayoutedElements(
        flowNodes,
        flowEdges
      );
      if (layoutNodes && layoutNodes.length > 0) {
        setNodes(layoutNodes);
        setEdges(layoutEdges);
      }
    } catch (layoutError) {
      console.error("Layout Error:", layoutError);
      setError("Layout failed (ELK error). Check console.");
    }
  }, [activeFilters, showPrivate, impactMode, filter, selectedElement, setNodes, setEdges]);

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
        symbolCount={
          nodes.filter((n) => n.type !== "districtGroup" && n.type !== "fileGroup").length
        }
        edgeCount={edges.length}
        lastUpdate={lastUpdate}
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

        <FilterPanel
          activeFilters={activeFilters}
          onToggleFilter={toggleFilter}
          showPrivate={showPrivate}
          onTogglePrivate={setShowPrivate}
          impactMode={impactMode}
          onToggleImpact={setImpactMode}
        />
      </ReactFlow>
    </div>
  );
}
