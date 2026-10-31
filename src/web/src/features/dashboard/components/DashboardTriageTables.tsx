import {
  CheckCircle2,
  Compass,
  ExternalLink,
  FileCode2,
  GitFork,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  TestTube2,
} from "lucide-react";
import type React from "react";
import { useMemo, useState } from "react";
import type {
  SecurityFindingUI,
  UnifiedReviewReportUI,
  UntestedSymbolUI,
} from "../../map-room/types";

export interface SplitItemUI {
  node_id: string;
  symbol: string;
  qualified_name: string;
  file_path: string;
  start_line: number;
  end_line: number;
  code_hash: string;
  branches_count: number;
  branches?: string[];
  status: string;
  rationale?: string | null;
}

interface DashboardTriageTablesProps {
  report: UnifiedReviewReportUI;
  allSplits: SplitItemUI[];
  allSecurityFindings: SecurityFindingUI[];
  onRefresh: () => void;
  onApproveSplit: (split: SplitItemUI, status: "approved" | "risk") => Promise<void>;
  onApproveSecurity: (finding: SecurityFindingUI, tag: string) => Promise<void>;
  onInspectSymbolOnMap: (symbol: string, filePath?: string) => void;
}

type TriageTab = "security" | "splits" | "coverage" | "checklist";

