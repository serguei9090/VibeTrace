import { Activity, Code, Layers, RefreshCw, Search } from "lucide-react";
import type React from "react";

interface MapHeaderProps {
  filter: string;
  onFilterChange: (val: string) => void;
  loading: boolean;
  onRefresh: () => void;
  symbolCount: number;
  edgeCount: number;
  lastUpdate: string;
}

export const MapHeader: React.FC<MapHeaderProps> = ({
  filter,
  onFilterChange,
  loading,
  onRefresh,
  symbolCount,
  edgeCount,
  lastUpdate,
}) => {
  return (
    <div className="vibe-header glass-panel">
      <Activity size={24} color="#6366f1" />
      <h2
        style={{
          margin: 0,
          fontSize: "18px",
          fontWeight: 700,
          letterSpacing: "-0.02em",
        }}
      >
        VIBEGRAPH <span style={{ color: "var(--text-dim)", fontWeight: 400 }}>MAP ROOM</span>
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

      <div className="stats-panel">
        <Layers size={14} /> <span>{symbolCount} Symbols</span>
        <div
          style={{
            width: 1,
            height: 16,
            background: "var(--border-color)",
          }}
        />
        <Code size={14} /> <span>{edgeCount} Edges</span>
        <div
          style={{
            width: 1,
            height: 16,
            background: "var(--border-color)",
          }}
        />
        <span style={{ fontSize: "11px", opacity: 0.5 }}>Last Sync: {lastUpdate}</span>
      </div>
    </div>
  );
};
