/**
 * error-reporter.ts — Forwards frontend errors to the self-healing pipeline.
 *
 * Every dashboard build is now observable by the autonomous repair loop:
 * whenever the React error boundary, window.onerror, or an unhandled
 * promise rejection fires, we POST a sanitized report to
 * `${API_BASE_URL}/ops/frontend-errors`. The backend writes an ops_alert
 * which the bugfix triage pipeline consumes like any other incident.
 *
 * Design constraints
 * ------------------
 * - Fire-and-forget: never block the UI, never throw from within the reporter.
 * - Dedup client-side: if the same (component + error.message) fires 3 times
 *   in 60s, we stop sending — prevents infinite-loop handlers from spamming.
 * - No bundled Sentry SDK: the backend owns the pipeline, the browser only
 *   needs a tiny POST helper. Keeps bundle size near zero.
 * - Strip secrets defensively on the client too: even though the backend
 *   redacts, the network hop itself should not carry obvious tokens.
 * - Respect page lifecycle: use `navigator.sendBeacon` when the page is
 *   unloading so errors during navigation still get through.
 */

const API_BASE_URL =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://127.0.0.1:8000";

const REPORT_URL = `${API_BASE_URL}/ops/frontend-errors`;

type Severity = "critical" | "warning" | "info";

export interface FrontendErrorReport {
  component: string;
  error_type: string;
  message: string;
  stack?: string | null;
  url?: string | null;
  user_agent?: string | null;
  shop_domain?: string | null;
  severity?: Severity;
  extra?: Record<string, unknown> | null;
}

// Client-side dedup window: (component + message) → first-seen timestamp and count.
const _seen = new Map<string, { firstTs: number; count: number }>();
const DEDUP_WINDOW_MS = 60_000;
const DEDUP_MAX_REPORTS = 3;

// Defense-in-depth secret stripping — mirrors the backend regex.
const SECRET_RE =
  /(bearer\s+[\w.\-]{8,}|api[_-]?key[=:]\s*[\w.\-]{8,}|sk_live_[\w]{8,}|sk_test_[\w]{8,})/gi;

function sanitize(value: string | null | undefined, max: number): string | null {
  if (!value) return null;
  try {
    return value.replace(SECRET_RE, "[REDACTED]").slice(0, max);
  } catch {
    return null;
  }
}

function shouldSend(dedupKey: string): boolean {
  const now = Date.now();
  const entry = _seen.get(dedupKey);
  if (!entry) {
    _seen.set(dedupKey, { firstTs: now, count: 1 });
    return true;
  }
  if (now - entry.firstTs > DEDUP_WINDOW_MS) {
    // Window expired, reset.
    _seen.set(dedupKey, { firstTs: now, count: 1 });
    return true;
  }
  entry.count += 1;
  return entry.count <= DEDUP_MAX_REPORTS;
}

function extractShopDomain(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const qs = new URLSearchParams(window.location.search);
    const shop = qs.get("shop");
    if (shop) return shop.slice(0, 256);
    // Fallback: look for a "shop" key in localStorage (set by the dashboard
    // on session bootstrap).
    const stored = window.localStorage?.getItem("hs_shop");
    return stored ? stored.slice(0, 256) : null;
  } catch {
    return null;
  }
}

export function reportFrontendError(input: FrontendErrorReport): void {
  if (typeof window === "undefined") return; // SSR safety
  let payload: string;
  try {
    const component = sanitize(input.component, 64) || "unknown";
    const errorType = sanitize(input.error_type, 64) || "UnknownError";
    const message = sanitize(input.message, 512) || "";
    const dedupKey = `${component}::${errorType}::${message}`;
    if (!shouldSend(dedupKey)) return;

    const body: FrontendErrorReport = {
      component,
      error_type: errorType,
      message,
      stack: sanitize(input.stack || null, 2048),
      url: sanitize(window.location.href, 256),
      user_agent: sanitize(navigator.userAgent, 256),
      shop_domain: input.shop_domain ?? extractShopDomain(),
      severity: input.severity || "warning",
      extra: input.extra ?? null,
    };
    payload = JSON.stringify(body);
  } catch {
    return; // never throw from the reporter
  }

  // Use sendBeacon when page is unloading — fetch gets cancelled during navigation.
  try {
    if (
      document.visibilityState === "hidden" &&
      typeof navigator.sendBeacon === "function"
    ) {
      const blob = new Blob([payload], { type: "application/json" });
      navigator.sendBeacon(REPORT_URL, blob);
      return;
    }
  } catch {
    /* fall through to fetch */
  }

  try {
    // fire-and-forget; we deliberately do not await
    void fetch(REPORT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      keepalive: true,
      credentials: "omit",
    }).catch(() => {});
  } catch {
    /* swallow */
  }
}

/**
 * Install the global handlers — idempotent. Call once from the root layout
 * on the client side. Captures:
 *   - uncaught runtime errors (window.onerror)
 *   - unhandled promise rejections
 *
 * Errors inside React render trees are captured by ClientErrorBoundary
 * (see components/ClientErrorBoundary.tsx), not here.
 */
let _installed = false;

export function installGlobalErrorReporter(): void {
  if (typeof window === "undefined" || _installed) return;
  _installed = true;

  window.addEventListener("error", (event) => {
    try {
      const err = event.error;
      reportFrontendError({
        component: "window.onerror",
        error_type: (err && err.name) || "Error",
        message: (err && err.message) || event.message || "unknown error",
        stack: (err && err.stack) || null,
        severity: "warning",
      });
    } catch {
      /* no-op */
    }
  });

  window.addEventListener("unhandledrejection", (event) => {
    try {
      const reason = event.reason;
      const message =
        reason instanceof Error
          ? reason.message
          : typeof reason === "string"
          ? reason
          : "unhandled promise rejection";
      reportFrontendError({
        component: "unhandledrejection",
        error_type: reason instanceof Error ? reason.name : "UnhandledRejection",
        message,
        stack: reason instanceof Error ? reason.stack || null : null,
        severity: "warning",
      });
    } catch {
      /* no-op */
    }
  });
}
