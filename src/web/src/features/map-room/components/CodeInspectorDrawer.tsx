import { Panel } from "@xyflow/react";
import axios from "axios";
import { Code2, FileCode, X } from "lucide-react";
import type React from "react";
import { useEffect, useState } from "react";
import type { RawGraphEdge, RawGraphNode, SelectedElement } from "../types";

interface CodeInspectorDrawerProps {
  apiUrl: string;
  selectedElement: SelectedElement | null;
  rawNodes: RawGraphNode[];
  rawEdges: RawGraphEdge[];
  onClose: () => void;
}

interface SourceResponse {
  file_path: string;
  start_line: number;
  end_line: number;
  source_code: string;
}

export const CodeInspectorDrawer: React.FC<CodeInspectorDrawerProps> = ({
  apiUrl,
  selectedElement,
  rawNodes,
  rawEdges,
  onClose,
}) => {
  const [sourceData, setSourceData] = useState<SourceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [errText, setErrText] = useState<string | null>(null);

  const selectedNode =
    selectedElement?.type === "node"
      ? rawNodes.find((n) => n.id === selectedElement.id) || null
      : null;

  const selectedEdge =
    selectedElement?.type === "edge"
      ? rawEdges.find(
          (e) => `${e.from_node_id}-${e.to_node_id}-${e.relation_type}` === selectedElement.id
        ) || null
      : null;

  const callerNode = selectedEdge
    ? rawNodes.find((n) => n.id === selectedEdge.from_node_id) || null
    : null;
  const calleeNode = selectedEdge
    ? rawNodes.find((n) => n.id === selectedEdge.to_node_id) || null
    : null;

  const targetNode = selectedNode || callerNode;
  const highlightCallLine = selectedEdge?.call_line || null;

  useEffect(() => {
    if (!targetNode?.file_path || targetNode.file_path === "external") {
      setSourceData(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setErrText(null);

    axios
      .get<SourceResponse>(`${apiUrl}/source`, {
        params: {
          file_path: targetNode.file_path,
          start_line: Math.max(1, (targetNode.start_line || 1) - 1),
          end_line: (targetNode.end_line || targetNode.start_line || 20) + 2,
        },
      })
      .then((res) => {
        if (!cancelled) setSourceData(res.data);
      })
      .catch((err) => {
        if (!cancelled) {
          setErrText(err.response?.data?.detail || err.message || "Could not load source slice");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [apiUrl, targetNode]);

  if (!selectedElement || !targetNode) return null;

  return (
    <Panel
      position="bottom-left"
      className="glass-panel"
      style={{
        width: "440px",
        maxHeight: "52vh",
        marginLeft: "20px",
        marginBottom: "20px",
        padding: "14px",
        display: "flex",
        flexDirection: "column",
        gap: 10,
        zIndex: 12,
        overflow: "hidden",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "13px" }}>
            <Code2 size={15} color="#6366f1" />
            <strong>{targetNode.qualified_name || targetNode.name}</strong>
            <span
              style={{
                fontSize: "10px",
                textTransform: "uppercase",
                padding: "1px 6px",
                borderRadius: 4,
                background: "rgba(99, 102, 241, 0.2)",
                color: "#a5b4fc",
              }}
            >
              {targetNode.kind}
            </span>
          </div>
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-dim)",
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <FileCode size={12} />
            <span>
              {targetNode.file_path}:L{targetNode.start_line}-{targetNode.end_line}
            </span>
          </div>
        </div>

        <button
          type="button"
          onClick={onClose}
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

      {selectedEdge && calleeNode && (
        <div
          style={{
            fontSize: "11px",
            padding: "6px 8px",
            borderRadius: 6,
            background: "rgba(16, 185, 129, 0.12)",
            border: "1px solid rgba(16, 185, 129, 0.35)",
            color: "#6ee7b7",
          }}
        >
          Edge: <strong>{targetNode.qualified_name || targetNode.name}</strong> → [
          {selectedEdge.relation_type}]{" "}
          <strong>{calleeNode.qualified_name || calleeNode.name}</strong>
          {highlightCallLine ? ` at Line ${highlightCallLine}` : ""}
        </div>
      )}

      {targetNode.docstring && (
        <div
          style={{
            fontSize: "11px",
            color: "#cbd5e1",
            fontStyle: "italic",
            borderLeft: "2px solid #6366f1",
            paddingLeft: 8,
          }}
        >
          {targetNode.docstring}
        </div>
      )}

      <div
        style={{
          background: "#090a0f",
          border: "1px solid rgba(255,255,255,0.08)",
          borderRadius: 8,
          padding: "10px",
          overflowY: "auto",
          fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
          fontSize: "11.5px",
          lineHeight: 1.5,
        }}
      >
        {loading && <div style={{ color: "var(--text-dim)" }}>Loading source slice...</div>}
        {errText && <div style={{ color: "#f87171" }}>{errText}</div>}
        {!loading &&
          !errText &&
          sourceData?.source_code.split("\n").map((line) => {
            const match = /^(\d+):/.exec(line);
            const lineNum = match ? Number(match[1]) : null;
            const isCallLine = highlightCallLine !== null && lineNum === highlightCallLine;
            return (
              <div
                key={line}
                style={{
                  whiteSpace: "pre",
                  padding: "1px 6px",
                  borderRadius: 4,
                  background: isCallLine ? "rgba(16, 185, 129, 0.22)" : "transparent",
                  borderLeft: isCallLine ? "3px solid #10b981" : "3px solid transparent",
                }}
              >
                {line}
              </div>
            );
          })}
      </div>
    </Panel>
  );
};
