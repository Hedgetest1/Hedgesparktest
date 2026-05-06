"use client";

/**
 * LiteTodaySection — base-analytics pulse for the Lite floor.
 *
 * The 2026-04-25 audit flagged that every cheap Shopify analytics tool
 * (Shopify free, Lifetimely Free, OrderMetrics, Better Reports) shows a
 * "today vs yesterday" KPI strip as the FIRST thing a merchant sees.
 * Lite historically jumped straight into RARS / peers / P&L / cassettoni
 * — the intelligence layer — without grounding the merchant in the
 * basic "where you stand right now" pulse. A merchant on day-1 paying
 * €39/mo expects to see today's numbers before any leak detection.
 *
 * Six KPIs in one compact strip + top-5 sellers today, all sourced
 * from /analytics/today-snapshot. Every number is a real DB row;
 * delta_pct is null when yesterday is zero so we never fabricate
 * "+∞%". Empty-state preview matches the RARS hero pattern so day-1
 * feels premium instead of a wall of zeros.
 *
 * Placement: between the RARS hero (the differentiator, stays #1)
 * and "You vs peers" (peers compare to YOU — first show what YOU
 * did, then compare). Visual coherence with the existing Lite
 * sections: rounded-3xl + accent stripe + h2 amber + ExportButton.
 */

import { useMemo } from "react";
import { CardSkeleton, CardError, useCardFetch } from "./_CardStates";
import { SectionErrorBoundary } from "./SectionErrorBoundary";
import { createMoneyFormatter, type DisplayCurrency } from "../lib/currency";

type DayMetrics = {
  revenue: number;
  orders: number;
  aov: number;
  sessions: number;
  conversion_rate_pct: number | null;
  new_customers: number;
  returning_customers: number;
};

type Deltas = {
  revenue_pct: number | null;
  orders_pct: number | null;
  aov_pct: number | null;
  sessions_pct: number | null;
  conversion_rate_pct_delta: number | null;
};

type TopSeller = {
  product_title: string;
  revenue: number;
  units_sold: number;
};

type Snapshot = {
  currency: string;
  timezone: string;
  today_iso: string;
  has_data: boolean;
  today: DayMetrics;
  yesterday: DayMetrics;
  deltas: Deltas;
  top_sellers_today: TopSeller[];
};

const ACCENT = "#34d399";

