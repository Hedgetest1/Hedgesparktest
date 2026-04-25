import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import {
  addSessionCookie,
  clearAll,
  getSmokeShop,
  waitForSessionCookie,
} from "./helpers/session";

/**
 * F6 — axe-core a11y baseline for AUTHENTICATED dashboard routes.
 *
 * Companion to a11y.spec.ts (public routes). Same policy: zero
 * Critical and Serious violations. The dashboard is where merchants
 * spend 99% of their time — silent contrast / aria-name / landmark
 * regressions here are the trust-leak class we cannot tolerate.
 *
 * Targets the SMOKE_SHOP merchant on prod HTTPS, exactly like
 * session_durability.spec.ts. Local 127.0.0.1:3000 cannot work
 * because hs_session is `secure=true` and the cookie domain is
 * `api.hedgesparkhq.com` — the prod scope is required.
 *
 * Required env: MERCHANT_SESSION_SECRET (source backend/.env),
 * E2E_BASE_URL=https://app.hedgesparkhq.com,
 * E2E_API_BASE=https://api.hedgesparkhq.com.
 *
 * Routes covered:
 *   /app                     — full dashboard (smoke is Pro)
 *   /app?as=lite             — Lite preview (downgrade override)
 *   /app/pro                 — Pro-tier dedicated page
 *
 * Routes intentionally NOT covered yet:
 *   /app/lite, /app/intelligence, /app/operations, /app/scale,
 *   /app/marketplace, /app/settings — covered incrementally as
 *   they ship the new design. Add them here as they go live.
 */

const SMOKE_SHOP = getSmokeShop();

const skipIfNoSecret = !process.env.MERCHANT_SESSION_SECRET;

test.describe("A11y — authenticated dashboard", () => {
  test.skip(
    skipIfNoSecret,
    "Set MERCHANT_SESSION_SECRET (source backend/.env) to run authenticated a11y suite",
  );

  test.beforeEach(async ({ context, page }) => {
    await clearAll(context, page);
    await addSessionCookie(context, { shop: SMOKE_SHOP });
    await waitForSessionCookie(context);
  });

  const ROUTES = [
    { path: "/app", label: "dashboard-root" },
    { path: "/app?as=lite", label: "dashboard-lite-preview" },
    { path: "/app/pro", label: "dashboard-pro" },
  ];

  for (const route of ROUTES) {
    test(`a11y: ${route.label} (${route.path}) — zero critical/serious violations`, async ({ page }) => {
      await page.goto(route.path, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("load");
      // Allow client-side hydration + initial useCardFetch network calls
      // to settle. We do not waitForLoadState("networkidle") because the
      // dashboard keeps SSE/poll streams open, which would never settle.
      await page.waitForTimeout(2_500);

      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();

      const blockers = results.violations.filter(
        (v) => v.impact === "critical" || v.impact === "serious",
      );

      if (blockers.length > 0) {
        const summary = blockers
          .map((v) => {
            const nodes = v.nodes
              .slice(0, 3)
              .map(
                (n) =>
                  `    • ${n.target.join(" ")} — ${n.failureSummary ?? ""}\n      HTML: ${(n.html || "").slice(0, 220)}`,
              )
              .join("\n");
            return `  [${v.impact}] ${v.id} — ${v.help}\n    ${v.helpUrl}\n${nodes}`;
          })
          .join("\n\n");
        throw new Error(
          `${blockers.length} critical/serious a11y violations on ${route.path}:\n\n${summary}`,
        );
      }

      expect(blockers, "zero critical/serious violations").toEqual([]);
    });
  }
});
