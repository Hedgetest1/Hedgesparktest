"use client";

import { createMoneyFormatter, type DisplayCurrency } from "../lib/currency";
import type { paths } from "../lib/api-client";

// Generated response type — the single source of truth. Regenerate via
// `npm run api:types` after backend changes.
type GatewayProductsResponse =
  paths["/pro/cohorts/ltv/products"]["get"]["responses"]["200"]["content"]["application/json"];

/**
 * GatewayProducts — Killer customer-acquisition product intelligence.
 *
 * Surfaces which products bring in HIGH-LTV customers (gateway products)
 * vs which products are bought repeatedly by existing customers (loyalty products).
 *
 * This is a HedgeSpark differentiator that competitors structurally cannot match:
 * we link first-purchase product to lifetime customer value, identifying
 * the products that should receive disproportionate ad spend.
 *
 * Data shape: see backend `ltv_engine.py:get_product_ltv_contribution()`
 *   {
 *     shop_domain: string,
 *     products: [
 *       {
 *         product: string,           // product key (slug/url)
 *         title: string | null,
 *         buyer_count: number,
 *         avg_buyer_ltv: number,     // average lifetime spend of this product's buyers
 *         avg_buyer_orders: number,
 *         buyer_repeat_rate: number, // 0-1
 *         gateway_rate: number,      // 0-1, fraction bought as first order
 *         is_gateway: boolean,       // gateway_rate > 0.5
 *       }
 *     ]
 *   }
 */

// Local alias using the generated type (for readability in the JSX below).
type GatewayProductsData = GatewayProductsResponse;

const fmtPct = (v: number) => `${Math.round((v || 0) * 100)}%`;

function truncateTitle(title: string | null | undefined, fallback: string, max = 42) {
  const t = title || fallback || "—";
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}

export function GatewayProducts({
  data,
  displayCurrency = "USD",
}: {
  data: GatewayProductsData | null;
  displayCurrency?: DisplayCurrency;
}) {
  const products = data?.products ?? [];
  const fmtMoney = createMoneyFormatter(displayCurrency, "USD");

  // Sort by avg_buyer_ltv descending — most valuable first
  const sorted = [...products].sort((a, b) => (b.avg_buyer_ltv || 0) - (a.avg_buyer_ltv || 0));
  const maxLtv = Math.max(...sorted.map((p) => p.avg_buyer_ltv || 0), 1);
  const top = sorted[0];

  // The killer narrative headline — based on top performer
  const headline = top
    ? `Your top gateway product brings in customers worth ${fmtMoney(top.avg_buyer_ltv)} each.`
    : "Gateway product analysis activates with 5+ identified customers per product.";

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
      {/* Header */}
      <div className="mb-5 flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="mb-1">
            <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#e8a04e]">
              Gateway Intelligence
            </span>
          </div>
          <h3 className="text-[15px] font-bold leading-tight text-white">
            Which products bring you customers worth keeping
          </h3>
          <p className="mt-1.5 text-[12px] leading-relaxed text-slate-400">{headline}</p>
        </div>
      </div>

      {/* Empty state */}
      {sorted.length === 0 && (
        <div className="rounded-xl border border-dashed border-white/[0.06] bg-white/[0.015] px-4 py-8 text-center">
          <p className="text-[12px] text-slate-500">
            We need at least 5 identified customers per product before we can surface
            gateway intelligence. Connect Shopify webhooks if you haven&apos;t yet —
            customer identity is the key.
          </p>
        </div>
      )}

      {/* Product list */}
      {sorted.length > 0 && (
        <div className="space-y-3">
          {sorted.slice(0, 8).map((p) => {
            const ltvWidth = Math.max(6, Math.round(((p.avg_buyer_ltv || 0) / maxLtv) * 100));
            const isGateway = p.is_gateway;
            const accent = isGateway ? "#e8a04e" : "#34d399"; // amber gateway / emerald loyalty
            const accentSoft = isGateway
              ? "rgba(232, 160, 78, 0.12)"
              : "rgba(52, 211, 153, 0.12)";
            const accentBorder = isGateway
              ? "rgba(232, 160, 78, 0.32)"
              : "rgba(52, 211, 153, 0.32)";

            return (
              <div
                key={p.product}
                className="group rounded-xl border border-white/[0.05] bg-white/[0.015] p-3.5 transition-colors hover:border-white/[0.1] hover:bg-white/[0.025]"
              >
                {/* Top row: title + badge */}
                <div className="mb-2 flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-semibold text-white">
                      {truncateTitle(p.title, p.product)}
                    </div>
                    <div className="mt-0.5 flex items-center gap-3 text-[11px] text-slate-500">
                      <span>{p.buyer_count} buyers</span>
                      <span className="text-slate-700">•</span>
                      <span>{p.avg_buyer_orders.toFixed(1)} orders/buyer</span>
                      <span className="text-slate-700">•</span>
                      <span
                        className={
                          p.buyer_repeat_rate >= 0.5
                            ? "text-emerald-400/90"
                            : p.buyer_repeat_rate >= 0.25
                            ? "text-amber-400/90"
                            : "text-slate-500"
                        }
                      >
                        {fmtPct(p.buyer_repeat_rate)} repeat
                      </span>
                    </div>
                  </div>
                  <span
                    className="flex-shrink-0 rounded-full border px-2.5 py-0.5 text-[9px] font-bold uppercase tracking-wider"
                    style={{
                      borderColor: accentBorder,
                      backgroundColor: accentSoft,
                      color: accent,
                    }}
                  >
                    {isGateway ? "Gateway" : "Loyalty"}
                  </span>
                </div>

                {/* LTV bar */}
                <div className="flex items-center gap-3">
                  <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.04]">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: `${ltvWidth}%`,
                        background: `linear-gradient(90deg, ${accent} 0%, ${accent}aa 100%)`,
                        boxShadow: `0 0 12px -2px ${accent}66`,
                      }}
                    />
                  </div>
                  <div
                    className="flex-shrink-0 text-[13px] font-bold tabular-nums"
                    style={{ color: accent }}
                  >
                    {fmtMoney(p.avg_buyer_ltv)}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Legend / insight footer */}
      {sorted.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[10px] text-slate-500">
          <div className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-[#e8a04e]" />
            <span>
              <strong className="text-[#e8a04e]">Gateway</strong> = bought as first order &gt; 50% of the time
            </span>
          </div>
          <div className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-[#34d399]" />
            <span>
              <strong className="text-emerald-400">Loyalty</strong> = bought repeatedly by existing customers
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
