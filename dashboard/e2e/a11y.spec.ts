import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Tier 6.2 — axe-core a11y baseline across public routes.
 *
 * Policy: zero Critical and Serious violations on any route a
 * cold-start visitor can reach without a Shopify session. Moderate
 * and Minor are tolerated for now — they're noise until we've closed
 * the top two tiers — but the same harness will tighten to zero-all
 * once this baseline holds.
 *
 * Why these rules and routes: the product sells itself by feeling
 * obviously easier than Triple Whale / Peel / Varos. Inaccessible
 * copy ("color contrast", "aria-name", "landmark missing") is a
 * quiet trust-leak. A merchant using screen-zoom, a keyboard-only
 * user, a partially-sighted buyer evaluating pricing — each one has
 * to get a product that works. This is the static guard that says
 * "never again" to a silent contrast regression.
 *
 * /app is intentionally excluded from this first pass — it requires
 * an authenticated Shopify merchant session and is covered by
 * interactive walkthrough (Tier 4, founder-gated).
 */

const PUBLIC_ROUTES = [
  { path: "/", label: "landing" },
  { path: "/pricing", label: "pricing" },
  { path: "/install", label: "install" },
  { path: "/privacy", label: "privacy" },
  { path: "/terms", label: "terms" },
  { path: "/cookies", label: "cookies" },
  { path: "/status", label: "status" },
];

for (const route of PUBLIC_ROUTES) {
  test(`a11y: ${route.label} (${route.path}) has zero critical/serious violations`, async ({ page }) => {
    await page.goto(route.path);
    // Let client-side hydration settle so landmarks + aria attrs are stable
    await page.waitForLoadState("networkidle");

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
            .map((n) => `    • ${n.target.join(" ")} — ${n.failureSummary ?? ""}`)
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
