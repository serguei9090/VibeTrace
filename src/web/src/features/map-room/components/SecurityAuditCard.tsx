import axios from "axios";
import {
  Check,
  ChevronDown,
  ChevronUp,
  ClipboardCopy,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import type React from "react";
import { useEffect, useState } from "react";
import type { SecurityAuditReportUI, SecurityFindingUI } from "../types";

interface SecurityAuditCardProps {
  apiUrl: string;
  filePath?: string;
  startLine?: number;
  endLine?: number;
}

const SEVERITY_COLOR: Record<string, string> = {
  CRITICAL: "#ef4444",
  HIGH: "#f97316",
  MEDIUM: "#eab308",
  LOW: "#60a5fa",
  INFO: "#94a3b8",
};

const buildFixPrompt = (f: SecurityFindingUI): string =>
  [
    `Fix security finding ${f.cwe_id} (${f.severity}) "${f.title}" in ${f.file_path}:${f.start_line}-${f.end_line}.`,
    f.message,
    f.remediation_guidance ? `Guidance: ${f.remediation_guidance}` : "",
    f.compliant_snippet ? `Compliant example:\n${f.compliant_snippet}` : "",
  ]
    .filter(Boolean)
    .join("\n\n");

export const SecurityAuditCard: React.FC<SecurityAuditCardProps> = ({
  apiUrl,
  filePath,
  startLine,
  endLine,
}) => {
  const [findings, setFindings] = useState<SecurityFindingUI[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [rationale, setRationale] = useState("Security review verified");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  useEffect(() => {
    if (!filePath || filePath === "external") {
      setFindings([]);
      return;
    }
    let cancelled = false;
    axios
      .get<SecurityAuditReportUI>(`${apiUrl}/api/security/audit`, {
        params: { file_path: filePath, skip_deps: true },
      })
      .then((res) => {
        if (cancelled) return;
        const inRange = res.data.findings.filter(
          (f) =>
            startLine == null ||
            endLine == null ||
            (f.start_line <= endLine && f.end_line >= startLine)
        );
        setFindings(inRange);
      })
      .catch(() => {
        if (!cancelled) setFindings([]);
      });
    return () => {
      cancelled = true;
    };
  }, [apiUrl, filePath, startLine, endLine]);

  const approve = async (f: SecurityFindingUI) => {
    setBusyId(f.id);
    try {
      await axios.post(`${apiUrl}/api/security/approve`, {
        finding_id: f.id,
        rule_id: f.rule_id,
        file_path: f.file_path,
        code_hash: f.code_hash,
        severity: f.severity,
        rationale: rationale || "Security review verified",
        status: "APPROVED",
        reviewed_by: "developer",
      });
      setFindings((prev) =>
        prev.map((x) => (x.id === f.id ? { ...x, status: "APPROVED", rationale } : x))
      );
    } finally {
      setBusyId(null);
    }
  };

  const copyPrompt = async (f: SecurityFindingUI) => {
    await navigator.clipboard.writeText(buildFixPrompt(f));
    setCopiedId(f.id);
    setTimeout(() => setCopiedId(null), 1500);
  };

  if (findings.length === 0) return null;

  return (
    <div
      style={{
        border: "1px solid rgba(239, 68, 68, 0.35)",
        background: "rgba(15, 23, 42, 0.75)",
        borderRadius: 8,
        padding: 8,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      {findings.map((f) => {
        const open = openId === f.id;
        const approved = f.status === "APPROVED";
        const color = SEVERITY_COLOR[f.severity] ?? "#94a3b8";
        return (
          <div key={f.id} style={{ fontSize: 11, color: "#e2e8f0" }}>
            <button
              type="button"
              onClick={() => setOpenId(open ? null : f.id)}
              aria-expanded={open}
              style={{
                all: "unset",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 6,
                width: "100%",
              }}
            >
              {approved ? (
                <ShieldCheck size={13} color="#34d399" />
              ) : (
                <ShieldAlert size={13} color={color} />
              )}
              <span style={{ color, fontWeight: 700 }}>{f.severity}</span>
              <span style={{ flex: 1 }}>
                {f.cwe_id} · {f.title} (L{f.start_line})
              </span>
              {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            </button>
            {open && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
                <div>{f.message}</div>
                {f.remediation_guidance && <div>{f.remediation_guidance}</div>}
                {f.non_compliant_snippet && (
                  <pre style={{ margin: 0, color: "#fca5a5", whiteSpace: "pre-wrap" }}>
                    {f.non_compliant_snippet}
                  </pre>
                )}
                {f.compliant_snippet && (
                  <pre style={{ margin: 0, color: "#6ee7b7", whiteSpace: "pre-wrap" }}>
                    {f.compliant_snippet}
                  </pre>
                )}
                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <button type="button" onClick={() => copyPrompt(f)}>
                    {copiedId === f.id ? <Check size={11} /> : <ClipboardCopy size={11} />} Copy AI
                    fix prompt
                  </button>
                  {!approved && (
                    <>
                      <input
                        aria-label="Approval rationale"
                        value={rationale}
                        onChange={(e) => setRationale(e.target.value)}
                        style={{ flex: 1, fontSize: 11 }}
                      />
                      <button type="button" disabled={busyId === f.id} onClick={() => approve(f)}>
                        Approve exception
                      </button>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};
