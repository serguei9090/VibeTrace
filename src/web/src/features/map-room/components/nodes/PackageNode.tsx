import { Handle, Position } from "@xyflow/react";
import { ArrowUpRight, Code, FileCode, Folder } from "lucide-react";
import { memo } from "react";
import type { PackageNodeData } from "../../utils/packageGraph";

export const PackageNode = memo(
  ({ data }: { data: PackageNodeData }) => {
    return (
      <div className="vibe-node package-node">
        <Handle
          id="target-top"
          type="target"
          position={Position.Top}
          style={{ background: "#6366f1", width: 8, height: 8 }}
        />
        <Handle
          id="target-left"
          type="target"
          position={Position.Left}
          style={{ background: "#6366f1", width: 8, height: 8 }}
        />
        <Handle
          id="source-bottom"
          type="source"
          position={Position.Bottom}
          style={{ background: "#6366f1", width: 8, height: 8 }}
        />
        <Handle
          id="source-right"
          type="source"
          position={Position.Right}
          style={{ background: "#6366f1", width: 8, height: 8 }}
        />

        {/* Package Header */}
        <div className="package-node-header">
          <div className="package-node-title-group">
            <Folder size={16} style={{ color: "#818cf8", flexShrink: 0 }} />
            <span className="package-node-title" title={data.label}>
              {data.shortName}
            </span>
          </div>
          <span className="package-node-badge">PACKAGE</span>
        </div>

        {/* Path Subtitle */}
        <div className="package-node-subtitle">{data.label}</div>

        {/* Metrics Row */}
        <div className="package-node-metrics">
          <span className="package-node-metric-chip">
            <FileCode size={12} style={{ color: "#38bdf8" }} />
            {data.fileCount} {data.fileCount === 1 ? "file" : "files"}
          </span>

          <span className="package-node-metric-chip">
            <Code size={12} style={{ color: "#a855f7" }} />
            {data.symbolCount} symbols
          </span>
        </div>

        {/* Notable Symbols Preview */}
        {data.topSymbols && data.topSymbols.length > 0 && (
          <div className="package-node-symbols-section">
            <span className="package-node-symbols-title">Key Symbols</span>
            <div className="package-node-symbols-list">
              {data.topSymbols.map((sym) => (
                <span key={sym} className="package-node-symbol-tag">
                  {sym}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Action Footer with nodrag nopan */}
        <button
          type="button"
          onClick={() => data.onExplorePackage(data.label)}
          className="vibe-btn vibe-btn-sm nodrag nopan package-node-explore-btn"
        >
          <span>Explore Package</span>
          <ArrowUpRight size={12} />
        </button>
      </div>
    );
  },
  (prev, next) =>
    prev.data.id === next.data.id &&
    prev.data.label === next.data.label &&
    prev.data.fileCount === next.data.fileCount &&
    prev.data.symbolCount === next.data.symbolCount &&
    prev.data.shortName === next.data.shortName &&
    prev.data.topSymbols?.length === next.data.topSymbols?.length
);

PackageNode.displayName = "PackageNode";
