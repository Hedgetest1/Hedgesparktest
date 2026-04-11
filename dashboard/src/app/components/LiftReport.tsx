"use client";

import { useEffect, useState } from "react";
import { createMoneyFormatter, type DisplayCurrency } from "../lib/currency";
import { apiClient, getHeaders, type paths } from "../lib/api-client";

// LiftReport — Prominent holdout lift summary for the Pro dashboard.
//
// This is HedgeSpark's most defensible proof of value:
// "We prove our nudges work with a control group. No other tool can say that."
//
// Sources from: GET /pro/lift — fully typed via the generated OpenAPI types.
// Only rendered for Pro users.

type LiftData =
  paths["/pro/lift"]["get"]["responses"]["200"]["content"]["application/json"];

function formatPct(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

// formatDollars is now created per-render inside the component, bound to
// the user's displayCurrency and the lift report's native currency. Moved
// inline below to capture both values from closure scope.

function shortUrl(url: string | undefined): string {
  if (!url) return "—";
  const slug = url.split("/").filter(Boolean).pop() || url;
  return slug.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()).slice(0, 28);
}

function liftColor(lift: number | null | undefined): string {
  if (lift == null) return "text-slate-400";
  if (lift > 5)  return "text-emerald-300";
  if (lift >= 0) return "text-amber-300";
  return "text-rose-300";
}

