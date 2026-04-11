"use client";

import { useEffect, useState } from "react";
import { apiClient, type paths } from "../lib/api-client";

// Source of truth: GET /orders/product-conversions → ProductConversionsResponse.
type ConversionData =
  paths["/orders/product-conversions"]["get"]["responses"]["200"]["content"]["application/json"];
type ProductRow = ConversionData["products"][number];

// Source of truth: GET /pro/heatmap → HeatmapResponse (single-product scroll).
type HeatmapData =
  paths["/pro/heatmap"]["get"]["responses"]["200"]["content"]["application/json"];
type ScrollBucket = HeatmapData["scroll"]["buckets"][number];

type SortKey = "revenue" | "views" | "purchases" | "cvr" | "atc_rate";

// Matches HeatmapCard.tsx visual family — sky → purple quartile ramp.
const BUCKET_COLORS = [
  { bg: "bg-sky-500/70",    text: "text-sky-300"    },
  { bg: "bg-blue-500/60",   text: "text-blue-300"   },
  { bg: "bg-violet-500/60", text: "text-violet-300" },
  { bg: "bg-purple-500/70", text: "text-purple-300" },
];

function ScrollDepthBar({ buckets }: { buckets: ScrollBucket[] }) {
  if (!buckets || buckets.length === 0) {
    return <p className="text-[11px] text-slate-600">No scroll data yet for this product.</p>;
  }
  return (
    <div className="space-y-2.5">
      {buckets.map((b, i) => {
        const pct = b.pct_of_viewers ?? 0;
        const colors = BUCKET_COLORS[i % BUCKET_COLORS.length];
        return (
          <div key={`drawer-b-${i}`}>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[10px] text-slate-500">{b.label}</span>
              <span className={`text-[11px] font-semibold tabular-nums ${colors.text}`}>
                {pct.toFixed(0)}%
                <span className="ml-1 font-normal text-slate-600">
                  ({b.visitor_count?.toLocaleString()} visitors)
                </span>
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-white/[0.04]">
              <div
                className={`h-full rounded-full transition-all duration-500 ${colors.bg}`}
                style={{ width: `${Math.min(100, pct)}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

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
  apiBase: _apiBase,
  shop,
}: {
  apiBase: string;
  shop: string;
}) {
  const [data, setData] = useState<ConversionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(7);
  const [sortKey, setSortKey] = useState<SortKey>("revenue");

  // Scroll heatmap drawer state — opens on row click, fetches /pro/heatmap
  // for the selected product_url. Closes on overlay/ESC.
  const [drawerProduct, setDrawerProduct] = useState<ProductRow | null>(null);
  const [drawerData, setDrawerData] = useState<HeatmapData | null>(null);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [drawerError, setDrawerError] = useState(false);

  useEffect(() => {
    if (!drawerProduct) return;
    let active = true;
    setDrawerLoading(true);
    setDrawerError(false);
    setDrawerData(null);
    apiClient
      .GET("/pro/heatmap", {
        params: { query: { product_url: drawerProduct.product_url, hours: 72 } },
      })
      .then((res) => {
        if (!active) return;
        if (res.data != null) setDrawerData(res.data);
        else setDrawerError(true);
      })
      .catch(() => { if (active) setDrawerError(true); })
      .finally(() => { if (active) setDrawerLoading(false); });
    return () => { active = false; };
  }, [drawerProduct]);

  useEffect(() => {
    if (!drawerProduct) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerProduct(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerProduct]);

  useEffect(() => {
    if (!shop) return;
    let active = true;
    setLoading(true);

    apiClient
      .GET("/orders/product-conversions", { params: { query: { days } } })
      .then((res) => {
        // Never wipe good data with null. If the new fetch fails (e.g. the
        // merchant toggled 7d→30d and the new window errors), the previous
        // table stays visible. Prevents the "table disappeared" UX bug.
        if (active && res.data != null) setData(res.data);
      })
      .catch(() => {})
      .finally(() => { if (active) setLoading(false); });

    return () => { active = false; };
  }, [shop, days]);

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
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] px-6 py-5">
        <div className="text-[13px] font-bold uppercase tracking-[0.16em] text-slate-500">
          Product Conversion Intelligence
        </div>
        <p className="mt-2 text-[15px] leading-relaxed text-slate-400">
          Product-level conversion data is building up. This section shows views, add-to-carts,
          purchases, and revenue per product once enriched order data is available.
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
        className={`text-right text-[12px] font-bold uppercase tracking-[0.1em] transition-colors ${
          active ? "text-[#d4893a]" : "text-slate-500 hover:text-slate-300"
        }`}
      >
        {label}{active ? " ↓" : ""}
      </button>
    );
  }

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-6">
      {/* Header */}
      <div className="mb-5 flex items-center justify-between">
        <div>
          <div className="text-[13px] font-bold uppercase tracking-[0.16em] hs-brand-gradient">
            Product Conversions
          </div>
          <div className="mt-1 text-[14px] text-slate-400">
            Real order data — not estimates
          </div>
        </div>
        <div className="flex gap-1.5">
          {[7, 30].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-lg px-3 py-1.5 text-[13px] font-semibold transition-colors ${
                days === d
                  ? "bg-[#d4893a]/15 text-[#e8a04e]"
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
        <table className="w-full text-left text-[14px]">
          <thead>
            <tr className="border-b border-white/[0.06]">
              <th className="pb-3 pr-3 text-[12px] font-bold uppercase tracking-[0.1em] text-slate-500">
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
                onClick={() => setDrawerProduct(p)}
                className="cursor-pointer border-t border-white/[0.04] transition-colors hover:bg-white/[0.03]"
                title="Click for scroll depth breakdown"
              >
                <td className="py-2 pr-3">
                  <span className="inline-flex items-center gap-1.5 text-slate-300" title={p.product_url}>
                    {shortName(p.product_name)}
                    <span className="text-[10px] text-slate-600">›</span>
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

      {/* Scroll heatmap drawer — opens on row click, fetches /pro/heatmap
          for the selected product. Reuses the visual language of HeatmapCard. */}
      {drawerProduct && (
        <div
          className="fixed inset-0 z-50 flex justify-end"
          role="dialog"
          aria-modal="true"
          aria-label="Product scroll heatmap"
        >
          <button
            type="button"
            onClick={() => setDrawerProduct(null)}
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            aria-label="Close drawer"
          />
          <div className="relative z-10 flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-white/[0.08] bg-[#0b0b14] shadow-2xl">
            <div className="sticky top-0 flex items-start justify-between gap-3 border-b border-white/[0.06] bg-[#0b0b14]/95 px-6 py-5 backdrop-blur">
              <div className="min-w-0">
                <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                  Scroll Intelligence
                </div>
                <h3 className="mt-1 truncate text-[15px] font-bold text-white" title={drawerProduct.product_url}>
                  {shortName(drawerProduct.product_name)}
                </h3>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  Last 72h · where visitors stop reading
                </p>
              </div>
              <button
                type="button"
                onClick={() => setDrawerProduct(null)}
                className="flex-shrink-0 rounded-lg border border-white/[0.08] bg-white/[0.02] px-2.5 py-1 text-[11px] font-semibold text-slate-400 transition-colors hover:border-white/[0.2] hover:text-white"
                aria-label="Close"
              >
                ✕
              </button>
            </div>

            <div className="flex-1 px-6 py-5">
              {drawerLoading && (
                <div className="animate-pulse space-y-3">
                  <div className="h-20 rounded-xl bg-white/[0.04]" />
                  <div className="h-6 rounded bg-white/[0.04]" />
                  <div className="h-6 rounded bg-white/[0.04]" />
                  <div className="h-6 rounded bg-white/[0.04]" />
                  <div className="h-6 rounded bg-white/[0.04]" />
                </div>
              )}

              {drawerError && !drawerLoading && (
                <div className="rounded-xl border border-amber-400/20 bg-amber-500/[0.06] px-4 py-3">
                  <span className="text-[12px] text-amber-300">Scroll data unavailable for this product.</span>
                </div>
              )}

              {!drawerLoading && !drawerError && drawerData && (
                <>
                  <div className="mb-4 grid grid-cols-3 gap-2">
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
                      <div className="text-[10px] uppercase text-slate-600">Viewers</div>
                      <div className="mt-0.5 text-[14px] font-semibold text-white">
                        {drawerData.scroll.total_viewers.toLocaleString()}
                      </div>
                    </div>
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
                      <div className="text-[10px] uppercase text-slate-600">Avg Scroll</div>
                      <div className="mt-0.5 text-[14px] font-semibold text-white">
                        {drawerData.scroll.avg_scroll_depth.toFixed(0)}%
                      </div>
                    </div>
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
                      <div className="text-[10px] uppercase text-slate-600">Median</div>
                      <div className="mt-0.5 text-[14px] font-semibold text-white">
                        {drawerData.scroll.median_scroll_depth.toFixed(0)}%
                      </div>
                    </div>
                  </div>

                  <ScrollDepthBar buckets={drawerData.scroll.buckets} />

                  {drawerData.scroll.insight && (
                    <div className="mt-4 rounded-xl border border-violet-400/[0.1] bg-violet-500/[0.04] px-4 py-3">
                      <p className="text-[12px] leading-[1.6] text-slate-300">
                        {drawerData.scroll.insight}
                      </p>
                    </div>
                  )}

                  {/* Context row: the conversion metrics this product has from the parent table */}
                  <div className="mt-5 rounded-xl border border-white/[0.06] bg-white/[0.015] px-4 py-3">
                    <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-600">
                      Conversion context
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5 text-[11px]">
                      <span className="text-slate-500">
                        Views: <span className="font-semibold tabular-nums text-slate-300">{drawerProduct.views.toLocaleString()}</span>
                      </span>
                      <span className="text-slate-500">
                        ATC: <span className="font-semibold tabular-nums text-slate-300">{fmtPct(drawerProduct.atc_rate)}</span>
                      </span>
                      <span className="text-slate-500">
                        Purchases: <span className="font-semibold tabular-nums text-slate-300">{drawerProduct.purchases}</span>
                      </span>
                      <span className="text-slate-500">
                        CVR: <span className="font-semibold tabular-nums text-slate-300">{fmtPct(drawerProduct.cvr)}</span>
                      </span>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
