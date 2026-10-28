import axios from "axios";
import { ChevronDown, ChevronUp, ShieldAlert, ShieldCheck } from "lucide-react";
import type React from "react";
import { useEffect, useState } from "react";

interface CallerSite {
  caller_name: string;
  caller_file: string;
  call_line?: number;
  call_snippet?: string;
}

interface CallerGuardResponse {
  found: boolean;
  symbol: string;
  file_path?: string;
  total_callers: number;
  callers: CallerSite[];
  safety_advisory: string;
}

interface CallerGuardBadgeProps {
  apiUrl: string;
  symbolName: string;
  filePath?: string;
}

export const CallerGuardBadge: React.FC<CallerGuardBadgeProps> = ({
  apiUrl,
  symbolName,
  filePath,
}) => {
  const [report, setReport] = useState<CallerGuardResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!symbolName) return;

    let cancelled = false;
    setLoading(true);

    axios
      .get<CallerGuardResponse>(`${apiUrl}/api/vibe/guard`, {
        params: {
          symbol: symbolName,
          file_path: filePath,
        },
      })
      .then((res) => {
        if (!cancelled) setReport(res.data);
      })
      .catch(() => {
        if (!cancelled) setReport(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [apiUrl, symbolName, filePath]);

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: "11px",
          color: "var(--text-dim)",
          padding: "4px 8px",
          borderRadius: 6,
          background: "rgba(255, 255, 255, 0.04)",
        }}
      >
        <ShieldCheck size={13} className="spin" />
        <span>Checking caller guard...</span>
      </div>
    );
  }

  if (!report?.found) return null;

  const isSafe = report.total_callers === 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "5px 9px",
          borderRadius: 6,
          cursor: "pointer",
          border: `1px solid ${isSafe ? "rgba(16, 185, 129, 0.35)" : "rgba(244, 63, 94, 0.35)"}`,
          background: isSafe ? "rgba(16, 185, 129, 0.12)" : "rgba(244, 63, 94, 0.12)",
          color: isSafe ? "#6ee7b7" : "#fda4af",
          fontSize: "11px",
          fontWeight: 600,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {isSafe ? <ShieldCheck size={14} /> : <ShieldAlert size={14} />}
          <span>
            {isSafe
              ? "Caller Guard: 0 Callers (Safe)"
              : `Caller Guard: ${report.total_callers} Callers`}
          </span>
        </div>
        {report.total_callers > 0 &&
          (expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />)}
      </button>

      {expanded && report.callers.length > 0 && (
        <div
          style={{
            maxHeight: "130px",
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 4,
            padding: "6px",
            background: "rgba(0, 0, 0, 0.4)",
            borderRadius: 6,
            border: "1px solid rgba(255, 255, 255, 0.06)",
            fontSize: "10.5px",
          }}
        >
          {report.callers.map((c) => (
            <div
              key={`${c.caller_file}:${c.caller_name}:${c.call_line ?? 0}`}
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 2,
                padding: "3px 5px",
                borderBottom: "1px solid rgba(255, 255, 255, 0.04)",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ fontWeight: 600, color: "var(--text-main)" }}>{c.caller_name}</span>
                <span style={{ color: "var(--text-dim)", fontSize: "10px" }}>
                  {c.caller_file}:{c.call_line || "?"}
                </span>
              </div>
              {c.call_snippet && (
                <div
                  style={{
                    color: "#94a3b8",
                    fontFamily: "monospace",
                    fontSize: "10px",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {c.call_snippet}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
