"use client";

import { useEffect, useState } from "react";

type OrderWindow = {
  order_count: number;
  total_revenue: number;
  avg_order_value: number;
};

type TopProduct = {
  product_title: string;
  revenue: number;
  units_sold: number;
};

type OrdersSummaryData = {
  has_orders: boolean;
  currency: string;
  last_7d: OrderWindow;
  last_30d: OrderWindow;
  top_products_by_revenue: TopProduct[];
};

function fmtCurrency(value: number, currency: string): string {
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    return `${currency} ${Math.round(value)}`;
  }
}

export function OrdersSummary({
  apiBase,
  shop,
}: {
  apiBase: string;
  shop: string;
}) {
  const [data, setData] = useState<OrdersSummaryData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!shop || !apiBase) return;
    let active = true;

    fetch(`${apiBase}/orders/summary?shop=${encodeURIComponent(shop)}`, {
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      cache: "no-store",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => {
        if (active) setData(json);
      })
      .catch(() => {})
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => { active = false; };
  }, [apiBase, shop]);

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
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] px-5 py-4">
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-600">
          Revenue
        </div>
        <p className="mt-2 text-[12px] leading-relaxed text-slate-500">
          No orders received yet. Revenue data will appear here once Shopify
          sends your first order webhook.
        </p>
      </div>
    );
  }

  const c = data.currency;
  const d7 = data.last_7d;
  const d30 = data.last_30d;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
      <div className="mb-4 text-[11px] font-semibold uppercase tracking-[0.16em] text-emerald-300/70">
        Revenue — Real Order Data
      </div>

      {/* KPI row */}
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <div className="text-[11px] text-slate-500">Last 7 days</div>
          <div className="mt-1 text-xl font-semibold tabular-nums text-white">
            {fmtCurrency(d7.total_revenue, c)}
          </div>
          <div className="text-[11px] text-slate-600">
            {d7.order_count} order{d7.order_count !== 1 ? "s" : ""}
          </div>
        </div>
        <div>
          <div className="text-[11px] text-slate-500">Last 30 days</div>
          <div className="mt-1 text-xl font-semibold tabular-nums text-white">
            {fmtCurrency(d30.total_revenue, c)}
          </div>
          <div className="text-[11px] text-slate-600">
            {d30.order_count} order{d30.order_count !== 1 ? "s" : ""}
          </div>
        </div>
        <div>
          <div className="text-[11px] text-slate-500">Avg Order Value</div>
          <div className="mt-1 text-xl font-semibold tabular-nums text-white">
            {fmtCurrency(d30.avg_order_value, c)}
          </div>
          <div className="text-[11px] text-slate-600">30-day average</div>
        </div>
      </div>

      {/* Top products */}
      {data.top_products_by_revenue.length > 0 && (
        <div className="mt-5 border-t border-white/[0.06] pt-4">
          <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.12em] text-slate-500">
            Top products by revenue (30d)
          </div>
          <div className="space-y-1.5">
            {data.top_products_by_revenue.map((p, i) => (
              <div
                key={`${p.product_title}-${i}`}
                className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-[12px] transition-colors hover:bg-white/[0.03]"
              >
                <span className="min-w-0 truncate text-slate-300">
                  {p.product_title}
                </span>
                <div className="flex flex-shrink-0 items-center gap-3">
                  <span className="tabular-nums text-slate-500">
                    {p.units_sold} sold
                  </span>
                  <span className="tabular-nums font-medium text-emerald-300">
                    {fmtCurrency(p.revenue, c)}
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
