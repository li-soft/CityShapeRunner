import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Keep the default same-origin client usable in development, including the
// review, gallery, map-placement and optional tools (not only generation).
export const API_PROXY_PATHS = [
  "/health", "/generate", "/interpret", "/edit-route", "/route-acceptance",
  "/gallery", "/timed-readiness", "/mural-plan", "/recognition-repair",
  "/inkproof-analysis", "/night-readiness", "/route-landmarks", "/occasions",
  "/art-rescue", "/accessibility-readiness", "/lesson-pack", "/destinations",
  "/shape-templates", "/shape-placement-preview", "/shape-upload", "/walkthrough-diagnostics",
];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(API_PROXY_PATHS.map(path => [path, "http://127.0.0.1:8000"])),
  },
});
