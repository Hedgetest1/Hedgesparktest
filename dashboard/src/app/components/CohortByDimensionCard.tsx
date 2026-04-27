"use client";

/**
 * CohortByDimensionCard — cohort retention sliced by acquisition dim.
 *
 * Born 2026-04-27 from brutal $0-70 audit Gap #8 close + parity doctrine.
 * Lifetimely $39 ships cohort-by-channel/product/discount at entry tier;
 * we match it.
 *
 * 3-axis differentiator on top (per `feedback_0_60_parity_doctrine.md`):
 *   1. CLARITY — single dropdown, no nested filters, plain bucket labels
 *   2. ACCURACY — coverage_rate surfaced honestly (estimated flag if low),
 *      cold-start guard refuses to fabricate insight on small samples
 *   3. UNIQUE — `best_vs_worst` plain-language insight panel above the
 *      table: "Customers acquired via X have N% higher repeat rate
 *      than Y" — single-line reading-grade takeaway no $0-60 competitor
 *      ships at this surface
 */

import { useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "../app/_lib/formatters";
import { useAsyncResource } from "./useTileFetch";
import { CardSkeleton, CardError, CardEmpty } from "./_CardStates";

type DisplayCurrency = "USD" | "EUR" | string;

type Dim = "first_channel" | "first_product" | "first_discount";

type Bucket = {
  dim_value: string;
  size: number;
  repeat_rate: number;
  revenue_per_customer: number;
  orders_per_customer: number;
  cohort_months: {
    cohort_month: string;
    size: number;
    revenue_total: number;
    repeat_rate: number;
  }[];
};

type BestVsWorst = {
  best_dim_value: string | null;
  worst_dim_value: string | null;
  best_repeat_rate: number | null;
  worst_repeat_rate: number | null;
  lift_pct: number | null;
  insight: string;
};

type CohortByDimData = {
  dim: string;
  window_months: number;
  generated_at: string;
  customer_coverage: {
    total_orders: number;
    identifiable_orders: number;
    unidentifiable_orders: number;
    coverage_rate: number;
  };
  buckets: Bucket[];
  best_vs_worst: BestVsWorst;
};

const DIM_LABELS: Record<Dim, string> = {
  first_channel: "Acquisition channel",
  first_product: "First product bought",
  first_discount: "First discount code",
};

const DIM_HINTS: Record<Dim, string> = {
  first_channel: "Cohort grouped by where each customer first arrived from.",
  first_product: "Cohort grouped by the first product each customer bought.",
  first_discount: "Cohort grouped by the discount code applied on first order (or none).",
};

export function CohortByDimensionCard({
  displayCurrency,
}: { displayCurrency: DisplayCurrency }) {
  const [dim, setDim] = useState<Dim>("first_channel");

  const { data, loading, error, retry } = useAsyncResource<CohortByDimData>(
    () => apiClient.GET("/analytics/cohorts/by-dimension", {
      params: { query: { dim, months: 6, limit_dim_values: 8 } },
    }).then(r => ({ data: r.data as unknown as CohortByDimData, error: r.error })),
    [dim],
  );

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
      {/* Header + dim picker */}
      <div className="mb-4 flex items-baseline justify-between gap-3 flex-wrap">
        <div>
          <div className="text-[14px] font-bold text-slate-100">
            Cohort retention by dimension
          </div>
          <div className="mt-0.5 text-[11px] text-slate-400">
            {DIM_HINTS[dim]}
          </div>
        </div>
        <div
          className="inline-flex rounded-lg border border-white/[0.08] bg-slate-900/40 p-0.5"
          role="tablist"
          aria-label="Cohort dimension picker"
        >
          {(Object.keys(DIM_LABELS) as Dim[]).map(d => {
            const active = d === dim;
            return (
              <button
                key={d}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setDim(d)}
                className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition focus:outline-none focus:ring-2 focus:ring-[#e8a04e]/50 ${
                  active
                    ? "bg-[#e8a04e]/[0.15] text-[#e8a04e]"
                    : "text-slate-300 hover:bg-white/[0.04]"
                }`}
              >
                {DIM_LABELS[d]}
              </button>
            );
          })}
        </div>
      </div>

      {loading && <CardSkeleton label={`Loading cohort by ${DIM_LABELS[dim].toLowerCase()}`} />}

      {error && !loading && (
        <CardError
          onRetry={retry}
          message={`Couldn't load cohort by ${DIM_LABELS[dim].toLowerCase()}.`}
        />
      )}

      {!loading && !error && data && (data.buckets.length === 0 ? (
        <CardEmpty title="No data yet" body={data.best_vs_worst.insight} />
      ) : (
        <>
          {/* Differentiator — plain-language insight panel */}
          {data.best_vs_worst.best_dim_value !== null && (
            <div
              className="mb-4 rounded-xl border border-emerald-400/20 bg-emerald-500/[0.04] px-4 py-3"
              role="note"
            >
              <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-emerald-300">
                Insight
              </div>
              <p className="mt-1 text-[12px] leading-relaxed text-slate-200">
                {data.best_vs_worst.insight}
              </p>
            </div>
          )}

          {/* Coverage banner — accuracy axis honesty */}
          {data.customer_coverage.coverage_rate < 0.7 && (
            <div className="mb-3 text-[10px] text-slate-400">
              Based on {data.customer_coverage.identifiable_orders.toLocaleString("en-US")} of{" "}
              {data.customer_coverage.total_orders.toLocaleString("en-US")} orders
              ({Math.round(data.customer_coverage.coverage_rate * 100)}%) — orders without
              identifiable customer (no email or customer_id) excluded.
            </div>
          )}

          {/* Bucket table */}
          <ul className="divide-y divide-white/[0.04]">
            {data.buckets.map(b => {
              const rrColor =
                b.repeat_rate >= 0.3 ? "text-emerald-300" :
                b.repeat_rate >= 0.15 ? "text-amber-300" : "text-rose-300";
              return (
                <li key={b.dim_value} className="flex items-center gap-3 py-2.5">
                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] font-semibold text-slate-200 truncate">
                      {b.dim_value}
                    </div>
                    <div className="text-[10px] tabular-nums text-slate-400">
                      {b.size.toLocaleString("en-US")} customers · {b.orders_per_customer} orders/customer · {formatMoneyCompact(b.revenue_per_customer, displayCurrency)}/customer
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={`text-[15px] font-bold tabular-nums ${rrColor}`}>
                      {(b.repeat_rate * 100).toFixed(0)}%
                    </div>
                    <div className="text-[10px] text-slate-400">repeat</div>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      ))}
    </div>
  );
}
