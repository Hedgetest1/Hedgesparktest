"use client";

/**
 * Root route-level error boundary.
 *
 * Next.js App Router convention: a file named `error.tsx` at a route
 * segment catches errors raised during rendering of any component in
 * that segment or its descendants that are not already caught by a
 * nested boundary. This sits at the ROOT and covers every page not
 * overridden by a sub-segment error.tsx (e.g. /app has its own more
 * tailored boundary — see app/error.tsx).
 *
 * Fires BEFORE global-error.tsx in the chain: layout crashes still
 * escalate to global-error; component-level render errors are caught
 * here first and keep the rest of the site alive.
 *
 * Every invocation reports to the self-healing pipeline via
 * reportFrontendError so the autonomous repair loop learns about
 * route-level render failures.
 *
 * Reference: https://nextjs.org/docs/app/api-reference/file-conventions/error
 */

import { useEffect } from "react";
import { reportFrontendError } from "./lib/error-reporter";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    reportFrontendError({
      component: "next:route-error",
      error_type: error.name || "RouteError",
      message: error.message || "route render failed",
      stack: error.stack || null,
      severity: "warning",
      extra: {
        digest: error.digest ?? null,
        path: typeof window !== "undefined" ? window.location.pathname : null,
      },
    });
  }, [error]);

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
          the app keeps working — try again or head home.
        </p>
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
