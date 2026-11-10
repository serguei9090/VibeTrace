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

// Minimal structural WebGPU interfaces to avoid external ambient type dependency
export interface WebGPUDeviceLike {
  destroy(): void;
}

export interface WebGPUAdapterLike {
  requestDevice(): Promise<WebGPUDeviceLike>;
}

export interface WebGPUNavigatorLike {
  requestAdapter(): Promise<WebGPUAdapterLike | null>;
}

export class WebGPURenderer {
  private canvas: HTMLCanvasElement;
  private backend: RendererBackend = "canvas2d";
  private ctx2d: CanvasRenderingContext2D | null = null;
  private gl: WebGL2RenderingContext | null = null;
  private glProgram: WebGLProgram | null = null;
  private glEdgeBuffer: WebGLBuffer | null = null;
  private glNodeBuffer: WebGLBuffer | null = null;
  private glLocViewport: WebGLUniformLocation | null = null;
  private glLocResolution: WebGLUniformLocation | null = null;
  private glLocDpr: WebGLUniformLocation | null = null;
  private gpuDevice: WebGPUDeviceLike | null = null;
  private fps = 60;
  private frameCount = 0;
  private lastFpsUpdate = performance.now();

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
  }

  public getDevice(): WebGPUDeviceLike | null {
    return this.gpuDevice;
  }

  public dispose(): void {
    if (this.gpuDevice) {
      this.gpuDevice.destroy();
      this.gpuDevice = null;
    }
    if (this.gl) {
      if (this.glEdgeBuffer) this.gl.deleteBuffer(this.glEdgeBuffer);
      if (this.glNodeBuffer) this.gl.deleteBuffer(this.glNodeBuffer);
      if (this.glProgram) this.gl.deleteProgram(this.glProgram);
      const ext = this.gl.getExtension("WEBGL_lose_context");
      ext?.loseContext();
      this.gl = null;
    }
    this.ctx2d = null;
    this.backend = "off";
  }

  private initWebGLShaders(gl: WebGL2RenderingContext): boolean {
    const vsSource = `#version 300 es
      precision mediump float;
      layout(location = 0) in vec2 a_position;
      layout(location = 1) in vec4 a_color;
      layout(location = 2) in float a_pointSize;

      uniform vec3 u_viewport;   // vec3(x, y, zoom)
      uniform vec2 u_resolution; // vec2(width, height) in backing store pixels
      uniform float u_dpr;       // device pixel ratio

      out vec4 v_color;

      void main() {
        // Transform graph space coordinate (x, y) by viewport (pan & zoom), scaled by DPR
        vec2 screenPos = (a_position * u_viewport.z + u_viewport.xy) * u_dpr;
        // Convert screen coordinates [0, width], [0, height] to WebGL clip space [-1, 1]
        vec2 zeroToOne = screenPos / u_resolution;
        vec2 zeroToTwo = zeroToOne * 2.0;
        vec2 clipSpace = vec2(zeroToTwo.x - 1.0, 1.0 - zeroToTwo.y);

        gl_Position = vec4(clipSpace, 0.0, 1.0);
        gl_PointSize = max(2.0, a_pointSize * u_viewport.z * u_dpr);
        v_color = a_color;
      }
    `;

    const fsSource = `#version 300 es
      precision mediump float;
      in vec4 v_color;
      out vec4 fragColor;

      void main() {
        fragColor = v_color;
      }
    `;

    const createShader = (type: number, source: string) => {
      const shader = gl.createShader(type);
      if (!shader) return null;
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        console.warn("Shader compile error:", gl.getShaderInfoLog(shader));
        gl.deleteShader(shader);
        return null;
      }
      return shader;
    };

    const vs = createShader(gl.VERTEX_SHADER, vsSource);
    const fs = createShader(gl.FRAGMENT_SHADER, fsSource);
    if (!vs || !fs) return false;

    const program = gl.createProgram();
    if (!program) return false;
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);

    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      console.warn("Program link error:", gl.getProgramInfoLog(program));
      gl.deleteProgram(program);
      return false;
    }

    this.glProgram = program;
    this.glLocViewport = gl.getUniformLocation(program, "u_viewport");
    this.glLocResolution = gl.getUniformLocation(program, "u_resolution");
    this.glLocDpr = gl.getUniformLocation(program, "u_dpr");
    this.glEdgeBuffer = gl.createBuffer();
    this.glNodeBuffer = gl.createBuffer();
    return true;
  }

  public async initialize(): Promise<RendererBackend> {
    // 1. Attempt WebGPU initialization (currently probe / adapter discovery)
    if (typeof navigator !== "undefined" && "gpu" in navigator) {
      try {
        const gpu = (navigator as unknown as { gpu?: WebGPUNavigatorLike }).gpu;
        const adapter = await gpu?.requestAdapter();
        if (adapter) {
          const device = await adapter.requestDevice();
          if (device) {
            this.gpuDevice = device;
            // WebGPU shaders are queued for future WGSL pipeline; route to WebGL2 for active GPU rendering
          }
        }
      } catch (e) {
        console.warn("WebGPU initialization probe failed, proceeding to WebGL2:", e);
      }
    }

    // 2. Hardware-accelerated WebGL2 batch pipeline
    const gl = this.canvas.getContext("webgl2", { alpha: true, antialias: true });
    if (gl && this.initWebGLShaders(gl)) {
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

    // Account for backing store device pixel ratio (HiDPI / Retina)
    const dpr = Math.min(typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1, 2);
    ctx.scale(dpr, dpr);

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
    const gl = this.gl;
    if (!gl || !this.glProgram || !this.glEdgeBuffer || !this.glNodeBuffer) return;

    const width = this.canvas.width;
    const height = this.canvas.height;
    gl.viewport(0, 0, width, height);
    gl.clearColor(0.05, 0.05, 0.07, 0.0);
    gl.clear(gl.COLOR_BUFFER_BIT);

    // Enable alpha blending
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    // biome-ignore lint/correctness/useHookAtTopLevel: gl.useProgram is a WebGL context method, not a React hook
    gl.useProgram(this.glProgram);
    if (this.glLocViewport) {
      gl.uniform3f(this.glLocViewport, viewport.x, viewport.y, viewport.zoom);
    }
    if (this.glLocResolution) {
      gl.uniform2f(this.glLocResolution, width, height);
    }
    if (this.glLocDpr) {
      const dpr = Math.min(typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1, 2);
      gl.uniform1f(this.glLocDpr, dpr);
    }

    // 1. Draw Batched Edges (gl.LINES: 2 vertices per edge, 7 floats per vertex: x, y, r, g, b, a, size)
    if (edges.length > 0) {
      const edgeFloats = new Float32Array(edges.length * 2 * 7);
      let offset = 0;
      for (let i = 0; i < edges.length; i++) {
        const e = edges[i];
        let [r, g, b, a] = e.color;
        if (e.isTrace) {
          const pulse = Math.sin(timeMs * 0.005 + i) * 0.3 + 0.7;
          r = 0.94;
          g = 0.27;
          b = 0.27;
          a = pulse;
        }

        // Vertex 1
        edgeFloats[offset++] = e.x1;
        edgeFloats[offset++] = e.y1;
        edgeFloats[offset++] = r;
        edgeFloats[offset++] = g;
        edgeFloats[offset++] = b;
        edgeFloats[offset++] = a;
        edgeFloats[offset++] = 1.0;

        // Vertex 2
        edgeFloats[offset++] = e.x2;
        edgeFloats[offset++] = e.y2;
        edgeFloats[offset++] = r;
        edgeFloats[offset++] = g;
        edgeFloats[offset++] = b;
        edgeFloats[offset++] = a;
        edgeFloats[offset++] = 1.0;
      }

      gl.bindBuffer(gl.ARRAY_BUFFER, this.glEdgeBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, edgeFloats, gl.DYNAMIC_DRAW);

      // Stride: 7 floats * 4 bytes = 28 bytes
      const stride = 7 * 4;
      gl.enableVertexAttribArray(0); // a_position
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, stride, 0);

      gl.enableVertexAttribArray(1); // a_color
      gl.vertexAttribPointer(1, 4, gl.FLOAT, false, stride, 2 * 4);

      gl.enableVertexAttribArray(2); // a_pointSize
      gl.vertexAttribPointer(2, 1, gl.FLOAT, false, stride, 6 * 4);

      gl.drawArrays(gl.LINES, 0, edges.length * 2);
    }

    // 2. Draw Batched Nodes (gl.POINTS: 1 vertex per node, 7 floats: x, y, r, g, b, a, radius*2)
    if (nodes.length > 0) {
      const nodeFloats = new Float32Array(nodes.length * 7);
      let offset = 0;
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        let [r, g, b, a] = n.color;
        let size = n.radius * 2;
        if (n.isTrace) {
          const pulse = Math.sin(timeMs * 0.008) * 0.4 + 0.6;
          r = 0.94;
          g = 0.27;
          b = 0.27;
          a = pulse;
          size *= 1.3;
        }

        nodeFloats[offset++] = n.x;
        nodeFloats[offset++] = n.y;
        nodeFloats[offset++] = r;
        nodeFloats[offset++] = g;
        nodeFloats[offset++] = b;
        nodeFloats[offset++] = a;
        nodeFloats[offset++] = size;
      }

      gl.bindBuffer(gl.ARRAY_BUFFER, this.glNodeBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, nodeFloats, gl.DYNAMIC_DRAW);

      const stride = 7 * 4;
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, stride, 0);

      gl.enableVertexAttribArray(1);
      gl.vertexAttribPointer(1, 4, gl.FLOAT, false, stride, 2 * 4);

      gl.enableVertexAttribArray(2);
      gl.vertexAttribPointer(2, 1, gl.FLOAT, false, stride, 6 * 4);

      gl.drawArrays(gl.POINTS, 0, nodes.length);
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
