import { useEffect, useRef, useState } from "react";
import type { AgentFocusEvent } from "../types";

export interface UseVibeSyncOptions {
  wsUrl: string;
  onRefresh: () => void;
}

export function useVibeSync({ wsUrl, onRefresh }: UseVibeSyncOptions) {
  const [lastAgentEvent, setLastAgentEvent] = useState<AgentFocusEvent | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let isCancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connectWS = () => {
      if (isCancelled) return;
      wsRef.current = new WebSocket(wsUrl);
      wsRef.current.onopen = () => console.log("Vibe-Sync Active");
      wsRef.current.onmessage = (event) => {
        if (event.data === "refresh") {
          onRefresh();
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
      wsRef.current.onclose = () => {
        if (!isCancelled) {
          reconnectTimer = setTimeout(connectWS, 3000);
        }
      };
    };

    connectWS();
    return () => {
      isCancelled = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      wsRef.current?.close();
    };
  }, [wsUrl, onRefresh]);

  return { lastAgentEvent, setLastAgentEvent, wsRef };
}
