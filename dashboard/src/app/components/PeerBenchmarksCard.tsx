"use client";

/**
 * PeerBenchmarksCard — "You vs. Similar Shops"
 *
 * Shows the merchant's percentile rank against peers in their revenue band
 * for 4 metrics (revenue, AOV, orders/day, growth). Loss-framed: every
 * row has a "recover by moving to p75" € estimate.
 *
 * Data source: GET /analytics/benchmarks (Lite-accessible, same data
 * as the old /pro/benchmarks). Privacy: minimum 10 peers per band,
 * below that an explicit insufficient-data note.
 *
 * Tier-agnostic since 2026-04-20: per founder directive "strada 2 —
 * completista", peer benchmarks become part of the €39 Lite surface.
 * The `isProUser` prop is retained for call-site back-compat but no
 * longer gates rendering.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";

type BenchmarkMetric = {
  value: number;
  band: string;
  peer_count: number;
  percentile_rank: number;
  p25: number;
  p50: number;
  p75: number;
  p90: number;
  recovery_to_p75_eur: number;
  status: string;
  narrative: string;
};

type BenchmarkData = {
  shop_domain: string;
  band: string | null;
  peer_count: number;
  metrics: Record<string, BenchmarkMetric>;
  total_recovery_potential_eur: number;
  // Shop's native currency — `_eur` fields are native.
  currency?: string;
  generated_at: string | null;
  note?: string | null;
  error?: string | null;
};

const METRIC_LABELS: Record<string, string> = {
  monthly_revenue: "Monthly revenue",
  aov: "Average order value",
  orders_per_day: "Orders per day",
  revenue_growth_30d_pct: "Revenue growth",
};

import { formatMoneyCompact } from "@/app/app/_lib/formatters";

function fmtMoney(n: number, currency?: string): string {
  return formatMoneyCompact(n, currency || "USD");
}

function fmtMetricValue(metric: string, v: number, currency?: string): string {
  if (metric === "revenue_growth_30d_pct") return v.toFixed(0) + "%";
  if (metric === "orders_per_day") return v.toFixed(1);
  if (metric === "monthly_revenue" || metric === "aov") return fmtMoney(v, currency);
  return String(Math.round(v));
}

function statusColor(status: string): string {
  switch (status) {
    case "top_decile":   return "#34d399"; // emerald
    case "top_quartile": return "#a3e635"; // lime
    case "above_median": return "#fbbf24"; // amber
    case "below_median": return "#f87171"; // rose
    default:             return "#94a3b8";
  }
}

export function PeerBenchmarksCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [data, setData] = useState<BenchmarkData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) { setLoading(false); return; }
    let active = true;
    setLoading(true);
    apiClient
      .GET("/analytics/benchmarks")
      .then(({ data: j, error: err }) => {
        if (!active) return;
        if (err || !j) setData(null);
        else setData(j as unknown as BenchmarkData);
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiBase, shop]);

  // `isProUser` retained in signature for back-compat (many callers
  // still pass it) but no longer affects rendering — benchmarks are
  // a Lite-tier feature since 2026-04-20.
  void isProUser;

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
        <div className="h-3 w-32 rounded bg-white/[0.06]" />
        <div className="mt-3 space-y-2">
          {[0, 1, 2, 3].map((i) => (<div key={i} className="h-10 rounded bg-white/[0.04]" />))}
        </div>
      </div>
    );
  }

  if (!data || data.error || data.note) {
    return (
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
        <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
          You vs. Similar Shops
        </div>
        <h3 className="text-[15px] font-bold text-white">How you compare to peers</h3>
        <p className="mt-2 text-[12px] leading-relaxed text-slate-400">
          {data?.note || "Comparison not available yet — we need at least 10 similar shops in your revenue band. Keep running — this activates automatically."}
        </p>
      </div>
    );
  }

  const entries = Object.entries(data.metrics);
  const totalRecovery = data.total_recovery_potential_eur || 0;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
            You vs. Similar Shops
          </div>
          <h3 className="text-[15px] font-bold text-white">How you compare to peers</h3>
          <p className="mt-1 text-[11px] text-slate-500">
            {data.peer_count} shops in the <span className="font-semibold text-slate-300">{data.band}</span> revenue band
          </p>
        </div>
        {totalRecovery > 0 && (
          <div className="flex-shrink-0 rounded-lg border border-amber-400/20 bg-amber-500/[0.06] px-3 py-2 text-right">
            <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-amber-400">
              Could recover
            </div>
            <div className="text-[18px] font-extrabold tabular-nums text-amber-300">
              {fmtMoney(totalRecovery, data?.currency)}/mo
            </div>
          </div>
        )}
      </div>

      <div className="space-y-2">
        {entries.map(([metric, m]) => {
          const color = statusColor(m.status);
          const rank = Math.round(m.percentile_rank);
          return (
            <div key={metric} className="rounded-xl border border-white/[0.04] bg-white/[0.015] p-3">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[12px] font-semibold text-slate-200">
                      {METRIC_LABELS[metric] || metric}
                    </span>
                    <span className="text-[10px] text-slate-500">
                      you: <span className="font-mono tabular-nums text-slate-300">{fmtMetricValue(metric, m.value, data?.currency)}</span>
                    </span>
                  </div>
                  <div className="mt-1 text-[10px] text-slate-500">
                    p25 {fmtMetricValue(metric, m.p25, data?.currency)} · p50 {fmtMetricValue(metric, m.p50, data?.currency)} · p75 {fmtMetricValue(metric, m.p75, data?.currency)}
                  </div>
                </div>
                <div
                  className="flex-shrink-0 rounded-full px-2.5 py-1 text-[10px] font-bold tabular-nums"
                  style={{ color, background: color + "20", border: `1px solid ${color}40` }}
                >
                  p{rank}
                </div>
              </div>
              {/* Rank bar */}
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{ width: `${Math.min(100, rank)}%`, background: color }}
                />
              </div>
              {m.recovery_to_p75_eur > 0 && (
                <div className="mt-1.5 text-[10px] text-amber-300">
                  → moving to p75 = <span className="font-semibold">+{fmtMoney(m.recovery_to_p75_eur, data?.currency)}/mo</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
