import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@northstar/design-tokens": resolve(import.meta.dirname, "../design-tokens/src"),
      "@northstar/ui-primitives": resolve(import.meta.dirname, "../ui-primitives/src"),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
