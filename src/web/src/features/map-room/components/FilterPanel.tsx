import { Activity, Filter } from "lucide-react";
import type React from "react";
import { Panel } from "reactflow";

interface FilterPanelProps {
  activeFilters: Set<string>;
  onToggleFilter: (kind: string) => void;
  showPrivate: boolean;
  onTogglePrivate: (show: boolean) => void;
  impactMode: boolean;
  onToggleImpact: (impact: boolean) => void;
}

export const FilterPanel: React.FC<FilterPanelProps> = ({
  activeFilters,
  onToggleFilter,
  showPrivate,
  onTogglePrivate,
  impactMode,
  onToggleImpact,
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
        }}
      >
        <div
          style={{
            fontSize: "12px",
            fontWeight: 600,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <Filter size={12} /> Filters
        </div>
        {["function", "class", "interface", "module"].map((kind) => (
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
          }}
        >
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
        </div>
      </Panel>
    </>
  );
};
