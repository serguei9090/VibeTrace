import { Panel } from "@xyflow/react";
import axios from "axios";
import { ArrowRight, GitBranch, X } from "lucide-react";
import type React from "react";
import { useState } from "react";
import type { RawGraphNode, TracePathResponse } from "../types";

interface TracePathBarProps {
  apiUrl: string;
  nodes: RawGraphNode[];
  activeTrace: TracePathResponse | null;
  onTraceResult: (res: TracePathResponse | null) => void;
}

export const TracePathBar: React.FC<TracePathBarProps> = ({
  apiUrl,
  nodes,
  activeTrace,
  onTraceResult,
}) => {
  const [fromSymbol, setFromSymbol] = useState("");
  const [toSymbol, setToSymbol] = useState("");
  const [busy, setBusy] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);

  const symbolOptions = Array.from(
    new Set(nodes.map((n) => n.qualified_name || n.name).filter(Boolean))
  ).slice(0, 300);

  const handleTrace = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!fromSymbol.trim() || !toSymbol.trim()) return;
    setBusy(true);
    setTraceError(null);
    try {
      const res = await axios.get<TracePathResponse>(`${apiUrl}/trace`, {
        params: {
          from_symbol: fromSymbol.trim(),
          to_symbol: toSymbol.trim(),
          max_depth: 6,
        },
      });
      onTraceResult(res.data);
      if (!res.data.found) {
        setTraceError(`No execution path found between '${fromSymbol}' and '${toSymbol}'.`);
      }
    } catch (err: any) {
      setTraceError(err.response?.data?.detail || err.message || "Trace request failed");
    } finally {
      setBusy(false);
    }
  };

  const handleClear = () => {
    setFromSymbol("");
    setToSymbol("");
    setTraceError(null);
    onTraceResult(null);
  };

  return (
    <Panel
      position="top-left"
      className="glass-panel"
      style={{
        marginTop: "92px",
        marginLeft: "20px",
        padding: "10px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        zIndex: 9,
      }}
    >
      <form
        onSubmit={handleTrace}
        style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: "12px",
            fontWeight: 600,
            color: "#10b981",
          }}
        >
          <GitBranch size={14} />
          <span>Tracepath</span>
        </div>

        <input
          list="vibegraph-symbols"
          className="vibe-input"
          style={{ width: 170, paddingLeft: 10 }}
          placeholder="From symbol..."
          value={fromSymbol}
          onChange={(e) => setFromSymbol(e.target.value)}
        />
        <ArrowRight size={14} style={{ opacity: 0.6 }} />
        <input
          list="vibegraph-symbols"
          className="vibe-input"
          style={{ width: 170, paddingLeft: 10 }}
          placeholder="To symbol..."
          value={toSymbol}
          onChange={(e) => setToSymbol(e.target.value)}
        />

        <datalist id="vibegraph-symbols">
          {symbolOptions.map((sym) => (
            <option key={sym} value={sym} />
          ))}
        </datalist>

        <button
          type="submit"
          className="vibe-button"
          style={{ background: "#10b981", padding: "6px 12px", fontSize: "12px" }}
          disabled={busy || !fromSymbol.trim() || !toSymbol.trim()}
        >
          {busy ? "Tracing..." : "Trace"}
        </button>

        {(activeTrace || traceError) && (
          <button
            type="button"
            onClick={handleClear}
            style={{
              background: "rgba(255,255,255,0.08)",
              border: "1px solid var(--border-color)",
              borderRadius: 6,
              color: "#fff",
              padding: "5px 8px",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
            }}
            title="Clear tracepath"
          >
            <X size={13} />
          </button>
        )}
      </form>

      {traceError && <div style={{ fontSize: "11px", color: "#f87171" }}>{traceError}</div>}

      {activeTrace?.found && (
        <div
          style={{
            fontSize: "11px",
            color: "#6ee7b7",
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
          }}
        >
          <strong>{activeTrace.hops.length} hop(s):</strong>
          {activeTrace.hops.map((hop) => (
            <span key={`${hop.step_index}-${hop.from_node.id}-${hop.to_node.id}`}>
              #{hop.step_index} {hop.from_node.qualified_name}
              {hop.call_line ? `:L${hop.call_line}` : ""} → {hop.to_node.qualified_name}
            </span>
          ))}
        </div>
      )}
    </Panel>
  );
};
