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
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
      <button
        type="button"
        onClick={() => onToggleFollow(!followAgent)}
        className={`vibe-btn ${followAgent ? "vibe-btn-agent-active" : ""}`}
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
            padding: "0 8px",
            height: "var(--control-height)",
            display: "inline-flex",
            alignItems: "center",
            borderRadius: "var(--radius-md)",
            maxWidth: 220,
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
