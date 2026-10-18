import { Panel } from "@xyflow/react";
import { Activity, Filter, GitBranch, Map as MapIcon } from "lucide-react";
import type React from "react";
import type { LayoutMode } from "../types";

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
}) => {
  return (
    <>
      <Panel
        position="top-right"
        className="glass-panel"
        style={{
          padding: "12px",
          display: "flex",
          flexDirection: "column",
          gap: 8,
          marginTop: "80px",
          minWidth: "185px",
        }}
      >
        {onLayoutModeChange && (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 6,
              borderBottom: "1px solid var(--border-color)",
              paddingBottom: 8,
              marginBottom: 4,
            }}
          >
            <div style={{ fontSize: "11px", fontWeight: 600, opacity: 0.7 }}>LAYOUT MODE</div>
            {(
              [
                { id: "map", label: "Architecture Map", icon: <MapIcon size={12} /> },
                { id: "trace", label: "Tracepath LR", icon: <GitBranch size={12} /> },
                { id: "impact", label: "Blast Radius", icon: <Activity size={12} /> },
              ] as const
            ).map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => onLayoutModeChange(item.id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "6px 8px",
                  fontSize: "12px",
                  borderRadius: 6,
                  border:
                    layoutMode === item.id
                      ? "1px solid #6366f1"
                      : "1px solid rgba(255,255,255,0.08)",
                  background:
                    layoutMode === item.id ? "rgba(99, 102, 241, 0.2)" : "rgba(255,255,255,0.03)",
                  color: "#fff",
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                {item.icon}
                <span>{item.label}</span>
              </button>
            ))}
          </div>
        )}

        <div
          style={{
            fontSize: "12px",
            fontWeight: 600,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <Filter size={12} /> Symbol Filters
        </div>
        {[
          "function",
          "class",
          "interface",
          "struct",
          "trait",
          "impl",
          "variable",
          "module",
          "endpoint",
        ].map((kind) => (
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

        <div
          style={{
            fontSize: "12px",
            fontWeight: 600,
            display: "flex",
            alignItems: "center",
            gap: 6,
            borderTop: "1px solid var(--border-color)",
            paddingTop: 8,
            marginTop: 4,
          }}
        >
          <GitBranch size={12} /> Connection Types
        </div>
        {[
          { id: "calls", label: "Calls", color: "#38bdf8", stroke: "solid" },
          { id: "routes", label: "Routes (HTTP)", color: "#f59e0b", stroke: "solid" },
          { id: "imports", label: "Imports", color: "#a78bfa", stroke: "dashed" },
          { id: "inherits", label: "Inherits", color: "#34d399", stroke: "solid" },
          { id: "defines", label: "Defines", color: "#94a3b8", stroke: "dotted" },
        ].map((rel) => (
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

        <div
          style={{
            borderTop: "1px solid var(--border-color)",
            margin: "4px 0",
            paddingTop: "8px",
            display: "flex",
            flexDirection: "column",
            gap: 8,
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
      </Panel>

      <Panel
        position="bottom-right"
        className="glass-panel"
        style={{ padding: "8px 12px", marginBottom: 20, marginRight: 20 }}
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
