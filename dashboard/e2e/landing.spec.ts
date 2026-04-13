import { expect, test } from "@playwright/test";

/**
 * Landing page smoke — verifies the golden path a first-time visitor
 * experiences. If any of these fail, new visitors see a broken page.
 */

test("landing page renders without errors", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("pageerror", (exc) => consoleErrors.push(exc.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  await page.goto("/");

  // Hero headline exists (partial match because it's split across nodes)
  await expect(page.getByText(/Your store is leaking money/i)).toBeVisible();
  await expect(page.getByText(/We show you where/i)).toBeVisible();

  // Primary CTA exists and points somewhere
  const cta = page.getByRole("link", { name: /install on shopify/i }).first();
  await expect(cta).toBeVisible();
  const href = await cta.getAttribute("href");
  expect(href).toBeTruthy();

  // Zero uncaught JS errors
  expect(consoleErrors, `Unexpected console errors: ${consoleErrors.join("\n")}`).toHaveLength(0);
});

test("ROI counter never shows fabricated floor", async ({ page }) => {
  await page.goto("/");

  // The old €125,000 floor must be gone. The banner should either show
  // a real live number OR the "Network launching" honesty copy. Never both,
  // never neither.
  const launching = page.getByText(/Network launching/i);
  const liveBadge = page.getByText(/Live · Network Recovery/i);

  // Wait for either to become visible; Playwright will fail if neither
  await expect(launching.or(liveBadge).first()).toBeVisible();

  // Make sure the forbidden fake €125,000 string is NOT on the page
  const bad = page.locator("text=€125,000");
  expect(await bad.count()).toBe(0);
});

test("capabilities section lists real features", async ({ page }) => {
  await page.goto("/");
  await page.getByText(/16 capabilities/i).scrollIntoViewIfNeeded();
  await expect(page.getByText(/16 capabilities/i)).toBeVisible();
});
