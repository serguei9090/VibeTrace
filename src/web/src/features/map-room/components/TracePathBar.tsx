import { Panel } from "@xyflow/react";
import axios from "axios";
import { ArrowRight, Bookmark, GitBranch, Trash2, X } from "lucide-react";
import type React from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { RawGraphNode, SavedTrace, TracePathResponse } from "../types";

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

  const [savedTraces, setSavedTraces] = useState<SavedTrace[]>([]);
  const [selectedSavedId, setSelectedSavedId] = useState<string>("");
  const [showSavePrompt, setShowSavePrompt] = useState(false);
  const [saveName, setSaveName] = useState("");

  const symbolOptions = useMemo(
    () =>
      Array.from(new Set(nodes.map((n) => n.qualified_name || n.name).filter(Boolean))).slice(
        0,
        300
      ),
    [nodes]
  );

  const fetchSavedTraces = useCallback(async () => {
    try {
      const res = await axios.get<SavedTrace[]>(`${apiUrl}/api/saved-traces`);
      setSavedTraces(res.data);
    } catch {
      // silently ignore if api is offline
    }
  }, [apiUrl]);

  useEffect(() => {
    fetchSavedTraces();
  }, [fetchSavedTraces]);

  const executeTrace = async (from: string, to: string) => {
    if (!from.trim() || !to.trim()) return;
    setBusy(true);
    setTraceError(null);
    try {
      const res = await axios.get<TracePathResponse>(`${apiUrl}/trace`, {
        params: {
          from_symbol: from.trim(),
          to_symbol: to.trim(),
          max_depth: 6,
        },
      });
      onTraceResult(res.data);
      if (!res.data.found) {
        setTraceError(`No execution path found between '${from}' and '${to}'.`);
      }
    } catch (err: any) {
      setTraceError(err.response?.data?.detail || err.message || "Trace request failed");
    } finally {
      setBusy(false);
    }
  };

  const handleTrace = async (e: React.FormEvent) => {
    e.preventDefault();
    await executeTrace(fromSymbol, toSymbol);
  };

  const handleSelectSaved = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value;
    setSelectedSavedId(val);
    if (!val) return;
    const found = savedTraces.find((t) => String(t.id) === val);
    if (found) {
      setFromSymbol(found.from_symbol);
      setToSymbol(found.to_symbol);
      await executeTrace(found.from_symbol, found.to_symbol);
    }
  };

  const handleSaveTrace = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!saveName.trim() || !fromSymbol.trim() || !toSymbol.trim()) return;
    try {
      await axios.post(`${apiUrl}/api/saved-traces`, {
        name: saveName.trim(),
        from_symbol: fromSymbol.trim(),
        to_symbol: toSymbol.trim(),
        max_depth: 6,
      });
      setShowSavePrompt(false);
      setSaveName("");
      await fetchSavedTraces();
    } catch (err: any) {
      setTraceError(err.response?.data?.detail || "Failed to save trace");
    }
  };

  const handleDeleteSaved = async () => {
    if (!selectedSavedId) return;
    try {
      await axios.delete(`${apiUrl}/api/saved-traces/${selectedSavedId}`);
      setSelectedSavedId("");
      await fetchSavedTraces();
    } catch {
      // ignore
    }
  };

  const handleClear = () => {
    setFromSymbol("");
    setToSymbol("");
    setSelectedSavedId("");
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

        {savedTraces.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <select
              value={selectedSavedId}
              onChange={handleSelectSaved}
              className="vibe-input"
              style={{
                fontSize: "11px",
                padding: "4px 8px",
                background: "rgba(16, 185, 129, 0.1)",
                borderColor: "rgba(16, 185, 129, 0.3)",
                color: "#6ee7b7",
                cursor: "pointer",
                maxWidth: 160,
              }}
              title="Saved Architectural Traces"
            >
              <option value="">★ Bookmarks ({savedTraces.length})</option>
              {savedTraces.map((t) => (
                <option key={t.id} value={String(t.id)}>
                  {t.name}
                </option>
              ))}
            </select>
            {selectedSavedId && (
              <button
                type="button"
                onClick={handleDeleteSaved}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "#f87171",
                  cursor: "pointer",
                  padding: "4px",
                  display: "flex",
                  alignItems: "center",
                }}
                title="Delete saved bookmark"
              >
                <Trash2 size={12} />
              </button>
            )}
          </div>
        )}

        <input
          list="vibetrace-symbols"
          className="vibe-input"
          style={{ width: 155, paddingLeft: 10 }}
          placeholder="From symbol..."
          value={fromSymbol}
          onChange={(e) => setFromSymbol(e.target.value)}
        />
        <ArrowRight size={14} style={{ opacity: 0.6 }} />
        <input
          list="vibetrace-symbols"
          className="vibe-input"
          style={{ width: 155, paddingLeft: 10 }}
          placeholder="To symbol..."
          value={toSymbol}
          onChange={(e) => setToSymbol(e.target.value)}
        />

        <datalist id="vibetrace-symbols">
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

        {activeTrace?.found && !showSavePrompt && (
          <button
            type="button"
            onClick={() => setShowSavePrompt(true)}
            style={{
              background: "rgba(245, 158, 11, 0.15)",
              border: "1px solid rgba(245, 158, 11, 0.4)",
              borderRadius: 6,
              color: "#fbbf24",
              padding: "5px 8px",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 4,
              fontSize: "11px",
            }}
            title="Bookmark this tracepath"
          >
            <Bookmark size={12} />
            <span>Save</span>
          </button>
        )}

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

      {showSavePrompt && (
        <form
          onSubmit={handleSaveTrace}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "4px 8px",
            background: "rgba(245, 158, 11, 0.1)",
            border: "1px solid rgba(245, 158, 11, 0.25)",
            borderRadius: 6,
          }}
        >
          <span style={{ fontSize: "11px", color: "#fbbf24" }}>Bookmark Name:</span>
          <input
            className="vibe-input"
            style={{ width: 180, fontSize: "11px", padding: "3px 8px" }}
            placeholder="e.g. AuthToDBFlow"
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
          />
          <button
            type="submit"
            className="vibe-button"
            style={{ background: "#f59e0b", fontSize: "11px", padding: "4px 10px" }}
            disabled={!saveName.trim()}
          >
            Save
          </button>
          <button
            type="button"
            onClick={() => setShowSavePrompt(false)}
            style={{
              background: "transparent",
              border: "none",
              color: "rgba(255,255,255,0.6)",
              cursor: "pointer",
              padding: "2px",
            }}
          >
            <X size={13} />
          </button>
        </form>
      )}

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
