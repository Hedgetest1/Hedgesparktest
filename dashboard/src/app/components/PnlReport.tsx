"use client";

import { createMoneyFormatter, type DisplayCurrency } from "../lib/currency";
import type { paths } from "../lib/api-client";

// Generated response type — single source of truth. Regenerate via
// `npm run api:types` after backend changes.
type PnlReportData =
  paths["/pro/pnl"]["get"]["responses"]["200"]["content"]["application/json"];

/**
 * PnlReport — Profit Intelligence cassettone.
 *
 * The killer feature that closes the gap vs Lifetimely and Triple Whale:
 * "I don't just tell you your revenue, I tell you what you keep."
 *
 * Visual model: revenue → cost stack → profit, rendered as a vertical
 * waterfall so merchants see the money leak at a glance. Every cost line
 * carries an "estimated" badge when default assumptions are in play — when
 * the merchant later provides real COGS per product, the badges disappear
 * and the precision level upgrades from "rough" → "refined" → "exact".
 *
 * Data shape: GET /pro/pnl → PnlReportResponse (see backend pnl_engine.py).
 */
export function PnlReport({
  data,
  displayCurrency = "USD",
}: {
  data: PnlReportData | null;
  displayCurrency?: DisplayCurrency;
}) {
  if (!data) {
    return (
      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-6 animate-pulse">
        <div className="h-4 w-40 rounded bg-white/[0.05]" />
        <div className="mt-4 h-20 rounded bg-white/[0.03]" />
      </div>
    );
  }

  const fmtMoney = createMoneyFormatter(displayCurrency, data.currency);

  // Empty state — no orders yet
  if (!data.has_data || data.gross_revenue === 0) {
    return (
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <div className="mb-1">
          <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-400">
            Profit Intelligence
          </span>
        </div>
        <h3 className="text-[15px] font-bold leading-tight text-white">
          What you actually keep after costs
        </h3>
        <p className="mt-1.5 text-[12px] leading-relaxed text-slate-400">
          Profit intelligence activates with your first orders. We compute gross
          profit from real Shopify revenue minus COGS, fees, and shipping —
          estimated first, then refined when you enter your real cost data.
        </p>
      </div>
    );
  }

  // --- Main render ---------------------------------------------------------
  const netMarginColor =
    data.net_margin_pct >= 20 ? "#34d399" // emerald — healthy
    : data.net_margin_pct >= 10 ? "#e8a04e" // amber — tight
    : data.net_margin_pct > 0    ? "#fb923c" // orange — thin
    : "#f87171";                               // red — loss

  const costs = data.costs;

  // Each cost line gets rendered uniformly — amount, label, source note, badge.
  const costLines = [
    {
      key: "cogs",
      label: "Cost of Goods Sold",
      sublabel: `${Math.round((costs.cogs.rate ?? 0) * 100)}% of revenue`,
      amount: costs.cogs.amount,
      estimated: costs.cogs.estimated,
      note: costs.cogs.note,
    },
    {
      key: "payment_fees",
      label: "Payment Processing",
      sublabel: `${(costs.payment_fees.rate * 100).toFixed(1)}% + ${fmtMoney(costs.payment_fees.flat)}/order`,
      amount: costs.payment_fees.amount,
      estimated: costs.payment_fees.estimated,
      note: costs.payment_fees.note,
    },
    {
      key: "shipping",
      label: "Shipping & Fulfillment",
      sublabel: `${fmtMoney(costs.shipping.rate)} per order (estimate)`,
      amount: costs.shipping.amount,
      estimated: costs.shipping.estimated,
      note: costs.shipping.note,
    },
    {
      key: "ad_spend",
      label: "Ad Spend",
      sublabel: "Not tracked yet",
      amount: costs.ad_spend.amount,
      estimated: costs.ad_spend.estimated,
      note: costs.ad_spend.note,
      isEmptyAdSpend: true,
    },
  ];

  const precisionLabel =
    data.precision === "exact"   ? "Exact — real cost data on every line"
    : data.precision === "refined" ? "Refined — partial real cost data"
    : "Rough — using default assumptions";

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
      {/* Header eyebrow + narrative headline */}
      <div className="mb-5">
        <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-400">
          Profit Intelligence
        </div>
        <h3 className="mt-1 text-[15px] font-bold leading-tight text-white">
          What you actually keep after costs
        </h3>
        <p className="mt-1.5 text-[12px] leading-relaxed text-slate-400">
          {data.verdict}
        </p>
      </div>

      {/* 3 big KPI tiles — Revenue, Profit, Margin */}
      <div className="mb-5 grid grid-cols-3 gap-3">
        <div
          className="rounded-xl border px-4 py-3"
          style={{ borderColor: "rgba(255, 255, 255, 0.06)", backgroundColor: "rgba(255, 255, 255, 0.02)" }}
        >
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
            Revenue
          </div>
          <div className="mt-1 text-[22px] font-extrabold tabular-nums leading-none text-white">
            {fmtMoney(data.gross_revenue)}
          </div>
          <div className="mt-1 text-[10px] text-slate-400">
            {data.order_count} orders · {data.window_days}d
          </div>
        </div>
        <div
          className="rounded-xl border px-4 py-3"
          style={{
            borderColor: `${netMarginColor}40`,
            backgroundColor: `${netMarginColor}0f`,
          }}
        >
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
            Net Profit
          </div>
          <div
            className="mt-1 text-[22px] font-extrabold tabular-nums leading-none"
            style={{ color: netMarginColor }}
          >
            {fmtMoney(data.net_profit)}
          </div>
          <div className="mt-1 text-[10px] text-slate-400">after tracked costs</div>
        </div>
        <div
          className="rounded-xl border px-4 py-3"
          style={{
            borderColor: `${netMarginColor}40`,
            backgroundColor: `${netMarginColor}0f`,
          }}
        >
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
            Margin
          </div>
          <div
            className="mt-1 text-[22px] font-extrabold tabular-nums leading-none"
            style={{ color: netMarginColor }}
          >
            {data.net_margin_pct.toFixed(1)}%
          </div>
          <div className="mt-1 text-[10px] text-slate-400">
            {data.net_margin_pct >= 20 ? "healthy" : data.net_margin_pct >= 10 ? "tight" : "thin"}
          </div>
        </div>
      </div>

      {/* Waterfall — Revenue → costs → Profit */}
      <div className="mb-5">
        <div className="mb-2 flex items-center justify-between">
          <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
            Cost waterfall
          </div>
          <div className="text-[10px] text-slate-400">revenue → profit</div>
        </div>

        <div className="space-y-1.5">
          {/* Top line — Gross Revenue */}
          <div className="flex items-center justify-between rounded-lg border border-white/[0.05] bg-white/[0.015] px-3.5 py-2.5">
            <div className="min-w-0 flex-1">
              <div className="text-[12px] font-semibold text-white">Gross Revenue</div>
              <div className="text-[10px] text-slate-400">from {data.order_count} real Shopify orders</div>
            </div>
            <div className="flex-shrink-0 text-[14px] font-bold tabular-nums text-white">
              {fmtMoney(data.gross_revenue)}
            </div>
          </div>

          {/* Cost lines */}
          {costLines.map((line) => {
            const isEmpty = line.isEmptyAdSpend && line.amount === 0;
            return (
              <div
                key={line.key}
                className="flex items-center justify-between rounded-lg border border-white/[0.04] bg-white/[0.01] px-3.5 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[12px] font-medium text-slate-200">
                      {line.label}
                    </span>
                    {line.estimated && !isEmpty && (
                      <span
                        className="flex-shrink-0 rounded-full border px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-wider"
                        style={{
                          borderColor: "rgba(232, 160, 78, 0.32)",
                          backgroundColor: "rgba(232, 160, 78, 0.08)",
                          color: "#e8a04e",
                        }}
                        title={line.note}
                      >
                        estimated
                      </span>
                    )}
                    {isEmpty && (
                      <span
                        className="flex-shrink-0 rounded-full border px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-wider"
                        style={{
                          borderColor: "rgba(148, 163, 184, 0.28)",
                          backgroundColor: "rgba(148, 163, 184, 0.06)",
                          color: "#94a3b8",
                        }}
                        title={line.note}
                      >
                        not tracked
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-[10px] text-slate-400">{line.sublabel}</div>
                </div>
                <div className="flex-shrink-0 text-[13px] font-semibold tabular-nums text-rose-300/90">
                  {isEmpty ? "—" : `− ${fmtMoney(line.amount)}`}
                </div>
              </div>
            );
          })}

          {/* Bottom line — Net Profit */}
          <div
            className="flex items-center justify-between rounded-lg border px-3.5 py-3"
            style={{
              borderColor: `${netMarginColor}40`,
              background: `linear-gradient(135deg, ${netMarginColor}14 0%, ${netMarginColor}05 100%)`,
            }}
          >
            <div className="min-w-0 flex-1">
              <div className="text-[12px] font-bold text-white">Net Profit</div>
              <div className="text-[10px] text-slate-400">
                {data.net_margin_pct.toFixed(1)}% margin
              </div>
            </div>
            <div
              className="flex-shrink-0 text-[16px] font-extrabold tabular-nums"
              style={{ color: netMarginColor }}
            >
              {fmtMoney(data.net_profit)}
            </div>
          </div>
        </div>
      </div>

      {/* Precision footer — honest CTA */}
      <div
        className="rounded-xl border px-4 py-3"
        style={{
          borderColor: "rgba(232, 160, 78, 0.18)",
          backgroundColor: "rgba(232, 160, 78, 0.04)",
        }}
      >
        <div className="flex items-start gap-3">
          <span
            className="mt-1 h-1.5 w-1.5 flex-shrink-0 rounded-full"
            style={{ backgroundColor: "#e8a04e", boxShadow: "0 0 6px rgba(232, 160, 78, 0.6)" }}
          />
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-semibold text-amber-300/90">
              Precision: {precisionLabel}
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
              These numbers use default assumptions (40% COGS, {fmtMoney(5)}/order shipping, Shopify Payments standard fees).
              Enter your real COGS per product and connect Meta Ads + Google Ads to see exact profit.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
