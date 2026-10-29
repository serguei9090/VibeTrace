import { Background, Controls, MiniMap, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./App.css";

import { useState } from "react";
import { DocsExplorerDrawer } from "./features/docs/components/DocsExplorerDrawer";
import { CanvasAutoFitter } from "./features/map-room/components/CanvasAutoFitter";
import { CodeInspectorDrawer } from "./features/map-room/components/CodeInspectorDrawer";
import { FilterPanel } from "./features/map-room/components/FilterPanel";
import { MapHeader } from "./features/map-room/components/MapHeader";
import { DistrictNode } from "./features/map-room/components/nodes/DistrictNode";
import { FileGroupNode } from "./features/map-room/components/nodes/FileGroupNode";
import { VibeNode } from "./features/map-room/components/nodes/VibeNode";
import { StatusOverlays } from "./features/map-room/components/StatusOverlays";
import { TracePathBar } from "./features/map-room/components/TracePathBar";
import { TreeSidebar } from "./features/map-room/components/TreeSidebar";
import { API_URL, useMapRoomState } from "./features/map-room/hooks/useMapRoomState";
import { getGraphBounds } from "./features/map-room/utils/elkLayout";

const nodeTypes = {
  vibeNode: VibeNode,
  districtGroup: DistrictNode,
  fileGroup: FileGroupNode,
};

export default function App() {
  const {
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,
    onConnect,
    loading,
    error,
    filter,
    setFilter,
    activeFilters,
    toggleFilter,
    activeEdgeTypes,
    toggleEdgeType,
    showPrivate,
    setShowPrivate,
    impactMode,
    handleImpactToggle,
    layoutMode,
    handleLayoutModeChange,
    viewMode,
    setViewMode,
    showAnonymous,
    setShowAnonymous,
    revealedNodeIds,
    isSidebarOpen,
    setIsSidebarOpen,
    onResetToRoots,
    onToggleNodeVisibility,
    onFocusNode,
    onExpandNodeConnections,
    onExpandCallees,
    onExpandCallers,
    lastUpdate,
    followAgent,
    setFollowAgent,
    lastAgentEvent,
    activeTrace,
    handleTraceResult,
    resetTrace,
    selectedElement,
    setSelectedElement,
    onNodeClick,
    onEdgeClick,
    onPaneClick,
    rawData,
    fetchGraph,
  } = useMapRoomState();
  const [docsDrawerOpen, setDocsDrawerOpen] = useState(false);

  return (
    <div className="graph-container">
      <MapHeader
        filter={filter}
        onFilterChange={setFilter}
        loading={loading}
        onRefresh={() => fetchGraph()}
        totalSymbols={rawData.current.nodes.length}
        totalEdges={rawData.current.edges.length}
        visibleSymbols={
          nodes.filter((n) => n.type !== "districtGroup" && n.type !== "fileGroup").length
        }
        layoutMode={layoutMode}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        onResetToRoots={onResetToRoots}
        isSidebarOpen={isSidebarOpen}
        onToggleSidebar={() => setIsSidebarOpen((prev) => !prev)}
        activeTrace={activeTrace}
        onResetTrace={resetTrace}
        lastUpdate={lastUpdate}
        followAgent={followAgent}
        onToggleFollowAgent={setFollowAgent}
        lastAgentEvent={lastAgentEvent}
        onToggleDocs={() => setDocsDrawerOpen((prev) => !prev)}
        isDocsOpen={docsDrawerOpen}
      />

      <TreeSidebar
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        rawNodes={rawData.current.nodes}
        rawEdges={rawData.current.edges}
        revealedNodeIds={revealedNodeIds}
        onToggleNodeVisibility={onToggleNodeVisibility}
        onFocusNode={onFocusNode}
        onExpandNodeConnections={onExpandNodeConnections}
        onResetToRoots={onResetToRoots}
        showAnonymous={showAnonymous}
        onToggleAnonymous={setShowAnonymous}
      />

      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        onPaneClick={onPaneClick}
        fitView
        fitViewOptions={{ padding: 0.35 }}
        translateExtent={getGraphBounds(nodes)}
      >
        <Controls />
        <MiniMap
          nodeStrokeWidth={3}
          zoomable
          pannable
          style={{
            backgroundColor: "#0d0d10",
            border: "1px solid rgba(99, 102, 241, 0.3)",
            width: 220,
            height: 160,
            bottom: 20,
            right: 20,
          }}
          nodeColor={(n: any) => {
            if (n.data?.kind === "class") return "#a855f7";
            if (n.data?.kind === "interface") return "#10b981";
            if (n.data?.kind === "endpoint") return "#f59e0b";
            return "#6366f1";
          }}
          maskColor="rgba(0, 0, 0, 0.7)"
        />
        <Background color="#1a1b23" gap={20} size={1} />

        <StatusOverlays loading={loading} error={error} onRetry={() => fetchGraph()} />
        <CanvasAutoFitter layoutMode={layoutMode} nodeCount={nodes.length} />

        <TracePathBar
          apiUrl={API_URL}
          nodes={rawData.current.nodes}
          activeTrace={activeTrace}
          onTraceResult={handleTraceResult}
          isSidebarOpen={isSidebarOpen}
        />

        <CodeInspectorDrawer
          apiUrl={API_URL}
          selectedElement={selectedElement}
          rawNodes={rawData.current.nodes}
          rawEdges={rawData.current.edges}
          revealedNodeIds={revealedNodeIds}
          onClose={() => setSelectedElement(null)}
          onFocusNode={onFocusNode}
          onExpandCallees={onExpandCallees}
          onExpandCallers={onExpandCallers}
          onExpandNodeConnections={onExpandNodeConnections}
        />

        <DocsExplorerDrawer
          isOpen={docsDrawerOpen}
          onClose={() => setDocsDrawerOpen(false)}
          apiUrl={API_URL}
        />

        <FilterPanel
          activeFilters={activeFilters}
          onToggleFilter={toggleFilter}
          activeEdgeTypes={activeEdgeTypes}
          onToggleEdgeType={toggleEdgeType}
          showPrivate={showPrivate}
          onTogglePrivate={setShowPrivate}
          impactMode={impactMode}
          onToggleImpact={handleImpactToggle}
          layoutMode={layoutMode}
          onLayoutModeChange={handleLayoutModeChange}
          isSidebarOpen={isSidebarOpen}
        />
      </ReactFlow>
    </div>
  );
}
