import axios from "axios";
import {
  CheckCircle2,
  GitFork,
  PhoneCall,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  TestTube2,
} from "lucide-react";
import type React from "react";
import { useCallback, useEffect, useState } from "react";
import type {
  RawGraphNode,
  SecurityAuditReportUI,
  SecurityFindingUI,
  UnifiedReviewReportUI,
} from "../../map-room/types";
import { DashboardTriageTables, type SplitItemUI } from "./DashboardTriageTables";

interface DashboardViewProps {
  apiUrl: string;
  rawNodes: RawGraphNode[];
  onSwitchToMapAndFocus: (nodeId: string) => void;
}

interface SplitsAuditResponseUI {
  total_splits: number;
  unapproved_count: number;
  modified_count: number;
  approved_count: number;
  splits: SplitItemUI[];
}

export const DashboardView: React.FC<DashboardViewProps> = ({
  apiUrl,
  rawNodes,
  onSwitchToMapAndFocus,
}) => {
  const [scope, setScope] = useState<"changed" | "full">("full");
  const [checkDeps, setCheckDeps] = useState(false);
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<UnifiedReviewReportUI | null>(null);
  const [allSplits, setAllSplits] = useState<SplitItemUI[]>([]);
  const [allSecurityFindings, setAllSecurityFindings] = useState<SecurityFindingUI[]>([]);
  const [splitsSummary, setSplitsSummary] = useState({
    total: 0,
    approved: 0,
    unapproved: 0,
    modified: 0,
  });

  const fetchAll = useCallback(
    async (targetScope: "changed" | "full", includeDeps: boolean) => {
      setLoading(true);
      try {
        const [revRes, splitsRes, secRes] = await Promise.all([
          axios.get<UnifiedReviewReportUI>(`${apiUrl}/api/review`, {
            params: { scope: targetScope, check_dependencies: includeDeps },
          }),
          axios
            .get<SplitsAuditResponseUI>(`${apiUrl}/api/splits/audit`)
            .catch(() => ({ data: null })),
          axios
            .get<SecurityAuditReportUI>(`${apiUrl}/api/security/audit`, {
              params: { skip_deps: !includeDeps },
            })
            .catch(() => ({ data: null })),
        ]);

        setReport(revRes.data);
        if (splitsRes.data) {
          setAllSplits(splitsRes.data.splits || []);
          setSplitsSummary({
            total: splitsRes.data.total_splits || 0,
            approved: splitsRes.data.approved_count || 0,
            unapproved: splitsRes.data.unapproved_count || 0,
            modified: splitsRes.data.modified_count || 0,
          });
        } else {
          setAllSplits(revRes.data.actionable_splits || []);
        }
        if (secRes.data) {
          setAllSecurityFindings(secRes.data.findings || []);
        } else {
          setAllSecurityFindings(revRes.data.security_findings || []);
        }
      } catch {
        // keep last report if offline
      } finally {
        setLoading(false);
      }
    },
    [apiUrl]
  );

  useEffect(() => {
    fetchAll(scope, checkDeps);
  }, [scope, checkDeps, fetchAll]);

  const handleInspectOnMap = (sym: string, filePath?: string) => {
    const match =
      rawNodes.find(
        (n) =>
          (n.name === sym || n.qualified_name === sym) && (!filePath || n.file_path === filePath)
      ) || rawNodes.find((n) => n.name === sym || n.qualified_name === sym);
    if (match) {
      onSwitchToMapAndFocus(match.id);
    }
  };

  const handleApproveSplit = async (split: SplitItemUI, status: "approved" | "risk") => {
    await axios.post(`${apiUrl}/api/splits/approve`, {
      symbol: split.symbol,
      qualified_name: split.qualified_name,
      file_path: split.file_path,
      code_hash: split.code_hash,
      branches: split.branches || [],
      status,
      rationale: "Verified via Quality Gate Dashboard",
    });
    await fetchAll(scope, checkDeps);
  };

  const handleApproveSecurity = async (finding: SecurityFindingUI, tag: string) => {
    await axios.post(`${apiUrl}/api/security/approve`, {
      finding_id: finding.id,
      rule_id: finding.rule_id,
      file_path: finding.file_path,
      code_hash: finding.code_hash,
      severity: finding.severity,
      category: finding.category,
      symbol_name: finding.symbol_name,
      status: "APPROVED",
      tag,
      rationale: "Accepted via Quality Gate Dashboard",
    });
    await fetchAll(scope, checkDeps);
  };

  const cg = report?.coverage_gate;
  const covPct = cg?.effective_coverage_pct ?? 100;
  const covThreshold = cg?.threshold_pct ?? 80;
  const covOk = covPct >= covThreshold;
  const secIssueCount =
    (report?.unapproved_security_count || 0) + (report?.modified_security_count || 0);
  const splitIssueCount = (report?.new_splits_count || 0) + (report?.modified_splits_count || 0);

  return (
    <main
      aria-label="Unified AI Quality Gate Dashboard"
      style={{
        position: "absolute",
        top: 86,
        left: 16,
        right: 16,
        bottom: 16,
        display: "flex",
        flexDirection: "column",
        gap: 14,
        overflow: "hidden",
      }}
    >
      {/* Top Control & Verdict Bar */}
      <div
        className="glass-panel"
        style={{
          padding: "14px 20px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {report?.passed ? (
            <ShieldCheck size={24} color="#34d399" />
          ) : (
            <ShieldAlert size={24} color="#f97316" />
          )}
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>Unified AI Quality Gate</h2>
              {report && (
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    padding: "2px 8px",
                    borderRadius: 4,
                    background: report.passed
                      ? "rgba(16, 185, 129, 0.2)"
                      : "rgba(249, 115, 22, 0.2)",
                    color: report.passed ? "#6ee7b7" : "#fdba74",
                    border: `1px solid ${
                      report.passed ? "rgba(16, 185, 129, 0.4)" : "rgba(249, 115, 22, 0.4)"
                    }`,
                  }}
                >
                  {report.passed ? "PASS — READY TO COMMIT" : "ACTION REQUIRED"}
                </span>
              )}
            </div>
            <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 2 }}>
              Semgrep Community + Polyglot AST · Inter-Procedural Def-Use Taint · IaC Scanner ·
              Gitleaks Entropy + DB-URI · Split Integrity · 80% Test Coverage Gate
            </div>
          </div>
        </div>

        {/* Scope & Refresh Controls */}
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div className="vibe-segmented-control">
            <button
              type="button"
              className={`vibe-segment-btn ${scope === "changed" ? "active" : ""}`}
              onClick={() => setScope("changed")}
            >
              Changed Code ({report?.changed_files?.length ?? 0} files)
            </button>
            <button
              type="button"
              className={`vibe-segment-btn ${scope === "full" ? "active" : ""}`}
              onClick={() => setScope("full")}
            >
              Full Repository
            </button>
          </div>

          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12,
              cursor: "pointer",
              padding: "0 10px",
              height: 36,
              borderRadius: 8,
              background: "rgba(255,255,255,0.03)",
              border: "1px solid var(--border-color)",
            }}
          >
            <input
              type="checkbox"
              checked={checkDeps}
              onChange={(e) => setCheckDeps(e.target.checked)}
            />
            <span>OSV Lockfile CVEs</span>
          </label>

          <button
            type="button"
            className="vibe-btn vibe-btn-primary"
            onClick={() => fetchAll(scope, checkDeps)}
            disabled={loading}
          >
            <RefreshCw size={14} className={loading ? "spin" : ""} />
            {loading ? "Scanning..." : "Re-run Gate"}
          </button>
        </div>
      </div>

      {/* 4 Executive KPI Cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 14,
        }}
      >
        {/* Card 1: Security, IaC & Git Secrets */}
        <div className="glass-panel" style={{ padding: "14px 18px" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              fontSize: 11.5,
              color: "var(--text-dim)",
              marginBottom: 6,
            }}
          >
            <span>SECURITY, IaC & GIT SECRETS</span>
            <ShieldCheck size={15} color={secIssueCount === 0 ? "#34d399" : "#f87171"} />
          </div>
          <div
            style={{
              fontSize: 22,
              fontWeight: 700,
              color: secIssueCount === 0 ? "#6ee7b7" : "#f87171",
            }}
          >
            {secIssueCount === 0 ? "0 Unapproved" : `${secIssueCount} Actionable`}
          </div>
          <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginTop: 4 }}>
            {allSecurityFindings.length} total findings tracked in{" "}
            <code>.vibetrace/security.toml</code>
          </div>
        </div>

        {/* Card 2: Architectural Split Integrity */}
        <div className="glass-panel" style={{ padding: "14px 18px" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              fontSize: 11.5,
              color: "var(--text-dim)",
              marginBottom: 6,
            }}
          >
            <span>ARCHITECTURAL SPLITS</span>
            <GitFork size={15} color={splitIssueCount === 0 ? "#a78bfa" : "#fdba74"} />
          </div>
          <div
            style={{
              fontSize: 22,
              fontWeight: 700,
              color: splitIssueCount === 0 ? "#6ee7b7" : "#fdba74",
            }}
          >
            {splitIssueCount === 0
              ? `${splitsSummary.approved || splitsSummary.total} Verified`
              : `${splitIssueCount} Tripwires`}
          </div>
          <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginTop: 4 }}>
            {splitsSummary.total} multi-route bifurcations hashed in{" "}
            <code>.vibetrace/splits.toml</code>
          </div>
        </div>

        {/* Card 3: Test Coverage Quality Gate (80%) */}
        <div className="glass-panel" style={{ padding: "14px 18px" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              fontSize: 11.5,
              color: "var(--text-dim)",
              marginBottom: 6,
            }}
          >
            <span>TEST COVERAGE GATE ({covThreshold}%)</span>
            <TestTube2 size={15} color={covOk ? "#34d399" : "#fbbf24"} />
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
            <span
              style={{
                fontSize: 22,
                fontWeight: 700,
                color: covOk ? "#6ee7b7" : "#fbbf24",
              }}
            >
              {covPct}%
            </span>
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
              ({cg?.tested_symbols_count ?? 0} / {cg?.total_target_symbols ?? 0} symbols tested)
            </span>
          </div>
          <div
            style={{
              height: 6,
              borderRadius: 3,
              background: "rgba(255, 255, 255, 0.08)",
              overflow: "hidden",
              marginTop: 8,
            }}
          >
            <div
              style={{
                width: `${Math.min(100, covPct)}%`,
                height: "100%",
                background: covOk ? "#10b981" : "#f59e0b",
                transition: "width 0.3s ease",
              }}
            />
          </div>
        </div>

        {/* Card 4: Caller Guard & Blast Radius */}
        <div className="glass-panel" style={{ padding: "14px 18px" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              fontSize: 11.5,
              color: "var(--text-dim)",
              marginBottom: 6,
            }}
          >
            <span>CALLER GUARD & BLAST RADIUS</span>
            <PhoneCall size={15} color="#38bdf8" />
          </div>
          <div style={{ fontSize: 22, fontWeight: 700, color: "#e0f2fe" }}>
            {report?.modified_symbols_with_callers ?? 0} Active Callers
          </div>
          <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginTop: 4 }}>
            Source:{" "}
            <code>
              {cg?.coverage_source === "ast_call_graph"
                ? "AST Call-Graph Reachability"
                : cg?.coverage_source || "AST Call-Graph"}
            </code>
          </div>
        </div>
      </div>

      {/* Interactive Triage Tables */}
      {report ? (
        <DashboardTriageTables
          report={report}
          allSplits={allSplits}
          allSecurityFindings={allSecurityFindings}
          onRefresh={() => fetchAll(scope, checkDeps)}
          onApproveSplit={handleApproveSplit}
          onApproveSecurity={handleApproveSecurity}
          onInspectSymbolOnMap={handleInspectOnMap}
        />
      ) : (
        <div
          className="glass-panel"
          style={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--text-dim)",
            fontSize: 13,
          }}
        >
          <CheckCircle2 size={18} style={{ marginRight: 8 }} />
          Running Unified AI Quality Gate scan...
        </div>
      )}
    </main>
  );
};
