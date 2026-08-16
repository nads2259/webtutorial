import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
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
