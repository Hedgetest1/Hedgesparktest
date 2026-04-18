"use client";

/**
 * RouteErrorFallback — shared UI for per-route error.tsx files.
 *
 * We cannot place a single error.tsx at the src/app/ root because that
 * file intercepts ANY hydration glitch on the landing page and replaces
 * the marketing content with a minimalist fallback — see the 2026-04-11
 * landing regression and test_root_layout_has_no_route_level_error_tsx.
 *
 * Instead, each route that can meaningfully crash gets its own thin
 * error.tsx under its own directory (/install/error.tsx, /pricing/error.tsx,
 * etc). Those tsx files delegate to this shared fallback to keep the
 * copy + design consistent without duplicating JSX across six routes.
 *
 * The fallback reports the error to the self-healing pipeline via
 * reportFrontendError (component = next:route:{route}) and offers
 * reset + "back to home" CTAs.
 */

import { useEffect } from "react";
import { reportFrontendError } from "../lib/error-reporter";

export function RouteErrorFallback({
  error,
  reset,
  route,
  severity = "warning",
}: {
  error: Error & { digest?: string };
  reset: () => void;
  route: string;
  severity?: "critical" | "warning" | "info";
}) {
  useEffect(() => {
    reportFrontendError({
      component: `next:route:${route}`,
      error_type: error.name || "RouteError",
      message: error.message || "route render failed",
      stack: error.stack || null,
      severity,
      extra: {
        digest: error.digest ?? null,
        route,
        path: typeof window !== "undefined" ? window.location.pathname : null,
      },
    });
  }, [error, route, severity]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center bg-[#07070f] px-6">
      <div className="max-w-md rounded-2xl border border-rose-400/20 bg-rose-500/[0.07] p-8 text-center">
        <div className="mb-1 text-[11px] font-bold uppercase tracking-[0.16em] text-rose-300">
          Page unavailable
        </div>
        <h2 className="mt-1 text-lg font-semibold text-rose-100">
          This page hit an error while rendering
        </h2>
        <p className="mt-3 text-sm text-rose-200/70">
          An automated report has been sent to engineering. The rest of
          the site keeps working — try again or head home.
        </p>
        {error.digest && (
          <p className="mt-2 inline-block rounded bg-black/30 px-2 py-1 font-mono text-[11px] text-rose-300/70">
            ref: {error.digest}
          </p>
        )}
        <div className="mt-5 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={reset}
            className="rounded-lg bg-rose-500/25 px-4 py-2 text-sm font-semibold text-rose-100 ring-1 ring-rose-400/25 transition hover:bg-rose-500/35"
          >
            Try again
          </button>
          <a
            href="/"
            className="rounded-lg border border-white/10 bg-white/[0.04] px-4 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.08]"
          >
            Back to home
          </a>
        </div>
      </div>
    </div>
  );
}
