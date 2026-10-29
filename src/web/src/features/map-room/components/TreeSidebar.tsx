import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  FileCode,
  Folder,
  FolderOpen,
  FolderTree,
  PlusCircle,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import type React from "react";
import { useEffect, useMemo, useState } from "react";
import type { RawGraphEdge, RawGraphNode } from "../types";
import { buildFileTree } from "../utils/treeHierarchy";

interface TreeSidebarProps {
  isOpen: boolean;
  onClose: () => void;
  rawNodes: RawGraphNode[];
  rawEdges: RawGraphEdge[];
  revealedNodeIds: Set<string>;
  onToggleNodeVisibility: (id: string) => void;
  onFocusNode: (id: string) => void;
  onExpandNodeConnections: (id: string) => void;
  onResetToRoots: () => void;
  showAnonymous: boolean;
  onToggleAnonymous: (val: boolean) => void;
}

const KIND_COLORS: Record<string, string> = {
  endpoint: "#f59e0b",
  class: "#a855f7",
  interface: "#10b981",
  struct: "#06b6d4",
  function: "#6366f1",
  variable: "#94a3b8",
  module: "#38bdf8",
  trait: "#34d399",
  impl: "#c084fc",
  enum: "#fb923c",
};

export const TreeSidebar: React.FC<TreeSidebarProps> = ({
  isOpen,
  onClose,
  rawNodes,
  rawEdges,
  revealedNodeIds,
  onToggleNodeVisibility,
  onFocusNode,
  onExpandNodeConnections,
  onResetToRoots,
  showAnonymous,
  onToggleAnonymous,
}) => {
  const [searchTerm, setSearchTerm] = useState("");
  const [kindFilter, setKindFilter] = useState("all");
  const [collapsedDirs, setCollapsedDirs] = useState<Set<string>>(new Set());
  const [collapsedFiles, setCollapsedFiles] = useState<Set<string>>(new Set());

  const tree = useMemo(() => {
    return buildFileTree(
      rawNodes,
      rawEdges,
      revealedNodeIds,
      searchTerm,
      kindFilter,
      showAnonymous
    );
  }, [rawNodes, rawEdges, revealedNodeIds, searchTerm, kindFilter, showAnonymous]);

  const totalVisibleCount = useMemo(() => {
    let count = 0;
    for (const dir of tree) {
      for (const file of dir.files) {
        count += file.symbols.length;
      }
    }
    return count;
  }, [tree]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const toggleDir = (dirPath: string) => {
    setCollapsedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(dirPath)) next.delete(dirPath);
      else next.add(dirPath);
      return next;
    });
  };

  const toggleFile = (filePath: string) => {
    setCollapsedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(filePath)) next.delete(filePath);
      else next.add(filePath);
      return next;
    });
  };

  return (
    <aside className="tree-sidebar glass-panel">
      {/* Sidebar Header */}
      <div className="tree-sidebar-header">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <FolderTree size={16} color="#6366f1" />
          <span style={{ fontWeight: 600, fontSize: "13px", letterSpacing: "-0.01em" }}>
            Code Hierarchy
          </span>
          <span
            style={{
              fontSize: "11px",
              padding: "1px 6px",
              borderRadius: "4px",
              background: "rgba(99, 102, 241, 0.15)",
              color: "#a5b4fc",
              fontWeight: 600,
            }}
          >
            {totalVisibleCount}
          </span>
        </div>
        <button
          type="button"
          className="vibe-btn vibe-btn-icon-sm"
          onClick={onClose}
          title="Close Hierarchy Sidebar"
        >
          <ChevronLeft size={16} />
        </button>
      </div>

      {/* Search Input */}
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-color)" }}>
        <div style={{ position: "relative" }}>
          <Search
            size={14}
            style={{
              position: "absolute",
              left: 10,
              top: 10,
              color: "var(--text-dim)",
            }}
          />
          <input
            className="vibe-input"
            type="text"
            placeholder="Filter files or symbols..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            style={{ width: "100%", paddingLeft: "32px", height: "32px", fontSize: "12px" }}
          />
          {searchTerm && (
            <button
              type="button"
              onClick={() => setSearchTerm("")}
              style={{
                position: "absolute",
                right: 8,
                top: 8,
                background: "transparent",
                border: "none",
                color: "var(--text-dim)",
                cursor: "pointer",
              }}
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Quick Filter Chips */}
        <div style={{ display: "flex", gap: 5, marginTop: 8, overflowX: "auto" }}>
          {[
            { id: "all", label: "All" },
            { id: "endpoint", label: "Endpoints" },
            { id: "class", label: "Classes" },
            { id: "function", label: "Functions" },
          ].map((chip) => (
            <button
              key={chip.id}
              type="button"
              onClick={() => setKindFilter(chip.id)}
              className={`vibe-btn vibe-btn-sm ${kindFilter === chip.id ? "vibe-btn-active" : ""}`}
              style={{ padding: "2px 8px", fontSize: "11px", height: "24px" }}
            >
              {chip.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tree Content */}
      <div className="tree-sidebar-body">
        {tree.length === 0 ? (
          <div
            style={{
              padding: 24,
              textAlign: "center",
              color: "var(--text-dim)",
              fontSize: "12px",
            }}
          >
            No symbols match current filter.
          </div>
        ) : (
          tree.map((dir) => {
            const isDirCollapsed = collapsedDirs.has(dir.dirPath);
            return (
              <div key={dir.dirPath} className="tree-dir-group">
                <button
                  type="button"
                  className="tree-dir-header"
                  onClick={() => toggleDir(dir.dirPath)}
                >
                  {isDirCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                  {isDirCollapsed ? (
                    <Folder size={14} color="#a5b4fc" />
                  ) : (
                    <FolderOpen size={14} color="#818cf8" />
                  )}
                  <span className="tree-dir-name" title={dir.dirPath}>
                    {dir.dirPath}
                  </span>
                  <span className="tree-count-badge">
                    {dir.files.reduce((acc, f) => acc + f.symbols.length, 0)}
                  </span>
                </button>

                {!isDirCollapsed && (
                  <div className="tree-dir-children">
                    {dir.files.map((file) => {
                      const isFileCollapsed = collapsedFiles.has(file.filePath);
                      return (
                        <div key={file.filePath} className="tree-file-group">
                          <button
                            type="button"
                            className="tree-file-header"
                            onClick={() => toggleFile(file.filePath)}
                          >
                            {isFileCollapsed ? (
                              <ChevronRight size={12} />
                            ) : (
                              <ChevronDown size={12} />
                            )}
                            <FileCode size={13} color="#94a3b8" />
                            <span className="tree-file-name" title={file.fileName}>
                              {file.fileName}
                            </span>
                            <span className="tree-count-badge">{file.symbols.length}</span>
                          </button>

                          {!isFileCollapsed && (
                            <div className="tree-symbol-list">
                              {file.symbols.map((sym) => {
                                const kindColor = KIND_COLORS[sym.kind] || "#818cf8";
                                return (
                                  <div
                                    key={sym.id}
                                    className={`tree-symbol-row ${sym.isVisible ? "is-visible" : ""}`}
                                  >
                                    <span
                                      className="symbol-kind-dot"
                                      style={{ backgroundColor: kindColor }}
                                      title={sym.kind}
                                    />
                                    <button
                                      type="button"
                                      className="symbol-info-btn"
                                      onClick={() => onFocusNode(sym.id)}
                                      title={`Focus ${sym.qualified_name || sym.name}`}
                                    >
                                      <span
                                        className="symbol-name"
                                        title={sym.qualified_name || sym.name}
                                      >
                                        {sym.name}
                                      </span>
                                      <span className="symbol-meta">
                                        L{sym.start_line} • {sym.inDegree} in • {sym.outDegree} out
                                      </span>
                                    </button>

                                    <div className="symbol-actions">
                                      <button
                                        type="button"
                                        className="tree-action-btn"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          onToggleNodeVisibility(sym.id);
                                        }}
                                        title={sym.isVisible ? "Hide on canvas" : "Show on canvas"}
                                      >
                                        {sym.isVisible ? (
                                          <Eye size={12} color="#10b981" />
                                        ) : (
                                          <EyeOff size={12} color="#64748b" />
                                        )}
                                      </button>
                                      <button
                                        type="button"
                                        className="tree-action-btn"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          onExpandNodeConnections(sym.id);
                                        }}
                                        title="Expand connected callers and callees"
                                      >
                                        <PlusCircle size={12} color="#818cf8" />
                                      </button>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Sidebar Footer Controls */}
      <div className="tree-sidebar-footer">
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: "11px",
            color: "var(--text-dim)",
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={showAnonymous}
            onChange={(e) => onToggleAnonymous(e.target.checked)}
          />
          <span>Show Anonymous Closures</span>
        </label>
        <button
          type="button"
          className="vibe-btn vibe-btn-sm"
          onClick={onResetToRoots}
          title="Reset canvas back to root entrypoints"
        >
          <Sparkles size={12} />
          Reset to Roots
        </button>
      </div>
    </aside>
  );
};
