import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` (5173) proxies /api + /.well-known to a local seller uvicorn (8000).
// Build: `npm run build` → dist/, which the seller FastAPI app serves statically.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/.well-known": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
