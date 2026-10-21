import { Activity, Code, Layers, RefreshCw, Search, X } from "lucide-react";
import type React from "react";
import type { AgentFocusEvent, LayoutMode, TracePathResponse } from "../types";
import { AgentRadarBadge } from "./AgentRadarBadge";

interface MapHeaderProps {
  filter: string;
  onFilterChange: (val: string) => void;
  loading: boolean;
  onRefresh: () => void;
  totalSymbols: number;
  totalEdges: number;
  visibleSymbols: number;
  layoutMode: LayoutMode;
  activeTrace: TracePathResponse | null;
  onResetTrace?: () => void;
  lastUpdate: string;
  followAgent: boolean;
  onToggleFollowAgent: (enabled: boolean) => void;
  lastAgentEvent: AgentFocusEvent | null;
}

export const MapHeader: React.FC<MapHeaderProps> = ({
  filter,
  onFilterChange,
  loading,
  onRefresh,
  totalSymbols,
  totalEdges,
  visibleSymbols,
  layoutMode,
  activeTrace,
  onResetTrace,
  lastUpdate,
  followAgent,
  onToggleFollowAgent,
  lastAgentEvent,
}) => {
  return (
    <div className="vibe-header glass-panel">
      <Activity size={24} color="#6366f1" />
      <h2
        style={{
          margin: 0,
          fontSize: "17px",
          fontWeight: 700,
          letterSpacing: "-0.02em",
          display: "flex",
          alignItems: "center",
          gap: "8px",
        }}
      >
        <span>VIBETRACE</span>
        <span
          style={{
            fontSize: "11px",
            fontWeight: 600,
            color: "#a5b4fc",
            background: "rgba(99, 102, 241, 0.15)",
            padding: "2px 8px",
            borderRadius: "6px",
            border: "1px solid rgba(99, 102, 241, 0.3)",
            letterSpacing: "0.05em",
          }}
        >
          {layoutMode === "trace"
            ? "TRACEPATH ISOLATED"
            : layoutMode === "impact"
              ? "BLAST RADIUS"
              : "ARCHITECTURE MAP"}
        </span>
      </h2>

      <div style={{ position: "relative" }}>
        <Search
          size={16}
          style={{
            position: "absolute",
            left: 12,
            top: 10,
            color: "var(--text-dim)",
          }}
        />
        <input
          className="vibe-input"
          type="text"
          placeholder="Search symbols..."
          value={filter}
          onChange={(e) => onFilterChange(e.target.value)}
        />
      </div>

      <button type="button" className="vibe-button" onClick={onRefresh} disabled={loading}>
        <RefreshCw size={16} className={loading ? "spin" : ""} />
        {loading ? "Syncing..." : "Sync Graph"}
      </button>

      {activeTrace?.found && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            background: "rgba(99, 102, 241, 0.2)",
            border: "1px solid rgba(99, 102, 241, 0.45)",
            borderRadius: 8,
            padding: "6px 12px",
            fontSize: "12px",
            color: "#e0e7ff",
          }}
        >
          <span style={{ fontWeight: 600, color: "#818cf8" }}>Trace Active:</span>
          <span>
            {activeTrace.from_symbol} ➔ {activeTrace.to_symbol}
          </span>
          <span style={{ opacity: 0.7, fontSize: "11px" }}>
            ({activeTrace.hops.length} hop{activeTrace.hops.length > 1 ? "s" : ""})
          </span>
          {onResetTrace && (
            <button
              type="button"
              onClick={onResetTrace}
              title="Clear Trace (Return to Architecture Map)"
              style={{
                background: "rgba(255, 255, 255, 0.1)",
                border: "none",
                borderRadius: 4,
                color: "#fff",
                cursor: "pointer",
                padding: "2px 4px",
                display: "flex",
                alignItems: "center",
                marginLeft: 4,
              }}
            >
              <X size={12} />
            </button>
          )}
        </div>
      )}

      <AgentRadarBadge
        followAgent={followAgent}
        onToggleFollow={onToggleFollowAgent}
        lastAgentEvent={lastAgentEvent}
      />

      <div className="stats-panel">
        <Layers size={14} style={{ color: "#6366f1" }} />
        <span>
          {visibleSymbols < totalSymbols && visibleSymbols > 0
            ? `Showing ${visibleSymbols} of ${totalSymbols} Symbols`
            : `${totalSymbols || visibleSymbols} Symbols`}
        </span>
        <div
          style={{
            width: 1,
            height: 16,
            background: "var(--border-color)",
          }}
        />
        <Code size={14} style={{ color: "#a855f7" }} /> <span>{totalEdges} Edges</span>
        <div
          style={{
            width: 1,
            height: 16,
            background: "var(--border-color)",
          }}
        />
        <span style={{ fontSize: "11px", opacity: 0.6 }}>Last Sync: {lastUpdate}</span>
      </div>
    </div>
  );
};
