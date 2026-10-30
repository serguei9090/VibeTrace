import { Panel } from "@xyflow/react";
import {
  Activity,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Filter,
  GitBranch,
  Layers,
  Map as MapIcon,
  SlidersHorizontal,
} from "lucide-react";
import type React from "react";
import { useState } from "react";
import type { LayoutMode } from "../types";

const ALL_SYMBOLS = [
  "function",
  "class",
  "interface",
  "struct",
  "trait",
  "impl",
  "enum",
  "variable",
  "module",
  "endpoint",
];

const CONNECTION_TYPES = [
  { id: "calls", label: "Calls", color: "#38bdf8", stroke: "solid" },
  { id: "routes", label: "Routes (HTTP)", color: "#f59e0b", stroke: "solid" },
  { id: "imports", label: "Imports", color: "#a78bfa", stroke: "dashed" },
  { id: "inherits", label: "Inherits", color: "#34d399", stroke: "solid" },
  { id: "defines", label: "Defines", color: "#94a3b8", stroke: "dotted" },
];

interface FilterPanelProps {
  activeFilters: Set<string>;
  onToggleFilter: (kind: string) => void;
  activeEdgeTypes: Set<string>;
  onToggleEdgeType: (relType: string) => void;
  showPrivate: boolean;
  onTogglePrivate: (show: boolean) => void;
  impactMode: boolean;
  onToggleImpact: (impact: boolean) => void;
  layoutMode?: LayoutMode;
  onLayoutModeChange?: (mode: LayoutMode) => void;
  isSidebarOpen?: boolean;
}

