import { expect, test } from "@playwright/test";
import {
  addSessionCookie,
  clearAll,
  getSmokeShop,
  waitForSessionCookie,
} from "./helpers/session";

/**
 * Sidebar floor-highlight regression — born 2026-04-28 night after
 * founder catch: clicking "Pro" in the sidebar lit "Lite". Root cause:
 * `app/page.tsx` hardcoded `currentFloor="pulse"` while serving as the
 * shared backing page for /app/pro (re-export shim). Fix: derive
 * `sidebarCurrentFloor` from `usePathname()`.
 *
 * /app/scale is NOT exercised here because it re-exports
 * /app/operations/page.tsx (uses FloorLayout + useSession) — a
 * different code path, gated on a different bug class. Scoped to the
 * exact reported regression to avoid false-positive failures on
 * unrelated session-recovery edges.
 */

const SMOKE_SHOP = getSmokeShop();
const skipIfNoSecret = !process.env.MERCHANT_SESSION_SECRET;

test.describe("Sidebar floor-highlight follows URL", () => {
  test.skip(skipIfNoSecret, "MERCHANT_SESSION_SECRET not set");
  test.beforeEach(async ({ context }) => {
    await clearAll(context);
    await addSessionCookie(context, { shop: SMOKE_SHOP });
    await waitForSessionCookie(context);
  });

  test("highlights Pro on /app/pro (not Lite — founder catch)", async ({ page }) => {
    await page.goto("/app/pro", { waitUntil: "networkidle", timeout: 30_000 });
    await expect(
      page.locator('a[href="/app/pro"]').first()
    ).toBeVisible({ timeout: 15_000 });

    // The active floor link is `aria-current="page"` (set by Sidebar
    // when isActive). Pro on /app/pro must be aria-current; Lite must
    // NOT be — that was the regression.
    await expect(page.locator('a[href="/app/pro"][aria-current="page"]')).toBeVisible();
    await expect(page.locator('a[href="/app/lite"][aria-current="page"]')).toHaveCount(0);
  });

  test("highlights Lite on /app/lite and on default /app", async ({ page }) => {
    await page.goto("/app/lite", { waitUntil: "networkidle", timeout: 30_000 });
    await expect(
      page.locator('a[href="/app/lite"]').first()
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('a[href="/app/lite"][aria-current="page"]')).toBeVisible();

    await page.goto("/app", { waitUntil: "networkidle", timeout: 30_000 });
    await expect(page.locator('a[href="/app/lite"][aria-current="page"]')).toBeVisible();
  });
});
