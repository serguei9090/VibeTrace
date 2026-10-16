import ELK from "elkjs/lib/elk.bundled.js";
import { memo, useCallback, useEffect, useRef, useState } from "react";
import ReactFlow, {
  addEdge,
  Background,
  type Connection,
  Controls,
  type Edge,
  Handle,
  MarkerType,
  MiniMap,
  type Node,
  Panel,
  Position,
  useEdgesState,
  useNodesState,
} from "reactflow";
import "reactflow/dist/style.css";
import axios from "axios";
import {
  Activity,
  Code,
  FileCode,
  Filter,
  Folder,
  Globe,
  Layers,
  Lock,
  RefreshCw,
  Search,
} from "lucide-react";
import "./App.css";

// API Configuration
const API_URL = "http://localhost:8000";
const WS_URL = "ws://localhost:8000/ws";

const elk = new ELK();

const NODE_WIDTH = 250;
const NODE_HEIGHT = 150;

// Helper to sanitize IDs for ELK (sometimes dots/slashes can be tricky, but usually fine)
// We will use the VibeGraph IDs as is, assuming they are unique strings.

const getLayoutedElements = async (nodes: Node[], edges: Edge[]) => {
  // 1. Group nodes by [District (Dir) -> File -> Symbols]
  const tree: Record<string, Record<string, Node[]>> = {};

  // Track all user nodes to ignore existing groups if re-running
  const contentNodes = nodes.filter((n) => n.type === "vibeNode"); // Assume 'vibeNode' is our content
  if (contentNodes.length === 0) return { nodes: [], edges };

  contentNodes.forEach((node) => {
    const filePath = node.data?.file_path || "unknown";
    const parts = filePath.split(/[\\/]/);
    const districtName = parts.length > 1 ? parts.slice(0, -1).join("/") : "root";

    if (!tree[districtName]) tree[districtName] = {};
    if (!tree[districtName][filePath]) tree[districtName][filePath] = [];
    tree[districtName][filePath].push(node);
  });

  // 2. Build ELK Graph Hierarchy
  // Hierarchy: Root -> District (node) -> File (node) -> Symbol (leaf)

  const elkDistricts = [];

  for (const [districtPath, files] of Object.entries(tree)) {
    const elkFiles = [];

    for (const [filePath, fileNodes] of Object.entries(files)) {
      const elkNodes = fileNodes.map((n) => ({
        id: n.id,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        // layoutOptions: { 'elk.portConstraints': 'FIXED_SIDE' } // optional
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
        "elk.padding": "[top=80,left=40,bottom=40,right=40]", // More breathing room for district
        "elk.spacing.nodeNode": "80", // Avoid file overlaps
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
      "elk.algorithm": "rectpacking", // Pack districts on the map
      "elk.padding": "[top=50,left=50,bottom=50,right=50]",
      "elk.spacing.nodeNode": "150", // More space between districts to prevent overlap
      "elk.aspectRatio": "1.4",
    },
  };

  try {
    const layoutedGraph = await elk.layout(rootGraph);

    const finalNodes: Node[] = [];

    // Traverse ELK result and rebuild React Flow Nodes with positions
    // Positions in ELK are relative to parent.
    // React Flow 'parentNode' handles relative positioning if we use it.
    // However, getting absolute positions can sometimes be safer for edge routing if we don't use nested node feature fully,
    // BUT React Flow's nested nodes are cool. Let's use them.
    // Note: ELK root -> District -> File -> Node

    // We need to recreate the District and File Group nodes based on ELK result.

    // layoutedGraph.children are Districts
    layoutedGraph.children?.forEach((district: any) => {
      // Create District Node
      finalNodes.push({
        id: district.id,
        type: "districtGroup",
        data: { label: district.labels?.[0]?.text || "District" },
        position: { x: district.x, y: district.y },
        style: {
          width: district.width,
          height: district.height,
          backgroundColor: "rgba(255, 255, 255, 0.02)",
          border: "3px solid rgba(99, 102, 241, 0.4)",
          borderRadius: "20px",
          paddingTop: "80px", // Match layout padding
        },
      });

      // Files in District
      district.children?.forEach((file: any) => {
        // Create File Node
        finalNodes.push({
          id: file.id,
          type: "fileGroup",
          data: {
            label: file.labels?.[0]?.text || "File",
            stats: `${file.children?.length} symbols`,
          },
          position: { x: file.x, y: file.y },
          parentNode: district.id,
          extent: "parent",
          style: {
            width: file.width,
            height: file.height,
            backgroundColor: "rgba(99, 102, 241, 0.05)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
            borderRadius: "12px",
            paddingTop: "5px", // 50px Header + 5px Padding
          },
        });

        // Symbols in File
        file.children?.forEach((symbol: any) => {
          // Find original data to preserve
          const original = contentNodes.find((n) => n.id === symbol.id);
          if (original) {
            finalNodes.push({
              ...original,
              position: { x: symbol.x, y: symbol.y },
              parentNode: file.id,
              extent: "parent",
              // Ensure dimensions are set if ELK didn't change them
              width: symbol.width,
              height: symbol.height,
            });
          }
        });
      });
    });

    return { nodes: finalNodes, edges }; // Edges don't change, React Flow handles routing between handles
  } catch (err) {
    console.error("ELK Layout Failed", err);
    return { nodes: [], edges: [] };
  }
};

// Custom Node Component
const VibeNode = memo(({ data }: { data: any }) => {
  const isClass = data.kind === "class";
  const isInterface = data.kind === "interface";
  const isHighlighted = data.isHighlighted;
  const isDimmed = data.isDimmed;
  const isPrivate = data.visibility === "private";
  const decorators = data.decorators ? data.decorators.split(",").filter(Boolean) : [];

  let borderColor = "#6366f1";
  let bgColor = "#1a1b23";

  if (isClass) borderColor = "#a855f7";
  if (isInterface) borderColor = "#10b981";

  // highlight styles
  if (isHighlighted) {
    bgColor = "#2e2f3d"; // slightly lighter background
    borderColor = "#ffffff"; // bright border
  }

  if (data.isImpact) {
    borderColor = "#f87171"; // Red for impact
  }

  const style: React.CSSProperties = {
    borderLeftColor: borderColor,
    backgroundColor: bgColor,
    opacity: isDimmed ? 0.2 : 1,
    transition: "all 0.3s ease",
    border: isHighlighted ? `2px solid ${borderColor}` : undefined,
    borderLeft: isHighlighted ? `2px solid ${borderColor}` : `3px solid ${borderColor}`,
  };

  return (
    <div className="vibe-node" style={style}>
      <Handle type="target" position={Position.Top} style={{ background: borderColor }} />
      <div className="node-header">
        <div className="node-type">
          {isPrivate ? (
            <Lock size={10} style={{ marginRight: 4 }} />
          ) : (
            <Globe size={10} style={{ marginRight: 4 }} />
          )}
          {data.kind}
        </div>
        {decorators.length > 0 && (
          <div className="node-decorators">
            {decorators.map((d: string) => (
              <span key={d} className="decorator-badge" title={d}>
                @{d.length > 10 ? d.substring(0, 10) + ".." : d}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="node-name">{data.label}</div>
      {data.signature && (
        <div style={{ marginTop: 4, fontSize: "9px", opacity: 0.6, fontFamily: "JetBrains Mono" }}>
          {data.signature.length > 30 ? data.signature.substring(0, 30) + "..." : data.signature}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} style={{ background: borderColor }} />
    </div>
  );
});

const DistrictNode = memo(({ data }: { data: any }) => {
  return (
    <div style={{ height: "100%", width: "100%", position: "relative" }}>
      <div
        style={{
          position: "absolute",
          top: -110,
          left: -4,
          background: "transparent",
          color: "rgba(255, 255, 255, 0.4)",
          fontSize: "22px",
          fontWeight: 300,
          display: "flex",
          alignItems: "center",
          gap: "8px",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          pointerEvents: "none",
        }}
      >
        <Folder size={20} />
        {data.label}
      </div>
    </div>
  );
});

const FileGroupNode = memo(({ data }: { data: any }) => {
  return (
    <div style={{ height: "100%", width: "100%", position: "relative" }}>
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: "50px",
          padding: "0 16px",
          background: "rgba(26, 27, 35, 0.95)", // More solid background
          backdropFilter: "blur(8px)",
          borderBottom: "1px solid rgba(255,255,255,0.1)",
          display: "flex",
          alignItems: "center",
          gap: "10px",
          color: "rgba(255,255,255,0.95)",
          fontSize: "13px",
          fontWeight: 600,
          borderTopLeftRadius: "11px",
          borderTopRightRadius: "11px",
          zIndex: 10,
        }}
      >
        <FileCode size={16} style={{ color: "#6366f1" }} />
        <span
          style={{
            flex: 1,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            fontFamily: "JetBrains Mono, monospace",
          }}
        >
          {data.label}
        </span>
        {data.stats && (
          <span
            style={{
              opacity: 0.5,
              fontSize: "11px",
              background: "rgba(255,255,255,0.05)",
              padding: "2px 8px",
              borderRadius: "4px",
            }}
          >
            {data.stats}
          </span>
        )}
      </div>
    </div>
  );
});

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

  // Interaction State
  const [filter, setFilter] = useState("");
  const [activeFilters, setActiveFilters] = useState<Set<string>>(
    new Set(["function", "class", "interface", "module"])
  );
  const [showPrivate, setShowPrivate] = useState(true);
  const [impactMode, setImpactMode] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<string>("");

  // Ref for raw graph data to support client-side filtering
  const rawData = useRef<{ nodes: any[]; edges: any[] }>({ nodes: [], edges: [] });
  const ws = useRef<WebSocket | null>(null);

  // Selection state for highlighting
  // Selection state for highlighting
  const [selectedElement, setSelectedElement] = useState<{
    id: string;
    type: "node" | "edge";
  } | null>(null);

  // Layout Graph w/ Filters & Selection
  const applyLayoutAndFilters = useCallback(async () => {
    const { nodes: rawNodes, edges: rawEdges } = rawData.current;

    if (rawNodes.length === 0) return;

    // 1. Filter Nodes by Type and Visibility
    const filteredNodes = rawNodes.filter((n) => {
      const typeMatch = activeFilters.has(n.kind);
      const visibilityMatch = showPrivate || n.visibility !== "private";
      return typeMatch && visibilityMatch;
    });

    // 2. Filter Edges (must connect two visible nodes)
    const visibleNodeIds = new Set(filteredNodes.map((n) => n.id));
    const filteredEdges = rawEdges.filter(
      (e) => visibleNodeIds.has(e.from_node_id) && visibleNodeIds.has(e.to_node_id)
    );

    // 3. Selection / Relationship Logic
    const searchTerm = filter.toLowerCase();
    const searchActive = searchTerm.length > 0;

    // Compute neighbors if something is selected
    const highlightedNodes = new Set<string>();
    const highlightedEdges = new Set<string>();

    if (selectedElement) {
      if (selectedElement.type === "node") {
        highlightedNodes.add(selectedElement.id);

        if (impactMode) {
          // BFS for Transitive Impact (3 levels upstream)
          // Impact flows from Callee -> Caller (opposite of 'calls' direction)
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
          // Normal Neighbor Highlighting (Local connections)
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
    }

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
          isDimmed = true; // Dim all edges when searching unless we want to highlight relationships
        }

        let label = e.relation_type;
        if (label === "defines") label = ""; // Reduce visual noise

        flowEdges.push({
          id: edgeId,
          source: e.from_node_id,
          target: e.to_node_id,
          label: isHighlighted ? e.relation_type : label, // Always show on highlight, otherwise hide if defines
          type: "smoothstep",
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: isHighlighted ? "#fff" : "rgba(255, 255, 255, 0.3)",
          },
          animated: isHighlighted,
          style: {
            stroke: isHighlighted ? "#6366f1" : "rgba(255, 255, 255, 0.2)",
            strokeWidth: isHighlighted ? 3 : 1,
            opacity: isDimmed ? 0.05 : 0.6, // Lower default opacity for edges
            transition: "opacity 0.3s ease, stroke 0.3s ease",
          },
        });
      }
    });

    // 4. Apply ELK Layout
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

  const fetchGraph = async () => {
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
  };

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
  }, []);

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge(params, eds)),
    [setEdges]
  );

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    // If clicking on a group, ignore or clear? Let's ignore groups for highlighting
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

  const getBounds = () => {
    if (nodes.length === 0) return undefined;
    let minX = Infinity,
      minY = Infinity,
      maxX = -Infinity,
      maxY = -Infinity;
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
    ] as [[number, number], [number, number]];
  };

  return (
    <div className="graph-container">
      <div className="vibe-header glass-panel">
        <Activity size={24} color="#6366f1" />
        <h2 style={{ margin: 0, fontSize: "18px", fontWeight: 700, letterSpacing: "-0.02em" }}>
          VIBEGRAPH <span style={{ color: "var(--text-dim)", fontWeight: 400 }}>MAP ROOM</span>
        </h2>

        <div style={{ position: "relative" }}>
          <Search
            size={16}
            style={{ position: "absolute", left: 12, top: 10, color: "var(--text-dim)" }}
          />
          <input
            className="vibe-input"
            type="text"
            placeholder="Search symbols..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>

        <button className="vibe-button" onClick={() => fetchGraph()} disabled={loading}>
          <RefreshCw size={16} className={loading ? "spin" : ""} />
          {loading ? "Syncing..." : "Sync Graph"}
        </button>

        <div className="stats-panel">
          <Layers size={14} /> <span>{nodes.filter((n) => n.type !== "group").length} Symbols</span>
          <div style={{ width: 1, height: 16, background: "var(--border-color)" }}></div>
          <Code size={14} /> <span>{edges.length} Edges</span>
          <div style={{ width: 1, height: 16, background: "var(--border-color)" }}></div>
          <span style={{ fontSize: "11px", opacity: 0.5 }}>Last Sync: {lastUpdate}</span>
        </div>
      </div>

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
        translateExtent={getBounds()}
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

        {loading && (
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              height: "100%",
              background: "rgba(26, 27, 35, 0.8)",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 20,
              backdropFilter: "blur(4px)",
              color: "#fff",
            }}
          >
            <RefreshCw size={48} className="spin" style={{ color: "#6366f1", marginBottom: 16 }} />
            <div style={{ fontSize: "18px", fontWeight: 600 }}>Syncing VibeGraph...</div>
            <div style={{ fontSize: "14px", opacity: 0.6, marginTop: 4 }}>
              Parsing AST & building relationships
            </div>
          </div>
        )}

        {error && (
          <div
            style={{
              position: "absolute",
              top: 20,
              left: "50%",
              transform: "translateX(-50%)",
              background: "rgba(239, 68, 68, 0.9)",
              color: "white",
              padding: "12px 24px",
              borderRadius: "8px",
              zIndex: 30,
              backdropFilter: "blur(4px)",
              border: "1px solid rgba(255,255,255,0.2)",
              display: "flex",
              alignItems: "center",
              gap: 12,
            }}
          >
            <div style={{ fontWeight: 600 }}>Error:</div>
            <div>{error}</div>
            <button
              onClick={() => fetchGraph()}
              style={{
                background: "rgba(255,255,255,0.2)",
                border: "none",
                borderRadius: 4,
                padding: "4px 8px",
                color: "white",
                cursor: "pointer",
                marginLeft: 12,
              }}
            >
              Retry
            </button>
          </div>
        )}

        <Panel
          position="top-right"
          className="glass-panel"
          style={{
            padding: "12px",
            display: "flex",
            flexDirection: "column",
            gap: 8,
            marginTop: "80px",
          }}
        >
          <div
            style={{
              fontSize: "12px",
              fontWeight: 600,
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <Filter size={12} /> Filters
          </div>
          {["function", "class", "interface", "module"].map((kind) => (
            <label
              key={kind}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: "12px",
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={activeFilters.has(kind)}
                onChange={() => toggleFilter(kind)}
              />
              <span style={{ textTransform: "capitalize" }}>{kind}</span>
            </label>
          ))}
          <div
            style={{
              borderTop: "1px solid var(--border-color)",
              margin: "4px 0",
              paddingTop: "8px",
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: "12px",
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={showPrivate}
                onChange={() => setShowPrivate(!showPrivate)}
              />
              <span>Show Private</span>
            </label>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: "12px",
                cursor: "pointer",
                color: impactMode ? "#f87171" : "inherit",
              }}
            >
              <input
                type="checkbox"
                checked={impactMode}
                onChange={() => setImpactMode(!impactMode)}
              />
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <Activity size={12} /> Deep Impact Mode
              </span>
            </label>
          </div>
        </Panel>

        <Panel
          position="bottom-right"
          className="glass-panel"
          style={{ padding: "8px 12px", marginBottom: 20, marginRight: 20 }}
        >
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-dim)",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <span
              style={{
                display: "inline-block",
                width: 8,
                height: 8,
                background: "#6366f1",
                borderRadius: 2,
              }}
            ></span>{" "}
            Function
            <span
              style={{
                display: "inline-block",
                width: 8,
                height: 8,
                background: "#a855f7",
                borderRadius: 2,
              }}
            ></span>{" "}
            Class
            <span
              style={{
                display: "inline-block",
                width: 8,
                height: 8,
                background: "#10b981",
                borderRadius: 2,
              }}
            ></span>{" "}
            Interface
          </div>
        </Panel>
      </ReactFlow>
    </div>
  );
}
