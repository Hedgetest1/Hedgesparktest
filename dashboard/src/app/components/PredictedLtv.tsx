"use client";

import { createMoneyFormatter, type DisplayCurrency } from "../lib/currency";
import type { paths } from "../lib/api-client";

// Generated response type — single source of truth. Regenerate via
// `npm run api:types` after backend changes.
type PredictedLtvResponse =
  paths["/pro/cohorts/ltv/customers"]["get"]["responses"]["200"]["content"]["application/json"];

/**
 * PredictedLtv — Top customers ranked by next-12-month predicted lifetime value.
 *
 * Killer feature: surfaces the merchant's most valuable customers AND tells them
 * how much more they're worth in the future. The 12-month total at the bottom
 * is the most actionable single number on the page — it's the revenue the
 * merchant should be protecting.
 *
 * Data shape: see backend `ltv_engine.py:get_predicted_ltv()`
 *   {
 *     shop_domain: string,
 *     count: number,
 *     customers: [
 *       {
 *         customer_key: string,
 *         email_hint: string | null,       // already masked: "s***@gmail.com"
 *         total_orders: number,
 *         total_spend: number,
 *         aov: number,
 *         days_since_last: number,
 *         repeat_probability_30d: number,  // 0-1
 *         predicted_30d_value: number,
 *         predicted_12m_ltv: number,
 *       }
 *     ]
 *   }
 */

// Local alias using generated types.
type PredictedLtvData = PredictedLtvResponse;


function tierFromProb(p: number): { label: string; color: string; bg: string; border: string } {
  if (p >= 0.5) {
    return {
      label: "HOT",
      color: "#34d399", // emerald-400
      bg: "rgba(52, 211, 153, 0.12)",
      border: "rgba(52, 211, 153, 0.32)",
    };
  }
  if (p >= 0.25) {
    return {
      label: "WARM",
      color: "#e8a04e", // amber brand
      bg: "rgba(232, 160, 78, 0.12)",
      border: "rgba(232, 160, 78, 0.32)",
    };
  }
  return {
    label: "COLD",
    color: "#94a3b8", // slate-400
    bg: "rgba(148, 163, 184, 0.08)",
    border: "rgba(148, 163, 184, 0.2)",
  };
}

function recencyLabel(days: number): string {
  if (days < 1) return "today";
  if (days < 2) return "1d ago";
  if (days < 30) return `${Math.round(days)}d ago`;
  if (days < 60) return "1mo ago";
  if (days < 365) return `${Math.round(days / 30)}mo ago`;
  return `${Math.round(days / 365)}y ago`;
}