export function LiftReport({
  apiBase,
  shop,
  apiHeaders,
  displayCurrency = "USD",
}: {
  apiBase: string;
  shop: string;
  apiHeaders: () => HeadersInit;
  displayCurrency?: DisplayCurrency;
}) {
  const [data, setData] = useState<LiftData | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!shop) return;
    let active = true;

    async function load() {
      try {
        setLoading(true);
        const res = await apiClient.GET("/pro/lift", {
          params: { query: { window_hours: 168 } },
          headers: getHeaders(apiHeaders),
        });
        if (active && res.data != null) setData(res.data);
      } catch { /* silent */ }
      finally { if (active) setLoading(false); }
    }

    load();
    return () => { active = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop]);

  if (loading) {
    return (
      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5 animate-pulse">
        <div className="h-4 w-40 rounded bg-white/[0.05]" />
        <div className="mt-3 h-3 w-full rounded bg-white/[0.03]" />
      </div>
    );
  }

  if (!data) return null;

  const hasData        = data.has_experiment_data;
  const lift           = data.lift_pct;
  const revenue        = data.attributed_revenue ?? 0;
  const breakdown      = data.nudge_breakdown ?? [];
  const nativeCurrency = data.currency ?? "USD";
  const formatDollars  = createMoneyFormatter(displayCurrency, nativeCurrency);
  const exposedCvr     = data.exposed_cvr ?? 0;
  const holdoutCvr     = data.holdout_cvr ?? 0;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
      {/* Header — branded eyebrow matching killer cassettone pattern */}
      <div className="mb-5">
        <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-400">
          Holdout Proof
        </div>
        <h3 className="mt-1 text-[15px] font-bold leading-tight text-white">
          Science, not stories
        </h3>
        <p className="mt-1.5 text-[12px] leading-relaxed text-slate-400">
          {hasData
            ? `We hold back ${data.total_holdout?.toLocaleString() ?? "a slice of"} visitors from every fix, compare them to the ${data.total_exposed?.toLocaleString() ?? "exposed"} who saw it, and measure the real lift. No inflation, no cherry-picking.`
            : `Deploy a nudge → we hold back 20% as control → compare who buys more. That's it.`}
        </p>
      </div>

      {!hasData ? (
        /* No experiment data yet */
        <div className="rounded-xl border border-dashed border-white/[0.06] bg-white/[0.015] px-4 py-8 text-center">
          <p className="text-[13px] font-semibold text-slate-300">
            No experiments running yet
          </p>
          <p className="mx-auto mt-2 max-w-md text-[12px] leading-relaxed text-slate-500">
            {data.verdict}
          </p>
        </div>
      ) : (
        /* Has experiment data */
        <div>
          {/* Main lift number — the hero moment */}
          <div className="flex items-end gap-6">
            <div>
              <div className={`text-[3rem] font-extrabold tabular-nums leading-none ${liftColor(lift)}`}>
                {formatPct(lift)}
              </div>
              <div className="mt-1 text-[13px] font-medium text-slate-400">conversion lift vs control</div>
            </div>
            {revenue > 0 && (
              <div className="mb-1.5">
                <div className="text-[1.5rem] font-bold tabular-nums leading-none text-emerald-300">
                  {formatDollars(revenue)}
                </div>
                <div className="mt-1 text-[13px] text-slate-400">extra revenue</div>
              </div>
            )}
          </div>

          {/* Exposed vs holdout CVR — with visual bars */}
          <div className="mt-5 grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3.5">
              <div className="text-[12px] font-bold uppercase tracking-[0.1em] text-slate-500">Saw your fix</div>
              <div className="mt-1 text-[20px] font-bold tabular-nums text-white">
                {(exposedCvr * 100).toFixed(2)}%
              </div>
              <div className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-white/[0.07]">
                <div className="h-full rounded-full bg-emerald-500" style={{ width: `${Math.min(100, exposedCvr * 100 * 20)}%` }} />
              </div>
              <div className="mt-1.5 text-[13px] text-slate-500">{data.total_exposed?.toLocaleString()} visitors</div>
            </div>
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3.5">
              <div className="text-[12px] font-bold uppercase tracking-[0.1em] text-slate-500">Control group</div>
              <div className="mt-1 text-[20px] font-bold tabular-nums text-white">
                {(holdoutCvr * 100).toFixed(2)}%
              </div>
              <div className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-white/[0.07]">
                <div className="h-full rounded-full bg-slate-600" style={{ width: `${Math.min(100, holdoutCvr * 100 * 20)}%` }} />
              </div>
              <div className="mt-1.5 text-[13px] text-slate-500">{data.total_holdout?.toLocaleString()} visitors</div>
            </div>
          </div>

          {/* Verdict */}
          <div className="mt-4 rounded-xl border border-[#d4893a]/10 bg-[#d4893a]/[0.03] px-5 py-4">
            <p className="text-[15px] leading-[1.6] text-slate-300">{data.verdict}</p>
          </div>

          {/* Nudge breakdown toggle */}
          {breakdown.length > 0 && (
            <div className="mt-4">
              <button
                onClick={() => setExpanded((x) => !x)}
                className="flex items-center gap-2 text-[14px] font-medium text-slate-400 hover:text-slate-200 transition-colors"
              >
                <svg
                  className={`h-4 w-4 transition-transform ${expanded ? "rotate-90" : ""}`}
                  fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
                {expanded ? "Hide" : "Show"} breakdown by nudge ({breakdown.length})
              </button>

              {expanded && (
                <div className="mt-3 space-y-2.5">
                  {breakdown.map((n, i) => (
                    <div key={`nb-${n.nudge_id ?? i}`} className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3.5">
                      <div className="flex items-center justify-between">
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[15px] font-semibold text-white">
                            {shortUrl(n.product_url)}
                          </div>
                          <div className="mt-1 text-[13px] text-slate-500">
                            {n.exposed_count?.toLocaleString()} saw fix &middot; {n.holdout_count?.toLocaleString()} control
                          </div>
                        </div>
                        <div className="ml-4 text-right">
                          <div className={`text-[18px] font-bold tabular-nums ${liftColor(n.lift_pct)}`}>
                            {formatPct(n.lift_pct)}
                          </div>
                          {n.attributed_revenue != null && n.attributed_revenue > 0 && (
                            <div className="text-[14px] font-semibold text-emerald-300/70">
                              {formatDollars(n.attributed_revenue)}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Trust footer — matches the killer cassettone pattern */}
      <div className="mt-5 inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3 py-1">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]" />
        <span className="text-[10px] text-slate-400">
          Quasi-experimental · hash-based assignment · measured over last 7 days
        </span>
      </div>
    </div>
  );
}
