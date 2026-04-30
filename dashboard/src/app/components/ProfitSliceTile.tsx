"use client";

/**
 * ProfitSliceTile — gross profit (revenue − COGS) sliced by dimension.
 *
 * Born 2026-04-27 from the brutal $0-70 audit Gap #3 close. Every profit-
 * tracker competitor at $20-49 (TrueProfit, BeProfit, Lifetimely, Profit
 * Calc, OrderMetrics, Putler) ships profit slicing across multiple
 * dimensions. We had product (margin-drag); this adds variant / country
 * / channel to reach $0-70 parity.
 *
 * UX: 3-tab picker. Default = Variant (most actionable daily). Country
 * + Channel tabs swap the data without remounting. COGS at default 40%
 * fallback is surfaced via "estimated" badge so the merchant understands
 * the precision floor (matches PnlReport conventions).
 *
 * The 4th competitor-named dimension — Product — is intentionally NOT a
 * tab here because Lite already ships /analytics/pnl/margin-drag in the
 * same section. Splitting "profit by product" across two tiles would be
 * redundant.
 *
 * Ad-spend dimension (the natural 4th dim once P.IVA unblocks the Meta/
 * Google APIs) is intentionally absent. When ad-spend lands, this tile
 * adds a "Channel + ROAS" mode that subtracts per-channel ad spend.
 */

import { useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "../app/_lib/formatters";
import { useAsyncResource } from "./useTileFetch";
import { CardSkeleton, CardError, CardEmpty } from "./_CardStates";

type DisplayCurrency = "USD" | "EUR" | string;

type DimRow = {
  key: string;
  label: string;
  revenue: number;
  cogs: number;
  margin: number;
  margin_pct: number | null;
  units_or_orders: number;
  cogs_source: string;
};

type ProfitDimData = {
  dim: string;
  window_days: number;
  currency: string;
  generated_at: string;
  total_revenue: number;
  total_margin: number;
  avg_margin_pct: number | null;
  rows: DimRow[];
  methodology: string;
  error?: string | null;
};

type Dim = "variant" | "country" | "channel";

const DIM_LABELS: Record<Dim, string> = {
  variant: "Variant",
  country: "Country",
  channel: "Channel",
};

const DIM_HINTS: Record<Dim, string> = {
  variant: "Per-variant gross profit. Pixel v15+ ingests variant_id.",
  country: "Per-country gross profit. Geo captured at purchase time.",
  channel: "Per-channel gross profit. UTM-deterministic at purchase.",
};

const DIM_UNITS_LABEL: Record<Dim, string> = {
  variant: "units",
  country: "orders",
  channel: "orders",
};

export function ProfitSliceTile({
  displayCurrency,
}: { displayCurrency: DisplayCurrency }) {
  // Default to "country" rather than "variant": variant_id is only ingested
  // by pixel v15+, so legacy orders return rows=[] on the variant dimension
  // and the card flashes empty on cold load. Country is always populated
  // from order shipping address. User can still flip to variant/channel
  // via the dim switcher.
  const [dim, setDim] = useState<Dim>("country");

  const { data, loading, error, retry } = useAsyncResource<ProfitDimData>(
    () => apiClient.GET("/analytics/pnl/profit-by-dimension", {
      params: { query: { dim, window_days: 30, limit: 10 } },
    }).then(r => ({ data: r.data as unknown as ProfitDimData, error: r.error })),
    [dim],  // re-fetch on dim change
  );

  const ccy = data?.currency ?? displayCurrency;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
      {/* Header + tab picker */}
      <div className="mb-3 flex items-baseline justify-between gap-3 flex-wrap">
        <div>
          <div className="text-[14px] font-bold text-slate-100">
            Profit by {DIM_LABELS[dim].toLowerCase()}
          </div>
          <div className="mt-0.5 text-[11px] text-slate-400">
            {DIM_HINTS[dim]}
          </div>
        </div>
        <div className="inline-flex rounded-lg border border-white/[0.08] bg-slate-900/40 p-0.5" role="tablist" aria-label="Dimension picker">
          {(Object.keys(DIM_LABELS) as Dim[]).map(d => {
            const active = d === dim;
            return (
              <button
                key={d}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setDim(d)}
                className={`rounded-md px-3 py-1 text-[12px] font-medium transition focus:outline-none focus:ring-2 focus:ring-[#e8a04e]/50 ${
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

      {/* Body — canonical state primitives from _CardStates.tsx */}
      {loading && (
        <CardSkeleton label={`Loading profit by ${DIM_LABELS[dim].toLowerCase()}`} />
      )}

      {error && !loading && (
        <CardError
          onRetry={retry}
          message={`Couldn't load profit by ${DIM_LABELS[dim].toLowerCase()}.`}
        />
      )}

      {!loading && !error && data && (data.rows.length === 0 ? (
        <CardEmpty title="No data yet" body={data.methodology} />
      ) : (
        <>
          {/* Total + avg margin badge */}
          <div className="mb-3 flex items-baseline justify-between text-[12px] tabular-nums">
            <span className="text-slate-400">
              Total margin · last {data.window_days} days
            </span>
            <span className="font-bold text-emerald-300">
              {formatMoneyCompact(data.total_margin, ccy)}
              {data.avg_margin_pct != null && (
                <span className="ml-2 text-slate-300">
                  ({data.avg_margin_pct.toFixed(1)}% avg)
                </span>
              )}
            </span>
          </div>

          {/* Top rows */}
          <ul className="divide-y divide-white/[0.04]">
            {data.rows.map((r, i) => {
              const isEstimated = r.cogs_source === "default_40pct";
              return (
                <li key={r.key + i} className="flex items-center gap-3 py-2">
                  <div className="w-6 text-right text-[12px] font-bold tabular-nums text-[#e8a04e]">
                    {i + 1}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 text-[13px] font-semibold text-slate-200 truncate">
                      <span className="truncate">{r.label}</span>
                      {isEstimated && (
                        <span
                          className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.06em] text-amber-300"
                          title="COGS estimated at 40% revenue — upload product_costs for exact"
                        >
                          est
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] tabular-nums text-slate-400">
                      {r.units_or_orders.toLocaleString("en-US")} {DIM_UNITS_LABEL[dim]} · revenue {formatMoneyCompact(r.revenue, ccy)}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-[13px] font-bold tabular-nums text-emerald-300">
                      {formatMoneyCompact(r.margin, ccy)}
                    </div>
                    {r.margin_pct != null && (
                      <div className={`text-[10px] tabular-nums ${r.margin_pct >= 50 ? "text-emerald-300" : r.margin_pct >= 20 ? "text-amber-300" : "text-rose-300"}`}>
                        {r.margin_pct.toFixed(1)}% margin
                      </div>
                    )}
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
