"use client";

import { useEffect, useState } from "react";

type ProductRow = {
  product_url: string;
  product_name: string;
  views: number;
  unique_viewers: number;
  add_to_cart: number;
  purchases: number;
  units_sold: number;
  revenue: number;
  cvr: number;
  atc_rate: number;
  avg_order_value: number;
};

type ConversionData = {
  products: ProductRow[];
  days: number;
  currency: string;
  has_data: boolean;
};

type SortKey = "revenue" | "views" | "purchases" | "cvr" | "atc_rate";

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

function fmtPct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function shortName(name: string): string {
  // /products/some-handle → Some Handle
  if (name.startsWith("/products/")) {
    return name
      .slice(10)
      .replace(/-/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }
  return name.length > 40 ? name.slice(0, 38) + "…" : name;
}

export function ProductConversions({
  apiBase,
  shop,
}: {
  apiBase: string;
  shop: string;
}) {
  const [data, setData] = useState<ConversionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(7);
  const [sortKey, setSortKey] = useState<SortKey>("revenue");

  useEffect(() => {
    if (!shop || !apiBase) return;
    let active = true;
    setLoading(true);

    fetch(
      `${apiBase}/orders/product-conversions?shop=${encodeURIComponent(shop)}&days=${days}`,
      { headers: { "Content-Type": "application/json" }, credentials: "include", cache: "no-store" }
    )
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => { if (active) setData(json); })
      .catch(() => {})
      .finally(() => { if (active) setLoading(false); });

    return () => { active = false; };
  }, [apiBase, shop, days]);

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
        <div className="h-4 w-48 rounded bg-white/[0.06]" />
        <div className="mt-4 space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-8 rounded bg-white/[0.04]" />
          ))}
        </div>
      </div>
    );
  }

  if (!data || !data.has_data) {
    return (
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] px-5 py-4">
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-600">
          Product Conversion Intelligence
        </div>
        <p className="mt-2 text-[12px] leading-relaxed text-slate-500">
          Product-level conversion data is building up. This section shows views, add-to-carts,
          purchases, and revenue per product once enriched order data is available. In the
          meantime, check Revenue and Orders Summary above for your overall store performance.
        </p>
      </div>
    );
  }

  const sorted = [...data.products].sort((a, b) => {
    const av = a[sortKey] ?? 0;
    const bv = b[sortKey] ?? 0;
    return bv - av;
  });

  const c = data.currency;

  function headerBtn(label: string, key: SortKey) {
    const active = sortKey === key;
    return (
      <button
        onClick={() => setSortKey(key)}
        className={`text-right text-[10px] font-medium uppercase tracking-[0.1em] transition-colors ${
          active ? "text-violet-300" : "text-slate-600 hover:text-slate-400"
        }`}
      >
        {label}{active ? " ↓" : ""}
      </button>
    );
  }

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-violet-300/70">
            Product Conversion Intelligence
          </div>
          <div className="mt-0.5 text-[11px] text-slate-500">
            Real data from orders — not estimates
          </div>
        </div>
        <div className="flex gap-1">
          {[7, 30].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-lg px-2.5 py-1 text-[11px] font-medium transition-colors ${
                days === d
                  ? "bg-violet-500/15 text-violet-300"
                  : "text-slate-500 hover:bg-white/[0.05] hover:text-slate-300"
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-[12px]">
          <thead>
            <tr className="border-b border-white/[0.06]">
              <th className="pb-2 pr-3 text-[10px] font-medium uppercase tracking-[0.1em] text-slate-600">
                Product
              </th>
              <th className="pb-2 px-2">{headerBtn("Views", "views")}</th>
              <th className="pb-2 px-2">{headerBtn("ATC", "atc_rate")}</th>
              <th className="pb-2 px-2">{headerBtn("Purchases", "purchases")}</th>
              <th className="pb-2 px-2">{headerBtn("Revenue", "revenue")}</th>
              <th className="pb-2 pl-2">{headerBtn("CVR", "cvr")}</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((p, i) => (
              <tr
                key={`${p.product_url}-${i}`}
                className="border-t border-white/[0.04] transition-colors hover:bg-white/[0.02]"
              >
                <td className="py-2 pr-3">
                  <span className="text-slate-300" title={p.product_url}>
                    {shortName(p.product_name)}
                  </span>
                </td>
                <td className="py-2 px-2 text-right tabular-nums text-slate-400">
                  {p.views.toLocaleString()}
                </td>
                <td className="py-2 px-2 text-right tabular-nums text-slate-400">
                  <span title={`${p.add_to_cart} visitors added to cart`}>
                    {fmtPct(p.atc_rate)}
                  </span>
                </td>
                <td className="py-2 px-2 text-right tabular-nums text-slate-400">
                  {p.purchases}
                </td>
                <td className="py-2 px-2 text-right tabular-nums font-medium text-emerald-300">
                  {fmtCurrency(p.revenue, c)}
                </td>
                <td className="py-2 pl-2 text-right">
                  <span
                    className={`tabular-nums font-medium ${
                      p.cvr >= 0.03
                        ? "text-emerald-300"
                        : p.cvr >= 0.01
                        ? "text-amber-300"
                        : "text-slate-500"
                    }`}
                  >
                    {fmtPct(p.cvr)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
