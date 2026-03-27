"use client";

import { useEffect, useState } from "react";

// LiftReport — Prominent holdout lift summary for the Pro dashboard.
//
// This is WishSpark's most defensible proof of value:
// "We prove our nudges work with a control group. No other tool can say that."
//
// Sources from: GET /pro/lift?shop=&window_hours=
// Only rendered for Pro users.

type NudgeBreakdown = {
  nudge_id?: number;
  product_url?: string;
  action_type?: string;
  holdout_pct?: number;
  exposed_count?: number;
  holdout_count?: number;
  exposed_cvr?: number;
  holdout_cvr?: number;
  lift_pct?: number | null;
  attributed_revenue?: number;
  currency?: string;
};

type LiftData = {
  has_experiment_data?: boolean;
  nudges_measured?: number;
  total_exposed?: number;
  total_holdout?: number;
  exposed_cvr?: number;
  holdout_cvr?: number;
  lift_pct?: number | null;
  attributed_revenue?: number;
  currency?: string;
  verdict?: string;
  nudge_breakdown?: NudgeBreakdown[];
  window_hours?: number;
  generated_at?: string;
};

function formatPct(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

function formatDollars(v: number | undefined): string {
  if (v == null || isNaN(v) || v === 0) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD",
    minimumFractionDigits: 0, maximumFractionDigits: 0,
  }).format(v);
}

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
}: {
  apiBase: string;
  shop: string;
  apiHeaders: () => HeadersInit;
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
        const res = await fetch(
          `${apiBase}/pro/lift?shop=${encodeURIComponent(shop)}&window_hours=168`,
          { headers: apiHeaders(), credentials: "include", cache: "no-store" }
        );
        if (!res.ok) return;
        const json = await res.json();
        if (active) setData(json as LiftData);
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
  const exposedCvr     = data.exposed_cvr ?? 0;
  const holdoutCvr     = data.holdout_cvr ?? 0;

  return (
    <div className="rounded-2xl border border-violet-400/[0.14] bg-gradient-to-br from-violet-950/20 to-[#09091a] p-5">
      {/* Header */}
      <div className="mb-1 flex items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-violet-400/60">
          Holdout Lift Report
        </span>
        <span className="rounded-full border border-violet-400/20 bg-violet-500/10 px-2 py-px text-[9px] font-semibold text-violet-300">
          Pro
        </span>
      </div>

      {!hasData ? (
        /* No experiment data yet */
        <div>
          <p className="mt-1 text-[13px] font-medium text-slate-300">
            No holdout experiments running yet
          </p>
          <p className="mt-1.5 text-[12px] leading-[1.6] text-slate-500">
            {data.verdict}
          </p>
          <div className="mt-3 rounded-xl border border-violet-400/[0.1] bg-violet-500/[0.04] px-4 py-3">
            <p className="text-[11px] leading-[1.6] text-slate-400">
              <strong className="text-slate-300">How to start:</strong> Create a nudge, then use{" "}
              <code className="rounded bg-white/[0.06] px-1 text-violet-300">PATCH /pro/nudges/{"{id}"}/holdout</code>{" "}
              to enable a 20% holdout group. WishSpark will measure whether the nudge
              drives more conversions than the control group.
            </p>
          </div>
        </div>
      ) : (
        /* Has experiment data */
        <div>
          {/* Main lift number */}
          <div className="mt-2 flex items-end gap-4">
            <div>
              <div className={`text-[32px] font-bold tabular-nums ${liftColor(lift)}`}>
                {formatPct(lift)}
              </div>
              <div className="text-[11px] text-slate-500">conversion lift vs control</div>
            </div>
            {revenue > 0 && (
              <div className="mb-1">
                <div className="text-[18px] font-semibold tabular-nums text-emerald-300">
                  {formatDollars(revenue)}
                </div>
                <div className="text-[11px] text-slate-500">attributed revenue</div>
              </div>
            )}
          </div>

          {/* Exposed vs holdout CVR */}
          <div className="mt-3 grid grid-cols-2 gap-2">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
              <div className="text-[10px] uppercase tracking-[0.1em] text-slate-600">Exposed CVR</div>
              <div className="mt-0.5 text-[14px] font-semibold tabular-nums text-white">
                {(exposedCvr * 100).toFixed(2)}%
              </div>
              <div className="text-[10px] text-slate-600">{data.total_exposed?.toLocaleString()} visitors</div>
            </div>
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
              <div className="text-[10px] uppercase tracking-[0.1em] text-slate-600">Control CVR</div>
              <div className="mt-0.5 text-[14px] font-semibold tabular-nums text-white">
                {(holdoutCvr * 100).toFixed(2)}%
              </div>
              <div className="text-[10px] text-slate-600">{data.total_holdout?.toLocaleString()} visitors</div>
            </div>
          </div>

          {/* Verdict */}
          <div className="mt-3 rounded-xl border border-violet-400/[0.1] bg-violet-500/[0.04] px-4 py-3">
            <p className="text-[12px] leading-[1.6] text-slate-300">{data.verdict}</p>
          </div>

          {/* Nudge breakdown toggle */}
          {breakdown.length > 0 && (
            <div className="mt-3">
              <button
                onClick={() => setExpanded((x) => !x)}
                className="flex items-center gap-1.5 text-[11px] text-slate-500 hover:text-slate-300 transition-colors"
              >
                <svg
                  className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-90" : ""}`}
                  fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
                {expanded ? "Hide" : "Show"} breakdown by nudge ({breakdown.length})
              </button>

              {expanded && (
                <div className="mt-2 space-y-2">
                  {breakdown.map((n, i) => (
                    <div key={`nb-${n.nudge_id ?? i}`} className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5">
                      <div className="flex items-center justify-between">
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[12px] font-medium text-white">
                            {shortUrl(n.product_url)}
                          </div>
                          <div className="mt-0.5 text-[10px] text-slate-500">
                            {n.exposed_count?.toLocaleString()} exposed · {n.holdout_count?.toLocaleString()} control
                          </div>
                        </div>
                        <div className="ml-3 text-right">
                          <div className={`text-[13px] font-semibold tabular-nums ${liftColor(n.lift_pct)}`}>
                            {formatPct(n.lift_pct)}
                          </div>
                          {n.attributed_revenue != null && n.attributed_revenue > 0 && (
                            <div className="text-[11px] text-emerald-300/70">
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

      <p className="mt-3 text-[10px] text-slate-600">
        Lift measured over last 7 days. Observational attribution — not a randomized controlled trial.
      </p>
    </div>
  );
}
