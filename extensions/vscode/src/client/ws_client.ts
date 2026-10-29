export type RefreshCallback = () => void;
export type AgentFocusCallback = (payload: {
  event_type: string;
  node_ids: string[];
  summary: string;
  from_symbol?: string;
  to_symbol?: string;
}) => void;
export type ConnectionStatusCallback = (connected: boolean) => void;

export class VibeTraceWsClient {
  private url: string;
  private ws: WebSocket | null = null;
  private isDisposed = false;
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  private refreshListeners: Set<RefreshCallback> = new Set();
  private agentFocusListeners: Set<AgentFocusCallback> = new Set();
  private connectionListeners: Set<ConnectionStatusCallback> = new Set();

  constructor(url = "ws://127.0.0.1:8000/ws") {
    this.url = url;
  }

  public updateUrl(url: string): void {
    if (this.url !== url) {
      this.url = url;
      this.reconnect();
    }
  }

  public connect(): void {
    if (this.isDisposed || this.ws) {
      return;
    }

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this.reconnectAttempts = 0;
        this.notifyConnectionStatus(true);
      };

      this.ws.onmessage = (event) => {
        const raw = String(event.data);
        if (raw === "refresh") {
          for (const listener of this.refreshListeners) {
            try {
              listener();
            } catch {
              // Ignore subscriber error
            }
          }
          return;
        }

        try {
          const parsed = JSON.parse(raw);
          if (parsed.type === "agent_focus") {
            for (const listener of this.agentFocusListeners) {
              try {
                listener(parsed);
              } catch {
                // Ignore subscriber error
              }
            }
          }
        } catch {
          // Non-JSON message, ignore
        }
      };

      this.ws.onclose = () => {
        this.cleanupSocket();
        this.notifyConnectionStatus(false);
        this.scheduleReconnect();
      };

      this.ws.onerror = () => {
        this.cleanupSocket();
        this.notifyConnectionStatus(false);
        this.scheduleReconnect();
      };
    } catch {
      this.cleanupSocket();
      this.scheduleReconnect();
    }
  }

  public reconnect(): void {
    this.cleanupSocket();
    this.connect();
  }

  public onRefresh(cb: RefreshCallback): () => void {
    this.refreshListeners.add(cb);
    return () => this.refreshListeners.delete(cb);
  }

  public onAgentFocus(cb: AgentFocusCallback): () => void {
    this.agentFocusListeners.add(cb);
    return () => this.agentFocusListeners.delete(cb);
  }

  public onConnectionChange(cb: ConnectionStatusCallback): () => void {
    this.connectionListeners.add(cb);
    return () => this.connectionListeners.delete(cb);
  }

  public isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  public dispose(): void {
    this.isDisposed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.cleanupSocket();
    this.refreshListeners.clear();
    this.agentFocusListeners.clear();
    this.connectionListeners.clear();
  }

  private cleanupSocket(): void {
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        // Safe close
      }
      this.ws = null;
    }
  }

  private notifyConnectionStatus(connected: boolean): void {
    for (const listener of this.connectionListeners) {
      try {
        listener(connected);
      } catch {
        // Safe notify
      }
    }
  }

  private scheduleReconnect(): void {
    if (this.isDisposed || this.reconnectTimer) {
      return;
    }
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 15000);
    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }
}
