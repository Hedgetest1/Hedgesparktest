"use client";

import { useEffect, useState } from "react";

// CohortTable — Weekly cohort retention analysis.
//
// The feature that attacks Lifetimely/Peel on their core territory.
// Shows weekly cohort retention rates from real Shopify order data.
//
// Sources from: GET /pro/cohorts?shop=&weeks=12
//
// WishSpark's future advantage: linking behavioral engagement in week 0
// to retention outcomes in week 8 — Lifetimely structurally cannot do this.
// This component is the foundation for that future positioning.

type CohortWeek = Record<string, number>;

type Cohort = {
  cohort_week?: string;
  cohort_start?: string;
  size?: number;
  revenue_total?: number;
  retention?: CohortWeek;
};

type CohortData = {
  window_weeks?: number;
  generated_at?: string;
  cohorts?: Cohort[];
  avg_week_1_retention?: number;
  avg_week_4_retention?: number;
  best_cohort?: string | null;
  total_customers?: number;
};

function pctColor(pct: number | undefined): string {
  if (pct == null) return "text-slate-700";
  if (pct >= 0.30) return "text-emerald-300";
  if (pct >= 0.15) return "text-amber-300";
  if (pct > 0)     return "text-slate-400";
  return "text-slate-700";
}

function pctBg(pct: number | undefined): string {
  if (pct == null || pct === 0) return "bg-transparent";
  if (pct >= 0.30) return "bg-emerald-500/15";
  if (pct >= 0.15) return "bg-amber-500/10";
  return "bg-white/[0.03]";
}

function formatPct(v: number | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(0)}%`;
}

function formatDollars(v: number | undefined): string {
  if (!v) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD",
    minimumFractionDigits: 0, maximumFractionDigits: 0,
  }).format(v);
}

export function CohortTable({
  apiBase,
  shop,
  apiHeaders,
}: {
  apiBase: string;
  shop: string;
  apiHeaders: () => HeadersInit;
}) {
  const [data, setData] = useState<CohortData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!shop) return;
    let active = true;

    async function load() {
      try {
        setLoading(true);
        const res = await fetch(
          `${apiBase}/pro/cohorts?shop=${encodeURIComponent(shop)}&weeks=8`,
          { headers: apiHeaders(), credentials: "include", cache: "no-store" }
        );
        if (!res.ok) return;
        const json = await res.json();
        if (active) setData(json as CohortData);
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
        <div className="h-4 w-36 rounded bg-white/[0.05]" />
        <div className="mt-3 h-32 rounded bg-white/[0.03]" />
      </div>
    );
  }

  const cohorts = data?.cohorts ?? [];
  const avgW1   = data?.avg_week_1_retention ?? 0;
  const avgW4   = data?.avg_week_4_retention ?? 0;
  const total   = data?.total_customers ?? 0;

  // Determine how many week columns to show (max weeks that have any data)
  const maxWeek = cohorts.reduce((max, c) => {
    const keys = Object.keys(c.retention ?? {}).map((k) => parseInt(k.replace("week_", "")));
    return Math.max(max, ...keys, 0);
  }, 0);

  const weekColumns = Array.from({ length: Math.min(maxWeek, 8) }, (_, i) => i + 1);

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      {/* Header */}
      <div className="mb-4 flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
            Cohort Retention
          </div>
          <h3 className="text-[14px] font-semibold text-white">Weekly repeat purchase rates</h3>
          <p className="mt-0.5 text-[11px] text-slate-500">
            First-purchase cohorts from real Shopify order data
          </p>
        </div>
        <span className="rounded-full border border-violet-400/30 bg-violet-500/15 px-2 py-0.5 text-[10px] font-semibold text-violet-300">
          Pro
        </span>
      </div>

      {/* Summary stats */}
      {total > 0 && (
        <div className="mb-4 grid grid-cols-3 gap-2">
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
            <div className="text-[10px] uppercase text-slate-600">Total Customers</div>
            <div className="mt-0.5 text-[13px] font-semibold text-white">
              {total.toLocaleString()}
            </div>
          </div>
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
            <div className="text-[10px] uppercase text-slate-600">Avg Week-1 Return</div>
            <div className={`mt-0.5 text-[13px] font-semibold ${pctColor(avgW1)}`}>
              {formatPct(avgW1)}
            </div>
          </div>
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
            <div className="text-[10px] uppercase text-slate-600">Avg Week-4 Return</div>
            <div className={`mt-0.5 text-[13px] font-semibold ${pctColor(avgW4)}`}>
              {formatPct(avgW4)}
            </div>
          </div>
        </div>
      )}

      {cohorts.length === 0 ? (
        <p className="text-[12px] text-slate-600">
          Cohort data will appear here after your first Shopify orders are ingested via
          the orders webhook. Each cohort represents customers grouped by their first
          purchase week.
        </p>
      ) : weekColumns.length === 0 ? (
        <p className="text-[12px] text-slate-600">
          Cohorts exist but no retention data yet — too few weeks have passed to measure repeats.
        </p>
      ) : (
        /* Retention matrix table */
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="py-2 pr-4 text-[10px] font-medium uppercase tracking-[0.1em] text-slate-600">
                  Cohort
                </th>
                <th className="py-2 pr-4 text-[10px] font-medium uppercase tracking-[0.1em] text-slate-600">
                  Size
                </th>
                <th className="py-2 pr-4 text-[10px] font-medium uppercase tracking-[0.1em] text-slate-600">
                  Revenue
                </th>
                {weekColumns.map((w) => (
                  <th key={`wh-${w}`} className="px-2 py-2 text-center text-[10px] font-medium uppercase tracking-[0.1em] text-slate-600">
                    W{w}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cohorts.map((cohort, i) => (
                <tr
                  key={`c-${cohort.cohort_week ?? i}`}
                  className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors"
                >
                  <td className="py-2 pr-4 font-mono text-[11px] text-slate-400">
                    {cohort.cohort_week}
                  </td>
                  <td className="py-2 pr-4 text-slate-300">
                    {cohort.size?.toLocaleString() ?? "—"}
                  </td>
                  <td className="py-2 pr-4 text-slate-400">
                    {formatDollars(cohort.revenue_total)}
                  </td>
                  {weekColumns.map((w) => {
                    const key = `week_${w}`;
                    const val = cohort.retention?.[key];
                    return (
                      <td
                        key={`w-${w}`}
                        className={`px-2 py-2 text-center font-semibold tabular-nums ${pctColor(val)} ${pctBg(val)} rounded`}
                      >
                        {val != null ? formatPct(val) : <span className="text-slate-700">—</span>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-3 text-[10px] text-slate-600">
        Customers grouped by first purchase week. Each cell = % who repurchased in that week after acquisition.
      </p>
    </div>
  );
}
