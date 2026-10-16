import { FileCode } from "lucide-react";
import { memo } from "react";

export const FileGroupNode = memo(({ data }: { data: any }) => {
  return (
    <div style={{ height: "100%", width: "100%", position: "relative" }}>
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: "50px",
          padding: "0 16px",
          background: "rgba(26, 27, 35, 0.95)",
          backdropFilter: "blur(8px)",
          borderBottom: "1px solid rgba(255,255,255,0.1)",
          display: "flex",
          alignItems: "center",
          gap: "10px",
          color: "rgba(255,255,255,0.95)",
          fontSize: "13px",
          fontWeight: 600,
          borderTopLeftRadius: "11px",
          borderTopRightRadius: "11px",
          zIndex: 10,
        }}
      >
        <FileCode size={16} style={{ color: "#6366f1" }} />
        <span
          style={{
            flex: 1,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            fontFamily: "JetBrains Mono, monospace",
          }}
        >
          {data.label}
        </span>
        {data.stats && (
          <span
            style={{
              opacity: 0.5,
              fontSize: "11px",
              background: "rgba(255,255,255,0.05)",
              padding: "2px 8px",
              borderRadius: "4px",
            }}
          >
            {data.stats}
          </span>
        )}
      </div>
    </div>
  );
});

FileGroupNode.displayName = "FileGroupNode";
