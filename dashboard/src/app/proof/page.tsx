"use client";

import { useEffect, useState } from "react";
import { reportFrontendError } from "../lib/error-reporter";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "";

interface ProofData {
  headline: string;
  proof: {
    type: string;
    lift_pct?: number;
    exposed_cvr?: number;
    holdout_cvr?: number;
    exposed_count?: number;
    holdout_count?: number;
    total_exposed?: number;
    total_holdout?: number;
    p_value?: number;
    incremental_revenue?: number;
    currency?: string;
    confidence?: string;
    nudges_measured?: number;
  };
  proof_type: string;
  view_count: number;
}

export default function ProofPage() {
  const [data, setData] = useState<ProofData | null>(null);
  const [error, setError] = useState(false);
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    // Synchronously deriving the token from window location + firing the
    // fetch is fine; but calling setError(true) INLINE on the "no token"
    // branch triggered a cascading render (React warns about setState
    // inside an effect body). Move the token resolution into a local
    // and only call setError via the catch path of an async IIFE.
    const params = new URLSearchParams(window.location.search);
    const t = params.get("t") || window.location.pathname.split("/proof/")[1];
    if (!t) {
      // Defer the state write to the next microtask so React doesn't
      // see it as a synchronous self-render inside the effect body.
      queueMicrotask(() => setError(true));
      return;
    }
    setToken(t);

    fetch(`${API}/public/proof/${t}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setData(d); else setError(true); })
      .catch((err: unknown) => {
        setError(true);
        const e = err as { name?: string; message?: string } | null;
        reportFrontendError({
          component: "ProofPage",
          error_type: (e && e.name) || "ProofFetchError",
          message: (e && e.message) || "public proof fetch failed",
          severity: "warning",
        });
      });
  }, []);

  const trackCta = () => {
    if (token) {
      fetch(`${API}/public/proof/${token}/event`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_type: "click_cta" }),
      }).catch((err: unknown) => {
        // Public proof-share analytics — the visitor flow is unblocked
        // regardless, but we report so we notice if the endpoint breaks.
        const e = err as { name?: string; message?: string } | null;
        reportFrontendError({
          component: "proofPage.trackCta",
          error_type: e?.name ?? "FetchError",
          message: e?.message ?? "Failed to POST proof event",
          severity: "info",
        });
      });
    }
  };

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#080811] text-white">
        <div className="text-center">
          <h1 className="text-2xl font-bold">Proof not found</h1>
          <p className="mt-3 text-slate-500">This proof report may have expired.</p>
          <a href="/" className="mt-6 inline-block text-violet-400 hover:text-violet-300">
            Learn about HedgeSpark &rarr;
          </a>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#080811]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-violet-400 border-t-transparent" />
      </div>
    );
  }

  const p = data.proof;
  const lift = p.lift_pct;
  const exposed = p.exposed_count || p.total_exposed || 0;
  const holdout = p.holdout_count || p.total_holdout || 0;
  const total = exposed + holdout;
  const pVal = p.p_value;

  return (
    <div className="min-h-screen bg-[#080811] text-white antialiased">
      <div className="mx-auto max-w-[36rem] px-6 py-20">
        {/* Header */}
        <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.2em] text-[#d4893a]/60">
          <span>Proof-based result</span>
          <span className="text-slate-700">&middot;</span>
          <span className="text-slate-700">HedgeSpark</span>
        </div>

        {/* Headline */}
        <h1 className="mt-6 text-[2rem] font-extrabold leading-[1.1] tracking-tight text-white sm:text-[2.5rem]">
          {data.headline}
        </h1>

        {/* Lift card */}
        <div className="mt-10 rounded-2xl border border-white/[0.05] bg-[#0a0a17] p-7">
          {lift != null && lift > 0 && (
            <div className="text-center">
              <div className="bg-gradient-to-r from-emerald-400 to-emerald-300 bg-clip-text text-[3rem] font-extrabold tabular-nums text-transparent">
                +{lift.toFixed(0)}%
              </div>
              <div className="mt-1 text-[13px] text-slate-500">conversion lift</div>
            </div>
          )}

          <div className="mt-6 space-y-3">
            {p.exposed_cvr != null && p.holdout_cvr != null && (
              <>
                <div className="flex items-center justify-between text-[13px]">
                  <span className="text-slate-400">Treatment group</span>
                  <span className="font-bold tabular-nums text-emerald-400">
                    {(p.exposed_cvr * 100).toFixed(2)}% CVR
                  </span>
                </div>
                <div className="flex items-center justify-between text-[13px]">
                  <span className="text-slate-400">Control group (holdout)</span>
                  <span className="font-bold tabular-nums text-slate-500">
                    {(p.holdout_cvr * 100).toFixed(2)}% CVR
                  </span>
                </div>
              </>
            )}
          </div>

          {(pVal != null || total > 0) && (
            <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-white/[0.05] pt-4">
              {pVal != null && pVal < 0.05 && (
                <span className="rounded-md bg-emerald-500/10 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-emerald-300 ring-1 ring-emerald-500/20">
                  Statistically significant
                </span>
              )}
              {pVal != null && (
                <span className="text-[10px] tabular-nums text-slate-600">
                  p = {pVal.toFixed(4)}
                </span>
              )}
              {total > 0 && (
                <span className="text-[10px] tabular-nums text-slate-600">
                  {total.toLocaleString()} visitors measured
                </span>
              )}
            </div>
          )}
        </div>

        {/* Methodology */}
        <div className="mt-8 rounded-xl border border-white/[0.04] bg-white/[0.01] p-5">
          <h3 className="text-[12px] font-bold uppercase tracking-[0.12em] text-slate-600">How this was measured</h3>
          <p className="mt-3 text-[13px] leading-[1.7] text-slate-500">
            A random 20% of visitors were held back as a control group and shown the original page.
            The remaining 80% saw the optimized version. Conversion rates were compared after sufficient
            traffic to determine statistical significance. No guessing — real holdout testing.
          </p>
        </div>

        {/* CTA */}
        <div className="mt-12 text-center">
          <p className="text-[14px] text-slate-400">
            Proof-based revenue intelligence for Shopify.
          </p>
          <a
            href="https://hedgesparkhq.com"
            onClick={trackCta}
            className="mt-5 inline-block rounded-xl bg-gradient-to-r from-[#d4893a] to-[#7c3aed] px-10 py-4 text-[15px] font-semibold text-white transition-all duration-300 hover:shadow-[0_4px_40px_rgba(212,137,58,0.3)]"
          >
            Get this for your Shopify store
          </a>
          <p className="mt-4 text-[12px] text-slate-600">
            First signal in 10 minutes. Built for Shopify stores.
          </p>
        </div>

        {/* Footer */}
        <div className="mt-16 border-t border-white/[0.03] pt-6 text-center text-[11px] text-slate-700">
          Measured by HedgeSpark &middot; Proof-Based Revenue Intelligence
        </div>
      </div>
    </div>
  );
}
