"use client";

/**
 * VerticalBenchmarksCard — Phase Ω moat.
 *
 * Vertical-aware benchmarks: peers compared within (vertical, revenue_band)
 * not just revenue band. A €15k beauty brand benchmarked against
 * other €15k *beauty* brands.
 *
 * Source: GET /analytics/benchmarks/vertical (Lite + Pro accessible).
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";

type Metric = {
  value: number;
  vertical: string;
  band: string;
  scope: string;
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

type VerticalBenchmarkData = {
  shop_domain: string;
  vertical: string;
  vertical_display: string;
  band: string;
  scope: string;
  peer_count: number;
  metrics: Record<string, Metric>;
  total_recovery_potential_eur: number;
  // Shop's native currency — money fields (monthly_revenue, aov,
  // total_recovery_potential_eur, fallback_baselines.aov_baseline_eur)
  // are all in this currency. `_eur` suffix on field names is historical.
  currency?: string;
  generated_at: string;
  note?: string;
  fallback_baselines?: { cvr_baseline_pct?: number; aov_baseline_eur?: number };
};

type VerticalSelf = {
  shop_domain: string;
  vertical: string;
  confidence: number;
  runner_up: string | null;
  runner_up_confidence: number;
  sample_size: number;
  classified_at: string;
};

const VERTICAL_DISPLAY: Record<string, string> = {
  beauty: "Beauty",
  wellness: "Wellness",
  fashion: "Fashion",
  home: "Home & Living",
  food: "Food & Beverage",
  pet: "Pet",
  electronics: "Electronics",
  kids: "Kids & Baby",
  outdoor: "Outdoor",
  jewelry: "Jewelry",
  general: "General Retail",
};

function displayVertical(v: string): string {
  return VERTICAL_DISPLAY[v] || v.charAt(0).toUpperCase() + v.slice(1);
}

const METRIC_LABELS: Record<string, string> = {
  monthly_revenue: "Monthly revenue",
  aov: "Average order value",
  orders_per_day: "Orders per day",
  revenue_growth_30d_pct: "Revenue growth",
};

const SCOPE_LABELS: Record<string, string> = {
  vertical_band: "same vertical, same revenue band",
  vertical_only: "same vertical, all revenue bands",
  band_only: "same revenue band, all verticals",
  insufficient: "vertical pool still warming up",
};

// Benchmark figures are in the shop's NATIVE currency (`_eur` suffix
// on field names is historical). The component reads the `currency`
// field from the response and passes it down to this helper.
function fmtMoney(n: number, currency?: string): string {
  return formatMoneyCompact(n, currency || "USD");
}

function fmtMetric(metric: string, v: number, currency?: string): string {
  if (metric === "revenue_growth_30d_pct") return v.toFixed(0) + "%";
  if (metric === "orders_per_day") return v.toFixed(1);
  if (metric === "monthly_revenue" || metric === "aov") return fmtMoney(v, currency);
  return String(Math.round(v));
}

function statusColor(status: string): string {
  switch (status) {
    case "top_decile":   return "#34d399";
    case "top_quartile": return "#a3e635";
    case "above_median": return "#fbbf24";
    case "below_median": return "#f87171";
    default:             return "#94a3b8";
  }
}

export function VerticalBenchmarksCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [data, setData] = useState<VerticalBenchmarkData | null>(null);
  const [classification, setClassification] = useState<VerticalSelf | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastLive, setLastLive] = useState<string | null>(null);

  useEffect(() => {
    if (!apiBase || !shop) { setLoading(false); return; }
    let active = true;
    setLoading(true);

    const refetch = async () => {
      try {
        const { data: j, error: err } = await apiClient.GET("/analytics/benchmarks/vertical");
        if (err || !j) throw new Error("fetch failed");
        if (active) {
          setData(j as unknown as VerticalBenchmarkData);
          setLastLive(new Date().toISOString());
        }
      } catch {
        if (active) setData(null);
      }
    };

    const fetchClassification = async () => {
      // Tier-gate aware: /pro/vertical for Pro merchants, /analytics/vertical
      // for Lite. Both endpoints return the same shape (VerticalSelfResponse).
      // The `isProUser` prop drives the choice — when this card renders on
      // the Lite floor (commit 2af2cd6), isProUser is false and we call the
      // Lite-accessible sibling. Mismatching this produces a silent 403
      // surfaced by `verify_lite_dashboard_e2e.js` axis-2 cell.
      const endpoint = isProUser ? "/pro/vertical" : "/analytics/vertical";
      const { data: j, error: err } = await apiClient.GET(endpoint);
      if (err || !j || !active) return;
      setClassification(j as unknown as VerticalSelf);
    };

    refetch().finally(() => { if (active) setLoading(false); });
    fetchClassification();

    // Phase Ω⁵ live stream
    let es: EventSource | null = null;
    try {
      es = new EventSource(`${apiBase}/pro/stream/dashboard`, { withCredentials: true });
      es.addEventListener("snapshot", (ev: MessageEvent) => {
        if (!active) return;
        try {
          const snap = JSON.parse(ev.data);
          setLastLive(new Date().toISOString());
          const incoming = snap?.benchmarks?.total_recovery_eur;
          const current = data?.total_recovery_potential_eur;
          if (incoming != null && current != null && Math.abs(incoming - current) > 1) {
            refetch();
          }
        } catch {}
      });
      es.onerror = () => {};
    } catch {}

    return () => {
      active = false;
      try { es?.close(); } catch {}
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, shop]);

  // `isProUser` kept in signature for call-site back-compat — no
  // longer gates rendering per Strada 3.1 (vertical benchmarks are
  // now a Lite feature).
  void isProUser;

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
        <div className="h-3 w-40 rounded bg-white/[0.06]" />
        <div className="mt-3 space-y-2">
          {[0, 1, 2, 3].map((i) => (<div key={i} className="h-10 rounded bg-white/[0.04]" />))}
        </div>
      </div>
    );
  }

  if (!data || data.scope === "insufficient" || !data.metrics) {
    return (
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
        <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
          Vertical Benchmarks
        </div>
        <h3 className="text-[15px] font-bold text-white">
          {data?.vertical_display || "Your vertical"} — peer pool warming up
        </h3>
        {classification && classification.confidence > 0 && (
          <div
            className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-violet-400/20 bg-violet-500/[0.06] px-2 py-0.5 text-[10px] text-violet-200/90"
            title={`Classified from ${classification.sample_size.toLocaleString("en")} signals.`}
            role="status"
          >
            <span aria-hidden="true">◈</span>
            <span>
              classified as{" "}
              <b className="font-bold text-violet-100">{displayVertical(classification.vertical)}</b>
              {" "}with <span className="tabular-nums">{Math.round(classification.confidence * 100)}%</span> confidence
            </span>
          </div>
        )}
        <p className="mt-2 text-[12px] leading-relaxed text-slate-400">
          {data?.note || "We need a minimum of 8 stores in your specific vertical and revenue band to compare you fairly. Below that, no fake numbers."}
        </p>
        {data?.fallback_baselines && (
          <div className="mt-3 rounded-lg border border-white/[0.06] bg-white/[0.02] p-3 text-[11px] text-slate-400">
            Industry medians (fallback):
            {" "}{data.fallback_baselines.cvr_baseline_pct && `CVR ${data.fallback_baselines.cvr_baseline_pct}% · `}
            {data.fallback_baselines.aov_baseline_eur && `AOV ${fmtMoney(data.fallback_baselines.aov_baseline_eur, data.currency)}`}
          </div>
        )}
      </div>
    );
  }

  const entries = Object.entries(data.metrics);
  const totalRecovery = data.total_recovery_potential_eur || 0;

  return (
    <section
      className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5"
      aria-labelledby="vertical-bench-heading"
      role="region"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]" aria-hidden="true">
            Vertical Benchmarks
          </div>
          <h3 id="vertical-bench-heading" className="text-[15px] font-bold text-white">
            You vs. {data.vertical_display}
          </h3>
          <p className="mt-1 text-[11px] text-slate-400">
            {data.peer_count} peers · {SCOPE_LABELS[data.scope] || data.scope}
            {lastLive && (
              <span
                className="ml-2 inline-flex items-center gap-1 rounded-full bg-white/[0.03] px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-emerald-300/80"
                title={`Live · ${new Date(lastLive).toLocaleTimeString()}`}
              >
                <span className="relative inline-flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/60"></span>
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400"></span>
                </span>
                live
              </span>
            )}
          </p>
          {classification && classification.confidence > 0 && (
            <div
              className="mt-1.5 inline-flex items-center gap-1.5 rounded-md border border-violet-400/20 bg-violet-500/[0.06] px-2 py-0.5 text-[10px] text-violet-200/90"
              title={
                classification.runner_up && classification.runner_up_confidence > 0
                  ? `Classified from ${classification.sample_size.toLocaleString("en")} signals. Runner-up: ${displayVertical(classification.runner_up)} (${Math.round(classification.runner_up_confidence * 100)}% confidence).`
                  : `Classified from ${classification.sample_size.toLocaleString("en")} signals.`
              }
              role="status"
            >
              <span aria-hidden="true">◈</span>
              <span>
                classified as{" "}
                <b className="font-bold text-violet-100">{displayVertical(classification.vertical)}</b>
                {" "}with <span className="tabular-nums">{Math.round(classification.confidence * 100)}%</span> confidence
                {classification.runner_up && classification.runner_up_confidence > 0 && (
                  <span className="text-violet-300/60">
                    {" "}· runner-up {displayVertical(classification.runner_up)}{" "}
                    <span className="tabular-nums">{Math.round(classification.runner_up_confidence * 100)}%</span>
                  </span>
                )}
              </span>
            </div>
          )}
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

      <ul className="space-y-2" aria-label="Per-metric benchmark comparison">
        {entries.map(([metric, m]) => {
          const color = statusColor(m.status);
          const rank = Math.round(m.percentile_rank);
          return (
            <li key={metric} className="rounded-xl border border-white/[0.04] bg-white/[0.015] p-3">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[12px] font-semibold text-slate-200">
                      {METRIC_LABELS[metric] || metric}
                    </span>
                    <span className="text-[10px] text-slate-400">
                      you: <span className="font-mono tabular-nums text-slate-300">{fmtMetric(metric, m.value, data?.currency)}</span>
                    </span>
                  </div>
                  <div className="mt-1 text-[10px] text-slate-400">
                    p25 {fmtMetric(metric, m.p25, data?.currency)} · p50 {fmtMetric(metric, m.p50, data?.currency)} · p75 {fmtMetric(metric, m.p75, data?.currency)}
                  </div>
                </div>
                <div
                  className="flex-shrink-0 rounded-full px-2.5 py-1 text-[10px] font-bold tabular-nums"
                  style={{ color, background: color + "20", border: `1px solid ${color}40` }}
                >
                  p{rank}
                </div>
              </div>
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
            </li>
          );
        })}
      </ul>
    </section>
  );
}
