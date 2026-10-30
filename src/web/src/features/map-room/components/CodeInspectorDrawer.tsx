import { Panel } from "@xyflow/react";
import axios from "axios";
import { ArrowDownRight, ArrowUpRight, Code2, FileCode, Network, X } from "lucide-react";
import type React from "react";
import { useEffect, useMemo, useState } from "react";
import type { RawGraphEdge, RawGraphNode, SelectedElement } from "../types";
import { CallerGuardBadge } from "./CallerGuardBadge";
import { SecurityAuditCard } from "./SecurityAuditCard";
import { SplitAuditCard } from "./SplitAuditCard";
import { VibeBundleButton } from "./VibeBundleButton";

interface CodeInspectorDrawerProps {
  apiUrl: string;
  selectedElement: SelectedElement | null;
  rawNodes: RawGraphNode[];
  rawEdges: RawGraphEdge[];
  revealedNodeIds?: Set<string>;
  onClose: () => void;
  onFocusNode?: (nodeId: string) => void;
  onExpandCallees?: (nodeId: string) => void;
  onExpandCallers?: (nodeId: string) => void;
  onExpandNodeConnections?: (nodeId: string) => void;
  onShowTracePath?: (from: string, to: string) => void;
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
  revealedNodeIds,
  onClose,
  onFocusNode,
  onExpandCallees,
  onExpandCallers,
  onExpandNodeConnections,
  onShowTracePath,
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

  const incomingCallers = useMemo(() => {
    if (!targetNode) return [];
    return rawEdges
      .filter((e) => e.to_node_id === targetNode.id && e.relation_type !== "defines")
      .map((e) => ({
        edge: e,
        node: rawNodes.find((n) => n.id === e.from_node_id),
      }))
      .filter((c): c is { edge: RawGraphEdge; node: RawGraphNode } => Boolean(c.node));
  }, [targetNode, rawEdges, rawNodes]);

  const outgoingCallees = useMemo(() => {
    if (!targetNode) return [];
    return rawEdges
      .filter((e) => e.from_node_id === targetNode.id && e.relation_type !== "defines")
      .map((e) => ({
        edge: e,
        node: rawNodes.find((n) => n.id === e.to_node_id),
      }))
      .filter((c): c is { edge: RawGraphEdge; node: RawGraphNode } => Boolean(c.node));
  }, [targetNode, rawEdges, rawNodes]);

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
      position="top-right"
      className="glass-panel"
      style={{
        width: "430px",
        maxHeight: "calc(100vh - 120px)",
        marginTop: "130px",
        marginRight: "16px",
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

      <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
        <div style={{ flex: 1 }}>
          <CallerGuardBadge
            apiUrl={apiUrl}
            symbolName={targetNode.name}
            filePath={targetNode.file_path}
          />
        </div>
        <VibeBundleButton apiUrl={apiUrl} symbolName={targetNode.name} />
      </div>

      <SplitAuditCard
        apiUrl={apiUrl}
        symbolName={targetNode.name}
        qualifiedName={targetNode.qualified_name}
        filePath={targetNode.file_path}
      />

      <SecurityAuditCard
        apiUrl={apiUrl}
        filePath={targetNode.file_path}
        startLine={targetNode.start_line}
        endLine={targetNode.end_line}
        onShowPath={onShowTracePath}
      />

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

      {/* Symbol Connections & Drill-Down Section */}
      <div
        style={{
          background: "rgba(15, 23, 42, 0.75)",
          border: "1px solid rgba(255, 255, 255, 0.08)",
          borderRadius: "8px",
          padding: "10px",
          display: "flex",
          flexDirection: "column",
          gap: "8px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <Network size={13} color="#818cf8" />
            <span
              style={{
                fontSize: "11px",
                fontWeight: 700,
                color: "#cbd5e1",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
              }}
            >
              Connections ({incomingCallers.length} In · {outgoingCallees.length} Out)
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "5px" }}>
            {incomingCallers.length > 0 && onExpandCallers && (
              <button
                type="button"
                onClick={() => onExpandCallers(targetNode.id)}
                style={{
                  background: "rgba(245, 158, 11, 0.15)",
                  border: "1px solid rgba(245, 158, 11, 0.4)",
                  color: "#fcd34d",
                  borderRadius: "4px",
                  padding: "2px 6px",
                  fontSize: "10px",
                  fontWeight: 600,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "3px",
                }}
                title="Expand upstream callers onto canvas"
              >
                <ArrowUpRight size={11} /> Callers (+{incomingCallers.length})
              </button>
            )}
            {outgoingCallees.length > 0 && onExpandCallees && (
              <button
                type="button"
                onClick={() => onExpandCallees(targetNode.id)}
                style={{
                  background: "rgba(99, 102, 241, 0.2)",
                  border: "1px solid rgba(99, 102, 241, 0.5)",
                  color: "#c7d2fe",
                  borderRadius: "4px",
                  padding: "2px 7px",
                  fontSize: "10px",
                  fontWeight: 600,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "3px",
                }}
                title="Expand all downstream calls onto canvas"
              >
                <ArrowDownRight size={11} /> Drill Down (+{outgoingCallees.length})
              </button>
            )}
            {onExpandNodeConnections &&
              (incomingCallers.length > 0 || outgoingCallees.length > 0) && (
                <button
                  type="button"
                  onClick={() => onExpandNodeConnections(targetNode.id)}
                  style={{
                    background: "rgba(16, 185, 129, 0.15)",
                    border: "1px solid rgba(16, 185, 129, 0.4)",
                    color: "#6ee7b7",
                    borderRadius: "4px",
                    padding: "2px 6px",
                    fontSize: "10px",
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                  title="Expand both callers and callees"
                >
                  All
                </button>
              )}
          </div>
        </div>

        {/* Incoming Callers */}
        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          <div style={{ fontSize: "10px", color: "#64748b", fontWeight: 600 }}>
            Incoming Callers ({incomingCallers.length})
          </div>
          {incomingCallers.length === 0 ? (
            <div style={{ fontSize: "10px", color: "#475569", fontStyle: "italic" }}>
              No incoming callers (root / entrypoint)
            </div>
          ) : (
            incomingCallers.slice(0, 5).map(({ edge, node }) => {
              const isRevealed = revealedNodeIds?.has(node.id);
              return (
                <div
                  key={`${edge.from_node_id}-${edge.relation_type}`}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "3px 6px",
                    background: "rgba(0,0,0,0.25)",
                    borderRadius: "4px",
                    fontSize: "11px",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "6px",
                      overflow: "hidden",
                    }}
                  >
                    <span
                      style={{
                        fontSize: "9px",
                        color: "#f59e0b",
                        background: "rgba(245, 158, 11, 0.15)",
                        padding: "1px 4px",
                        borderRadius: "3px",
                      }}
                    >
                      {edge.relation_type}
                    </span>
                    <span
                      style={{
                        color: "#e2e8f0",
                        textOverflow: "ellipsis",
                        overflow: "hidden",
                        whiteSpace: "nowrap",
                      }}
                      title={node.name}
                    >
                      {node.name}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      if (onFocusNode) onFocusNode(node.id);
                    }}
                    style={{
                      background: isRevealed
                        ? "rgba(255,255,255,0.06)"
                        : "rgba(99, 102, 241, 0.25)",
                      border: "none",
                      color: isRevealed ? "#94a3b8" : "#818cf8",
                      borderRadius: "3px",
                      padding: "2px 6px",
                      fontSize: "10px",
                      cursor: "pointer",
                      flexShrink: 0,
                    }}
                  >
                    {isRevealed ? "Focus" : "+ Reveal"}
                  </button>
                </div>
              );
            })
          )}
        </div>

