import { expect, test } from "@playwright/test";
import {
  addSessionCookie,
  clearAll,
  getSmokeShop,
  waitForSessionCookie,
} from "./helpers/session";

/**
 * E2E smoke for the Custom Report Builder (Gap #1, 2026-04-28).
 *
 * Flow:
 *   1. Open /app/reports → Hub renders + 6 prebuilt tiles
 *   2. Click "+ New report" → wizard opens
 *   3. Fill name + select metric=revenue + dim=payment_method
 *   4. Click "Save & view" → redirect to /app/reports/[id]
 *   5. Viewer renders chart (or scalar) without errors
 *
 * Same prod-scoped pattern as a11y_app.spec.ts: requires
 * MERCHANT_SESSION_SECRET + E2E_BASE_URL=https://app.hedgesparkhq.com
 * (hs_session is secure-only and scoped to *.hedgesparkhq.com).
 *
 * Skipped silently when env not configured so PR CI doesn't fail
 * for missing secrets.
 */

const SMOKE_SHOP = getSmokeShop();
const skipIfNoSecret = !process.env.MERCHANT_SESSION_SECRET;

test.describe("Custom Report Builder — E2E smoke", () => {
  test.skip(skipIfNoSecret, "MERCHANT_SESSION_SECRET not set — sourcing backend/.env required");
  test.beforeEach(async ({ context }) => {
    await clearAll(context);
    await addSessionCookie(context, { shop: SMOKE_SHOP });
    await waitForSessionCookie(context);
  });

  test("Reports Hub renders + Builder Wizard saves a report", async ({ page }) => {
    // 1. Hub
    await page.goto("/app/reports", { waitUntil: "networkidle", timeout: 30_000 });
    // Diagnostic: dump page state if assertions about to fail
    const ttl = await page.title();
    const bodyText = (await page.locator("body").innerText()).slice(0, 400);
    console.log(`[E2E reports] title="${ttl}" body-snippet="${bodyText}"`);

    // FloorLayout shows "Loading your plan…" until useSession resolves.
    await expect(page.getByRole("heading", { level: 1, name: /^Reports$/ })).toBeVisible({
      timeout: 20_000,
    });
    // The dashboard's main scroll container can confuse innerText-based
    // selectors; use a direct h2 selector for the section heading.
    await expect(
      page.locator('h2', { hasText: 'Prebuilt reports' }).first()
    ).toBeVisible({ timeout: 15_000 });

    // The 6 prebuilt tiles render — locate by tile role-content
    const main = page.locator('main');
    for (const title of [
      "Revenue at Risk",
      "Peer benchmarks",
      "Vertical benchmarks",
      "P&L waterfall",
      "Monthly cohorts",
      "Channel attribution",
    ]) {
      await expect(main.locator(`text=${title}`).first()).toBeAttached();
    }

    // 2. Click "+ New report"
    await page.getByRole("link", { name: /\+ New report/i }).click();
    await expect(page).toHaveURL(/\/app\/reports\/new/);
    await expect(page.getByRole("heading", { level: 1, name: /Build a report/i })).toBeVisible();

    // 3. Fill name + select metric=revenue (default) + dim=payment_method
    const reportName = `E2E smoke ${Date.now()}`;
    await page.getByPlaceholder(/Revenue by channel/i).fill(reportName);
    // Metric: Revenue is already the default in EMPTY_REPORT; nothing to click.
    // Dimension: Payment method (chips render inside the wizard form)
    await page.getByRole("button", { name: /^Payment method$/ }).click();

    // 4. Click "Save & view" → redirect
    await page.getByRole("button", { name: /Save & view/i }).click();
    await page.waitForURL(/\/app\/reports\/\d+/, { timeout: 10_000 });

    // 5. Viewer renders the report header + the chart area
    await expect(page.getByRole("heading", { level: 1, name: reportName })).toBeVisible();
    await expect(page.getByText(/Revenue/).first()).toBeVisible();
    await expect(page.getByRole("link", { name: /Edit/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Export CSV/i })).toBeVisible();

    // Cleanup — delete the smoke-test report so we don't pollute state
    page.on("dialog", (d) => d.accept());
    await page.getByRole("button", { name: /^Delete$/i }).click();
    await page.waitForURL(/\/app\/reports$/, { timeout: 10_000 });
  });
});
