import axios from "axios";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  GitFork,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import type React from "react";
import { useEffect, useState } from "react";

interface BranchDetail {
  target_name: string;
  target_file: string;
  relation_type: string;
  call_line?: number | null;
}

interface ArchitecturalSplit {
  node_id: string;
  symbol: string;
  qualified_name: string;
  file_path: string;
  start_line: number;
  end_line: number;
  code_hash: string;
  branches_count: number;
  branches: BranchDetail[];
  status: string; // APPROVED | MODIFIED_CODE | NEW_UNAPPROVED | RISK
  baseline_hash?: string | null;
  rationale?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
}

interface SplitAuditResponse {
  total_splits: number;
  approved_count: number;
  modified_count: number;
  new_unapproved_count: number;
  splits: ArchitecturalSplit[];
}

interface SplitAuditCardProps {
  apiUrl: string;
  symbolName: string;
  qualifiedName?: string | null;
  filePath?: string;
}

export const SplitAuditCard: React.FC<SplitAuditCardProps> = ({
  apiUrl,
  symbolName,
  qualifiedName,
  filePath,
}) => {
  const [split, setSplit] = useState<ArchitecturalSplit | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);
  const [rationale, setRationale] = useState("Architectural review verified");
  const [approving, setApproving] = useState(false);

  useEffect(() => {
    if (!symbolName) return;

    let cancelled = false;
    setLoading(true);

    axios
      .get<SplitAuditResponse>(`${apiUrl}/api/splits/audit`, {
        params: { symbol: symbolName },
      })
      .then((res) => {
        if (!cancelled) {
          const match =
            res.data.splits.find(
              (s) =>
                s.symbol === symbolName || (qualifiedName && s.qualified_name === qualifiedName)
            ) || null;
          setSplit(match);
        }
      })
      .catch(() => {
        if (!cancelled) setSplit(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [apiUrl, symbolName, qualifiedName]);

  const handleApprove = async () => {
    if (!split) return;
    setApproving(true);
    try {
      await axios.post(`${apiUrl}/api/splits/approve`, {
        symbol: split.symbol,
        qualified_name: split.qualified_name,
        file_path: split.file_path || filePath || "",
        code_hash: split.code_hash,
        branches: split.branches.map((b) => b.target_name),
        status: "approved",
        rationale: rationale || "Architectural review verified",
        reviewed_by: "developer",
      });

      setSplit({
        ...split,
        status: "APPROVED",
        baseline_hash: split.code_hash,
        rationale: rationale || "Architectural review verified",
        reviewed_by: "developer",
        reviewed_at: new Date().toISOString(),
      });
      setShowPrompt(false);
    } catch {
      // Keep state on failure
    } finally {
      setApproving(false);
    }
  };

  if (loading || !split) return null;

  const isApproved = split.status === "APPROVED";
  const isModified = split.status === "MODIFIED_CODE";
  const isRisk = split.status === "RISK";

  let badgeBorder = "rgba(245, 158, 11, 0.35)";
  let badgeBg = "rgba(245, 158, 11, 0.12)";
  let badgeColor = "#fcd34d";
  let statusText = `Split: ${split.branches_count} Routes (Unreviewed)`;

  if (isApproved) {
    badgeBorder = "rgba(16, 185, 129, 0.35)";
    badgeBg = "rgba(16, 185, 129, 0.12)";
    badgeColor = "#6ee7b7";
    statusText = `Approved Split (${split.branches_count} Routes)`;
  } else if (isModified) {
    badgeBorder = "rgba(239, 68, 68, 0.4)";
    badgeBg = "rgba(239, 68, 68, 0.15)";
    badgeColor = "#fca5a5";
    statusText = "Split Modified (Hash Changed)";
  } else if (isRisk) {
    badgeBorder = "rgba(244, 63, 94, 0.45)";
    badgeBg = "rgba(244, 63, 94, 0.18)";
    badgeColor = "#fda4af";
    statusText = "Flagged Architectural Risk";
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "8px 10px",
        borderRadius: 8,
        background: "rgba(15, 23, 42, 0.65)",
        border: `1px solid ${badgeBorder}`,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            background: badgeBg,
            border: `1px solid ${badgeBorder}`,
            color: badgeColor,
            padding: "4px 8px",
            borderRadius: 6,
            fontSize: "11px",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          {isApproved ? (
            <ShieldCheck size={14} />
          ) : isModified ? (
            <AlertTriangle size={14} />
          ) : isRisk ? (
            <ShieldAlert size={14} />
          ) : (
            <GitFork size={14} />
          )}
          <span>{statusText}</span>
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        </button>

        {!isApproved && !showPrompt && (
          <button
            type="button"
            onClick={() => setShowPrompt(true)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              fontSize: "11px",
              padding: "4px 8px",
              borderRadius: 6,
              background: "#4f46e5",
              color: "#fff",
              border: "none",
              cursor: "pointer",
              fontWeight: 500,
            }}
          >
            <Check size={12} />
            <span>{isModified ? "Re-Approve" : "Approve Split"}</span>
          </button>
        )}
      </div>

      {showPrompt && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 6,
            padding: "8px",
            borderRadius: 6,
            background: "rgba(0, 0, 0, 0.5)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
          }}
        >
          <div style={{ fontSize: "11px", color: "var(--text-dim)" }}>
            Approve SHA-256 baseline (<code>{split.code_hash.slice(0, 8)}</code>
            ):
          </div>
          <input
            type="text"
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            placeholder="Review rationale..."
            style={{
              padding: "4px 8px",
              fontSize: "11px",
              borderRadius: 4,
              background: "rgba(255, 255, 255, 0.08)",
              border: "1px solid rgba(255, 255, 255, 0.15)",
              color: "#f8fafc",
            }}
          />
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              gap: 6,
              marginTop: 2,
            }}
          >
            <button
              type="button"
              onClick={() => setShowPrompt(false)}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--text-dim)",
                fontSize: "11px",
                cursor: "pointer",
                padding: "2px 6px",
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={approving}
              onClick={handleApprove}
              style={{
                background: "#10b981",
                border: "none",
                borderRadius: 4,
                color: "#fff",
                fontSize: "11px",
                fontWeight: 600,
                padding: "3px 8px",
                cursor: approving ? "wait" : "pointer",
              }}
            >
              {approving ? "Saving..." : "Confirm Baseline"}
            </button>
          </div>
        </div>
      )}

      {isApproved && split.rationale && (
        <div
          style={{
            fontSize: "10.5px",
            color: "#94a3b8",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <span>Rationale: {split.rationale}</span>
          <span style={{ color: "#64748b" }}>({split.code_hash.slice(0, 8)})</span>
        </div>
      )}

      {expanded && (
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
          <div
            style={{
              color: "var(--text-dim)",
              fontWeight: 600,
              fontSize: "10px",
              marginBottom: 2,
            }}
          >
            DISPATCH TARGETS ({split.branches.length})
          </div>
          {split.branches.map((b) => (
            <div
              key={`${b.target_file}:${b.target_name}:${b.call_line ?? 0}`}
              style={{
                display: "flex",
                flexDirection: "column",
                borderLeft: "2px solid #6366f1",
                paddingLeft: 6,
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  color: "#cbd5e1",
                }}
              >
                <span>{b.target_name}</span>
                <span style={{ color: "#818cf8" }}>{b.relation_type}</span>
              </div>
              <span style={{ color: "#64748b", fontSize: "9.5px" }}>
                {b.target_file}
                {b.call_line ? `:L${b.call_line}` : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
