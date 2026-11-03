import axios from "axios";
import { Check, ChevronDown, ChevronUp, ClipboardCopy, Gauge, Sparkles } from "lucide-react";
import type React from "react";
import { useEffect, useState } from "react";
import type { FunctionComplexityUI, QualityAuditReportUI, QualityFindingUI } from "../types";

interface QualityAuditCardProps {
  apiUrl: string;
  filePath?: string;
  symbolName?: string;
  startLine?: number;
  endLine?: number;
}

const SEVERITY_COLOR: Record<string, string> = {
  HIGH: "#f97316",
  MEDIUM: "#eab308",
  LOW: "#60a5fa",
  INFO: "#94a3b8",
};

const getComplexityBadge = (score: number) => {
  if (score <= 15) {
    return {
      bg: "rgba(16, 185, 129, 0.15)",
      border: "rgba(16, 185, 129, 0.4)",
      color: "#34d399",
      label: "Low (Compliant)",
    };
  }
  if (score <= 25) {
    return {
      bg: "rgba(234, 179, 8, 0.15)",
      border: "rgba(234, 179, 8, 0.4)",
      color: "#facc15",
      label: "Moderate",
    };
  }
  return {
    bg: "rgba(239, 68, 68, 0.15)",
    border: "rgba(239, 68, 68, 0.4)",
    color: "#f87171",
    label: "High (Refactor)",
  };
};

const buildFixPrompt = (f: QualityFindingUI): string =>
  [
    `Fix quality smell ${f.rule_id} (${f.severity}) "${f.title}" in ${f.file_path}:${f.start_line}-${f.end_line}.`,
    f.message,
    f.remediation_guidance ? `Guidance: ${f.remediation_guidance}` : "",
    f.compliant_snippet ? `Compliant pattern:\n${f.compliant_snippet}` : "",
  ]
    .filter(Boolean)
    .join("\n\n");