export function PredictedLtv({
  data,
  displayCurrency = "USD",
}: {
  data: PredictedLtvData | null;
  displayCurrency?: DisplayCurrency;
}) {
  const customers = data?.customers ?? [];
  const fmtMoney = createMoneyFormatter(displayCurrency, "USD");
  const fmtMoneyBig = fmtMoney; // same formatter, rendered larger via CSS

  // Already sorted by total_spend DESC from backend, but resort by predicted_12m_ltv
  // for our narrative ("most valuable looking forward")
  const sorted = [...customers].sort(
    (a, b) => (b.predicted_12m_ltv || 0) - (a.predicted_12m_ltv || 0),
  );

  const top10 = sorted.slice(0, 10);
  const totalPredicted12m = top10.reduce((sum, c) => sum + (c.predicted_12m_ltv || 0), 0);
  const totalPredicted30d = top10.reduce((sum, c) => sum + (c.predicted_30d_value || 0), 0);

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
      {/* Header */}
      <div className="mb-5 flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="mb-1">
            <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-400">
              Predicted Value
            </span>
          </div>
          <h3 className="text-[15px] font-bold leading-tight text-white">
            Your most valuable customers, ranked by next 12 months
          </h3>
          <p className="mt-1.5 text-[12px] leading-relaxed text-slate-400">
            {sorted.length > 0
              ? `The top ${Math.min(10, sorted.length)} customers below represent revenue you should be actively protecting.`
              : "Predicted value activates once you have customers with 1+ identified orders."}
          </p>
        </div>
      </div>

      {/* Empty state */}
      {sorted.length === 0 && (
        <div className="rounded-xl border border-dashed border-white/[0.06] bg-white/[0.015] px-4 py-8 text-center">
          <p className="text-[12px] text-slate-400">
            We need orders with customer identity (email or Shopify customer_id) before we
            can compute predicted lifetime value.
          </p>
        </div>
      )}

      {/* Customer table */}
      {top10.length > 0 && (
        <div className="space-y-2">
          {top10.map((c, idx) => {
            const tier = tierFromProb(c.repeat_probability_30d || 0);
            const isUp = c.predicted_12m_ltv > c.total_spend;
            const ratio = c.total_spend > 0 ? c.predicted_12m_ltv / c.total_spend : 1;

            return (
              <div
                key={c.customer_key}
                className="group flex items-center gap-3 rounded-xl border border-white/[0.05] bg-white/[0.015] px-3.5 py-3 transition-colors hover:border-white/[0.1] hover:bg-white/[0.025]"
              >
                {/* Rank */}
                <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-white/[0.04] text-[11px] font-bold tabular-nums text-slate-400">
                  {idx + 1}
                </div>

                {/* Customer info */}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[12px] font-mono text-slate-300">
                      {c.email_hint || c.customer_key.slice(0, 18) + "…"}
                    </span>
                    <span
                      className="flex-shrink-0 rounded-full border px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider"
                      style={{
                        borderColor: tier.border,
                        backgroundColor: tier.bg,
                        color: tier.color,
                      }}
                    >
                      {tier.label}
                    </span>
                  </div>
                  <div className="mt-0.5 flex items-center gap-3 text-[10px] text-slate-400">
                    <span>{c.total_orders} orders</span>
                    <span className="text-slate-700">•</span>
                    <span>spent {fmtMoney(c.total_spend)}</span>
                    <span className="text-slate-700">•</span>
                    <span>{recencyLabel(c.days_since_last)}</span>
                  </div>
                </div>

                {/* Predicted values */}
                <div className="flex-shrink-0 text-right">
                  <div className="text-[12px] font-bold tabular-nums text-white">
                    {fmtMoney(c.predicted_12m_ltv)}
                  </div>
                  <div className="mt-0.5 flex items-center justify-end gap-1 text-[10px] text-slate-400">
                    <span>next 12mo</span>
                    {isUp && (
                      <span
                        className="inline-flex items-center gap-0.5 rounded font-medium"
                        style={{ color: ratio > 1.5 ? "#34d399" : "#94a3b8" }}
                        title={`${ratio.toFixed(1)}× current spend`}
                      >
                        ↗
                      </span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* The killer footer — total expected revenue from top 10 */}
      {top10.length > 0 && (
        <div className="mt-5 rounded-xl border border-emerald-500/20 bg-gradient-to-br from-emerald-500/[0.06] to-emerald-500/[0.02] p-4">
          <div className="flex items-end justify-between gap-4">
            <div className="min-w-0 flex-1">
              <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-emerald-400/80">
                Top 10 expected revenue
              </div>
              <div className="mt-1 text-[11px] text-slate-400">
                Sum of next-12-month predicted lifetime value across your top customers.
                This is the revenue at stake — protect it.
              </div>
            </div>
            <div className="flex-shrink-0 text-right">
              <div
                className="text-[28px] font-extrabold tabular-nums leading-none"
                style={{ color: "#34d399" }}
              >
                {fmtMoneyBig(totalPredicted12m)}
              </div>
              <div className="mt-1 text-[10px] text-slate-400">
                {fmtMoney(totalPredicted30d)} in next 30d
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
