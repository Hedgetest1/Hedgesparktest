/**
 * Next.js instrumentation hook (Next 15+).
 *
 * Loads the Sentry server / edge config at process boot — ensures the
 * SDK is initialized before any request handler runs. The client config
 * (sentry.client.config.ts) is wired by @sentry/nextjs at build time.
 *
 * Tier: TIER_0.
 */
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  }
  if (process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.edge.config");
  }
}

export async function onRequestError(...args: unknown[]) {
  // Forward unhandled request errors to Sentry; @sentry/nextjs ships
  // the helper. Lazy import keeps the bundle clean when DSN unset.
  if (!process.env.NEXT_PUBLIC_SENTRY_DSN && !process.env.SENTRY_DSN) {
    return;
  }
  try {
    const { captureRequestError } = await import("@sentry/nextjs");
    // @ts-expect-error — Next signature is forwarded as-is to Sentry.
    return captureRequestError(...args);
  } catch {
    // SILENT-EXCEPT-OK: instrumentation must never break a request
  }
}
