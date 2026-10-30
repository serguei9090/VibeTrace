import { Panel } from "@xyflow/react";
import axios from "axios";
import {
  CheckCircle2,
  GitFork,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  TestTube2,
  X,
} from "lucide-react";
import type React from "react";
import { useCallback, useEffect, useState } from "react";
import type { RawGraphNode, UnifiedReviewReportUI } from "../types";

interface QualityGateDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  apiUrl: string;
  rawNodes?: RawGraphNode[];
  onFocusNode?: (nodeId: string) => void;
}

export const QualityGateDrawer: React.FC<QualityGateDrawerProps> = ({
  isOpen,
  onClose,
  apiUrl,
  rawNodes,
  onFocusNode,
}) => {
  const [scope, setScope] = useState<"changed" | "full">("changed");
  const [report, setReport] = useState<UnifiedReviewReportUI | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchReview = useCallback(
    async (targetScope: "changed" | "full") => {
      setLoading(true);
      try {
        const res = await axios.get<UnifiedReviewReportUI>(`${apiUrl}/api/review`, {
          params: { scope: targetScope, check_dependencies: false },
        });
        setReport(res.data);
      } catch {
        setReport(null);
      } finally {
        setLoading(false);
      }
    },
    [apiUrl]
  );

  useEffect(() => {
    if (isOpen) {
      fetchReview(scope);
    }
  }, [isOpen, scope, fetchReview]);

  if (!isOpen) return null;

  const focusSymbol = (sym: string, filePath?: string) => {
    if (!rawNodes || !onFocusNode) return;
    const match =
      rawNodes.find(
        (n) =>
          (n.name === sym || n.qualified_name === sym) && (!filePath || n.file_path === filePath)
      ) || rawNodes.find((n) => n.name === sym || n.qualified_name === sym);
    if (match) onFocusNode(match.id);
  };

  const cg = report?.coverage_gate;
  const covOk = (cg?.effective_coverage_pct ?? 100) >= (cg?.threshold_pct ?? 80);

  return (
    <Panel
      position="top-right"
      className="glass-panel"
      aria-label="Quality Gate Review Drawer"
      style={{
        width: 430,
        maxHeight: "calc(100vh - 120px)",
        marginTop: 88,
        marginRight: 20,
        padding: 14,
        display: "flex",
        flexDirection: "column",
        gap: 12,
        zIndex: 15,
        overflowY: "auto",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {report?.passed ? (
            <ShieldCheck size={18} color="#34d399" />
          ) : (
            <ShieldAlert size={18} color="#f97316" />
          )}
          <strong style={{ fontSize: 13 }}>Unified AI Quality Gate</strong>
          {report && (
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                padding: "2px 7px",
                borderRadius: 4,
                background: report.passed ? "rgba(16, 185, 129, 0.2)" : "rgba(249, 115, 22, 0.2)",
                color: report.passed ? "#6ee7b7" : "#fdba74",
              }}
            >
              {report.passed ? "PASS" : "ACTION REQUIRED"}
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <button
            type="button"
            className="vibe-btn"
            onClick={() => fetchReview(scope)}
            disabled={loading}
            title="Re-run Quality Gate"
            style={{ padding: "3px 7px", fontSize: 11 }}
          >
            <RefreshCw size={12} className={loading ? "spin" : ""} />
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close Quality Gate"
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text-dim)",
              cursor: "pointer",
              padding: 4,
            }}
          >
            <X size={15} />
          </button>
        </div>
      </div>

      {/* Scope Toggle */}
      <div style={{ display: "flex", gap: 6 }}>
        <button
          type="button"
          className={`vibe-btn ${scope === "changed" ? "vibe-btn-active" : ""}`}
          onClick={() => setScope("changed")}
          style={{ flex: 1, fontSize: 11, justifyContent: "center" }}
        >
          Changed Code ({report?.changed_files.length ?? 0} files)
        </button>
        <button
          type="button"
          className={`vibe-btn ${scope === "full" ? "vibe-btn-active" : ""}`}
          onClick={() => setScope("full")}
          style={{ flex: 1, fontSize: 11, justifyContent: "center" }}
        >
          Full Repository
        </button>
      </div>

      {report && (
        <>
          {/* 3 Gate Summary Cards */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
            <div
              style={{
                padding: 8,
                borderRadius: 6,
                background: "rgba(15, 23, 42, 0.65)",
                border: "1px solid rgba(255,255,255,0.08)",
                fontSize: 11,
              }}
            >
              <div style={{ color: "var(--text-dim)", fontSize: 10 }}>Security & IaC</div>
              <div style={{ fontWeight: 700, fontSize: 14, marginTop: 2 }}>
                {report.unapproved_security_count + report.modified_security_count === 0 ? (
                  <span style={{ color: "#34d399" }}>0 Issues</span>
                ) : (
                  <span style={{ color: "#ef4444" }}>
                    {report.unapproved_security_count + report.modified_security_count} Active
                  </span>
                )}
              </div>
            </div>

            <div
              style={{
                padding: 8,
                borderRadius: 6,
                background: "rgba(15, 23, 42, 0.65)",
                border: "1px solid rgba(255,255,255,0.08)",
                fontSize: 11,
              }}
            >
              <div style={{ color: "var(--text-dim)", fontSize: 10 }}>Split Integrity</div>
              <div style={{ fontWeight: 700, fontSize: 14, marginTop: 2 }}>
                {report.new_splits_count + report.modified_splits_count === 0 ? (
                  <span style={{ color: "#34d399" }}>Verified</span>
                ) : (
                  <span style={{ color: "#fcd34d" }}>
                    {report.new_splits_count + report.modified_splits_count} Pending
                  </span>
                )}
              </div>
            </div>

            <div
              style={{
                padding: 8,
                borderRadius: 6,
                background: "rgba(15, 23, 42, 0.65)",
                border: "1px solid rgba(255,255,255,0.08)",
                fontSize: 11,
              }}
            >
              <div style={{ color: "var(--text-dim)", fontSize: 10 }}>
                Test Gate ({cg?.threshold_pct ?? 80}%)
              </div>
              <div
                style={{
                  fontWeight: 700,
                  fontSize: 14,
                  marginTop: 2,
                  color: covOk ? "#34d399" : "#fcd34d",
                }}
              >
                {cg?.effective_coverage_pct ?? 100}%
              </div>
            </div>
          </div>

          {/* Coverage Bar */}
          {cg && (
            <div style={{ fontSize: 11, display: "flex", flexDirection: "column", gap: 4 }}>
              <div style={{ display: "flex", justifyContent: "space-between", color: "#cbd5e1" }}>
                <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <TestTube2 size={12} color="#818cf8" />
                  Coverage ({cg.coverage_source})
                </span>
                <span>
                  {cg.tested_symbols_count}/{cg.total_target_symbols} symbols tested
                </span>
              </div>
              <div
                style={{
                  height: 6,
                  borderRadius: 3,
                  background: "rgba(255,255,255,0.08)",
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${Math.min(100, cg.effective_coverage_pct)}%`,
                    height: "100%",
                    background: covOk ? "#10b981" : "#f59e0b",
                  }}
                />
              </div>
            </div>
          )}

          {/* Actionable Splits List */}
          {report.actionable_splits.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <div style={{ fontSize: 10.5, fontWeight: 600, color: "#fcd34d" }}>
                UNAPPROVED / MODIFIED SPLITS ({report.actionable_splits.length})
              </div>
              {report.actionable_splits.slice(0, 8).map((s) => (
                <button
                  key={`${s.file_path}:${s.qualified_name}`}
                  type="button"
                  onClick={() => focusSymbol(s.symbol, s.file_path)}
                  style={{
                    all: "unset",
                    cursor: "pointer",
                    fontSize: 11,
                    padding: "5px 8px",
                    borderRadius: 5,
                    background: "rgba(245, 158, 11, 0.1)",
                    border: "1px solid rgba(245, 158, 11, 0.25)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                  }}
                >
                  <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <GitFork size={12} color="#fcd34d" />
                    <strong>{s.symbol}</strong>
                    <span style={{ color: "var(--text-dim)", fontSize: 10 }}>
                      ({s.file_path}:L{s.start_line})
                    </span>
                  </span>
                  <span style={{ fontSize: 10, color: "#fcd34d" }}>{s.status}</span>
                </button>
              ))}
            </div>
          )}

          {/* Security / IaC / Git Secret Findings List */}
          {report.security_findings.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <div style={{ fontSize: 10.5, fontWeight: 600, color: "#fca5a5" }}>
                SECURITY, IAC & SECRET FINDINGS ({report.security_findings.length})
              </div>
              {report.security_findings.slice(0, 8).map((f) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => focusSymbol(f.symbol_name || "", f.file_path)}
                  style={{
                    all: "unset",
                    cursor: "pointer",
                    fontSize: 11,
                    padding: "5px 8px",
                    borderRadius: 5,
                    background: "rgba(239, 68, 68, 0.12)",
                    border: "1px solid rgba(239, 68, 68, 0.3)",
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <strong>
                      [{f.severity}] {f.cwe_id} · {f.title}
                    </strong>
                    <span style={{ fontSize: 9.5, opacity: 0.8 }}>{f.category}</span>
                  </div>
                  <span style={{ fontSize: 10, color: "var(--text-dim)" }}>
                    {f.file_path}:L{f.start_line}
                  </span>
                </button>
              ))}
            </div>
          )}

          {/* Untested Symbols */}
          {cg && cg.untested_symbols.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <div style={{ fontSize: 10.5, fontWeight: 600, color: "#94a3b8" }}>
                UNTESTED SYMBOLS ({cg.untested_symbols.length})
              </div>
              {cg.untested_symbols.slice(0, 6).map((u) => (
                <button
                  key={`${u.file_path}:${u.symbol}:${u.start_line}`}
                  type="button"
                  onClick={() => focusSymbol(u.symbol, u.file_path)}
                  style={{
                    all: "unset",
                    cursor: "pointer",
                    fontSize: 11,
                    padding: "4px 8px",
                    borderRadius: 5,
                    background: "rgba(255, 255, 255, 0.04)",
                    display: "flex",
                    justifyContent: "space-between",
                  }}
                >
                  <span>{u.symbol}</span>
                  <span style={{ fontSize: 10, color: "var(--text-dim)" }}>
                    {u.file_path}:L{u.start_line}
                  </span>
                </button>
              ))}
            </div>
          )}

          {report.passed && report.actions_required.length === 0 && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 11.5,
                color: "#6ee7b7",
                padding: 8,
                borderRadius: 6,
                background: "rgba(16, 185, 129, 0.1)",
              }}
            >
              <CheckCircle2 size={14} />
              All security, IaC, secret, split, and coverage checks pass!
            </div>
          )}
        </>
      )}
    </Panel>
  );
};
