import { defineConfig, devices } from "@playwright/test";

/**
 * HedgeSpark E2E smoke suite — minimal foundation.
 *
 * Philosophy: a handful of tests that run against a real running
 * backend + frontend and fail loudly the moment the golden path
 * breaks. These are not unit tests — they exist to catch "you shipped
 * and the app is white-screening". Keep them fast (<30s total) and
 * deterministic.
 *
 * Run locally:
 *   npx playwright install chromium
 *   npx playwright test
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",

  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:3000",
    actionTimeout: 5_000,
    navigationTimeout: 10_000,
    trace: "on-first-retry",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
