"use client";

/**
 * ProductsInDecline — "Products Losing You Money"
 *
 * Shows the top N products whose order momentum is declining. Loss-framed:
 * projected monthly € loss if the decline continues. v1 uses the
 * order-frequency proxy (see refund_loss service).
 *
 * Tier-aware data source (founder directive 2026-04-26 — Lifetimely-parity unlock):
 *   - Pro merchants: GET /pro/refund-losses
 *   - Lite merchants: GET /analytics/refund-losses
 * Both endpoints back onto the same `get_refund_loss_report` service.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";

type ProductRow = {
  product_title: string;
  product_id: string | null;
  orders_recent_14d: number;
  orders_prior_14d: number;
  avg_price_recent: number;
  avg_price_prior: number;
  revenue_recent_14d: number;
  revenue_prior_14d: number;
  loss_eur: number;
  decline_pct: number;
  reason: string;
};

import { formatMoneyCompact } from "@/app/app/_lib/formatters";

type RefundLossData = {
  shop_domain: string;
  total_loss_eur_per_month: number;
  product_count: number;
  products: ProductRow[];
  // Shop's native currency — product price/loss fields are native.
  currency?: string;
  generated_at: string | null;
  method: string | null;
  headline: string | null;
};

function fmtMoney(n: number, currency?: string): string {
  return formatMoneyCompact(n, currency || "USD");
}

export function ProductsInDecline({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [data, setData] = useState<RefundLossData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) { setLoading(false); return; }
    let active = true;
    setLoading(true);
    const req = isProUser
      ? apiClient.GET("/pro/refund-losses")
      : apiClient.GET("/analytics/refund-losses");
    req
      .then(({ data: j, error: err }) => {
        if (!active) return;
        if (err || !j) setData(null);
        else setData(j as unknown as RefundLossData);
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiBase, shop, isProUser]);

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
        <div className="h-3 w-44 rounded bg-white/[0.06]" />
        <div className="mt-3 space-y-2">
          {[0, 1, 2].map((i) => (<div key={i} className="h-8 rounded bg-white/[0.04]" />))}
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-2xl border border-amber-400/20 bg-amber-500/[0.04] p-5">
        <span className="text-[12px] text-amber-300">Product decline data unavailable.</span>
      </div>
    );
  }

  const isEmpty = (data.product_count || 0) === 0;
  const total = data.total_loss_eur_per_month || 0;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-rose-400">
            Products Losing You Money
          </div>
          <h3 className="text-[15px] font-bold text-white">
            {isEmpty ? "No products in decline" : `${data.product_count} products slipping`}
          </h3>
          <p className="mt-1 text-[11px] text-slate-400">
            {isEmpty
              ? "Your catalog is stable over the last 28 days."
              : "Based on last 14d vs. prior 14d order momentum"}
          </p>
        </div>
        {!isEmpty && (
          <div className="flex-shrink-0 rounded-lg border border-rose-400/20 bg-rose-500/[0.06] px-3 py-2 text-right">
            <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-rose-300">
              Projected loss
            </div>
            <div className="text-[18px] font-extrabold tabular-nums text-rose-300">
              {fmtMoney(total, data?.currency)}/mo
            </div>
          </div>
        )}
      </div>

      {isEmpty ? (
        <p className="text-[12px] text-slate-400">✓ {data.headline || "All products holding steady."}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[12px]">
            <thead>
              <tr className="border-b border-white/[0.05]">
                <th className="pb-2 pr-3 text-[10px] font-bold uppercase tracking-[0.1em] text-slate-400">Product</th>
                <th className="pb-2 px-2 text-right text-[10px] font-bold uppercase tracking-[0.1em] text-slate-400">Orders 14d</th>
                <th className="pb-2 px-2 text-right text-[10px] font-bold uppercase tracking-[0.1em] text-slate-400">Decline</th>
                <th className="pb-2 pl-2 text-right text-[10px] font-bold uppercase tracking-[0.1em] text-slate-400">Monthly loss</th>
              </tr>
            </thead>
            <tbody>
              {data.products.slice(0, 5).map((p, i) => (
                <tr key={(p.product_id || "") + i} className="border-t border-white/[0.03]">
                  <td className="py-2 pr-3 text-slate-300">{p.product_title}</td>
                  <td className="py-2 px-2 text-right tabular-nums text-slate-400">
                    <span className="text-rose-300">{p.orders_recent_14d}</span>
                    <span className="mx-1 text-slate-600">vs</span>
                    <span className="text-slate-500">{p.orders_prior_14d}</span>
                  </td>
                  <td className="py-2 px-2 text-right tabular-nums text-rose-300">
                    -{p.decline_pct.toFixed(0)}%
                  </td>
                  <td className="py-2 pl-2 text-right font-mono font-semibold tabular-nums text-rose-300">
                    {fmtMoney(p.loss_eur, data?.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