export const FilterPanel: React.FC<FilterPanelProps> = ({
  activeFilters,
  onToggleFilter,
  activeEdgeTypes,
  onToggleEdgeType,
  showPrivate,
  onTogglePrivate,
  impactMode,
  onToggleImpact,
  layoutMode = "map",
  onLayoutModeChange,
  isSidebarOpen = false,
}) => {
  const [isCollapsed, setIsCollapsed] = useState(true);
  const [openSections, setOpenSections] = useState({
    layout: true,
    symbols: true,
    connections: true,
    options: true,
  });

  const toggleSection = (section: keyof typeof openSections) => {
    setOpenSections((prev) => ({ ...prev, [section]: !prev[section] }));
  };

  return (
    <>
      <Panel
        position="top-right"
        style={{
          marginTop: "88px",
          marginRight: "16px",
          zIndex: 10,
        }}
      >
        {isCollapsed ? (
          <button
            type="button"
            onClick={() => setIsCollapsed(false)}
            className="vibe-btn glass-panel"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 7,
              padding: "8px 14px",
              cursor: "pointer",
              boxShadow: "0 4px 20px rgba(0, 0, 0, 0.5)",
              border: "1px solid rgba(99, 102, 241, 0.35)",
              background: "rgba(18, 20, 32, 0.94)",
              color: "var(--text-main)",
              borderRadius: "8px",
              fontSize: "12px",
              fontWeight: 600,
            }}
            title="Expand Layout & Filter Controls"
          >
            <SlidersHorizontal size={14} style={{ color: "var(--primary)" }} />
            <span>Layout & Filters</span>
            <ChevronLeft size={13} style={{ opacity: 0.6 }} />
          </button>
        ) : (
          <div
            className="glass-panel"
            style={{
              padding: "12px",
              display: "flex",
              flexDirection: "column",
              gap: 10,
              minWidth: "200px",
              maxWidth: "220px",
              maxHeight: "calc(100vh - 130px)",
              overflowY: "auto",
              boxShadow: "0 8px 32px rgba(0, 0, 0, 0.5)",
            }}
          >
            {/* Header with Collapse Button */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                paddingBottom: 6,
                borderBottom: "1px solid var(--border-color)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: "12px",
                  fontWeight: 700,
                  color: "var(--text-main)",
                }}
              >
                <SlidersHorizontal size={13} style={{ color: "var(--primary)" }} />
                <span>Controls</span>
              </div>
              <button
                type="button"
                onClick={() => setIsCollapsed(true)}
                className="vibe-btn vibe-btn-sm"
                style={{
                  padding: "2px 6px",
                  fontSize: "11px",
                  display: "flex",
                  alignItems: "center",
                  gap: 3,
                  opacity: 0.8,
                }}
                title="Collapse Panel"
              >
                <span>Hide</span>
                <ChevronRight size={12} />
              </button>
            </div>

            {/* Section 1: Layout Mode */}
            {onLayoutModeChange && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <button
                  type="button"
                  onClick={() => toggleSection("layout")}
                  style={{
                    background: "none",
                    border: "none",
                    padding: 0,
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    cursor: "pointer",
                    color: "inherit",
                    fontSize: "11px",
                    fontWeight: 700,
                    opacity: 0.75,
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                  }}
                >
                  <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                    <Layers size={11} /> Layout Mode
                  </span>
                  {openSections.layout ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                </button>

                {openSections.layout && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                    {(
                      [
                        { id: "map", label: "Architecture Map", icon: <MapIcon size={13} /> },
                        { id: "trace", label: "Tracepath LR", icon: <GitBranch size={13} /> },
                        { id: "impact", label: "Blast Radius", icon: <Activity size={13} /> },
                      ] as const
                    ).map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => onLayoutModeChange(item.id)}
                        className={`vibe-btn vibe-btn-sm ${layoutMode === item.id ? "vibe-btn-active" : ""}`}
                        style={{
                          justifyContent: "flex-start",
                          width: "100%",
                        }}
                      >
                        {item.icon}
                        <span>{item.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Section 2: Symbol Filters */}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 6,
                borderTop: "1px solid var(--border-color)",
                paddingTop: 8,
              }}
            >
              <button
                type="button"
                onClick={() => toggleSection("symbols")}
                style={{
                  background: "none",
                  border: "none",
                  padding: 0,
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  cursor: "pointer",
                  color: "inherit",
                  fontSize: "11px",
                  fontWeight: 700,
                  opacity: 0.75,
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                }}
              >
                <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  <Filter size={11} /> Symbols ({activeFilters.size}/{ALL_SYMBOLS.length})
                </span>
                {openSections.symbols ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              </button>

              {openSections.symbols && (
                <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  {ALL_SYMBOLS.map((kind) => (
                    <label
                      key={kind}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        fontSize: "12px",
                        cursor: "pointer",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={activeFilters.has(kind)}
                        onChange={() => onToggleFilter(kind)}
                      />
                      <span style={{ textTransform: "capitalize" }}>{kind}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>

            {/* Section 3: Connection Types */}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 6,
                borderTop: "1px solid var(--border-color)",
                paddingTop: 8,
              }}
            >
              <button
                type="button"
                onClick={() => toggleSection("connections")}
                style={{
                  background: "none",
                  border: "none",
                  padding: 0,
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  cursor: "pointer",
                  color: "inherit",
                  fontSize: "11px",
                  fontWeight: 700,
                  opacity: 0.75,
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                }}
              >
                <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  <GitBranch size={11} /> Connections ({activeEdgeTypes.size}/
                  {CONNECTION_TYPES.length})
                </span>
                {openSections.connections ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              </button>

              {openSections.connections && (
                <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  {CONNECTION_TYPES.map((rel) => (
                    <label
                      key={rel.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        fontSize: "12px",
                        cursor: "pointer",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={activeEdgeTypes.has(rel.id)}
                        onChange={() => onToggleEdgeType(rel.id)}
                      />
                      <span
                        style={{
                          display: "inline-block",
                          width: 14,
                          height: 2,
                          backgroundColor: rel.color,
                          borderRadius: 1,
                        }}
                      />
                      <span>{rel.label}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>

            {/* Section 4: Options */}
            <div
              style={{
                borderTop: "1px solid var(--border-color)",
                paddingTop: 8,
                display: "flex",
                flexDirection: "column",
                gap: 7,
              }}
            >
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: "12px",
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={showPrivate}
                  onChange={() => onTogglePrivate(!showPrivate)}
                />
                <span>Show Private</span>
              </label>
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: "12px",
                  cursor: "pointer",
                  color: impactMode ? "#f87171" : "inherit",
                }}
              >
                <input
                  type="checkbox"
                  checked={impactMode}
                  onChange={() => onToggleImpact(!impactMode)}
                />
                <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <Activity size={12} /> Deep Impact Mode
                </span>
              </label>
            </div>
          </div>
        )}
      </Panel>

      {/* Bottom Left Legend */}
      <Panel
        position="bottom-left"
        className="glass-panel"
        style={{
          padding: "8px 12px",
          marginBottom: 20,
          marginLeft: isSidebarOpen ? 370 : 68,
          transition: "margin-left 0.25s ease",
          zIndex: 5,
        }}
      >
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-dim)",
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
          }}
        >
          <span style={{ fontWeight: 600, color: "var(--text-main)" }}>Nodes:</span>
          <span
            style={{
              display: "inline-block",
              width: 8,
              height: 8,
              background: "#6366f1",
              borderRadius: 2,
            }}
          />{" "}
          Function
          <span
            style={{
              display: "inline-block",
              width: 8,
              height: 8,
              background: "#a855f7",
              borderRadius: 2,
            }}
          />{" "}
          Class
          <span
            style={{
              display: "inline-block",
              width: 8,
              height: 8,
              background: "#10b981",
              borderRadius: 2,
            }}
          />{" "}
          Interface
          <span
            style={{
              display: "inline-block",
              width: 8,
              height: 8,
              background: "#f59e0b",
              borderRadius: 2,
            }}
          />{" "}
          Endpoint
          <span style={{ opacity: 0.4 }}>|</span>
          <span style={{ fontWeight: 600, color: "var(--text-main)" }}>Lines:</span>
          <span
            style={{
              display: "inline-block",
              width: 12,
              height: 2,
              background: "#38bdf8",
            }}
          />{" "}
          Calls
          <span
            style={{
              display: "inline-block",
              width: 12,
              height: 2,
              background: "#a78bfa",
            }}
          />{" "}
          Imports
        </div>
      </Panel>
    </>
  );
};
