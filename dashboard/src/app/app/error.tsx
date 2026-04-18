"use client";

/**
 * Dashboard segment error boundary — catches any render crash inside
 * /app/* that the per-section SectionErrorBoundary wraps did not
 * catch (e.g. an error raised by the layout component itself or by
 * hooks that run before any section mounts).
 *
 * Merchant-facing message is tailored for the dashboard context:
 * reassure that underlying data is safe, offer reload, point to
 * status page so merchants can verify ops is aware.
 */

import { useEffect } from "react";
import { reportFrontendError } from "../lib/error-reporter";

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    reportFrontendError({
      component: "next:dashboard-error",
      error_type: error.name || "DashboardRouteError",
      message: error.message || "dashboard route render failed",
      stack: error.stack || null,
      severity: "critical",
      extra: {
        digest: error.digest ?? null,
        path: typeof window !== "undefined" ? window.location.pathname : null,
      },
    });
  }, [error]);

  return (
    <div className="flex min-h-[70vh] items-center justify-center bg-[#07070f] px-6">
      <div className="max-w-lg rounded-2xl border border-rose-400/25 bg-rose-500/[0.05] p-10 text-center">
        <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-rose-300">
          Dashboard error
        </div>
        <h2 className="text-xl font-semibold text-rose-100">
          We couldn&apos;t render your dashboard
        </h2>
        <p className="mt-4 text-sm text-rose-200/75">
          Your store data is safe — this is a display issue. An
          automated report has been sent to the self-healing pipeline.
          Retry below, or check the status page if this persists.
        </p>
        {error.digest && (
          <p className="mt-3 inline-block rounded bg-black/30 px-2 py-1 font-mono text-[11px] text-rose-300/70">
            ref: {error.digest}
          </p>
        )}
        <div className="mt-6 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={reset}
            className="rounded-lg bg-rose-500/30 px-4 py-2 text-sm font-semibold text-rose-50 ring-1 ring-rose-400/30 transition hover:bg-rose-500/40"
          >
            Retry dashboard
          </button>
          <a
            href="/status"
            className="rounded-lg border border-white/10 bg-white/[0.04] px-4 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.08]"
          >
            Status page
          </a>
        </div>
      </div>
    </div>
  );
}
