/**
 * Sentry browser-side init for the Next.js dashboard.
 *
 * Loaded by @sentry/nextjs into every client bundle. Conservative
 * defaults: low trace + replay sample rates so the Team plan quota
 * (50 replays/month base) doesn't burn before we have signal on what
 * real merchants are doing.
 *
 * DSN: NEXT_PUBLIC_SENTRY_DSN. If unset, init is skipped (no-op).
 *
 * Bundle cost
 * -----------
 * `Sentry.replayIntegration()` is statically imported below, which
 * adds ~60KB gzipped to the client bundle regardless of
 * `replaysSessionSampleRate`. The sample rate ONLY affects how many
 * sessions actually get recorded + uploaded — NOT bundle size.
 * Adjusting the rate does not change bundle budget (`audit_bundle_
 * budget.py`). What DOES change budget: adding new integrations
 * (canvasIntegration, browserTracingIntegration extras, etc.). Last
 * measured post-C4 (2026-04-24): largest_chunk 87.3% / root 87.7% /
 * total 93.5% of cap — headroom ~100KB per bucket. Any new
 * integration here MUST be audited against that headroom.
 *
 * Separate frontend project recommendation
 * ----------------------------------------
 * Today the frontend and backend share one DSN + project, tagged by
 * `component` for filtering. For cleaner stack-trace symbolication +
 * independent quota accounting, a second Sentry project
 * (hedgespark-frontend) is recommended long-term — zero cost on the
 * Team plan. Docs/SENTRY_OPS.md "Separate projects" section has the
 * migration steps. Tracked as ledger entry SENTRY-1.
 *
 * DSN separation is already wired at the config layer: set
 * NEXT_PUBLIC_SENTRY_DSN to a different value than backend SENTRY_DSN
 * and the two surfaces report to separate projects with no code
 * change needed.
 *
 * Tier: TIER_0 (observability config).
 */
import * as Sentry from "@sentry/nextjs";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN || "";
const release = process.env.NEXT_PUBLIC_SENTRY_RELEASE || undefined;
const env = process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || "production";

if (dsn) {
  Sentry.init({
    dsn,
    environment: env,
    release,

    // Performance traces — 5% in prod, 0% otherwise. Errors always 100%.
    tracesSampleRate: env === "production" ? 0.05 : 0.0,

    // Session Replay — Team plan, 50 replays/month base. 1% baseline +
    // 100% on error so we capture real failures cheaply.
    replaysSessionSampleRate: env === "production" ? 0.01 : 0.0,
    replaysOnErrorSampleRate: 1.0,

    // Integrations: replay needs explicit instantiation in v10.
    integrations: [
      Sentry.replayIntegration({
        // Mask all text + media by default. The dashboard renders merchant
        // GMV/AOV figures + customer counts; even though shop_domain is
        // a tenant identifier (DPIA), the actual rendered numbers are
        // commercially sensitive. blockAllMedia avoids leaking imagery.
        maskAllText: true,
        blockAllMedia: true,
      }),
    ],

    // Component tag — matches the backend `component` tag pattern from
    // app/core/sentry_init.py so we can filter "frontend errors" cleanly.
    initialScope: {
      tags: { component: "frontend" },
    },
  });
}
