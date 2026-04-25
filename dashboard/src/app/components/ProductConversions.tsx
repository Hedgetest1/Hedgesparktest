"use client";

/**
 * ProductConversions — per-product sortable conversion table.
 *
 * Shows views, cart rate, purchases, revenue and conversion rate for
 * each tracked product, with a 7d/30d toggle. Clicking a row opens a
 * drawer with the scroll-depth heatmap for that product so the
 * merchant can see WHERE visitors stop reading the product page.
 *
 * Data sources:
 *   - GET /orders/product-conversions (main table)
 *   - GET /pro/heatmap                 (scroll heatmap per product)
 */

import { useEffect, useState } from "react";
import { apiClient, type paths } from "../lib/api-client";
import { reportFrontendError } from "../lib/error-reporter";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
} from "./DetailDrawer";

// Source of truth: GET /orders/product-conversions → ProductConversionsResponse.
type ConversionData =
  paths["/orders/product-conversions"]["get"]["responses"]["200"]["content"]["application/json"];
type ProductRow = ConversionData["products"][number];

// Source of truth: GET /pro/heatmap → HeatmapResponse (single-product scroll).
type HeatmapData =
  paths["/pro/heatmap"]["get"]["responses"]["200"]["content"]["application/json"];
type ScrollBucket = HeatmapData["scroll"]["buckets"][number];

type SortKey = "revenue" | "views" | "purchases" | "cvr" | "atc_rate";

const BUCKET_COLORS = [
  { bg: "#38bdf8", text: "#7dd3fc" },
  { bg: "#60a5fa", text: "#93c5fd" },
  { bg: "#8b5cf6", text: "#c4b5fd" },
  { bg: "#a855f7", text: "#d8b4fe" },
];

