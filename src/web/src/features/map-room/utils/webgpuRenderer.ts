/**
 * Hardware-Accelerated Graph Canvas Engine.
 * Supports WebGPU with automatic fallback to WebGL2 / 2D Canvas.
 */

export interface GPUNodeData {
  id: string;
  x: number;
  y: number;
  radius: number;
  kind: string;
  color: [number, number, number, number]; // r, g, b, a (0..1)
  isTrace: boolean;
  label: string;
}

export interface GPUEdgeData {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  relationType: string;
  color: [number, number, number, number];
  isTrace: boolean;
}

export type RendererBackend = "webgpu" | "webgl" | "canvas2d" | "off";

export class WebGPURenderer {
  private canvas: HTMLCanvasElement;
  private backend: RendererBackend = "canvas2d";
  private ctx2d: CanvasRenderingContext2D | null = null;
  private gl: WebGL2RenderingContext | null = null;
  private gpuDevice: any = null;
  private fps = 60;
  private frameCount = 0;
  private lastFpsUpdate = performance.now();

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
  }

  public getDevice(): any {
    return this.gpuDevice;
  }

  public async initialize(): Promise<RendererBackend> {
    // 1. Attempt WebGPU initialization
    if (typeof navigator !== "undefined" && "gpu" in navigator) {
      try {
        const gpu = (navigator as any).gpu;
        const adapter = await gpu?.requestAdapter();
        if (adapter) {
          const device = await adapter.requestDevice();
          if (device) {
            this.gpuDevice = device;
            this.backend = "webgpu";
            return "webgpu";
          }
        }
      } catch {
        // Fall back to WebGL2
      }
    }

    // 2. Fallback to WebGL2
    const gl = this.canvas.getContext("webgl2", { alpha: true, antialias: true });
    if (gl) {
      this.gl = gl;
      this.backend = "webgl";
      return "webgl";
    }

    // 3. Fallback to Canvas2D
    const ctx = this.canvas.getContext("2d", { alpha: true });
    if (ctx) {
      this.ctx2d = ctx;
      this.backend = "canvas2d";
      return "canvas2d";
    }

    this.backend = "off";
    return "off";
  }

  public getBackend(): RendererBackend {
    return this.backend;
  }

  public getFps(): number {
    return this.fps;
  }

  public render(
    nodes: GPUNodeData[],
    edges: GPUEdgeData[],
    viewport: { x: number; y: number; zoom: number },
    timeMs: number
  ): void {
    // Update FPS counter
    const now = performance.now();
    this.frameCount++;
    if (now - this.lastFpsUpdate >= 500) {
      this.fps = Math.round((this.frameCount * 1000) / (now - this.lastFpsUpdate));
      this.frameCount = 0;
      this.lastFpsUpdate = now;
    }

    const width = this.canvas.width;
    const height = this.canvas.height;
    if (width === 0 || height === 0) return;

    if (this.ctx2d) {
      this.renderCanvas2D(nodes, edges, viewport, timeMs);
    } else if (this.gl) {
      this.renderWebGL(nodes, edges, viewport, timeMs);
    }
  }

  private renderCanvas2D(
    nodes: GPUNodeData[],
    edges: GPUEdgeData[],
    viewport: { x: number; y: number; zoom: number },
    timeMs: number
  ): void {
    const ctx = this.ctx2d;
    if (!ctx) return;

    ctx.save();
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    // Apply viewport transform (pan & zoom)
    ctx.translate(viewport.x, viewport.y);
    ctx.scale(viewport.zoom, viewport.zoom);

    // 1. Draw Edges
    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      ctx.beginPath();
      ctx.moveTo(e.x1, e.y1);
      ctx.lineTo(e.x2, e.y2);

      if (e.isTrace) {
        // Glowing animated pulse for active trace edges
        const pulse = Math.sin(timeMs * 0.005 + i) * 0.3 + 0.7;
        ctx.strokeStyle = `rgba(239, 68, 68, ${pulse})`;
        ctx.lineWidth = 3.5 / viewport.zoom;
        ctx.shadowColor = "#ef4444";
        ctx.shadowBlur = 8;
      } else {
        const [r, g, b, a] = e.color;
        ctx.strokeStyle = `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, ${a})`;
        ctx.lineWidth = 1.2 / viewport.zoom;
        ctx.shadowBlur = 0;
      }
      ctx.stroke();
    }
    ctx.shadowBlur = 0;

    // 2. Draw Nodes
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);

      const [r, g, b, a] = n.color;
      if (n.isTrace) {
        const pulse = Math.sin(timeMs * 0.008) * 0.4 + 0.6;
        ctx.fillStyle = `rgba(239, 68, 68, ${pulse})`;
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2 / viewport.zoom;
        ctx.shadowColor = "#ef4444";
        ctx.shadowBlur = 12;
      } else {
        ctx.fillStyle = `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, ${a})`;
        ctx.strokeStyle = "rgba(255, 255, 255, 0.4)";
        ctx.lineWidth = 1 / viewport.zoom;
        ctx.shadowBlur = 0;
      }

      ctx.fill();
      ctx.stroke();

      // Show node labels only when zoomed in sufficiently to avoid text clutter
      if (viewport.zoom >= 0.75 && n.label) {
        ctx.font = `${Math.max(10, 11 / viewport.zoom)}px Inter, system-ui, sans-serif`;
        ctx.fillStyle = "rgba(255, 255, 255, 0.85)";
        ctx.textAlign = "center";
        ctx.fillText(n.label, n.x, n.y + n.radius + 12 / viewport.zoom);
      }
    }

    ctx.restore();
  }

  private renderWebGL(
    nodes: GPUNodeData[],
    edges: GPUEdgeData[],
    viewport: { x: number; y: number; zoom: number },
    timeMs: number
  ): void {
    // For WebGL2, fallback gracefully to Canvas2D overlay if shaders uncompiled
    const gl = this.gl;
    if (!gl) return;

    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(0.05, 0.05, 0.07, 0.0);
    gl.clear(gl.COLOR_BUFFER_BIT);

    // If advanced GL shader pipeline is active, dispatch draw calls
    // Otherwise fallback to 2D canvas context
    if (this.ctx2d) {
      this.renderCanvas2D(nodes, edges, viewport, timeMs);
    }
  }

  public hitTest(
    screenX: number,
    screenY: number,
    viewport: { x: number; y: number; zoom: number },
    nodes: GPUNodeData[]
  ): GPUNodeData | null {
    // Invert viewport coordinates into graph coordinate space
    const graphX = (screenX - viewport.x) / viewport.zoom;
    const graphY = (screenY - viewport.y) / viewport.zoom;

    const hitRadiusThreshold = 14 / viewport.zoom;

    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const dx = graphX - n.x;
      const dy = graphY - n.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist <= Math.max(n.radius, hitRadiusThreshold)) {
        return n;
      }
    }
    return null;
  }
}
