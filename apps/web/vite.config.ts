import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Same-origin API access in dev so the ns_session cookie stays on the app origin.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      // SEO endpoints served from the site root (proxied to the API, no rewrite).
      "/sitemap.xml": { target: "http://localhost:8000", changeOrigin: true },
      "/robots.txt": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  resolve: {
    alias: {
      "@northstar/design-tokens": resolve(import.meta.dirname, "../../packages/design-tokens/src"),
      "@northstar/ui-primitives": resolve(import.meta.dirname, "../../packages/ui-primitives/src"),
      "@northstar/editor-adapter": resolve(import.meta.dirname, "../../packages/editor-adapter/src"),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
