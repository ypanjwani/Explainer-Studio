import { defineConfig } from "vite";

export default defineConfig({
  root: "app",
  server: {
    port: 5173,
    // Same-origin API calls in dev; the Flask server runs on 8787 (scripts/dev-api.mjs).
    // "/s/" = public share-link pages (routes/share.py); their /viewer.js comes from app/public via Vite.
    proxy: { "/api": { target: "http://127.0.0.1:8787" }, "/s/": { target: "http://127.0.0.1:8787" } },
  },
  build: { outDir: "../dist", emptyOutDir: true },
});
