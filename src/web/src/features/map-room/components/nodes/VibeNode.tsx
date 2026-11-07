import { Handle, Position } from "@xyflow/react";
import { ArrowDownRight, ArrowUpRight, Globe, Lock } from "lucide-react";
import type React from "react";
import { memo } from "react";
import type { VibeNodeData } from "../../types";

export const VibeNode = memo(({ id, data }: { id?: string; data: VibeNodeData }) => {
  const nodeId = id || (data.id as string);
  const isHighlighted = data.isHighlighted;
  const isDimmed = data.isDimmed;
  const isPrivate = data.visibility === "private";
  const isTraceMode = data.layoutMode === "trace";
  const decorators =
    typeof data.decorators === "string"
      ? data.decorators.split(",").filter(Boolean)
      : Array.isArray(data.decorators)
        ? data.decorators
        : [];

  const KIND_COLOR_MAP: Record<string, { accent: string; glow: string }> = {
    endpoint: { accent: "#f59e0b", glow: "rgba(245, 158, 11, 0.4)" },
    class: { accent: "#c084fc", glow: "rgba(192, 132, 252, 0.4)" },
    interface: { accent: "#34d399", glow: "rgba(52, 211, 153, 0.4)" },
    trait: { accent: "#34d399", glow: "rgba(52, 211, 153, 0.4)" },
    struct: { accent: "#06b6d4", glow: "rgba(6, 182, 212, 0.4)" },
    impl: { accent: "#a855f7", glow: "rgba(168, 85, 247, 0.4)" },
    enum: { accent: "#fb923c", glow: "rgba(251, 146, 60, 0.4)" },
    variable: { accent: "#94a3b8", glow: "rgba(148, 163, 184, 0.4)" },
    module: { accent: "#38bdf8", glow: "rgba(56, 189, 248, 0.4)" },
  };

  const kindTheme = KIND_COLOR_MAP[data.kind || ""] || {
    accent: "#818cf8",
    glow: "rgba(129, 140, 248, 0.4)",
  };
  let accentColor = kindTheme.accent;
  let glowColor = kindTheme.glow;

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
    opacity: isDimmed ? 0.45 : 1,
    transition: "opacity 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease",
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
          {data.splitBadge && (
            <span
              style={{
                background: "rgba(245, 158, 11, 0.2)",
                border: "1px solid rgba(245, 158, 11, 0.5)",
                color: "#fbbf24",
                fontWeight: 700,
                fontSize: "10px",
                padding: "2px 6px",
                borderRadius: "999px",
                display: "inline-flex",
                alignItems: "center",
                gap: 2,
              }}
              title={
                data.splitBranches && data.splitBranches.length > 0
                  ? `Branching Point (${data.splitBadge} paths):\n${(data.splitBranches as string[]).join("\n")}`
                  : `Branching Point: splits into ${data.splitBadge} calls`
              }
            >
              ⑂ {data.splitBadge}
            </span>
          )}
          {data.securityBadge && (
            <span
              style={{
                background:
                  data.securityBadge === "CRITICAL"
                    ? "linear-gradient(135deg, #ef4444 0%, #b91c1c 100%)"
                    : data.securityBadge === "HIGH"
                      ? "linear-gradient(135deg, #f97316 0%, #c2410c 100%)"
                      : "linear-gradient(135deg, #eab308 0%, #a16207 100%)",
                color: "#fff",
                fontWeight: 700,
                fontSize: "10px",
                padding: "2px 6px",
                borderRadius: "999px",
                display: "inline-flex",
                alignItems: "center",
                gap: 2,
                boxShadow: "0 0 8px rgba(239, 68, 68, 0.4)",
              }}
              title={`Security Finding: ${data.securityBadge}`}
            >
              🛡️ {data.securityBadge}
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

      {((data.unrevealedCallees !== undefined && data.unrevealedCallees > 0) ||
        (data.unrevealedCallers !== undefined && data.unrevealedCallers > 0)) && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "5px",
            marginTop: "8px",
            flexWrap: "wrap",
          }}
        >
          {data.unrevealedCallers !== undefined && data.unrevealedCallers > 0 && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                if (nodeId && data.onExpandCallers) data.onExpandCallers(nodeId);
              }}
              className="node-expand-pill"
              title={`Reveal ${data.unrevealedCallers} upstream callers`}
            >
              <ArrowUpRight size={10} /> +{data.unrevealedCallers} callers
            </button>
          )}
          {data.unrevealedCallees !== undefined && data.unrevealedCallees > 0 && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                if (nodeId && data.onExpandCallees) data.onExpandCallees(nodeId);
              }}
              className="node-expand-pill node-expand-callees"
              title={`Reveal ${data.unrevealedCallees} downstream callees`}
            >
              <ArrowDownRight size={10} /> +{data.unrevealedCallees} callees
            </button>
          )}
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
