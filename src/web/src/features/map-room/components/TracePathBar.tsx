import { Panel } from "@xyflow/react";
import axios from "axios";
import { ArrowRight, Bookmark, GitBranch, GitFork, Trash2, X } from "lucide-react";
import type React from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  RawGraphNode,
  ReachableSymbolsResponse,
  SavedTrace,
  TracePathResponse,
} from "../types";

interface TracePathBarProps {
  apiUrl: string;
  nodes: RawGraphNode[];
  activeTrace: TracePathResponse | null;
  onTraceResult: (res: TracePathResponse | null) => void;
  isSidebarOpen?: boolean;
}

export const TracePathBar: React.FC<TracePathBarProps> = ({
  apiUrl,
  nodes,
  activeTrace,
  onTraceResult,
  isSidebarOpen = false,
}) => {
  const [fromSymbol, setFromSymbol] = useState("");
  const [toSymbol, setToSymbol] = useState("");
  const [busy, setBusy] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);

  const [onlyReachable, setOnlyReachable] = useState(true);
  const [detectSplits, setDetectSplits] = useState(true);
  const [reachableSymbols, setReachableSymbols] = useState<string[]>([]);
  const [isFetchingReachable, setIsFetchingReachable] = useState(false);
  const [selectedRouteIdx, setSelectedRouteIdx] = useState<number>(0);

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

  // Debounced fetch of reachable downstream symbols when fromSymbol changes
  useEffect(() => {
    if (!onlyReachable || !fromSymbol.trim()) {
      setReachableSymbols([]);
      return;
    }

    const timer = setTimeout(async () => {
      setIsFetchingReachable(true);
      try {
        const res = await axios.get<ReachableSymbolsResponse>(`${apiUrl}/api/reachable`, {
          params: { from_symbol: fromSymbol.trim(), max_depth: 8 },
        });
        setReachableSymbols(res.data.reachable_symbols || []);
      } catch {
        setReachableSymbols([]);
      } finally {
        setIsFetchingReachable(false);
      }
    }, 250);

    return () => clearTimeout(timer);
  }, [fromSymbol, onlyReachable, apiUrl]);

  const toOptions = useMemo(() => {
    if (onlyReachable && reachableSymbols.length > 0) {
      return reachableSymbols;
    }
    return symbolOptions;
  }, [onlyReachable, reachableSymbols, symbolOptions]);

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
    setSelectedRouteIdx(0);
    try {
      const res = await axios.get<TracePathResponse>(`${apiUrl}/trace`, {
        params: {
          from_symbol: from.trim(),
          to_symbol: to.trim(),
          max_depth: 6,
          detect_splits: detectSplits,
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

  const selectRoute = (idx: number) => {
    if (!activeTrace?.paths?.[idx]) return;
    setSelectedRouteIdx(idx);
    const chosenPath = activeTrace.paths[idx];
    const nodeIds = [chosenPath[0].from_node.id, ...chosenPath.map((h) => h.to_node.id)];
    onTraceResult({
      ...activeTrace,
      activeRouteIndex: idx,
      hops: chosenPath,
      node_ids: nodeIds,
    });
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
        marginTop: "88px",
        marginLeft: isSidebarOpen ? "344px" : "16px",
        padding: "10px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        zIndex: 9,
        transition: "margin-left 0.25s ease",
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
                padding: "0 8px",
                height: "var(--control-height-sm)",
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
                className="vibe-btn vibe-btn-icon-sm"
                style={{ color: "#f87171" }}
                title="Delete saved bookmark"
              >
                <Trash2 size={12} />
              </button>
            )}
          </div>
        )}

        <input
          list="vibetrace-from-symbols"
          className="vibe-input"
          style={{
            width: 155,
            padding: "0 8px",
            height: "var(--control-height-sm)",
            fontSize: "12px",
          }}
          placeholder="From symbol..."
          value={fromSymbol}
          onChange={(e) => setFromSymbol(e.target.value)}
        />
        <ArrowRight size={13} style={{ opacity: 0.6 }} />
        <input
          list="vibetrace-to-symbols"
          className="vibe-input"
          style={{
            width: 155,
            padding: "0 8px",
            height: "var(--control-height-sm)",
            fontSize: "12px",
          }}
          placeholder="To symbol..."
          value={toSymbol}
          onChange={(e) => setToSymbol(e.target.value)}
        />

        <datalist id="vibetrace-from-symbols">
          {symbolOptions.map((sym) => (
            <option key={sym} value={sym} />
          ))}
        </datalist>

        <datalist id="vibetrace-to-symbols">
          {toOptions.map((sym) => (
            <option key={sym} value={sym} />
          ))}
        </datalist>

        <button
          type="submit"
          className="vibe-btn vibe-btn-sm"
          style={{
            background: "#10b981",
            borderColor: "#059669",
            color: "#fff",
            fontWeight: 600,
          }}
          disabled={busy || !fromSymbol.trim() || !toSymbol.trim()}
        >
          {busy ? "Tracing..." : "Trace"}
        </button>

        {activeTrace?.found && !showSavePrompt && (
          <button
            type="button"
            onClick={() => setShowSavePrompt(true)}
            className="vibe-btn vibe-btn-sm"
            style={{
              background: "rgba(245, 158, 11, 0.15)",
              borderColor: "rgba(245, 158, 11, 0.4)",
              color: "#fbbf24",
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
            className="vibe-btn vibe-btn-icon-sm"
            title="Clear tracepath"
          >
            <X size={13} />
          </button>
        )}
      </form>

      {/* Intelligence Controls Row: Toggles for Reachable Only & Split Detection */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 14,
          fontSize: "11px",
          color: "var(--text-dim)",
          paddingTop: "2px",
          borderTop: "1px solid rgba(255, 255, 255, 0.06)",
        }}
      >
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 5,
            cursor: "pointer",
            userSelect: "none",
          }}
        >
          <input
            type="checkbox"
            checked={onlyReachable}
            onChange={(e) => setOnlyReachable(e.target.checked)}
          />
          <span style={{ color: onlyReachable ? "#6ee7b7" : "inherit" }}>Reachable Only</span>
          {onlyReachable && fromSymbol.trim() && (
            <span
              style={{
                color: "#10b981",
                fontSize: "10px",
                fontWeight: 600,
                background: "rgba(16, 185, 129, 0.15)",
                padding: "1px 6px",
                borderRadius: "4px",
              }}
            >
              {isFetchingReachable ? "searching..." : `${reachableSymbols.length} reachable`}
            </span>
          )}
        </label>

        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 5,
            cursor: "pointer",
            userSelect: "none",
          }}
        >
          <input
            type="checkbox"
            checked={detectSplits}
            onChange={(e) => setDetectSplits(e.target.checked)}
          />
          <span
            style={{
              color: detectSplits ? "#fbbf24" : "inherit",
              display: "flex",
              alignItems: "center",
              gap: 3,
            }}
          >
            <GitFork size={11} /> Detect Splits & Routes
          </span>
        </label>
      </div>

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
            className="vibe-btn vibe-btn-sm"
            style={{
              background: "#f59e0b",
              color: "#000",
              fontWeight: 600,
              fontSize: "11px",
              padding: "4px 10px",
            }}
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

      {/* Multi-Route Selector Tabs */}
      {activeTrace?.paths && activeTrace.paths.length > 1 && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            paddingTop: 4,
            borderTop: "1px solid rgba(255, 255, 255, 0.06)",
          }}
        >
          <span style={{ fontSize: "11px", color: "var(--text-dim)", fontWeight: 600 }}>
            Alternative Routes ({activeTrace.paths.length}):
          </span>
          {activeTrace.paths.map((p, idx) => {
            const isSelected = (activeTrace.activeRouteIndex ?? selectedRouteIdx) === idx;
            const routeColor = idx === 1 ? "#f59e0b" : idx === 2 ? "#ec4899" : "#6366f1";
            return (
              <button
                key={p.map((h) => `${h.from_node.id}->${h.to_node.id}`).join("|")}
                type="button"
                onClick={() => selectRoute(idx)}
                className={`vibe-btn vibe-btn-sm ${isSelected ? "vibe-btn-active" : ""}`}
                style={{
                  fontSize: "11px",
                  padding: "2px 8px",
                  background: isSelected ? routeColor : "rgba(255, 255, 255, 0.06)",
                  borderColor: isSelected ? routeColor : "rgba(255, 255, 255, 0.12)",
                  color: isSelected ? "#ffffff" : "var(--text-main)",
                  fontWeight: isSelected ? 700 : 500,
                }}
              >
                Route {idx + 1} ({p.length} {p.length === 1 ? "hop" : "hops"})
              </button>
            );
          })}
        </div>
      )}

      {/* Split Points Summary */}
      {activeTrace?.split_points && activeTrace.split_points.length > 0 && (
        <div
          style={{
            fontSize: "11px",
            color: "#fbbf24",
            display: "flex",
            alignItems: "center",
            gap: 6,
            flexWrap: "wrap",
            paddingTop: 4,
          }}
        >
          <span style={{ fontWeight: 600 }}>
            ⑂ {activeTrace.split_points.length} Branching Split(s):
          </span>
          {activeTrace.split_points.map((sp) => (
            <span
              key={sp.node_id}
              style={{
                background: "rgba(245, 158, 11, 0.15)",
                border: "1px solid rgba(245, 158, 11, 0.3)",
                padding: "1px 6px",
                borderRadius: 4,
                fontFamily: "JetBrains Mono, monospace",
                fontSize: "10px",
              }}
              title={`Alternate branches out of ${sp.symbol_name}:\n${sp.branch_names.join("\n")}`}
            >
              {sp.symbol_name} ({sp.total_outgoing_branches} calls)
            </span>
          ))}
        </div>
      )}

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
              #{hop.step_index} {hop.from_node.qualified_name || hop.from_node.name}
              {hop.call_line ? `:L${hop.call_line}` : ""} →{" "}
              {hop.to_node.qualified_name || hop.to_node.name}
            </span>
          ))}
        </div>
      )}
    </Panel>
  );
};