function ScrollDepthBars({ buckets }: { buckets: ScrollBucket[] }) {
  if (!buckets || buckets.length === 0) {
    return (
      <p style={{ color: "#94a3b8", fontSize: "12px" }}>
        No scroll data yet for this product.
      </p>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {buckets.map((b, i) => {
        const pct = b.pct_of_viewers ?? 0;
        const colors = BUCKET_COLORS[i % BUCKET_COLORS.length];
        return (
          <div key={`scroll-b-${i}`}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "4px",
              }}
            >
              <span style={{ color: "#94a3b8", fontSize: "11px" }}>{b.label}</span>
              <span
                style={{
                  color: colors.text,
                  fontSize: "12px",
                  fontWeight: 600,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {pct.toFixed(0)}%
                <span style={{ color: "#94a3b8", fontWeight: 400, marginLeft: "6px" }}>
                  ({b.visitor_count?.toLocaleString()} visitors)
                </span>
              </span>
            </div>
            <div
              style={{
                height: "8px",
                width: "100%",
                background: "rgba(148,163,184,0.08)",
                borderRadius: "4px",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${Math.min(100, pct)}%`,
                  height: "100%",
                  background: colors.bg,
                  borderRadius: "4px",
                  transition: "width 0.5s cubic-bezier(0.16,1,0.3,1)",
                }}
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
  const [days, setDays] = useState(7);
  const [sortKey, setSortKey] = useState<SortKey>("revenue");

  const { data, state, retry } = useCardFetch<ConversionData>({
    url: `${apiBase}/orders/product-conversions?days=${days}`,
    enabled: !!shop && !!apiBase,
    isEmpty: (d) => !d.has_data || !d.products?.length,
  });

  // Drawer state: fetches /pro/heatmap for the selected product.
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
      .catch((err: unknown) => {
        if (!active) return;
        setDrawerError(true);
        const e = err as { name?: string; message?: string } | null;
        reportFrontendError({
          component: "ProductConversionsDrawer",
          error_type: (e && e.name) || "HeatmapFetchError",
          message: (e && e.message) || "product heatmap fetch failed",
          severity: "warning",
        });
      })
      .finally(() => {
        if (active) setDrawerLoading(false);
      });
    return () => {
      active = false;
    };
  }, [drawerProduct]);

  if (state === "loading") {
    return <CardSkeleton label="Loading your product conversion table" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Product conversions unavailable"
        message="We couldn't load your product conversion table. Your order history is safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="violet"
        title="Product conversions warming up"
        body="This table shows views, cart rate, purchases, revenue and conversion rate per product — straight from your real order data, not estimates. It lights up once enriched order data has had time to flow through."
        eta="Needs ~24h after install"
      />
    );
  }

  const sorted = [...data.products].sort((a, b) => {
    const av = a[sortKey] ?? 0;
    const bv = b[sortKey] ?? 0;
    return bv - av;
  });

  const c = data.currency;

  function headerBtn(label: string, key: SortKey) {
    const activeSort = sortKey === key;
    return (
      <button
        type="button"
        onClick={() => setSortKey(key)}
        className={`text-right text-[11px] font-bold uppercase tracking-[0.1em] transition-colors focus:outline-none focus-visible:text-[#e8a04e] ${
          activeSort ? "text-[#e8a04e]" : "text-slate-500 hover:text-slate-300"
        }`}
      >
        {label}
        {activeSort ? " ↓" : ""}
      </button>
    );
  }

  const scroll = drawerData?.scroll;

  return (
    <>
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-6">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-[#e8a04e]">
              Product conversions
            </div>
            <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
              Which products actually sell
            </h3>
            <p className="mt-2 text-[14px] leading-relaxed text-slate-400">
              Real order data for every tracked product. Click a row to see where visitors stop
              reading.
            </p>
          </div>
          <div className="flex flex-shrink-0 gap-1.5">
            {[7, 30].map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDays(d)}
                className={`rounded-lg px-3 py-1.5 text-[13px] font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[#e8a04e] ${
                  days === d
                    ? "bg-[#e8a04e]/15 text-[#e8a04e]"
                    : "text-slate-500 hover:bg-white/[0.05] hover:text-slate-300"
                }`}
                aria-pressed={days === d}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-[14px]">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="pb-3 pr-3 text-[11px] font-bold uppercase tracking-[0.1em] text-slate-400">
                  Product
                </th>
                <th className="pb-3 px-2">{headerBtn("Views", "views")}</th>
                <th className="pb-3 px-2">{headerBtn("Cart rate", "atc_rate")}</th>
                <th className="pb-3 px-2">{headerBtn("Purchases", "purchases")}</th>
                <th className="pb-3 px-2">{headerBtn("Revenue", "revenue")}</th>
                <th className="pb-3 pl-2">{headerBtn("Conversion", "cvr")}</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p, i) => (
                <tr
                  key={`${p.product_url}-${i}`}
                  role="button"
                  tabIndex={0}
                  aria-haspopup="dialog"
                  aria-label={`Open scroll depth for ${shortName(p.product_name)}`}
                  onClick={() => setDrawerProduct(p)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setDrawerProduct(p);
                    }
                  }}
                  className="cursor-pointer border-t border-white/[0.04] transition-colors hover:bg-white/[0.03] focus:outline-none focus-visible:bg-white/[0.05] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#e8a04e]"
                >
                  <td className="py-3 pr-3">
                    <span className="inline-flex items-center gap-2 text-[13px] text-slate-300">
                      {shortName(p.product_name)}
                      <span className="text-[11px] text-slate-400" aria-hidden="true">
                        ›
                      </span>
                    </span>
                  </td>
                  <td className="py-3 px-2 text-right tabular-nums text-slate-400">
                    {p.views.toLocaleString()}
                  </td>
                  <td className="py-3 px-2 text-right tabular-nums text-slate-400">
                    {fmtPct(p.atc_rate)}
                  </td>
                  <td className="py-3 px-2 text-right tabular-nums text-slate-400">{p.purchases}</td>
                  <td className="py-3 px-2 text-right font-semibold tabular-nums text-emerald-300">
                    {fmtCurrency(p.revenue, c)}
                  </td>
                  <td className="py-3 pl-2 text-right">
                    <span
                      className={`font-semibold tabular-nums ${
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

      <DetailDrawer
        open={!!drawerProduct}
        onClose={() => setDrawerProduct(null)}
        icon="📖"
        title={drawerProduct ? shortName(drawerProduct.product_name) : ""}
        subtitle="Where visitors stop reading · last 72 hours"
        widthPx={560}
      >
        {drawerLoading && (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            <div
              style={{
                height: "80px",
                borderRadius: "12px",
                background: "rgba(148,163,184,0.06)",
                animation: "pulse 1.5s ease-in-out infinite",
              }}
            />
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                style={{
                  height: "32px",
                  borderRadius: "8px",
                  background: "rgba(148,163,184,0.04)",
                  animation: "pulse 1.5s ease-in-out infinite",
                }}
              />
            ))}
          </div>
        )}

        {drawerError && !drawerLoading && (
          <div
            style={{
              padding: "14px 16px",
              borderRadius: "12px",
              background: "rgba(245,158,11,0.06)",
              border: "1px solid rgba(245,158,11,0.25)",
              color: "#fcd34d",
              fontSize: "13px",
              lineHeight: 1.55,
            }}
          >
            Scroll depth data isn&apos;t available for this product yet. We need at least a handful
            of visitors to read far enough down the page before the heatmap can stand on its own.
          </div>
        )}

        {!drawerLoading && !drawerError && scroll && drawerProduct && (
          <>
            <DrawerExplainer
              body={
                "This is how far your visitors actually scrolled down this product page in the last " +
                "72 hours. Each bar is a depth band — the taller it is, the more visitors reached " +
                "that point. If the graph collapses early, your pitch is ending before the visitor's " +
                "attention does."
              }
              why={
                "A page that converts well almost always has visitors reaching the bottom. If most of " +
                "them bail at 25% or 50%, the section below that point is being ignored — moving the " +
                "key product value higher can unlock the same traffic you already paid for."
              }
            />

            <DrawerBigStat
              label="Average scroll depth"
              value={`${scroll.avg_scroll_depth.toFixed(0)}%`}
              sublabel={`${scroll.total_viewers.toLocaleString()} viewers · median ${scroll.median_scroll_depth.toFixed(
                0,
              )}%`}
              color="#a78bfa"
            />

            <DrawerKeyValueList
              items={[
                {
                  label: "Views (table window)",
                  value: drawerProduct.views.toLocaleString(),
                },
                {
                  label: "Cart rate",
                  value: fmtPct(drawerProduct.atc_rate),
                },
                {
                  label: "Purchases",
                  value: `${drawerProduct.purchases}`,
                },
                {
                  label: "Conversion rate",
                  value: fmtPct(drawerProduct.cvr),
                  color:
                    drawerProduct.cvr >= 0.03
                      ? "#10b981"
                      : drawerProduct.cvr >= 0.01
                      ? "#fbbf24"
                      : "#94a3b8",
                },
                {
                  label: "Revenue",
                  value: fmtCurrency(drawerProduct.revenue, c),
                  color: "#10b981",
                },
              ]}
            />

            <DrawerSectionHeading>Scroll depth breakdown</DrawerSectionHeading>
            <ScrollDepthBars buckets={scroll.buckets} />

            {scroll.insight && (
              <div
                style={{
                  marginTop: "18px",
                  padding: "14px 16px",
                  borderRadius: "12px",
                  background: "rgba(139,92,246,0.05)",
                  border: "1px solid rgba(139,92,246,0.2)",
                  color: "#ddd6fe",
                  fontSize: "13px",
                  lineHeight: 1.55,
                }}
              >
                {scroll.insight}
              </div>
            )}
          </>
        )}
      </DetailDrawer>
    </>
  );
}
