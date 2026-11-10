import { useViewport } from "@xyflow/react";
import type React from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { RawGraphEdge, RawGraphNode, TracePathResponse } from "../types";
import {
  type GPUEdgeData,
  type GPUNodeData,
  type RendererBackend,
  WebGPURenderer,
} from "../utils/webgpuRenderer";

interface WebGPUCanvasProps {
  rawNodes: RawGraphNode[];
  rawEdges: RawGraphEdge[];
  nodePositions?: Map<string, { x: number; y: number }>;
  activeTrace: TracePathResponse | null;
  onSelectNode?: (nodeId: string) => void;
  onRendererStatusChange?: (backend: RendererBackend, fps: number) => void;
}

const KIND_COLORS: Record<string, [number, number, number, number]> = {
  class: [0.658, 0.333, 0.968, 0.9], // #a855f7
  interface: [0.062, 0.725, 0.505, 0.9], // #10b981
  endpoint: [0.96, 0.619, 0.043, 0.9], // #f59e0b
  module: [0.231, 0.51, 0.964, 0.85], // #3b82f6
  function: [0.388, 0.4, 0.945, 0.85], // #6366f1
  method: [0.55, 0.45, 0.95, 0.85],
};

const DEFAULT_COLOR: [number, number, number, number] = [0.388, 0.4, 0.945, 0.8];

export const WebGPUCanvas: React.FC<WebGPUCanvasProps> = ({
  rawNodes,
  rawEdges,
  nodePositions,
  activeTrace,
  onSelectNode,
  onRendererStatusChange,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rendererRef = useRef<WebGPURenderer | null>(null);
  const viewport = useViewport();
  const [hoveredNode, setHoveredNode] = useState<GPUNodeData | null>(null);

  const traceNodeIds = useMemo(() => {
    if (!activeTrace?.node_ids) return new Set<string>();
    return new Set(activeTrace.node_ids);
  }, [activeTrace]);

  const traceEdgeKeys = useMemo(() => {
    if (!activeTrace?.hops) return new Set<string>();
    const keys = new Set<string>();
    for (const hop of activeTrace.hops) {
      keys.add(`${hop.from_node.id}->${hop.to_node.id}`);
    }
    return keys;
  }, [activeTrace]);

  // Transform nodes into GPU buffer format
  const gpuNodes = useMemo<GPUNodeData[]>(() => {
    return rawNodes.map((n, idx) => {
      const pos = nodePositions?.get(n.id) || {
        x: (idx % 20) * 160 + ((idx / 20) | 0) * 40,
        y: Math.floor(idx / 20) * 120,
      };
      const kindColor = KIND_COLORS[n.kind.toLowerCase()] || DEFAULT_COLOR;
      const isTrace = traceNodeIds.has(n.id);
      const span = Math.max(1, n.end_line - n.start_line);
      const radius = isTrace ? 12 : Math.max(6, Math.min(18, span * 0.35 + 5));

      return {
        id: n.id,
        x: pos.x,
        y: pos.y,
        radius,
        kind: n.kind,
        color: isTrace ? [0.937, 0.266, 0.266, 1.0] : kindColor,
        isTrace,
        label: n.name || n.id,
      };
    });
  }, [rawNodes, nodePositions, traceNodeIds]);

  // Lookup map for fast edge coordinate resolution
  const nodeCoordMap = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    for (const n of gpuNodes) {
      map.set(n.id, { x: n.x, y: n.y });
    }
    return map;
  }, [gpuNodes]);

  // Transform edges into GPU buffer format
  const gpuEdges = useMemo<GPUEdgeData[]>(() => {
    const edges: GPUEdgeData[] = [];
    for (const e of rawEdges) {
      const src = nodeCoordMap.get(e.from_node_id);
      const tgt = nodeCoordMap.get(e.to_node_id);
      if (!src || !tgt) continue;

      const isTrace = traceEdgeKeys.has(`${e.from_node_id}->${e.to_node_id}`);
      edges.push({
        id: `${e.from_node_id}->${e.to_node_id}`,
        x1: src.x,
        y1: src.y,
        x2: tgt.x,
        y2: tgt.y,
        relationType: e.relation_type,
        color: isTrace ? [0.937, 0.266, 0.266, 1.0] : [0.4, 0.45, 0.55, 0.35],
        isTrace,
      });
    }
    return edges;
  }, [rawEdges, nodeCoordMap, traceEdgeKeys]);

  // Initialize renderer
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const renderer = new WebGPURenderer(canvas);
    rendererRef.current = renderer;

    renderer
      .initialize()
      .then((backend) => {
        onRendererStatusChange?.(backend, renderer.getFps());
      })
      .catch((err) => {
        console.warn("WebGPU initialization failed, falling back to 2D canvas:", err);
        onRendererStatusChange?.("canvas2d", 0);
      });

    const handleResize = () => {
      if (!canvas) return;
      const rect = canvas.parentElement?.getBoundingClientRect() || {
        width: window.innerWidth,
        height: window.innerHeight,
      };
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(rect.width * dpr);
      canvas.height = Math.round(rect.height * dpr);
    };
    handleResize();
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      renderer.dispose();
      rendererRef.current = null;
    };
  }, [onRendererStatusChange]);

  // Animation frame render loop
  useEffect(() => {
    let animId: number;

    const loop = (time: number) => {
      const renderer = rendererRef.current;
      if (renderer) {
        renderer.render(gpuNodes, gpuEdges, viewport, time);
        onRendererStatusChange?.(renderer.getBackend(), renderer.getFps());
      }
      animId = requestAnimationFrame(loop);
    };

    animId = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(animId);
  }, [gpuNodes, gpuEdges, viewport, onRendererStatusChange]);

  // Mouse hit testing
  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    const renderer = rendererRef.current;
    if (!canvas || !renderer) return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const hit = renderer.hitTest(mouseX, mouseY, viewport, gpuNodes);
    setHoveredNode(hit);
  };

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    const renderer = rendererRef.current;
    if (!canvas || !renderer) return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const hit = renderer.hitTest(mouseX, mouseY, viewport, gpuNodes);
    if (hit) {
      onSelectNode?.(hit.id);
    }
  };

  return (
    <div style={{ position: "absolute", inset: 0, pointerEvents: "auto", zIndex: 1 }}>
      <canvas
        ref={canvasRef}
        onMouseMove={handleMouseMove}
        onClick={handleClick}
        style={{
          width: "100%",
          height: "100%",
          display: "block",
          cursor: hoveredNode ? "pointer" : "default",
        }}
      />
      {hoveredNode && (
        <div
          style={{
            position: "absolute",
            left: hoveredNode.x * viewport.zoom + viewport.x + 15,
            top: hoveredNode.y * viewport.zoom + viewport.y - 15,
            backgroundColor: "rgba(15, 23, 42, 0.95)",
            border: "1px solid rgba(99, 102, 241, 0.5)",
            borderRadius: 6,
            padding: "4px 8px",
            color: "#ffffff",
            fontSize: 12,
            fontFamily: "Inter, sans-serif",
            pointerEvents: "none",
            boxShadow: "0 4px 12px rgba(0, 0, 0, 0.5)",
            zIndex: 10,
          }}
        >
          <div style={{ fontWeight: 600 }}>{hoveredNode.label}</div>
          <div style={{ color: "#94a3b8", fontSize: 10 }}>Kind: {hoveredNode.kind}</div>
        </div>
      )}
    </div>
  );
};
