# VibeGraph UI/UX Findings & Improvement Plan

## 1. Current Gaps
- **Linear Layout Overload**: Nodes are currently arranged in a single long row, making large projects impossible to navigate.
- **Lost in Space**: The canvas is infinite, meaning users can pan into the "void" and lose their graph.
- **Structure Visibility**: The project structure (folders/modules) is flattened, losing the hierarchical context.
- **Minimap Contrast**: The minimap was too dark/transparent, making it blend into the background.

## 2. Structural Improvements
### File-Based Clustering (The "Map Room" Concept)
Instead of a single graph, we organize the UI like a physical map:
- **Clusters**: Group nodes by folder/file.
- **Grids**: Arrange these clusters in a multi-column grid.
- **Connectors**: Only show critical cross-file edges to reduce visual noise.

### Spatial Constraints
- **Translate Extent**: Lock the camera to the actual area occupied by nodes (+ safe padding).
- **Auto-Fit**: Ensure the view zooms to the active area on load.

## 3. Implementation Roadmap
## 4. The "Neighborhood" Approach (v3 Proposal)
To solve the "long row" problem, we will move from a flat grid to a **nested hierarchy**:

### District Level (Directories)
- Every folder (e.g., `src/web`, `src/indexer`) becomes a **District**.
- Districts are arranged in a tight **2D Masonry Grid** (rather than a simple row).
- This keeps related code physically close and prevents vertical/horizontal runaway.

### Block Level (Files)
- Inside each District, **File Blocks** are arranged in a local grid.
- This creates "islands" of logic.

### Connectivity
- **Internal edges** (inside a file) are always visible.
- **Cross-file edges** (imports/calls) are drawn with higher curvature/opacity to distinguish them from structure.

### UX Benefits
- **Compactness**: High information density without the "spaghetti" look.
- **Scanning**: You can find "the parser area" or "the API area" instantly by looking for the District label.