function formatPct(v: number | null | undefined, suffix: string = "%"): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}${suffix}`;
}

function deltaTone(v: number | null | undefined): {
  color: string;
  bg: string;
} {
  if (v === null || v === undefined || Number.isNaN(v)) {
    return { color: "#94a3b8", bg: "rgba(148,163,184,0.08)" };
  }
  if (v > 0) return { color: "#34d399", bg: "rgba(52,211,153,0.10)" };
  if (v < 0) return { color: "#f87171", bg: "rgba(248,113,113,0.10)" };
  return { color: "#94a3b8", bg: "rgba(148,163,184,0.08)" };
}

function KpiTile({
  label,
  value,
  delta,
  deltaLabel,
  yesterday,
  hint,
}: {
  label: string;
  value: string;
  delta: number | null | undefined;
  deltaLabel?: string;
  yesterday?: string;
  hint?: string;
}) {
  const tone = deltaTone(delta);
  return (
    <div className="rounded-2xl border border-white/[0.05] bg-white/[0.02] p-4 transition-colors hover:border-white/[0.10]">
      <div
        className="flex items-center gap-1.5 text-[10.5px] font-bold uppercase tracking-[0.16em] text-slate-400"
        title={hint}
      >
        <span>{label}</span>
        {hint && (
          <span
            aria-label={hint}
            className="cursor-help text-[11px] font-normal text-slate-400 hover:text-slate-400"
          >
            ⓘ
          </span>
        )}
      </div>
      <div className="mt-2 text-[28px] font-extrabold leading-none tabular-nums text-white sm:text-[30px]">
        {value}
      </div>
      <div className="mt-2.5 flex items-center justify-between gap-2">
        <span
          className="rounded-full px-2 py-0.5 text-[10.5px] font-bold tabular-nums"
          style={{ color: tone.color, background: tone.bg }}
          aria-label={
            delta === null || delta === undefined
              ? "no comparison available"
              : `${delta > 0 ? "up" : delta < 0 ? "down" : "flat"} ${Math.abs(
                  delta
                ).toFixed(1)} ${deltaLabel || "percent"} vs yesterday`
          }
        >
          {deltaLabel === "pp"
            ? formatPct(delta, "pp")
            : formatPct(delta)}
        </span>
        {yesterday && (
          <span className="text-[10.5px] tabular-nums text-slate-400">
            yest. {yesterday}
          </span>
        )}
      </div>
    </div>
  );
}

export function LiteTodaySection({
  apiBase,
  shop,
  displayCurrency,
}: {
  apiBase: string;
  shop: string;
  displayCurrency: DisplayCurrency;
}) {
  const { data, state, retry } = useCardFetch<Snapshot>({
    url: `${apiBase}/analytics/today-snapshot`,
    enabled: !!shop && !!apiBase,
    isEmpty: (d) => !d.has_data,
    component: "LiteTodaySection",
  });

  // Currency formatter is computed regardless of data presence so the
  // empty-state preview can show realistic-looking sample numbers.
  const fmt = useMemo(
    () => createMoneyFormatter(displayCurrency, data?.currency ?? "USD"),
    [displayCurrency, data?.currency]
  );

  return (
    <section
      id="section-lite-today"
      aria-labelledby="lite-today-heading"
      className="relative mb-8 overflow-hidden rounded-3xl border border-emerald-400/[0.15] bg-gradient-to-br from-[#0a1612] via-[#0a0a14] to-[#0b0c18] p-7 sm:p-9"
    >
      <SectionErrorBoundary name="Today">
      <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-transparent via-[#34d399] to-transparent opacity-50" />
      <div className="pointer-events-none absolute -right-32 -top-32 h-[340px] w-[340px] rounded-full bg-[#34d399]/[0.05] blur-[150px]" />

      <div className="relative">
        <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2
              id="lite-today-heading"
              className="text-[2rem] font-extrabold leading-[1.05] tracking-tight text-[#34d399] sm:text-[2.5rem]"
            >
              Today
            </h2>
            <div className="mt-1 text-[16px] font-medium leading-snug text-slate-200 sm:text-[17px]">
              Where you stand right now — vs yesterday
            </div>
            <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-slate-400">
              Six numbers a merchant checks every morning before anything
              else: revenue, orders, average order value, sessions,
              conversion, and new vs returning customers. Each is sourced
              straight from real Shopify orders or visitor events — zero
              estimates.
            </p>
          </div>
        </div>

        {state === "loading" && (
          <CardSkeleton label="Loading today's snapshot" />
        )}

        {state === "error" && (
          <CardError
            label="Today's snapshot unavailable"
            message="We couldn't pull today's KPIs right now. Your order history is safe — this card recovers automatically."
            onRetry={retry}
          />
        )}

        {(state === "ready" || state === "empty") && data && (
          <>
            <div
              className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6"
              aria-label={
                state === "empty"
                  ? "Empty today snapshot — preview placeholder"
                  : "Today vs yesterday — six base KPIs"
              }
              style={state === "empty" ? { opacity: 0.55 } : undefined}
            >
              <KpiTile
                label="Revenue"
                value={fmt(data.today.revenue)}
                delta={data.deltas.revenue_pct}
                yesterday={fmt(data.yesterday.revenue)}
              />
              <KpiTile
                label="Orders"
                value={`${data.today.orders}`}
                delta={data.deltas.orders_pct}
                yesterday={`${data.yesterday.orders}`}
              />
              <KpiTile
                label="Avg order"
                value={fmt(data.today.aov)}
                delta={data.deltas.aov_pct}
                yesterday={fmt(data.yesterday.aov)}
              />
              <KpiTile
                label="Sessions"
                value={`${data.today.sessions}`}
                delta={data.deltas.sessions_pct}
                yesterday={`${data.yesterday.sessions}`}
                hint="Distinct visitor IDs that fired at least one page-view event today."
              />
              <KpiTile
                label="Conversion"
                value={
                  data.today.conversion_rate_pct === null
                    ? "—"
                    : `${data.today.conversion_rate_pct.toFixed(2)}%`
                }
                delta={data.deltas.conversion_rate_pct_delta}
                deltaLabel="pp"
                yesterday={
                  data.yesterday.conversion_rate_pct === null
                    ? "—"
                    : `${data.yesterday.conversion_rate_pct.toFixed(2)}%`
                }
                hint="Orders today divided by sessions today. Delta is shown in percentage points (pp), not relative %."
              />
              <KpiTile
                label="New / Returning"
                value={
                  data.today.new_customers + data.today.returning_customers === 0
                    ? "—"
                    : `${data.today.new_customers} / ${data.today.returning_customers}`
                }
                delta={null}
                yesterday={
                  data.yesterday.new_customers +
                    data.yesterday.returning_customers ===
                  0
                    ? "—"
                    : `${data.yesterday.new_customers} / ${data.yesterday.returning_customers}`
                }
                hint='"New" = customer placing their first ever order today. "Returning" = customer with at least one prior order in any window.'
              />
            </div>

            {state === "empty" && (
              <div className="mt-5 flex items-center gap-2 rounded-xl border border-emerald-400/15 bg-emerald-500/[0.05] px-3 py-2 text-[12px] font-semibold text-emerald-200">
                <span
                  className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400"
                  aria-hidden="true"
                />
                Watching your storefront — the first numbers populate the moment a visitor or order arrives.
              </div>
            )}

            <p className="mt-5 text-[11px] leading-relaxed text-slate-400">
              <span className="font-semibold text-slate-400">How this is measured.</span>{" "}
              Revenue / orders / AOV from{" "}
              <code className="rounded bg-white/[0.04] px-1 py-0.5 text-slate-400">shop_orders</code>
              {" "}filtered to your store&apos;s primary currency, day-bucketed in your shop timezone. Sessions = distinct visitor IDs that fired a page-view event. Conversion = orders / sessions; delta is percentage points. Delta is hidden (—) whenever yesterday is zero so we never fabricate &quot;+∞%&quot; against a zero baseline.
            </p>

            {state === "ready" && data.top_sellers_today.length > 0 && (
              <div className="mt-7 border-t border-white/[0.06] pt-6">
                <div className="mb-4 text-[11px] font-bold uppercase tracking-[0.16em] text-slate-400">
                  Top sellers · today, ranked by revenue
                </div>
                <ul className="space-y-2">
                  {data.top_sellers_today.map((p, i) => (
                    <li
                      key={`${p.product_title}-${i}`}
                      className="flex items-center gap-4 rounded-xl border border-white/[0.04] bg-white/[0.015] px-4 py-3"
                    >
                      <span
                        className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full text-[12px] font-extrabold tabular-nums"
                        style={{
                          color: ACCENT,
                          background: `${ACCENT}1a`,
                          border: `1px solid ${ACCENT}40`,
                        }}
                        aria-hidden="true"
                      >
                        {i + 1}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-200">
                        {p.product_title}
                      </span>
                      <span className="flex-shrink-0 text-[11.5px] tabular-nums text-slate-400">
                        {p.units_sold} sold
                      </span>
                      <span
                        className="flex-shrink-0 text-[14.5px] font-extrabold tabular-nums"
                        style={{ color: ACCENT }}
                      >
                        {fmt(p.revenue)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
      </SectionErrorBoundary>
    </section>
  );
}
