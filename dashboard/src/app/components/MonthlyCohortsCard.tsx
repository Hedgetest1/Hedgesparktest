"use client";

/**
 * MonthlyCohortsCard — monthly acquisition LTV breakdown (Strada 3.3).
 *
 * Complements CohortSummaryCard (weekly top-line retention) with a
 * monthly-cohort lens: for each month, we show the acquired customer
 * count, revenue per customer, orders per customer, and repeat rate.
 * This is the "customer economics by when you acquired them" view —
 * Peel's specialty, simplified for the Lite tier.
 *
 * What's shown:
 *   - Overall band: total customers, avg ARPC, avg orders/customer,
 *     repeat rate (the hero row).
 *   - Per-cohort rows: month label, size, revenue/customer,
 *     orders/customer, repeat rate. Color-coded by repeat-rate tier
 *     (strong / typical / weak).
 *   - Best month spotlight — the cohort with the highest revenue per
 *     customer, so the merchant knows which acquisition month to
 *     study and replicate.
 *
 * What's NOT shown (Lite scope):
 *   - Cumulative-revenue curves per cohort (Pro/Scale — larger chart
 *     surface, more screen space).
 *   - Per-customer predicted LTV (Pro — /pro/cohorts/ltv/customers).
 *   - Full weekly cohort matrix (Pro — CohortTable).
 *
 * Data: GET /analytics/cohorts/monthly (opened 2026-04-20).
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "../app/_lib/formatters";
import type { components } from "../lib/api-types";

type MonthlyCohortsData = components["schemas"]["MonthlyCohortsResponse"];
type MonthlyCohortRow = components["schemas"]["MonthlyCohortRow"];

function repeatRateColor(rate: number): string {
  if (rate >= 0.3) return "#34d399"; // strong
  if (rate >= 0.15) return "#e8a04e"; // typical
  if (rate > 0) return "#f87171"; // weak
  return "#94a3b8"; // none yet
}

function repeatRateLabel(rate: number): string {
  if (rate >= 0.3) return "Strong";
  if (rate >= 0.15) return "Typical";
  if (rate > 0) return "Weak";
  return "—";
}

function CumulativeSparkline({
  points,
  color,
  width = 110,
  height = 32,
}: {
  points: number[];
  color: string;
  width?: number;
  height?: number;
}) {
  if (points.length < 2) return null;
  const max = Math.max(...points, 0.0001);
  const min = Math.min(...points, 0);
  const range = Math.max(max - min, 0.0001);
  const stepX = width / (points.length - 1);
  const coords = points.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const path = `M ${coords.join(" L ")}`;
  const area = `${path} L ${width.toFixed(2)},${height} L 0,${height} Z`;
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Cumulative revenue curve, ${points.length} points`}
      className="flex-shrink-0"
    >
      <path d={area} fill={color} opacity="0.14" />
      <path d={path} fill="none" stroke={color} strokeWidth={1.6} />
      <circle
        cx={(points.length - 1) * stepX}
        cy={height - ((points[points.length - 1] - min) / range) * (height - 4) - 2}
        r={2.2}
        fill={color}
      />
    </svg>
  );
}

function formatMonthLabel(s: string): string {
  // "2025-04" → "Apr 2025"
  try {
    const [y, m] = s.split("-");
    const d = new Date(Number(y), Number(m) - 1, 1);
    return d.toLocaleDateString("en-US", { month: "short", year: "numeric" });
  } catch {
    return s;
  }
}

export function MonthlyCohortsCard({
  apiBase,
  shop,
  displayCurrency = "USD",
}: {
  apiBase: string;
  shop: string;
  displayCurrency?: "USD" | "EUR";
}) {
  const [data, setData] = useState<MonthlyCohortsData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;
    apiClient
      .GET("/analytics/cohorts/monthly", { params: { query: { months: 6 } } })
      .then(({ data: raw }) => {
        if (!active) return;
        setData((raw as MonthlyCohortsData) ?? null);
      })
      .catch(() => {
        if (active) setData(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [apiBase, shop]);

  const cohorts = data?.cohorts ?? [];
  const overall = data?.overall;
  const hasData = !loading && cohorts.length > 0 && (overall?.total_customers ?? 0) > 0;

  // Best month = highest revenue_per_customer among cohorts with size > 0.
  // Surfaces "here's the month you should try to replicate" — the most
  // actionable single insight on this card.
  const bestCohort: MonthlyCohortRow | null = hasData
    ? cohorts
        .filter((c) => c.size > 0)
        .reduce<MonthlyCohortRow | null>(
          (best, c) =>
            best === null || c.revenue_per_customer > best.revenue_per_customer ? c : best,
          null,
        )
    : null;

  if (loading) {
    return (
      <div className="rounded-2xl border border-white/[0.05] bg-[#0b0b14]/50 p-8">
        <div className="text-[13px] text-slate-400">Computing monthly cohorts…</div>
      </div>
    );
  }

  if (!hasData) {
    return (
      <div className="rounded-2xl border border-dashed border-white/[0.12] bg-[#0b0b14]/40 p-6">
        <div className="mb-3 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
          <span
            className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-300"
            aria-hidden="true"
          />
          Preview — monthly customer economics
        </div>
        <p className="mb-5 text-[13px] leading-relaxed text-slate-400">
          Once orders start flowing, each acquisition month appears here
          with revenue per customer, orders per customer, and repeat rate.
          The month with the strongest repeat rate becomes your
          &quot;replicate this&quot; spotlight. Sample below.
        </p>
        <ul className="pointer-events-none space-y-2 opacity-50">
          {[
            { label: "Mar 2026", size: 38, rpc: 142, opc: 1.6, rr: 0.32 },
            { label: "Feb 2026", size: 52, rpc: 98, opc: 1.2, rr: 0.18 },
            { label: "Jan 2026", size: 41, rpc: 120, opc: 1.4, rr: 0.24 },
          ].map((s) => (
            <li
              key={s.label}
              className="flex items-center justify-between gap-4 rounded-xl border border-white/[0.05] bg-[#0b0b14]/60 px-4 py-3"
            >
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-semibold text-white">{s.label}</div>
                <div className="mt-0.5 text-[11.5px] text-slate-400">
                  {s.size} customers acquired
                </div>
              </div>
              <div className="flex flex-shrink-0 items-baseline gap-4 text-right">
                <div>
                  <div className="text-[9.5px] font-semibold uppercase tracking-wider text-slate-500">ARPC</div>
                  <div className="text-[13.5px] font-bold tabular-nums text-white">€{s.rpc}</div>
                </div>
                <div>
                  <div className="text-[9.5px] font-semibold uppercase tracking-wider text-slate-500">Repeat</div>
                  <div
                    className="text-[13.5px] font-bold tabular-nums"
                    style={{ color: repeatRateColor(s.rr) }}
                  >
                    {(s.rr * 100).toFixed(0)}%
                  </div>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div>
      {/* Overall band — hero row */}
      {overall && (
        <div className="mb-5 grid gap-3 sm:grid-cols-4">
          <div className="rounded-2xl border border-white/[0.06] bg-[#0e0e1a]/70 p-4">
            <div className="text-[10.5px] font-bold uppercase tracking-[0.16em] text-slate-400">
              Total customers
            </div>
            <div className="mt-2 text-[1.75rem] font-extrabold leading-none tabular-nums text-white">
              {overall.total_customers.toLocaleString()}
            </div>
          </div>
          <div className="rounded-2xl border border-emerald-400/[0.18] bg-emerald-500/[0.05] p-4">
            <div className="text-[10.5px] font-bold uppercase tracking-[0.16em] text-emerald-300">
              Repeat rate
            </div>
            <div
              className="mt-2 text-[1.75rem] font-extrabold leading-none tabular-nums"
              style={{ color: repeatRateColor(overall.repeat_rate) }}
            >
              {(overall.repeat_rate * 100).toFixed(0)}%
            </div>
            <div className="mt-1 text-[11px] text-slate-400">
              {repeatRateLabel(overall.repeat_rate)}
            </div>
          </div>
          <div className="rounded-2xl border border-white/[0.06] bg-[#0e0e1a]/70 p-4">
            <div className="text-[10.5px] font-bold uppercase tracking-[0.16em] text-slate-400">
              Avg ARPC
            </div>
            <div className="mt-2 text-[1.75rem] font-extrabold leading-none tabular-nums text-white">
              {formatMoneyCompact(overall.avg_revenue_per_customer, displayCurrency)}
            </div>
          </div>
          <div className="rounded-2xl border border-white/[0.06] bg-[#0e0e1a]/70 p-4">
            <div className="text-[10.5px] font-bold uppercase tracking-[0.16em] text-slate-400">
              Orders / customer
            </div>
            <div className="mt-2 text-[1.75rem] font-extrabold leading-none tabular-nums text-white">
              {overall.avg_orders_per_customer.toFixed(1)}
            </div>
          </div>
        </div>
      )}

      {/* Best month spotlight */}
      {bestCohort && (
        <div className="mb-5 rounded-2xl border border-emerald-400/[0.2] bg-emerald-500/[0.04] p-5">
          <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-emerald-300">
            Your best acquisition month
          </div>
          <div className="mt-2 flex flex-wrap items-baseline gap-4">
            <span className="text-[1.5rem] font-extrabold leading-none text-white">
              {formatMonthLabel(bestCohort.cohort_month)}
            </span>
            <span className="text-[14px] text-slate-400">
              · {formatMoneyCompact(bestCohort.revenue_per_customer, displayCurrency)} per customer ·
              {" "}{bestCohort.orders_per_customer.toFixed(1)} orders each ·{" "}
              <span style={{ color: repeatRateColor(bestCohort.repeat_rate) }}>
                {(bestCohort.repeat_rate * 100).toFixed(0)}% repeat
              </span>
            </span>
          </div>
          <p className="mt-3 text-[12.5px] leading-relaxed text-slate-400">
            Study the traffic mix, creatives, and offers you ran this month
            — the cohort from this period is your most valuable customer
            segment in the window. Replicating the acquisition pattern
            typically boosts lifetime value meaningfully.
          </p>
        </div>
      )}

      {/* Per-month rows */}
      <ul className="space-y-2">
        {cohorts.map((c) => {
          const color = repeatRateColor(c.repeat_rate);
          const points = c.cumulative_revenue ?? [];
          return (
            <li
              key={c.cohort_month}
              className="flex flex-wrap items-center gap-4 rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 px-4 py-3"
            >
              <div className="min-w-[120px] flex-1">
                <div className="text-[13px] font-semibold text-white">
                  {formatMonthLabel(c.cohort_month)}
                </div>
                <div className="mt-0.5 text-[11.5px] text-slate-400">
                  {c.size} customer{c.size !== 1 ? "s" : ""} acquired · {formatMoneyCompact(c.revenue_total, displayCurrency)} total
                </div>
              </div>
              {/* Cumulative-revenue sparkline — Strada 4 dominance.
                  Peel's killer visual: the shape tells you whether a
                  cohort keeps producing revenue month after month or
                  plateaus after the first purchase. Now on Lite. */}
              {points.length >= 2 && (
                <CumulativeSparkline
                  points={points.map((p) => p.revenue)}
                  color={color}
                />
              )}
              <div className="flex flex-shrink-0 items-baseline gap-5 text-right">
                <div>
                  <div className="text-[9.5px] font-semibold uppercase tracking-wider text-slate-500">ARPC</div>
                  <div className="text-[13.5px] font-bold tabular-nums text-white">
                    {formatMoneyCompact(c.revenue_per_customer, displayCurrency)}
                  </div>
                </div>
                <div>
                  <div className="text-[9.5px] font-semibold uppercase tracking-wider text-slate-500">Orders</div>
                  <div className="text-[13.5px] font-bold tabular-nums text-white">
                    {c.orders_per_customer.toFixed(1)}
                  </div>
                </div>
                <div>
                  <div className="text-[9.5px] font-semibold uppercase tracking-wider text-slate-500">Repeat</div>
                  <div className="text-[13.5px] font-bold tabular-nums" style={{ color }}>
                    {(c.repeat_rate * 100).toFixed(0)}%
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      {/* Methodology footer */}
      <div className="mt-5 rounded-xl border border-white/[0.04] bg-[#0b0b14]/40 px-4 py-3">
        <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
          How this is measured
        </div>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-400">
          Each cohort is customers whose FIRST order landed in that month.
          Repeat rate = fraction who placed another order since. ARPC = all
          revenue from that cohort ÷ cohort size. Customer identity uses
          Shopify customer_id (preferred) or email — orders with neither
          are excluded from the cohort math, not imputed.
        </p>
      </div>
    </div>
  );
}
