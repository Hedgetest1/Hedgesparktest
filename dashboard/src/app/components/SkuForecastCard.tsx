"use client";

/**
 * SkuForecastCard — per-SKU revenue forecast for top-N products.
 *
 * Born 2026-04-27 from Gap #6 close (brutal $0-70 audit + parity doctrine).
 * Lebesgue $59 + Forthcast $19.99 ship per-product forecasts at entry tier;
 * we match per founder doctrine 2026-04-27, with built-on-top differentiator.
 *
 * 3-axis differentiator (per `feedback_0_60_parity_doctrine.md`):
 *   1. CLARITY — single horizon picker (7d / 14d / 30d), no nested params
 *   2. ACCURACY — confidence label + accuracy_pct backtest scalar per row
 *      (no $0-60 competitor surfaces backtest accuracy honestly)
 *   3. UNIQUE — biggest_riser / biggest_faller plain-language insight panel
 *      ("Re-stock the riser, investigate the faller before inventory builds")
 */

import { useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "../app/_lib/formatters";
import { useAsyncResource } from "./useTileFetch";
import { CardSkeleton, CardError, CardEmpty } from "./_CardStates";

type DisplayCurrency = "USD" | "EUR" | string;

type Horizon = 7 | 14 | 30;

type SkuRow = {
  product_key: string;
  title: string;
  observed_revenue: number;
  forecast_point: number;
  forecast_lower_80: number;
  forecast_upper_80: number;
  forecast_lower_95: number;
  forecast_upper_95: number;
  delta_pct: number;
  direction: string;
  confidence: string;
  accuracy_pct: number | null;
  n_days: number;
  r2: number;
};

type BigMover = {
  product_key: string;
  title: string;
  delta_pct: number;
};

type SkuForecastData = {
  shop_domain: string;
  horizon_days: number;
  window_days: number;
  currency: string;
  generated_at: string;
  products: SkuRow[];
  biggest_riser: BigMover | null;
  biggest_faller: BigMover | null;
  insight: string;
};

const CONFIDENCE_COLOR: Record<string, string> = {
  high: "text-emerald-300 bg-emerald-500/15",
  medium: "text-amber-300 bg-amber-500/15",
  low: "text-slate-300 bg-slate-500/15",
  insufficient: "text-rose-300 bg-rose-500/15",
};

const DIRECTION_ICON: Record<string, string> = {
  rising: "↑",
  falling: "↓",
  stable: "→",
};

const DIRECTION_COLOR: Record<string, string> = {
  rising: "text-emerald-300",
  falling: "text-rose-300",
  stable: "text-slate-400",
};

export function SkuForecastCard({
  displayCurrency,
}: { displayCurrency: DisplayCurrency }) {
  const [horizon, setHorizon] = useState<Horizon>(14);

  const { data, loading, error, retry } = useAsyncResource<SkuForecastData>(
    () => apiClient.GET("/analytics/forecast/by-sku", {
      params: { query: { horizon_days: horizon, window_days: 60, top_n: 10 } },
    }).then(r => ({ data: r.data as unknown as SkuForecastData, error: r.error })),
    [horizon],
  );

  const ccy = data?.currency ?? displayCurrency;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
      {/* Header + horizon picker */}
      <div className="mb-4 flex items-baseline justify-between gap-3 flex-wrap">
        <div>
          <div className="text-[14px] font-bold text-slate-100">
            Per-product forecast
          </div>
          <div className="mt-0.5 text-[11px] text-slate-400">
            Holt double-exp smoothing on daily revenue. Confidence + backtest
            accuracy per row.
          </div>
        </div>
        <div
          className="inline-flex rounded-lg border border-white/[0.08] bg-slate-900/40 p-0.5"
          role="tablist"
          aria-label="Forecast horizon picker"
        >
          {([7, 14, 30] as Horizon[]).map(h => {
            const active = h === horizon;
            return (
              <button
                key={h}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setHorizon(h)}
                className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition focus:outline-none focus:ring-2 focus:ring-[#e8a04e]/50 ${
                  active
                    ? "bg-[#e8a04e]/[0.15] text-[#e8a04e]"
                    : "text-slate-300 hover:bg-white/[0.04]"
                }`}
              >
                {h}d
              </button>
            );
          })}
        </div>
      </div>

      {loading && <CardSkeleton label={`Loading ${horizon}-day SKU forecast`} />}

      {error && !loading && (
        <CardError onRetry={retry} message="Couldn't load per-product forecast." />
      )}

      {!loading && !error && data && (data.products.length === 0 ? (
        <CardEmpty title="No data yet" body={data.insight} />
      ) : (
        <>
          {/* Differentiator — biggest riser/faller insight */}
          {(data.biggest_riser || data.biggest_faller) && (
            <div
              className="mb-4 rounded-xl border border-emerald-400/20 bg-emerald-500/[0.04] px-4 py-3"
              role="note"
            >
              <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-emerald-300">
                Insight
              </div>
              <p className="mt-1 text-[12px] leading-relaxed text-slate-200">
                {data.insight}
              </p>
            </div>
          )}

          {/* Per-product forecast table */}
          <ul className="divide-y divide-white/[0.04]">
            {data.products.map(p => {
              const dirColor = DIRECTION_COLOR[p.direction] ?? "text-slate-400";
              const dirIcon = DIRECTION_ICON[p.direction] ?? "→";
              const confColor = CONFIDENCE_COLOR[p.confidence] ?? "text-slate-300 bg-slate-500/15";
              const isInsuff = p.confidence === "insufficient";
              return (
                <li key={p.product_key} className="flex items-center gap-3 py-2.5">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 text-[13px] font-semibold text-slate-200 truncate">
                      <span className="truncate">{p.title}</span>
                      <span
                        className={`rounded-full px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.06em] ${confColor}`}
                        title={`Confidence based on ${p.n_days} days of data + r²=${p.r2}`}
                      >
                        {p.confidence}
                      </span>
                    </div>
                    <div className="text-[10px] tabular-nums text-slate-400">
                      observed {formatMoneyCompact(p.observed_revenue, ccy)} · {p.n_days}d history
                      {p.accuracy_pct != null && (
                        <span> · backtest {p.accuracy_pct.toFixed(0)}% accuracy</span>
                      )}
                    </div>
                  </div>
                  <div className="text-right">
                    {isInsuff ? (
                      <div className="text-[11px] text-slate-400">need 7+ days</div>
                    ) : (
                      <>
                        <div className="text-[13px] font-bold tabular-nums text-emerald-300">
                          {formatMoneyCompact(p.forecast_point, ccy)}
                          <span className="text-[10px] text-slate-400">/day</span>
                        </div>
                        <div className={`text-[10px] tabular-nums ${dirColor}`}>
                          {dirIcon} {p.delta_pct >= 0 ? "+" : ""}{p.delta_pct.toFixed(0)}% vs last 7d
                        </div>
                      </>
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
