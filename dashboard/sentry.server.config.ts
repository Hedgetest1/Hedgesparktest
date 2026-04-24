/**
 * Sentry Node-side init for the Next.js dashboard's server runtime.
 * Captures errors from API routes, server components, middleware.
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
      tags: { component: "frontend-ssr" },
    },
  });
}
