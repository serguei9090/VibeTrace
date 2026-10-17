import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("elkjs")) {
            return "vendor_elk";
          }
          if (id.includes("@xyflow")) {
            return "vendor_flow";
          }
          if (id.includes("lucide-react")) {
            return "vendor_icons";
          }
        },
      },
    },
  },
});
