import { Folder } from "lucide-react";
import { memo } from "react";

interface DistrictNodeData {
  label?: string;
  [key: string]: unknown;
}

export const DistrictNode = memo(({ data }: { data: DistrictNodeData }) => {
  return (
    <div style={{ height: "100%", width: "100%", position: "relative" }}>
      <div
        style={{
          position: "absolute",
          top: -110,
          left: -4,
          background: "transparent",
          color: "rgba(255, 255, 255, 0.4)",
          fontSize: "22px",
          fontWeight: 300,
          display: "flex",
          alignItems: "center",
          gap: "8px",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          pointerEvents: "none",
        }}
      >
        <Folder size={20} />
        {data.label}
      </div>
    </div>
  );
});

DistrictNode.displayName = "DistrictNode";
