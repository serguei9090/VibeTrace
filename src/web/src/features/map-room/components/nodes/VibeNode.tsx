import { Handle, Position } from "@xyflow/react";
import { Globe, Lock } from "lucide-react";
import type React from "react";
import { memo } from "react";
import type { VibeNodeData } from "../../types";

export const VibeNode = memo(({ data }: { data: VibeNodeData }) => {
  const isClass = data.kind === "class";
  const isInterface = data.kind === "interface";
  const isModule = data.kind === "module";
  const isHighlighted = data.isHighlighted;
  const isDimmed = data.isDimmed;
  const isPrivate = data.visibility === "private";
  const isTraceMode = data.layoutMode === "trace";
  const decorators = data.decorators ? String(data.decorators).split(",").filter(Boolean) : [];

  let accentColor = "#818cf8"; // indigo-400
  let glowColor = "rgba(129, 140, 248, 0.4)";

  if (isClass) {
    accentColor = "#c084fc"; // purple-400
    glowColor = "rgba(192, 132, 252, 0.4)";
  } else if (isInterface) {
    accentColor = "#34d399"; // emerald-400
    glowColor = "rgba(52, 211, 153, 0.4)";
  } else if (isModule) {
    accentColor = "#38bdf8"; // sky-400
    glowColor = "rgba(56, 189, 248, 0.4)";
  }

  if (data.isImpact) {
    accentColor = "#f87171"; // red-400
    glowColor = "rgba(248, 113, 113, 0.4)";
  }

  const bgGradient = isHighlighted
    ? "linear-gradient(135deg, rgba(35, 39, 64, 0.98) 0%, rgba(22, 25, 42, 0.98) 100%)"
    : "linear-gradient(135deg, rgba(23, 26, 40, 0.95) 0%, rgba(14, 16, 26, 0.95) 100%)";

  const cardStyle: React.CSSProperties = {
    background: bgGradient,
    borderLeft: `4px solid ${accentColor}`,
    borderTop: isHighlighted ? `1px solid ${accentColor}` : "1px solid rgba(255, 255, 255, 0.08)",
    borderRight: isHighlighted ? `1px solid ${accentColor}` : "1px solid rgba(255, 255, 255, 0.08)",
    borderBottom: isHighlighted
      ? `1px solid ${accentColor}`
      : "1px solid rgba(255, 255, 255, 0.08)",
    boxShadow: isHighlighted
      ? `0 0 24px ${glowColor}, 0 8px 32px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.15)`
      : "0 4px 16px -2px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.06)",
    opacity: isDimmed ? 0.22 : 1,
    transition: "all 0.25s cubic-bezier(0.16, 1, 0.3, 1)",
  };

  return (
    <div className="vibe-node" style={cardStyle}>
      <Handle
        type="target"
        position={isTraceMode ? Position.Left : Position.Top}
        style={{
          background: accentColor,
          width: 8,
          height: 8,
          border: "2px solid #090a10",
        }}
      />
      <div className="node-header">
        <div className="node-type" style={{ color: accentColor }}>
          {isPrivate ? (
            <Lock size={11} style={{ marginRight: 4 }} />
          ) : (
            <Globe size={11} style={{ marginRight: 4 }} />
          )}
          {data.kind}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          {data.stepBadge && (
            <span
              style={{
                background: "linear-gradient(135deg, #10b981 0%, #059669 100%)",
                color: "#ffffff",
                fontWeight: 700,
                fontSize: "10px",
                padding: "2px 7px",
                borderRadius: "999px",
                boxShadow: "0 2px 6px rgba(16, 185, 129, 0.4)",
              }}
            >
              {data.stepBadge}
            </span>
          )}
          {data.impactBadge && (
            <span
              style={{
                background:
                  data.impactBadge === "TARGET"
                    ? "linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)"
                    : "linear-gradient(135deg, #ef4444 0%, #dc2626 100%)",
                color: "#fff",
                fontWeight: 700,
                fontSize: "10px",
                padding: "2px 7px",
                borderRadius: "999px",
              }}
            >
              {data.impactBadge}
            </span>
          )}
          {decorators.length > 0 && (
            <div className="node-decorators">
              {decorators.map((d: string) => (
                <span key={d} className="decorator-badge" title={d}>
                  @{d.length > 10 ? `${d.substring(0, 10)}..` : d}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
      <div
        className="node-name"
        style={{ color: "#f8fafc" }}
        title={String(data.qualified_name || data.label)}
      >
        {data.qualified_name || data.label}
      </div>
      {data.signature && (
        <div
          style={{
            marginTop: 6,
            fontSize: "10px",
            color: "#94a3b8",
            fontFamily: "JetBrains Mono, monospace",
            background: "rgba(0, 0, 0, 0.25)",
            padding: "3px 6px",
            borderRadius: "4px",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={String(data.signature)}
        >
          {data.signature.length > 36 ? `${data.signature.substring(0, 36)}...` : data.signature}
        </div>
      )}
      <Handle
        type="source"
        position={isTraceMode ? Position.Right : Position.Bottom}
        style={{
          background: accentColor,
          width: 8,
          height: 8,
          border: "2px solid #090a10",
        }}
      />
    </div>
  );
});

VibeNode.displayName = "VibeNode";
