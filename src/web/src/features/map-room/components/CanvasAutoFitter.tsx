import { useReactFlow } from "@xyflow/react";
import { useEffect } from "react";
import type { LayoutMode } from "../types";

interface CanvasAutoFitterProps {
  layoutMode: LayoutMode;
  nodeCount: number;
}

export const CanvasAutoFitter = ({ layoutMode, nodeCount }: CanvasAutoFitterProps) => {
  const { fitView } = useReactFlow();

  useEffect(() => {
    if (nodeCount > 0 && layoutMode) {
      const timer = setTimeout(() => {
        fitView({ padding: 0.35, duration: 300 });
      }, 150);
      return () => clearTimeout(timer);
    }
  }, [layoutMode, nodeCount, fitView]);

  return null;
};