export const QualityAuditCard: React.FC<QualityAuditCardProps> = ({
  apiUrl,
  filePath,
  symbolName,
  startLine,
  endLine,
}) => {
  const [report, setReport] = useState<QualityAuditReportUI | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [rationale, setRationale] = useState("Code quality review verified");
  const [tag, setTag] = useState("intentional");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  useEffect(() => {
    if (!filePath || filePath === "external") {
      setReport(null);
      return;
    }
    let cancelled = false;
    axios
      .get<QualityAuditReportUI>(`${apiUrl}/api/quality/audit`, {
        params: { file_path: filePath, threshold: 15 },
      })
      .then((res) => {
        if (cancelled) return;
        setReport(res.data);
      })
      .catch(() => {
        if (!cancelled) setReport(null);
      });
    return () => {
      cancelled = true;
    };
  }, [apiUrl, filePath]);

  if (!report) return null;

  // Filter complexities and findings for currently selected symbol/range if present
  const currentComplexity: FunctionComplexityUI | undefined = symbolName
    ? report.complexities.find((c) => c.name === symbolName)
    : report.complexities.find(
        (c) =>
          startLine != null && endLine != null && c.start_line <= endLine && c.end_line >= startLine
      );

  const relevantFindings = report.findings.filter(
    (f) =>
      startLine == null || endLine == null || (f.start_line <= endLine && f.end_line >= startLine)
  );

  const approve = async (f: QualityFindingUI) => {
    setBusyId(f.id);
    try {
      await axios.post(`${apiUrl}/api/quality/approve`, {
        finding_id: f.id,
        rule_id: f.rule_id,
        file_path: f.file_path,
        code_hash: f.code_hash,
        severity: f.severity,
        rationale: rationale || "Code quality review verified",
        status: "APPROVED",
        tag,
        reviewed_by: "developer",
      });
      setReport((prev) =>
        prev
          ? {
              ...prev,
              findings: prev.findings.map((x) =>
                x.id === f.id ? { ...x, status: "APPROVED", tag, rationale } : x
              ),
            }
          : null
      );
    } finally {
      setBusyId(null);
    }
  };

  const copyPrompt = async (f: QualityFindingUI) => {
    await navigator.clipboard.writeText(buildFixPrompt(f));
    setCopiedId(f.id);
    setTimeout(() => setCopiedId(null), 1500);
  };

  const badge = currentComplexity
    ? getComplexityBadge(currentComplexity.cognitive_complexity)
    : null;

  return (
    <div
      style={{
        border: "1px solid rgba(99, 102, 241, 0.35)",
        background: "rgba(15, 23, 42, 0.75)",
        borderRadius: 8,
        padding: 8,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      {/* Executive Summary Matrix */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderBottom: "1px solid rgba(255, 255, 255, 0.08)",
          paddingBottom: 6,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Gauge size={14} color="#818cf8" />
          <span style={{ fontSize: 11, fontWeight: 700, color: "#e2e8f0" }}>
            Quality & Complexity Matrix
          </span>
        </div>
        {badge && currentComplexity && (
          <span
            style={{
              fontSize: 10,
              padding: "2px 6px",
              borderRadius: 4,
              background: badge.bg,
              border: `1px solid ${badge.border}`,
              color: badge.color,
              fontWeight: 600,
            }}
          >
            Cognitive: {currentComplexity.cognitive_complexity} · {badge.label}
          </span>
        )}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr",
          gap: 6,
          fontSize: 10,
          color: "#94a3b8",
        }}
      >
        <div
          style={{
            background: "rgba(255, 255, 255, 0.03)",
            padding: "4px 6px",
            borderRadius: 4,
          }}
        >
          Functions: <strong style={{ color: "#e2e8f0" }}>{report.total_functions_audited}</strong>
        </div>
        <div
          style={{
            background: "rgba(255, 255, 255, 0.03)",
            padding: "4px 6px",
            borderRadius: 4,
          }}
        >
          High Score:{" "}
          <strong
            style={{
              color: report.high_complexity_functions > 0 ? "#f87171" : "#34d399",
            }}
          >
            {report.high_complexity_functions}
          </strong>
        </div>
        <div
          style={{
            background: "rgba(255, 255, 255, 0.03)",
            padding: "4px 6px",
            borderRadius: 4,
          }}
        >
          Smells:{" "}
          <strong
            style={{
              color: report.unapproved_count > 0 ? "#fbbf24" : "#34d399",
            }}
          >
            {relevantFindings.length}
          </strong>
        </div>
      </div>

      {/* Clean Code Findings List */}
      {relevantFindings.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {relevantFindings.map((f) => {
            const open = openId === f.id;
            const approved = f.status === "APPROVED";
            const color = SEVERITY_COLOR[f.severity] ?? "#94a3b8";
            return (
              <div
                key={f.id}
                style={{
                  fontSize: 11,
                  color: "#e2e8f0",
                  background: "rgba(0, 0, 0, 0.2)",
                  padding: 6,
                  borderRadius: 6,
                }}
              >
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
                  <Sparkles size={13} color={approved ? "#34d399" : color} />
                  <span style={{ color, fontWeight: 700, fontSize: 10 }}>{f.severity}</span>
                  <span
                    style={{
                      fontSize: 9,
                      padding: "1px 5px",
                      borderRadius: 4,
                      background: "rgba(99, 102, 241, 0.2)",
                      color: "#c7d2fe",
                      textTransform: "uppercase",
                    }}
                  >
                    {f.rule_id.split(":").pop() ?? f.rule_id}
                  </span>
                  <span
                    style={{
                      flex: 1,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {f.title}
                  </span>
                  {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                </button>

                {open && (
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 6,
                      marginTop: 6,
                    }}
                  >
                    <div style={{ color: "#cbd5e1" }}>{f.message}</div>
                    {f.remediation_guidance && (
                      <div style={{ color: "#93c5fd", fontSize: 10 }}>{f.remediation_guidance}</div>
                    )}
                    {f.non_compliant_snippet && (
                      <pre
                        style={{
                          margin: 0,
                          color: "#fca5a5",
                          whiteSpace: "pre-wrap",
                          fontSize: 10,
                          background: "rgba(0,0,0,0.3)",
                          padding: 4,
                          borderRadius: 4,
                        }}
                      >
                        {f.non_compliant_snippet}
                      </pre>
                    )}
                    {f.compliant_snippet && (
                      <pre
                        style={{
                          margin: 0,
                          color: "#6ee7b7",
                          whiteSpace: "pre-wrap",
                          fontSize: 10,
                          background: "rgba(0,0,0,0.3)",
                          padding: 4,
                          borderRadius: 4,
                        }}
                      >
                        {f.compliant_snippet}
                      </pre>
                    )}
                    {approved && (f.rationale || f.tag) && (
                      <div style={{ color: "#34d399", fontSize: 10 }}>
                        Approved{f.tag ? ` [${f.tag}]` : ""}: {f.rationale}
                      </div>
                    )}
                    <div
                      style={{
                        display: "flex",
                        gap: 6,
                        alignItems: "center",
                        marginTop: 2,
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => copyPrompt(f)}
                        style={{
                          fontSize: 10,
                          padding: "2px 6px",
                          display: "flex",
                          alignItems: "center",
                          gap: 4,
                        }}
                      >
                        {copiedId === f.id ? <Check size={11} /> : <ClipboardCopy size={11} />} Copy
                        Prompt
                      </button>
                      {!approved && (
                        <>
                          <select
                            aria-label="Disposition tag"
                            value={tag}
                            onChange={(e) => setTag(e.target.value)}
                            style={{ fontSize: 10, padding: "2px 4px" }}
                          >
                            <option value="intentional">intentional</option>
                            <option value="legacy">legacy</option>
                            <option value="perf-critical">perf-critical</option>
                          </select>
                          <input
                            aria-label="Approval rationale"
                            value={rationale}
                            onChange={(e) => setRationale(e.target.value)}
                            style={{ flex: 1, fontSize: 10, padding: "2px 4px" }}
                          />
                          <button
                            type="button"
                            disabled={busyId === f.id}
                            onClick={() => approve(f)}
                            style={{ fontSize: 10, padding: "2px 6px" }}
                          >
                            Approve
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
      )}
    </div>
  );
};
