"use client";

import { useEffect, useState } from "react";
import { createMoneyFormatter, type DisplayCurrency } from "../lib/currency";
import { apiClient, type paths } from "../lib/api-client";

// Source of truth: GET /orders/summary → OrdersSummaryResponse.
type OrdersSummaryData =
  paths["/orders/summary"]["get"]["responses"]["200"]["content"]["application/json"];

// Money formatter imported from /lib/currency.ts as the single source of truth.

export function OrdersSummary({
  apiBase: _apiBase,
  shop,
  displayCurrency = "USD",
}: {
  apiBase: string;
  shop: string;
  displayCurrency?: DisplayCurrency;
}) {
  const [data, setData] = useState<OrdersSummaryData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!shop) return;
    let active = true;

    apiClient
      .GET("/orders/summary", {})
      .then((res) => {
        // Never wipe good data with null. If fetch fails, keep previous state.
        if (active && res.data != null) setData(res.data);
      })
      .catch(() => {})
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => { active = false; };
  }, [shop]);

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
        <div className="h-4 w-32 rounded bg-white/[0.06]" />
        <div className="mt-4 h-8 w-24 rounded bg-white/[0.06]" />
      </div>
    );
  }

  if (!data || !data.has_orders) {
    return (
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] px-6 py-5">
        <div className="text-[13px] font-bold uppercase tracking-[0.16em] text-slate-500">
          Revenue
        </div>
        <p className="mt-2 text-[15px] leading-relaxed text-slate-400">
          No orders received yet. Revenue data will appear here once Shopify
          sends your first order webhook.
        </p>
      </div>
    );
  }

  const c = data.currency;
  const d7 = data.last_7d;
  const d30 = data.last_30d;
  const fmtCurrency = createMoneyFormatter(displayCurrency, c);

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-6">
      <div className="mb-5 text-[13px] font-bold uppercase tracking-[0.16em] hs-brand-gradient">
        Revenue — Real Orders
      </div>

      {/* KPI row */}
      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <div className="text-[13px] font-medium text-slate-400">Last 7 days</div>
          <div className="mt-1.5 text-[1.75rem] font-bold tabular-nums text-white">
            {fmtCurrency(d7.total_revenue)}
          </div>
          <div className="mt-1 text-[14px] text-slate-500">
            {d7.order_count} order{d7.order_count !== 1 ? "s" : ""}
          </div>
        </div>
        <div>
          <div className="text-[13px] font-medium text-slate-400">Last 30 days</div>
          <div className="mt-1.5 text-[1.75rem] font-bold tabular-nums text-white">
            {fmtCurrency(d30.total_revenue)}
          </div>
          <div className="mt-1 text-[14px] text-slate-500">
            {d30.order_count} order{d30.order_count !== 1 ? "s" : ""}
          </div>
        </div>
        <div>
          <div className="text-[13px] font-medium text-slate-400">Avg Order Value</div>
          <div className="mt-1.5 text-[1.75rem] font-bold tabular-nums text-white">
            {fmtCurrency(d30.avg_order_value)}
          </div>
          <div className="mt-1 text-[14px] text-slate-500">30-day average</div>
        </div>
      </div>

      {/* Top products */}
      {data.top_products_by_revenue.length > 0 && (
        <div className="mt-6 border-t border-white/[0.06] pt-5">
          <div className="mb-3 text-[13px] font-bold uppercase tracking-[0.12em] text-slate-400">
            Top products by revenue (30d)
          </div>
          <div className="space-y-2">
            {data.top_products_by_revenue.map((p, i) => (
              <div
                key={`${p.product_title}-${i}`}
                className="flex items-center justify-between gap-3 rounded-xl px-3 py-2.5 text-[14px] transition-colors hover:bg-white/[0.03]"
              >
                <span className="min-w-0 truncate font-medium text-slate-200">
                  {p.product_title}
                </span>
                <div className="flex flex-shrink-0 items-center gap-4">
                  <span className="tabular-nums text-slate-500">
                    {p.units_sold} sold
                  </span>
                  <span className="tabular-nums font-bold text-emerald-300">
                    {fmtCurrency(p.revenue)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
