import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` (5174) proxies the buyer API to a local buyer uvicorn (8101).
// Build: `npm run build` → dist/, which the buyer FastAPI app serves statically.
const api = { target: "http://localhost:8101", changeOrigin: true };

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5174,
    proxy: {
      "/briefs": api,
      "/status": api,
      "/runs": api,
      "/run": api,
      "/sellers": api,
      "/packages": api,
      "/pricing": api,
      "/healthz": api,
    },
  },
});
