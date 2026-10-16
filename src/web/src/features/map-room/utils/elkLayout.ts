import ELK from "elkjs/lib/elk.bundled.js";
import type { Edge, Node } from "reactflow";

const elk = new ELK();

export const NODE_WIDTH = 250;
export const NODE_HEIGHT = 150;

export const getLayoutedElements = async (
  nodes: Node[],
  edges: Edge[]
): Promise<{ nodes: Node[]; edges: Edge[] }> => {
  const contentNodes = nodes.filter((n) => n.type === "vibeNode");
  if (contentNodes.length === 0) return { nodes: [], edges };

  const tree: Record<string, Record<string, Node[]>> = {};

  contentNodes.forEach((node) => {
    const filePath = node.data?.file_path || "unknown";
    const parts = filePath.split(/[\\/]/);
    const districtName = parts.length > 1 ? parts.slice(0, -1).join("/") : "root";

    if (!tree[districtName]) tree[districtName] = {};
    if (!tree[districtName][filePath]) tree[districtName][filePath] = [];
    tree[districtName][filePath].push(node);
  });

  const elkDistricts = [];

  for (const [districtPath, files] of Object.entries(tree)) {
    const elkFiles = [];

    for (const [filePath, fileNodes] of Object.entries(files)) {
      const elkNodes = fileNodes.map((n) => ({
        id: n.id,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
      }));

      elkFiles.push({
        id: `group-${filePath}`,
        children: elkNodes,
        layoutOptions: {
          "elk.algorithm": "rectpacking",
          "org.eclipse.elk.padding": "[top=80,left=20,bottom=20,right=20]",
          "elk.padding": "[top=80,left=20,bottom=20,right=20]",
          "elk.spacing.nodeNode": "40",
          "elk.aspectRatio": "1.5",
        },
        labels: [{ text: filePath }],
      });
    }

    elkDistricts.push({
      id: `district-${districtPath}`,
      children: elkFiles,
      layoutOptions: {
        "elk.algorithm": "rectpacking",
        "elk.padding": "[top=80,left=40,bottom=40,right=40]",
        "elk.spacing.nodeNode": "80",
        "elk.aspectRatio": "1.2",
      },
      labels: [{ text: districtPath }],
    });
  }

  const rootGraph = {
    id: "root",
    children: elkDistricts,
    edges: edges.map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
    layoutOptions: {
      "elk.algorithm": "rectpacking",
      "elk.padding": "[top=50,left=50,bottom=50,right=50]",
      "elk.spacing.nodeNode": "150",
      "elk.aspectRatio": "1.4",
    },
  };

  try {
    const layoutedGraph = await elk.layout(rootGraph);
    const finalNodes: Node[] = [];

    layoutedGraph.children?.forEach((district: any) => {
      finalNodes.push({
        id: district.id,
        type: "districtGroup",
        data: { label: district.labels?.[0]?.text || "District" },
        position: { x: district.x, y: district.y },
        style: {
          width: district.width,
          height: district.height,
          backgroundColor: "rgba(255, 255, 255, 0.02)",
          border: "3px solid rgba(99, 102, 241, 0.4)",
          borderRadius: "20px",
          paddingTop: "80px",
        },
      });

      district.children?.forEach((file: any) => {
        finalNodes.push({
          id: file.id,
          type: "fileGroup",
          data: {
            label: file.labels?.[0]?.text || "File",
            stats: `${file.children?.length} symbols`,
          },
          position: { x: file.x, y: file.y },
          parentNode: district.id,
          extent: "parent",
          style: {
            width: file.width,
            height: file.height,
            backgroundColor: "rgba(99, 102, 241, 0.05)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
            borderRadius: "12px",
            paddingTop: "5px",
          },
        });

        file.children?.forEach((symbol: any) => {
          const original = contentNodes.find((n) => n.id === symbol.id);
          if (original) {
            finalNodes.push({
              ...original,
              position: { x: symbol.x, y: symbol.y },
              parentNode: file.id,
              extent: "parent",
              width: symbol.width,
              height: symbol.height,
            });
          }
        });
      });
    });

    return { nodes: finalNodes, edges };
  } catch (err) {
    console.error("ELK Layout Failed", err);
    return { nodes: [], edges: [] };
  }
};

export const getGraphBounds = (nodes: Node[]): [[number, number], [number, number]] | undefined => {
  if (nodes.length === 0) return undefined;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  nodes.forEach((n) => {
    minX = Math.min(minX, n.position.x);
    minY = Math.min(minY, n.position.y);
    const w = n.style?.width ? (n.style.width as number) : NODE_WIDTH;
    const h = n.style?.height ? (n.style.height as number) : NODE_HEIGHT;
    maxX = Math.max(maxX, n.position.x + w);
    maxY = Math.max(maxY, n.position.y + h);
  });

  const padding = 500;
  return [
    [minX - padding, minY - padding],
    [maxX + padding, maxY + padding],
  ];
};
