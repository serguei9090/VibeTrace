import { RefreshCw } from "lucide-react";
import type React from "react";

interface StatusOverlaysProps {
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

export const StatusOverlays: React.FC<StatusOverlaysProps> = ({ loading, error, onRetry }) => {
  return (
    <>
      {loading && (
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: "100%",
            height: "100%",
            background: "rgba(26, 27, 35, 0.8)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 20,
            backdropFilter: "blur(4px)",
            color: "#fff",
          }}
        >
          <RefreshCw size={48} className="spin" style={{ color: "#6366f1", marginBottom: 16 }} />
          <div style={{ fontSize: "18px", fontWeight: 600 }}>Syncing VibeTrace...</div>
          <div style={{ fontSize: "14px", opacity: 0.6, marginTop: 4 }}>
            Parsing AST & building relationships
          </div>
        </div>
      )}

      {error && (
        <div
          style={{
            position: "absolute",
            top: 20,
            left: "50%",
            transform: "translateX(-50%)",
            background: "rgba(239, 68, 68, 0.9)",
            color: "white",
            padding: "12px 24px",
            borderRadius: "8px",
            zIndex: 30,
            backdropFilter: "blur(4px)",
            border: "1px solid rgba(255,255,255,0.2)",
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <div style={{ fontWeight: 600 }}>Error:</div>
          <div>{error}</div>
          <button
            type="button"
            onClick={onRetry}
            style={{
              background: "rgba(255,255,255,0.2)",
              border: "none",
              borderRadius: 4,
              padding: "4px 8px",
              color: "white",
              cursor: "pointer",
              marginLeft: 12,
            }}
          >
            Retry
          </button>
        </div>
      )}
    </>
  );
};