export const DashboardTriageTables: React.FC<DashboardTriageTablesProps> = ({
  report,
  allSplits,
  allSecurityFindings,
  onApproveSplit,
  onApproveSecurity,
  onInspectSymbolOnMap,
}) => {
  const [activeTab, setActiveTab] = useState<TriageTab>("coverage");
  const [searchQuery, setSearchQuery] = useState("");
  const [splitFilter, setSplitFilter] = useState<"all" | "actionable">("all");
  const [secFilter, setSecFilter] = useState<"all" | "actionable">("all");
  const [secCategoryFilter, setSecCategoryFilter] = useState<string>("all");
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const untestedList: UntestedSymbolUI[] = report.coverage_gate?.untested_symbols || [];

  const filteredUntested = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return untestedList;
    return untestedList.filter(
      (u) => u.symbol.toLowerCase().includes(q) || u.file_path.toLowerCase().includes(q)
    );
  }, [untestedList, searchQuery]);

  const filteredSplits = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return allSplits.filter((s) => {
      if (splitFilter === "actionable" && s.status === "APPROVED") return false;
      if (!q) return true;
      return (
        s.qualified_name.toLowerCase().includes(q) ||
        s.symbol.toLowerCase().includes(q) ||
        s.file_path.toLowerCase().includes(q)
      );
    });
  }, [allSplits, splitFilter, searchQuery]);

  const filteredSecurity = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return allSecurityFindings.filter((f) => {
      if (secFilter === "actionable" && f.status === "APPROVED") return false;
      if (secCategoryFilter !== "all" && f.category !== secCategoryFilter) return false;
      if (!q) return true;
      return (
        f.title.toLowerCase().includes(q) ||
        f.cwe_id.toLowerCase().includes(q) ||
        f.file_path.toLowerCase().includes(q) ||
        f.category.toLowerCase().includes(q)
      );
    });
  }, [allSecurityFindings, secFilter, secCategoryFilter, searchQuery]);

  return (
    <div
      className="glass-panel"
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        border: "1px solid var(--border-color)",
      }}
    >
      {/* Sub-navigation bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 18px",
          borderBottom: "1px solid var(--border-color)",
          background: "rgba(0, 0, 0, 0.25)",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <div
          role="tablist"
          aria-label="Quality Gate Triage Categories"
          style={{ display: "flex", gap: 6 }}
        >
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "coverage"}
            className={`vibe-btn vibe-btn-sm ${activeTab === "coverage" ? "vibe-btn-active" : ""}`}
            onClick={() => setActiveTab("coverage")}
          >
            <TestTube2 size={14} color="#fbbf24" />
            Untested Symbols ({untestedList.length})
          </button>

          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "splits"}
            className={`vibe-btn vibe-btn-sm ${activeTab === "splits" ? "vibe-btn-active" : ""}`}
            onClick={() => setActiveTab("splits")}
          >
            <GitFork size={14} color="#a78bfa" />
            Architectural Splits ({allSplits.length})
          </button>

          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "security"}
            className={`vibe-btn vibe-btn-sm ${activeTab === "security" ? "vibe-btn-active" : ""}`}
            onClick={() => setActiveTab("security")}
          >
            <ShieldAlert size={14} color="#34d399" />
            Security, IaC & Secrets ({allSecurityFindings.length})
          </button>

          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "checklist"}
            className={`vibe-btn vibe-btn-sm ${activeTab === "checklist" ? "vibe-btn-active" : ""}`}
            onClick={() => setActiveTab("checklist")}
          >
            <CheckCircle2 size={14} color="#38bdf8" />
            AI Remediation Checklist ({report.actions_required?.length || 0})
          </button>
        </div>

        {/* Search & Quick Filter */}
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {activeTab === "splits" && (
            <div className="vibe-segmented-control" style={{ height: 28 }}>
              <button
                type="button"
                className={`vibe-segment-btn ${splitFilter === "all" ? "active" : ""}`}
                onClick={() => setSplitFilter("all")}
              >
                All ({allSplits.length})
              </button>
              <button
                type="button"
                className={`vibe-segment-btn ${splitFilter === "actionable" ? "active" : ""}`}
                onClick={() => setSplitFilter("actionable")}
              >
                Unapproved ({allSplits.filter((s) => s.status !== "APPROVED").length})
              </button>
            </div>
          )}

          {activeTab === "security" && (
            <div className="vibe-segmented-control" style={{ height: 28 }}>
              <button
                type="button"
                className={`vibe-segment-btn ${secFilter === "all" ? "active" : ""}`}
                onClick={() => setSecFilter("all")}
              >
                All ({allSecurityFindings.length})
              </button>
              <button
                type="button"
                className={`vibe-segment-btn ${secFilter === "actionable" ? "active" : ""}`}
                onClick={() => setSecFilter("actionable")}
              >
                Unapproved ({allSecurityFindings.filter((f) => f.status !== "APPROVED").length})
              </button>
              <button
                type="button"
                className={`vibe-segment-btn ${secCategoryFilter === "package_cve" ? "active" : ""}`}
                onClick={() =>
                  setSecCategoryFilter(secCategoryFilter === "package_cve" ? "all" : "package_cve")
                }
                title="Toggle OSV Dependency CVEs Only"
              >
                CVEs ({allSecurityFindings.filter((f) => f.category === "package_cve").length})
              </button>
            </div>
          )}

          <div style={{ position: "relative", width: 240 }}>
            <Search
              size={13}
              style={{ position: "absolute", left: 10, top: 8, color: "var(--text-dim)" }}
            />
            <input
              type="text"
              placeholder="Filter symbols or files..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="vibe-input"
              style={{ height: 28, fontSize: 12, paddingLeft: 30, width: "100%" }}
            />
          </div>
        </div>
      </div>

      {/* Tab Content Area */}
      <div style={{ flex: 1, overflowY: "auto", padding: "14px 18px" }}>
        {activeTab === "coverage" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {filteredUntested.length === 0 ? (
              <div
                style={{
                  padding: "36px",
                  textAlign: "center",
                  color: "#6ee7b7",
                  fontSize: 13,
                }}
              >
                ✅ All target symbols in this scope are covered by tests!
              </div>
            ) : (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))",
                  gap: 8,
                }}
              >
                {filteredUntested.slice(0, 120).map((u) => (
                  <div
                    key={`${u.file_path}:${u.symbol}:${u.start_line}`}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "8px 12px",
                      borderRadius: 8,
                      background: "rgba(255, 255, 255, 0.03)",
                      border: "1px solid rgba(255, 255, 255, 0.07)",
                      gap: 10,
                    }}
                  >
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div
                        style={{
                          fontSize: 12.5,
                          fontWeight: 600,
                          color: "#f8fafc",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {u.symbol}
                      </div>
                      <div
                        style={{
                          fontSize: 11,
                          color: "var(--text-dim)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {u.file_path}:L{u.start_line}
                      </div>
                    </div>
                    <button
                      type="button"
                      className="vibe-btn vibe-btn-sm"
                      onClick={() => onInspectSymbolOnMap(u.symbol, u.file_path)}
                      title="Open in Map Room and focus symbol"
                    >
                      <Compass size={12} color="#818cf8" />
                      Inspect on Map
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === "splits" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {filteredSplits.map((sp) => {
              const isApproved = sp.status === "APPROVED";
              const key = `${sp.file_path}:${sp.qualified_name}`;
              return (
                <div
                  key={key}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "10px 14px",
                    borderRadius: 8,
                    background: isApproved
                      ? "rgba(16, 185, 129, 0.05)"
                      : "rgba(249, 115, 22, 0.09)",
                    border: `1px solid ${
                      isApproved ? "rgba(16, 185, 129, 0.22)" : "rgba(249, 115, 22, 0.35)"
                    }`,
                    gap: 12,
                  }}
                >
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <strong style={{ fontSize: 13, color: "#f8fafc" }}>
                        {sp.qualified_name}
                      </strong>
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          padding: "2px 6px",
                          borderRadius: 4,
                          background: isApproved
                            ? "rgba(16, 185, 129, 0.2)"
                            : "rgba(249, 115, 22, 0.25)",
                          color: isApproved ? "#6ee7b7" : "#fdba74",
                        }}
                      >
                        {sp.status} · {sp.branches_count} routes
                      </span>
                      <code style={{ fontSize: 10.5, opacity: 0.6 }}>{sp.code_hash}</code>
                    </div>
                    <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginTop: 2 }}>
                      <FileCode2 size={11} style={{ marginRight: 4, verticalAlign: "middle" }} />
                      {sp.file_path}:L{sp.start_line}-{sp.end_line}
                      {sp.rationale && (
                        <span style={{ marginLeft: 10, color: "#a5b4fc" }}>— "{sp.rationale}"</span>
                      )}
                    </div>
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                    {!isApproved && (
                      <button
                        type="button"
                        className="vibe-btn vibe-btn-sm vibe-btn-primary"
                        disabled={busyKey === key}
                        onClick={async () => {
                          setBusyKey(key);
                          try {
                            await onApproveSplit(sp, "approved");
                          } finally {
                            setBusyKey(null);
                          }
                        }}
                      >
                        {busyKey === key ? <RefreshCw size={12} className="spin" /> : null}
                        Approve Baseline
                      </button>
                    )}
                    <button
                      type="button"
                      className="vibe-btn vibe-btn-sm"
                      onClick={() => onInspectSymbolOnMap(sp.symbol, sp.file_path)}
                    >
                      <ExternalLink size={12} />
                      Map
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {activeTab === "security" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {filteredSecurity.length === 0 ? (
              <div
                style={{
                  padding: "36px",
                  textAlign: "center",
                  color: "#6ee7b7",
                  fontSize: 13,
                }}
              >
                <ShieldCheck size={28} style={{ marginBottom: 8, opacity: 0.85 }} />
                <div>Zero security, IaC, secret, or call-graph taint vulnerabilities detected.</div>
              </div>
            ) : (
              filteredSecurity.map((f) => {
                const isApproved = f.status === "APPROVED";
                return (
                  <div
                    key={f.id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "10px 14px",
                      borderRadius: 8,
                      background: isApproved
                        ? "rgba(16, 185, 129, 0.05)"
                        : "rgba(239, 68, 68, 0.1)",
                      border: `1px solid ${
                        isApproved ? "rgba(16, 185, 129, 0.22)" : "rgba(239, 68, 68, 0.35)"
                      }`,
                      gap: 12,
                    }}
                  >
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span
                          style={{
                            fontSize: 10,
                            fontWeight: 700,
                            padding: "2px 6px",
                            borderRadius: 4,
                            background: "rgba(239, 68, 68, 0.25)",
                            color: "#fca5a5",
                          }}
                        >
                          {f.severity} · {f.cwe_id}
                        </span>
                        <span
                          style={{
                            fontSize: 10,
                            padding: "1px 6px",
                            borderRadius: 4,
                            background: "rgba(99, 102, 241, 0.2)",
                            color: "#c7d2fe",
                          }}
                        >
                          {f.category.replace("_", " ")}
                        </span>
                        <strong style={{ fontSize: 13 }}>{f.title}</strong>
                      </div>
                      <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginTop: 3 }}>
                        {f.file_path}:L{f.start_line} — {f.message}
                      </div>
                    </div>

                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      {!isApproved && (
                        <button
                          type="button"
                          className="vibe-btn vibe-btn-sm vibe-btn-primary"
                          disabled={busyKey === f.id}
                          onClick={async () => {
                            setBusyKey(f.id);
                            try {
                              await onApproveSecurity(f, "risk-accepted");
                            } finally {
                              setBusyKey(null);
                            }
                          }}
                        >
                          Approve Exception
                        </button>
                      )}
                      {f.symbol_name && (
                        <button
                          type="button"
                          className="vibe-btn vibe-btn-sm"
                          onClick={() => onInspectSymbolOnMap(f.symbol_name || "", f.file_path)}
                        >
                          <Compass size={12} />
                          Map
                        </button>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}

        {activeTab === "checklist" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {(report.actions_required || []).length === 0 ? (
              <div
                style={{
                  padding: "36px",
                  textAlign: "center",
                  color: "#6ee7b7",
                  fontSize: 13,
                }}
              >
                ✅ No remediation actions required — ready to commit!
              </div>
            ) : (
              report.actions_required.map((act) => (
                <div
                  key={act}
                  style={{
                    padding: "10px 14px",
                    borderRadius: 8,
                    background: "rgba(255, 255, 255, 0.03)",
                    border: "1px solid rgba(255, 255, 255, 0.08)",
                    fontSize: 12.5,
                    color: "#e2e8f0",
                    lineHeight: 1.45,
                  }}
                >
                  {act}
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
};
