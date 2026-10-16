import { Radio } from "lucide-react";
import type React from "react";
import type { AgentFocusEvent } from "../types";

interface AgentRadarBadgeProps {
  followAgent: boolean;
  onToggleFollow: (enabled: boolean) => void;
  lastAgentEvent: AgentFocusEvent | null;
}

export const AgentRadarBadge: React.FC<AgentRadarBadgeProps> = ({
  followAgent,
  onToggleFollow,
  lastAgentEvent,
}) => {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <button
        type="button"
        onClick={() => onToggleFollow(!followAgent)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "6px 12px",
          borderRadius: 999,
          fontSize: "12px",
          fontWeight: 600,
          cursor: "pointer",
          border: followAgent ? "1px solid #10b981" : "1px solid var(--border-color)",
          background: followAgent ? "rgba(16, 185, 129, 0.16)" : "rgba(255,255,255,0.04)",
          color: followAgent ? "#6ee7b7" : "var(--text-dim)",
        }}
        title="Automatically focus and highlight symbols queried by AI Agents via MCP/CLI"
      >
        <Radio size={14} color={followAgent ? "#10b981" : "#94a3b8"} />
        <span>Follow AI Agent</span>
      </button>

      {followAgent && lastAgentEvent && (
        <span
          style={{
            fontSize: "11px",
            color: "#6ee7b7",
            background: "rgba(16, 185, 129, 0.1)",
            border: "1px solid rgba(16, 185, 129, 0.3)",
            padding: "4px 8px",
            borderRadius: 6,
            maxWidth: 240,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={lastAgentEvent.summary}
        >
          AI: {lastAgentEvent.summary}
        </span>
      )}
    </div>
  );
};
