import { Globe, Lock } from "lucide-react";
import type React from "react";
import { memo } from "react";
import { Handle, Position } from "reactflow";
import type { VibeNodeData } from "../../types";

export const VibeNode = memo(({ data }: { data: VibeNodeData }) => {
  const isClass = data.kind === "class";
  const isInterface = data.kind === "interface";
  const isHighlighted = data.isHighlighted;
  const isDimmed = data.isDimmed;
  const isPrivate = data.visibility === "private";
  const decorators = data.decorators ? data.decorators.split(",").filter(Boolean) : [];

  let borderColor = "#6366f1";
  let bgColor = "#1a1b23";

  if (isClass) borderColor = "#a855f7";
  if (isInterface) borderColor = "#10b981";

  if (isHighlighted) {
    bgColor = "#2e2f3d";
    borderColor = "#ffffff";
  }

  if (data.isImpact) {
    borderColor = "#f87171";
  }

  const style: React.CSSProperties = {
    borderLeftColor: borderColor,
    backgroundColor: bgColor,
    opacity: isDimmed ? 0.2 : 1,
    transition: "all 0.3s ease",
    border: isHighlighted ? `2px solid ${borderColor}` : undefined,
    borderLeft: isHighlighted ? `2px solid ${borderColor}` : `3px solid ${borderColor}`,
  };

  return (
    <div className="vibe-node" style={style}>
      <Handle type="target" position={Position.Top} style={{ background: borderColor }} />
      <div className="node-header">
        <div className="node-type">
          {isPrivate ? (
            <Lock size={10} style={{ marginRight: 4 }} />
          ) : (
            <Globe size={10} style={{ marginRight: 4 }} />
          )}
          {data.kind}
        </div>
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
      <div className="node-name">{data.label}</div>
      {data.signature && (
        <div
          style={{
            marginTop: 4,
            fontSize: "9px",
            opacity: 0.6,
            fontFamily: "JetBrains Mono",
          }}
        >
          {data.signature.length > 30 ? `${data.signature.substring(0, 30)}...` : data.signature}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} style={{ background: borderColor }} />
    </div>
  );
});

VibeNode.displayName = "VibeNode";
