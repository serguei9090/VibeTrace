import { Handle, Position } from "@xyflow/react";
import { ArrowUpRight, Code, FileCode, Folder } from "lucide-react";
import { memo } from "react";
import type { PackageNodeData } from "../../utils/packageGraph";

export const PackageNode = memo(({ data }: { data: PackageNodeData }) => {
  return (
    <div
      className="vibe-node package-node"
      style={{
        background:
          "linear-gradient(135deg, rgba(23, 26, 42, 0.96) 0%, rgba(15, 17, 28, 0.98) 100%)",
        border: "1.5px solid rgba(99, 102, 241, 0.35)",
        borderLeft: "4px solid #6366f1",
        borderRadius: "12px",
        padding: "14px 16px",
        minWidth: "260px",
        maxWidth: "300px",
        boxShadow: "0 8px 32px rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.08)",
        display: "flex",
        flexDirection: "column",
        gap: "10px",
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: "#6366f1", width: 8, height: 8 }}
      />
      <Handle
        type="target"
        position={Position.Left}
        style={{ background: "#6366f1", width: 8, height: 8 }}
      />
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ background: "#6366f1", width: 8, height: 8 }}
      />
      <Handle
        type="source"
        position={Position.Right}
        style={{ background: "#6366f1", width: 8, height: 8 }}
      />

      {/* Package Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
          <Folder size={16} style={{ color: "#818cf8", flexShrink: 0 }} />
          <span
            style={{
              fontSize: "13px",
              fontWeight: 700,
              color: "#f8fafc",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
            title={data.label}
          >
            {data.shortName}
          </span>
        </div>
        <span
          style={{
            fontSize: "10px",
            textTransform: "uppercase",
            fontWeight: 700,
            letterSpacing: "0.05em",
            color: "#818cf8",
            background: "rgba(99, 102, 241, 0.15)",
            padding: "2px 6px",
            borderRadius: "4px",
            border: "1px solid rgba(99, 102, 241, 0.3)",
          }}
        >
          PACKAGE
        </span>
      </div>

      {/* Path Subtitle */}
      <div
        style={{
          fontSize: "11px",
          color: "var(--text-dim)",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          fontFamily: "JetBrains Mono, monospace",
        }}
      >
        {data.label}
      </div>

      {/* Metrics Row */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            background: "rgba(255, 255, 255, 0.04)",
            padding: "3px 7px",
            borderRadius: "4px",
          }}
        >
          <FileCode size={12} style={{ color: "#38bdf8" }} />
          {data.fileCount} {data.fileCount === 1 ? "file" : "files"}
        </span>

        <span
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            background: "rgba(255, 255, 255, 0.04)",
            padding: "3px 7px",
            borderRadius: "4px",
          }}
        >
          <Code size={12} style={{ color: "#a855f7" }} />
          {data.symbolCount} symbols
        </span>
      </div>

      {/* Notable Symbols Preview */}
      {data.topSymbols && data.topSymbols.length > 0 && (
        <div
          style={{
            borderTop: "1px solid rgba(255, 255, 255, 0.06)",
            paddingTop: "6px",
            display: "flex",
            flexDirection: "column",
            gap: 3,
          }}
        >
          <span style={{ fontSize: "10px", color: "var(--text-dim)", textTransform: "uppercase" }}>
            Key Symbols
          </span>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {data.topSymbols.map((sym) => (
              <span
                key={sym}
                style={{
                  fontSize: "10px",
                  background: "rgba(99, 102, 241, 0.12)",
                  color: "#c7d2fe",
                  padding: "2px 5px",
                  borderRadius: "3px",
                  fontFamily: "JetBrains Mono, monospace",
                }}
              >
                {sym}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Action Footer */}
      <button
        type="button"
        onClick={() => data.onExplorePackage(data.label)}
        className="vibe-btn vibe-btn-sm"
        style={{
          marginTop: "4px",
          width: "100%",
          justifyContent: "center",
          background: "rgba(99, 102, 241, 0.2)",
          borderColor: "rgba(99, 102, 241, 0.4)",
          fontSize: "11px",
          fontWeight: 600,
        }}
      >
        <span>Explore Package</span>
        <ArrowUpRight size={12} />
      </button>
    </div>
  );
});

PackageNode.displayName = "PackageNode";
