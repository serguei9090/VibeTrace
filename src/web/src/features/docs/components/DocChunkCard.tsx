import { ChevronDown, ChevronUp, FileText, Layers } from "lucide-react";
import type React from "react";
import { useState } from "react";
import type { DocChunk } from "../types";

interface DocChunkCardProps {
  chunk: DocChunk;
}

export const DocChunkCard: React.FC<DocChunkCardProps> = ({ chunk }) => {
  const [expanded, setExpanded] = useState(false);
  const isGlobal = chunk.scope === "global";

  return (
    <div
      style={{
        background: "rgba(20, 24, 38, 0.75)",
        border: "1px solid rgba(255, 255, 255, 0.08)",
        borderRadius: "8px",
        padding: "12px 14px",
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        transition: "border-color 0.2s ease",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "8px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
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
            {chunk.scope.toUpperCase()}
          </span>
          <span
            style={{
              fontSize: "11px",
              fontWeight: 600,
              color: "var(--text-dim)",
              background: "rgba(255, 255, 255, 0.06)",
              padding: "2px 6px",
              borderRadius: "4px",
            }}
          >
            {chunk.catalog}
          </span>
          <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
            score: {chunk.score.toFixed(4)}
          </span>
        </div>

        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--text-dim)",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            padding: "2px",
          }}
          title={expanded ? "Collapse" : "Expand"}
        >
          {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "6px",
          fontSize: "12px",
          color: "#93c5fd",
        }}
      >
        <FileText size={14} color="#60a5fa" />
        <span style={{ fontWeight: 600 }}>{chunk?.file_path || "Unknown document"}</span>
        <span style={{ color: "var(--text-muted)" }}>
          (L{chunk?.start_line ?? 0}-L{chunk?.end_line ?? 0})
        </span>
      </div>

      {chunk.heading_path && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "6px",
            fontSize: "11px",
            color: "#a5b4fc",
          }}
        >
          <Layers size={13} color="#818cf8" />
          <span style={{ fontStyle: "italic" }}>{chunk.heading_path}</span>
        </div>
      )}

      <div
        style={{
          fontSize: "12px",
          color: "var(--text-dim)",
          lineHeight: "1.5",
          background: "rgba(10, 12, 20, 0.6)",
          padding: "8px 10px",
          borderRadius: "6px",
          border: "1px solid rgba(255, 255, 255, 0.04)",
          whiteSpace: "pre-wrap",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
          maxHeight: expanded ? "400px" : "100px",
          overflowY: "auto",
        }}
      >
        {expanded
          ? chunk.content
          : `${chunk.content.slice(0, 260)}${chunk.content.length > 260 ? "..." : ""}`}
      </div>
    </div>
  );
};
