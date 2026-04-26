/**
 * vitest.config.ts — component-level test infra (F5 closure 2026-04-26).
 *
 * Why Vitest:
 *   - Native ESM + TypeScript support (no Jest config gymnastics)
 *   - 5–10× faster than Jest on a Next.js codebase this size
 *   - Industry standard for Next.js 15+ React-19 stacks
 *   - Already used by every major Shopify analytics competitor
 *
 * Scope: complements existing Playwright E2E (`e2e/*.spec.ts`).
 *   - E2E: real-data render in real browser, slower (~60s suite)
 *   - Vitest: mocked-data render, fast (~ms per test)
 *   - Both run in CI when wired
 *
 * Test files live next to the components: `*.test.tsx` /
 * `*.test.ts` per file. The `e2e/` directory remains
 * Playwright-only — Vitest is excluded from there.
 */
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["node_modules", ".next", "e2e/**"],
    setupFiles: ["./vitest.setup.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
});
