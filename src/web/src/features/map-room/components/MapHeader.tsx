import axios from "axios";
import {
  Activity,
  BookOpen,
  ChevronDown,
  ChevronRight,
  Code,
  FolderGit2,
  GitBranch,
  Layers,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import React, { useEffect, useMemo, useState } from "react";
import type {
  AgentFocusEvent,
  LayoutMode,
  ProjectStatusUI,
  RawGraphEdge,
  RawGraphNode,
  SelectedElement,
  TracePathResponse,
  ViewMode,
  WorkspaceMode,
} from "../types";
import { AgentRadarBadge } from "./AgentRadarBadge";

interface MapHeaderProps {
  apiUrl?: string;
  workspaceMode: WorkspaceMode;
  onWorkspaceModeChange: (mode: WorkspaceMode) => void;
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
  selectedElement?: SelectedElement | null;
  rawNodes?: RawGraphNode[];
  rawEdges?: RawGraphEdge[];
  revealedNodeIds?: Set<string>;
  onFocusNode?: (nodeId: string) => void;
  webGpuTurbo?: boolean;
  onToggleWebGpuTurbo?: (enabled: boolean) => void;
  rendererMode?: "webgpu" | "webgl" | "canvas2d" | "off";
  liveFps?: number;
}

export const MapHeader: React.FC<MapHeaderProps> = ({
  apiUrl = "http://127.0.0.1:8000",
  workspaceMode,
  onWorkspaceModeChange,
  filter,
  onFilterChange,
  loading,
  onRefresh,
  totalSymbols,
  totalEdges,
  visibleSymbols,
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
  selectedElement,
  rawNodes,
  rawEdges,
  revealedNodeIds,
  onFocusNode,
  webGpuTurbo = false,
  onToggleWebGpuTurbo,
  rendererMode = "canvas2d",
  liveFps = 60,
}) => {
  const [projectStatus, setProjectStatus] = useState<ProjectStatusUI | null>(null);
  const [showProjectDropdown, setShowProjectDropdown] = useState(false);

  useEffect(() => {
    let mounted = true;
    axios
      .get<ProjectStatusUI>(`${apiUrl}/api/project`)
      .then((res) => {
        if (mounted && res.data) {
          setProjectStatus(res.data);
        }
      })
      .catch(() => {
        // Fallback gracefully if endpoint is unreachable
      });
    return () => {
      mounted = false;
    };
  }, [apiUrl]);

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
        {workspaceMode === "map" && (
          <button
            type="button"
            className={`vibe-btn vibe-btn-icon ${isSidebarOpen ? "vibe-btn-active" : ""}`}
            onClick={onToggleSidebar}
            title={isSidebarOpen ? "Close Code Hierarchy Sidebar" : "Open Code Hierarchy Sidebar"}
          >
            {isSidebarOpen ? <PanelLeftClose size={17} /> : <PanelLeftOpen size={17} />}
          </button>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: 8, position: "relative" }}>
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
            <span>VIBEGRAPH</span>
            <span style={{ opacity: 0.5, fontWeight: 400, fontSize: "12px", borderLeft: "1px solid rgba(255,255,255,0.2)", paddingLeft: "8px" }}>ARCHITECTURE MAP</span>
          </h2>

          {projectStatus && (
            <div style={{ position: "relative" }}>
              <button
                type="button"
                onClick={() => setShowProjectDropdown((prev) => !prev)}
                className="vibe-stat-chip"
                style={{
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 5,
                  padding: "2px 8px",
                  fontSize: "11px",
                  fontWeight: 600,
                  background: projectStatus.is_federated
                    ? "rgba(168, 85, 247, 0.15)"
                    : "rgba(255, 255, 255, 0.05)",
                  borderColor: projectStatus.is_federated
                    ? "rgba(168, 85, 247, 0.35)"
                    : "rgba(255, 255, 255, 0.1)",
                  color: projectStatus.is_federated ? "#e9d5ff" : "rgba(255, 255, 255, 0.7)",
                }}
                title={
                  projectStatus.is_federated
                    ? `Federated Multi-Repo: ${projectStatus.project_name} (${projectStatus.member_count} repos)`
                    : `Project: ${projectStatus.project_name}`
                }
              >
                {projectStatus.is_federated ? (
                  <GitBranch size={12} color="#c084fc" />
                ) : (
                  <FolderGit2 size={12} color="#94a3b8" />
                )}
                <span>{projectStatus.project_name}</span>
                {projectStatus.is_federated && (
                  <span style={{ opacity: 0.7, fontSize: "10px" }}>
                    ({projectStatus.member_count} repos)
                  </span>
                )}
                <ChevronDown size={10} style={{ opacity: 0.6 }} />
              </button>

              {showProjectDropdown && (
                <div
                  style={{
                    position: "absolute",
                    top: "100%",
                    left: 0,
                    marginTop: 6,
                    background: "#0f172a",
                    border: "1px solid rgba(255, 255, 255, 0.12)",
                    borderRadius: 8,
                    padding: "8px",
                    width: 280,
                    zIndex: 1000,
                    boxShadow: "0 10px 25px rgba(0, 0, 0, 0.5)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      marginBottom: 6,
                      paddingBottom: 4,
                      borderBottom: "1px solid rgba(255, 255, 255, 0.08)",
                    }}
                  >
                    <span style={{ fontSize: "11px", fontWeight: 700, color: "#e2e8f0" }}>
                      {projectStatus.is_federated ? "Federated Member Repos" : "Project Workspace"}
                    </span>
                    <span
                      style={{
                        fontSize: "10px",
                        color: projectStatus.is_federated ? "#c084fc" : "#94a3b8",
                      }}
                    >
                      {projectStatus.storage}
                    </span>
                  </div>
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 4,
                      maxHeight: 200,
                      overflowY: "auto",
                    }}
                  >
                    {projectStatus.members.map((m) => (
                      <div
                        key={m.name}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          padding: "4px 6px",
                          borderRadius: 4,
                          background: "rgba(255, 255, 255, 0.03)",
                          fontSize: "11px",
                        }}
                      >
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 6,
                            overflow: "hidden",
                          }}
                        >
                          <span
                            style={{
                              width: 6,
                              height: 6,
                              borderRadius: "50%",
                              background: m.exists ? "#34d399" : "#f87171",
                              flexShrink: 0,
                            }}
                          />
                          <span
                            style={{
                              fontWeight: 600,
                              color: "#f1f5f9",
                              textOverflow: "ellipsis",
                              overflow: "hidden",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {m.name}
                          </span>
                        </div>
                        <span style={{ fontSize: "10px", color: "#94a3b8", flexShrink: 0 }}>
                          {m.node_count ?? 0} symbols
                        </span>
                      </div>
                    ))}
                  </div>
                  {projectStatus.is_federated && (
                    <div
                      style={{
                        marginTop: 6,
                        paddingTop: 4,
                        borderTop: "1px solid rgba(255, 255, 255, 0.08)",
                        fontSize: "10px",
                        color: "#94a3b8",
                        display: "flex",
                        justifyContent: "space-between",
                      }}
                    >
                      <span>Cross-repo contracts:</span>
                      <strong style={{ color: "#38bdf8" }}>
                        {projectStatus.cross_repo_link_count} links
                      </strong>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Primary 3-Workspace Switcher */}
      <div
        role="tablist"
        aria-label="Primary Workspace Mode"
        className="vibe-segmented-control"
        style={{ flexShrink: 0 }}
      >
        <button
          type="button"
          role="tab"
          aria-selected={workspaceMode === "map"}
          className={`vibe-segment-btn ${workspaceMode === "map" ? "active" : ""}`}
          onClick={() => onWorkspaceModeChange("map")}
          style={{ display: "flex", alignItems: "center", gap: 6 }}
        >
          <Layers size={13} />
          Map Room
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={workspaceMode === "dashboard"}
          className={`vibe-segment-btn ${workspaceMode === "dashboard" ? "active" : ""}`}
          onClick={() => onWorkspaceModeChange("dashboard")}
          style={{ display: "flex", alignItems: "center", gap: 6 }}
        >
          <ShieldCheck size={13} color="#34d399" />
          Quality Gate
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={workspaceMode === "docs"}
          className={`vibe-segment-btn ${workspaceMode === "docs" ? "active" : ""}`}
          onClick={() => onWorkspaceModeChange("docs")}
          style={{ display: "flex", alignItems: "center", gap: 6 }}
        >
          <BookOpen size={13} color="#a855f7" />
          Docs
        </button>
      </div>

      {/* Map Room Specific Controls */}
      {workspaceMode === "map" && (
        <>
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
        </>
      )}

      {/* Active Branch Breadcrumbs (Explorer Mode) */}
      {workspaceMode === "map" && viewMode === "explorer" && breadcrumbs.length > 1 && (
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
      {workspaceMode === "map" && (
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
      )}

      {/* Map Room Only: Sync Graph, Active Tracepath Pill & Follow AI Agent */}
      {workspaceMode === "map" && (
        <>
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
          </div>

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
              {activeTrace.paths && activeTrace.paths.length > 1 && (
                <span
                  style={{
                    fontSize: "10px",
                    fontWeight: 700,
                    padding: "1px 5px",
                    borderRadius: 3,
                    background:
                      activeTrace.activeRouteIndex === 1
                        ? "#f59e0b"
                        : activeTrace.activeRouteIndex === 2
                          ? "#ec4899"
                          : "#6366f1",
                    color: "#fff",
                  }}
                >
                  Route {(activeTrace.activeRouteIndex ?? 0) + 1}/{activeTrace.paths.length}
                </span>
              )}
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

          <AgentRadarBadge
            followAgent={followAgent}
            onToggleFollow={onToggleFollowAgent}
            lastAgentEvent={lastAgentEvent}
          />

          {/* WebGPU Hardware Acceleration Toggle */}
          <button
            type="button"
            className={`vibe-btn ${webGpuTurbo ? "vibe-btn-primary" : "vibe-btn-ghost"}`}
            onClick={() => onToggleWebGpuTurbo?.(!webGpuTurbo)}
            title="Toggle WebGPU / Hardware-Accelerated 60-120 FPS Canvas"
            style={{
              padding: "4px 10px",
              fontSize: "12px",
              borderColor: webGpuTurbo ? "rgba(99, 102, 241, 0.8)" : "var(--border-color)",
              boxShadow: webGpuTurbo ? "0 0 10px rgba(99, 102, 241, 0.35)" : "none",
            }}
          >
            <Sparkles size={14} style={{ color: webGpuTurbo ? "#818cf8" : "inherit" }} />
            <span>
              {webGpuTurbo
                ? `⚡ GPU (${rendererMode.toUpperCase()}) ${liveFps} FPS`
                : "⚡ GPU Turbo"}
            </span>
          </button>
        </>
      )}

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
