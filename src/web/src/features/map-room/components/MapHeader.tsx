import {
  Activity,
  BookOpen,
  ChevronRight,
  Code,
  Layers,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import React, { useMemo } from "react";
import type {
  AgentFocusEvent,
  LayoutMode,
  RawGraphEdge,
  RawGraphNode,
  SelectedElement,
  TracePathResponse,
  ViewMode,
} from "../types";
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
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
  onResetToRoots: () => void;
  isSidebarOpen: boolean;
  onToggleSidebar: () => void;
  activeTrace: TracePathResponse | null;
  onResetTrace?: () => void;
  lastUpdate: string;
  followAgent: boolean;
  onToggleFollowAgent: (enabled: boolean) => void;
  lastAgentEvent: AgentFocusEvent | null;
  onToggleDocs?: () => void;
  isDocsOpen?: boolean;
  onToggleReview?: () => void;
  isReviewOpen?: boolean;
  selectedElement?: SelectedElement | null;
  rawNodes?: RawGraphNode[];
  rawEdges?: RawGraphEdge[];
  revealedNodeIds?: Set<string>;
  onFocusNode?: (nodeId: string) => void;
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
  viewMode,
  onViewModeChange,
  onResetToRoots,
  isSidebarOpen,
  onToggleSidebar,
  activeTrace,
  onResetTrace,
  lastUpdate,
  followAgent,
  onToggleFollowAgent,
  lastAgentEvent,
  onToggleDocs,
  isDocsOpen,
  onToggleReview,
  isReviewOpen,
  selectedElement,
  rawNodes,
  rawEdges,
  revealedNodeIds,
  onFocusNode,
}) => {
  const breadcrumbs = useMemo(() => {
    if (
      !selectedElement ||
      selectedElement.type !== "node" ||
      !rawNodes ||
      !rawEdges ||
      !revealedNodeIds
    ) {
      return [];
    }
    const target = rawNodes.find((n) => n.id === selectedElement.id);
    if (!target) return [];

    const chain: RawGraphNode[] = [target];
    const visited = new Set<string>([target.id]);
    let curr = target;

    while (chain.length < 5) {
      const incoming = rawEdges.find(
        (e) =>
          e.to_node_id === curr.id &&
          e.relation_type !== "defines" &&
          revealedNodeIds.has(e.from_node_id) &&
          !visited.has(e.from_node_id)
      );
      if (!incoming) break;
      const caller = rawNodes.find((n) => n.id === incoming.from_node_id);
      if (!caller) break;
      chain.unshift(caller);
      visited.add(caller.id);
      curr = caller;
    }

    return chain;
  }, [selectedElement, rawNodes, rawEdges, revealedNodeIds]);
  return (
    <header className="vibe-header glass-panel">
      {/* Brand & Hierarchy Sidebar Toggle */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
        <button
          type="button"
          className={`vibe-btn vibe-btn-icon ${isSidebarOpen ? "vibe-btn-active" : ""}`}
          onClick={onToggleSidebar}
          title={isSidebarOpen ? "Close Code Hierarchy Sidebar" : "Open Code Hierarchy Sidebar"}
        >
          {isSidebarOpen ? <PanelLeftClose size={17} /> : <PanelLeftOpen size={17} />}
        </button>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Activity size={20} color="#6366f1" />
          <h2
            style={{
              margin: 0,
              fontSize: "15px",
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
                fontSize: "10px",
                fontWeight: 600,
                color: "#a5b4fc",
                background: "rgba(99, 102, 241, 0.15)",
                padding: "2px 7px",
                borderRadius: "var(--radius-sm)",
                border: "1px solid rgba(99, 102, 241, 0.3)",
                letterSpacing: "0.05em",
              }}
            >
              {layoutMode === "trace"
                ? "TRACEPATH"
                : layoutMode === "impact"
                  ? "BLAST RADIUS"
                  : viewMode === "explorer"
                    ? "EXPLORER"
                    : "FULL MAP"}
            </span>
          </h2>
        </div>
      </div>

      {/* Explorer vs Full Map Segmented Switch */}
      <div className="vibe-segmented-control" style={{ flexShrink: 0 }}>
        <button
          type="button"
          className={`vibe-segment-btn ${viewMode === "explorer" ? "active" : ""}`}
          onClick={() => onViewModeChange("explorer")}
          title="On-demand progressive disclosure: start with entrypoints and expand branches"
        >
          Hierarchical Explorer
        </button>
        <button
          type="button"
          className={`vibe-segment-btn ${viewMode === "full" ? "active" : ""}`}
          onClick={() => onViewModeChange("full")}
          title="Bird's-eye view of all symbols and districts"
        >
          Full Map
        </button>
      </div>

      {/* Reset to roots in explorer mode */}
      {viewMode === "explorer" && (
        <button
          type="button"
          className="vibe-btn"
          onClick={onResetToRoots}
          title="Reset canvas back to root entrypoints (main & endpoints)"
          style={{ flexShrink: 0 }}
        >
          <Sparkles size={14} color="#818cf8" />
          Roots
        </button>
      )}

      {/* Active Branch Breadcrumbs (Explorer Mode) */}
      {viewMode === "explorer" && breadcrumbs.length > 1 && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "3px",
            background: "rgba(15, 23, 42, 0.75)",
            border: "1px solid rgba(255, 255, 255, 0.08)",
            borderRadius: "6px",
            padding: "2px 6px",
            fontSize: "11px",
            overflow: "hidden",
            flexShrink: 1,
            maxWidth: "360px",
          }}
        >
          {breadcrumbs.map((crumb, idx) => {
            const isLast = idx === breadcrumbs.length - 1;
            const dotColor =
              crumb.kind === "endpoint"
                ? "#f59e0b"
                : crumb.kind === "class"
                  ? "#c084fc"
                  : "#818cf8";
            return (
              <React.Fragment key={crumb.id}>
                {idx > 0 && <ChevronRight size={11} color="rgba(255, 255, 255, 0.3)" />}
                <button
                  type="button"
                  onClick={() => onFocusNode?.(crumb.id)}
                  style={{
                    background: isLast ? "rgba(99, 102, 241, 0.22)" : "transparent",
                    border: isLast ? "1px solid rgba(99, 102, 241, 0.4)" : "none",
                    color: isLast ? "#c7d2fe" : "#94a3b8",
                    borderRadius: "4px",
                    padding: "2px 5px",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: "4px",
                    fontSize: "10.5px",
                    fontWeight: isLast ? 600 : 400,
                    maxWidth: "110px",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={`Focus ${crumb.qualified_name || crumb.name}`}
                >
                  <span
                    style={{
                      width: 5,
                      height: 5,
                      borderRadius: "50%",
                      background: dotColor,
                      flexShrink: 0,
                    }}
                  />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{crumb.name}</span>
                </button>
              </React.Fragment>
            );
          })}
        </div>
      )}

      {/* Search Bar */}
      <div style={{ position: "relative", flexShrink: 1, minWidth: 160, maxWidth: 240 }}>
        <Search
          size={15}
          style={{
            position: "absolute",
            left: 11,
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
          style={{ width: "100%" }}
        />
        {filter && (
          <button
            type="button"
            onClick={() => onFilterChange("")}
            style={{
              position: "absolute",
              right: 8,
              top: 9,
              background: "transparent",
              border: "none",
              color: "var(--text-dim)",
              cursor: "pointer",
            }}
          >
            <X size={14} />
          </button>
        )}
      </div>

      {/* Action Buttons: Sync Graph & Docs */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
        <button
          type="button"
          className="vibe-btn vibe-btn-primary"
          onClick={onRefresh}
          disabled={loading}
          title="Re-fetch symbols and edges from database"
        >
          <RefreshCw size={15} className={loading ? "spin" : ""} />
          {loading ? "Syncing..." : "Sync Graph"}
        </button>

        {onToggleDocs && (
          <button
            type="button"
            className={`vibe-btn ${isDocsOpen ? "vibe-btn-active" : ""}`}
            onClick={onToggleDocs}
            title="Toggle Architecture Documentation"
          >
            <BookOpen size={15} color={isDocsOpen ? "#a5b4fc" : undefined} />
            Docs
          </button>
        )}

        {onToggleReview && (
          <button
            type="button"
            className={`vibe-btn ${isReviewOpen ? "vibe-btn-active" : ""}`}
            onClick={onToggleReview}
            title="Toggle Unified AI Quality Gate (Security, Splits, Test Coverage)"
          >
            <ShieldCheck size={15} color={isReviewOpen ? "#34d399" : "#6ee7b7"} />
            Quality Gate
          </button>
        )}
      </div>

      {/* Active Tracepath Pill */}
      {activeTrace?.found && (
        <div
          className="vibe-stat-chip"
          style={{
            background: "rgba(99, 102, 241, 0.2)",
            borderColor: "rgba(99, 102, 241, 0.45)",
            color: "#e0e7ff",
            flexShrink: 0,
          }}
        >
          <span style={{ fontWeight: 600, color: "#818cf8" }}>Trace:</span>
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
                borderRadius: "var(--radius-sm)",
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

      {/* Follow AI Agent Badge */}
      <AgentRadarBadge
        followAgent={followAgent}
        onToggleFollow={onToggleFollowAgent}
        lastAgentEvent={lastAgentEvent}
      />

      {/* Responsive Standardized Stats Chip */}
      <div className="vibe-stat-chip" style={{ marginLeft: "auto", flexShrink: 0 }}>
        <Layers size={14} style={{ color: "#6366f1" }} />
        <span>
          {visibleSymbols < totalSymbols && visibleSymbols > 0
            ? `${visibleSymbols} / ${totalSymbols}`
            : `${totalSymbols || visibleSymbols}`}
        </span>
        <div style={{ width: 1, height: 14, background: "var(--border-color)" }} />
        <Code size={14} style={{ color: "#a855f7" }} />
        <span>{totalEdges}</span>
        {lastUpdate && (
          <>
            <div style={{ width: 1, height: 14, background: "var(--border-color)" }} />
            <span style={{ fontSize: "11px", opacity: 0.6 }}>{lastUpdate}</span>
          </>
        )}
      </div>
    </header>
  );
};