        {/* Outgoing Callees */}
        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          <div style={{ fontSize: "10px", color: "#64748b", fontWeight: 600 }}>
            Outgoing Callees ({outgoingCallees.length})
          </div>
          {outgoingCallees.length === 0 ? (
            <div style={{ fontSize: "10px", color: "#475569", fontStyle: "italic" }}>
              Leaf node (no outgoing calls)
            </div>
          ) : (
            outgoingCallees.slice(0, 6).map(({ edge, node }) => {
              const isRevealed = revealedNodeIds?.has(node.id);
              return (
                <div
                  key={`${edge.to_node_id}-${edge.relation_type}`}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "3px 6px",
                    background: "rgba(0,0,0,0.25)",
                    borderRadius: "4px",
                    fontSize: "11px",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "6px",
                      overflow: "hidden",
                    }}
                  >
                    <span
                      style={{
                        fontSize: "9px",
                        color: "#818cf8",
                        background: "rgba(99, 102, 241, 0.15)",
                        padding: "1px 4px",
                        borderRadius: "3px",
                      }}
                    >
                      {edge.relation_type}
                    </span>
                    <span
                      style={{
                        color: "#e2e8f0",
                        textOverflow: "ellipsis",
                        overflow: "hidden",
                        whiteSpace: "nowrap",
                      }}
                      title={node.qualified_name || node.name}
                    >
                      {node.name}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      if (onFocusNode) onFocusNode(node.id);
                    }}
                    style={{
                      background: isRevealed
                        ? "rgba(255,255,255,0.06)"
                        : "rgba(16, 185, 129, 0.25)",
                      border: "none",
                      color: isRevealed ? "#94a3b8" : "#34d399",
                      borderRadius: "3px",
                      padding: "2px 6px",
                      fontSize: "10px",
                      cursor: "pointer",
                      flexShrink: 0,
                    }}
                  >
                    {isRevealed ? "Focus" : "+ Reveal"}
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>

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
