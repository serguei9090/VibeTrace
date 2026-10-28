import { Folder, RefreshCw } from "lucide-react";
import type React from "react";
import type { DocCatalog } from "../types";

interface CatalogCardProps {
  catalog: DocCatalog;
  onIndex: (catalogName: string, isGlobal: boolean) => Promise<void>;
  indexing: boolean;
}

export const CatalogCard: React.FC<CatalogCardProps> = ({ catalog, onIndex, indexing }) => {
  const isGlobal = catalog.scope === "global";

  return (
    <div
      style={{
        background: "rgba(20, 24, 38, 0.75)",
        border: "1px solid rgba(255, 255, 255, 0.08)",
        borderRadius: "8px",
        padding: "14px",
        display: "flex",
        flexDirection: "column",
        gap: "10px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-main)" }}>
            {catalog.name}
          </span>
          <span
            style={{
              fontSize: "10px",
              fontWeight: 700,
              padding: "2px 6px",
              borderRadius: "4px",
              letterSpacing: "0.05em",
              background: isGlobal ? "rgba(168, 85, 247, 0.2)" : "rgba(16, 185, 129, 0.2)",
              color: isGlobal ? "#c084fc" : "#34d399",
              border: `1px solid ${isGlobal ? "rgba(168, 85, 247, 0.4)" : "rgba(16, 185, 129, 0.4)"}`,
            }}
          >
            {catalog.scope.toUpperCase()}
          </span>
        </div>

        <button
          type="button"
          disabled={indexing}
          onClick={() => onIndex(catalog.name, isGlobal)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "5px",
            background: "rgba(99, 102, 241, 0.15)",
            border: "1px solid rgba(99, 102, 241, 0.3)",
            color: "#a5b4fc",
            padding: "4px 8px",
            borderRadius: "5px",
            fontSize: "11px",
            fontWeight: 600,
            cursor: indexing ? "not-allowed" : "pointer",
          }}
        >
          <RefreshCw size={12} className={indexing ? "spin" : ""} />
          {indexing ? "Indexing..." : "Re-Index"}
        </button>
      </div>

      <div style={{ display: "flex", gap: "16px", fontSize: "12px", color: "var(--text-dim)" }}>
        <span>
          <strong style={{ color: "var(--text-main)" }}>{catalog.doc_count}</strong> files
        </span>
        <span>
          <strong style={{ color: "var(--text-main)" }}>{catalog.chunk_count}</strong> chunks
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
        {catalog.paths.map((p) => (
          <div
            key={p}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              fontSize: "11px",
              color: "var(--text-muted)",
            }}
          >
            <Folder size={12} color="#6366f1" />
            <code
              style={{ background: "rgba(0,0,0,0.3)", padding: "1px 4px", borderRadius: "3px" }}
            >
              {p}
            </code>
          </div>
        ))}
      </div>
    </div>
  );
};
