import { useEffect, useRef, useState } from "react";
import type { AgentFocusEvent } from "../types";

export interface UseVibeSyncOptions {
  wsUrl: string;
  onRefresh: () => void;
}

export function useVibeSync({ wsUrl, onRefresh }: UseVibeSyncOptions) {
  const [lastAgentEvent, setLastAgentEvent] = useState<AgentFocusEvent | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const onRefreshRef = useRef(onRefresh);

  useEffect(() => {
    onRefreshRef.current = onRefresh;
  });

  useEffect(() => {
    let isCancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    let retryCount = 0;

    const connectWS = () => {
      if (isCancelled) return;
      wsRef.current = new WebSocket(wsUrl);
      wsRef.current.onopen = () => {
        retryCount = 0;
        console.log("Vibe-Sync Active");
      };
      wsRef.current.onmessage = (event) => {
        if (event.data === "refresh") {
          onRefreshRef.current();
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
          const delay = Math.min(1000 * 2 ** retryCount, 15000);
          retryCount++;
          reconnectTimer = setTimeout(connectWS, delay);
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
  }, [wsUrl]);

  return { lastAgentEvent, setLastAgentEvent, wsRef };
}
