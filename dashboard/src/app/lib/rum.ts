/**
 * rum.ts — Real-user monitoring web-vitals collector.
 *
 * Captures the five Core Web Vitals we care about — TTFB, FCP, LCP,
 * CLS, INP — from real visitors and ships them to the backend
 * `/rum/metric` endpoint. The backend aggregates into rolling samples
 * and runs daily p75 regression detection. See
 * app/services/rum_monitor.py.
 *
 * Why native PerformanceObserver, not the `web-vitals` npm package?
 * ---------------------------------------------------------------
 *   1. Zero bundle size. The package is ~3KB min+gzip but the code
 *      below is smaller and gives us exactly what we need.
 *   2. Fewer dependency surfaces for the self-healing pipeline to
 *      worry about on version bumps.
 *   3. We control the dispatch contract (sendBeacon + our own
 *      endpoint + our own fingerprint of `route`).
 *
 * Design
 * ------
 * - Start observing on mount (client only). One instance per page.
 * - Accumulate: LCP = last observed, CLS = sum of session-window
 *   layout shifts, INP = max interaction duration seen.
 * - Flush on pagehide OR visibilitychange=hidden. Browsers do not
 *   reliably fire `beforeunload` on mobile, so pagehide is the
 *   canonical unload hook.
 * - Use navigator.sendBeacon when leaving the page (survives nav);
 *   fall back to fetch keepalive if Beacon is blocked/missing.
 * - Defensive: every callback is wrapped in try/catch — the
 *   collector must never throw into the app.
 */

const API_BASE_URL =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://127.0.0.1:8000";

const RUM_URL = `${API_BASE_URL}/rum/metric`;

type VitalsSnapshot = {
  ttfb?: number;
  fcp?: number;
  lcp?: number;
  cls?: number;
  inp?: number;
};

let _installed = false;

function _route(): string {
  try {
    const path = window.location.pathname || "/";
    return path.length > 128 ? path.slice(0, 128) : path;
  } catch {
    return "/";
  }
}

function _postOne(metric: keyof VitalsSnapshot, value: number, route: string): void {
  try {
    const body = JSON.stringify({ route, metric, value: Math.max(0, value) });
    if (
      document.visibilityState === "hidden" &&
      typeof navigator.sendBeacon === "function"
    ) {
      const blob = new Blob([body], { type: "application/json" });
      const ok = navigator.sendBeacon(RUM_URL, blob);
      if (ok) return;
    }
    void fetch(RUM_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
      credentials: "omit",
    }).catch(() => {
      /* RUM is best-effort; swallow here — error-reporter already
         captures unhandled rejections at the global level. */
    });
  } catch {
    /* never throw */
  }
}

function _flush(snapshot: VitalsSnapshot, route: string): void {
  (Object.keys(snapshot) as (keyof VitalsSnapshot)[]).forEach((metric) => {
    const v = snapshot[metric];
    if (typeof v === "number" && isFinite(v) && v >= 0) {
      _postOne(metric, v, route);
    }
  });
}

/**
 * Install the collector. Idempotent — safe to call on every route
 * change; subsequent calls are no-ops.
 */
export function installRumCollector(): void {
  if (typeof window === "undefined") return;
  if (_installed) return;
  _installed = true;

  const route = _route();
  const snapshot: VitalsSnapshot = {};

  // --- TTFB from the navigation entry, available synchronously ---
  try {
    const navEntries = performance.getEntriesByType(
      "navigation",
    ) as PerformanceNavigationTiming[];
    if (navEntries.length > 0) {
      const nav = navEntries[0];
      // TTFB = responseStart - startTime. startTime is 0 for the initial
      // document navigation, so responseStart is the standard proxy.
      const ttfb = Math.max(0, nav.responseStart - nav.startTime);
      if (isFinite(ttfb)) snapshot.ttfb = Math.round(ttfb);
    }
  } catch {
    /* no-op */
  }

  // --- FCP via paint entries ---
  try {
    const po = new PerformanceObserver((list) => {
      try {
        for (const entry of list.getEntries()) {
          if (entry.name === "first-contentful-paint") {
            snapshot.fcp = Math.round(entry.startTime);
          }
        }
      } catch {
        /* no-op */
      }
    });
    po.observe({ type: "paint", buffered: true });
  } catch {
    /* no-op — older Safari may not support `paint` */
  }

  // --- LCP via largest-contentful-paint ---
  try {
    const po = new PerformanceObserver((list) => {
      try {
        const entries = list.getEntries();
        if (entries.length > 0) {
          const last = entries[entries.length - 1];
          // startTime on LCP entries is the paint time.
          snapshot.lcp = Math.round(last.startTime);
        }
      } catch {
        /* no-op */
      }
    });
    po.observe({ type: "largest-contentful-paint", buffered: true });
  } catch {
    /* no-op */
  }

  // --- CLS via layout-shift, session-windowed ---
  // Google's official algorithm: max across 1s-gap / 5s-window sessions.
  // For our drift-detection use case, cumulative is close enough and far
  // simpler. Upgrade later only if we find false-negatives.
  try {
    const po = new PerformanceObserver((list) => {
      try {
        let cls = snapshot.cls || 0;
        for (const entry of list.getEntries() as PerformanceEntry[]) {
          type LayoutShiftEntry = PerformanceEntry & {
            value?: number;
            hadRecentInput?: boolean;
          };
          const ls = entry as LayoutShiftEntry;
          if (!ls.hadRecentInput && typeof ls.value === "number") {
            cls += ls.value;
          }
        }
        snapshot.cls = Math.min(10, cls);
      } catch {
        /* no-op */
      }
    });
    po.observe({ type: "layout-shift", buffered: true });
  } catch {
    /* no-op */
  }

  // --- INP via event entries with durationThreshold ---
  // Fallback to `first-input` on browsers that do not yet support
  // observing `event` with durationThreshold (older Safari).
  try {
    const po = new PerformanceObserver((list) => {
      try {
        let worst = snapshot.inp || 0;
        for (const entry of list.getEntries()) {
          const d = (entry as PerformanceEntry).duration || 0;
          if (d > worst) worst = d;
        }
        snapshot.inp = Math.round(worst);
      } catch {
        /* no-op */
      }
    });
    // Try `event` first; some browsers reject unknown entryTypes.
    try {
      po.observe({ type: "event", buffered: true, durationThreshold: 40 } as PerformanceObserverInit);
    } catch {
      po.observe({ type: "first-input", buffered: true });
    }
  } catch {
    /* no-op */
  }

  // --- Flush on page hide (reliable cross-browser) ---
  const flushOnce = () => _flush(snapshot, route);
  try {
    addEventListener("pagehide", flushOnce, { once: true });
    addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") flushOnce();
    });
  } catch {
    /* no-op */
  }
}
