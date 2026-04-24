/**
 * Sentry edge-runtime init for the Next.js dashboard.
 * Used by Next.js edge middleware + edge API routes (rare in this app
 * since middleware is minimal). Mirrors server config.
 *
 * Tier: TIER_0.
 */
import * as Sentry from "@sentry/nextjs";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN || process.env.SENTRY_DSN || "";
const release = process.env.SENTRY_RELEASE || process.env.NEXT_PUBLIC_SENTRY_RELEASE || undefined;
const env = process.env.SENTRY_ENVIRONMENT || process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || "production";

if (dsn) {
  Sentry.init({
    dsn,
    environment: env,
    release,
    tracesSampleRate: env === "production" ? 0.05 : 0.0,
    initialScope: {
      tags: { component: "frontend-edge" },
    },
  });
}
