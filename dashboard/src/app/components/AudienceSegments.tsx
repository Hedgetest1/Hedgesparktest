"use client";

/**
 * AudienceSegments — per-product hot/warm/cold visitor breakdown for Pro merchants.
 *
 * Fetches GET /segments?product_url=...&hours=72 for the top products
 * (from the overview data already loaded) and renders a compact segment
 * card showing visitor counts, CVR estimates, and revenue window per segment.
 *
 * Product selection: uses the top 5 products from the parent's topProducts state.
 * If no products are available yet, shows a contextual empty state.
 */

import { useEffect, useState } from "react";

type Segment = {
  level: string;
  visitor_count: number;
  cvr_estimate: number;
  revenue_window: number;
  behavioral_index_avg: number;
};

type ProductSegments = {
  product_url: string;
  segments: Segment[];
  total_active: number;
  loading: boolean;
  error: boolean;
};

function shortProduct(url: string): string {
  const m = url.match(/\/products\/(.+)/);
  return m ? m[1].replace(/-/g, " ") : url;
}

const LEVEL_STYLES: Record<string, { dot: string; text: string }> = {
  HOT:  { dot: "bg-rose-400 shadow-[0_0_5px_rgba(251,113,133,0.6)]", text: "text-rose-300" },
  WARM: { dot: "bg-amber-300 shadow-[0_0_5px_rgba(252,211,77,0.5)]", text: "text-amber-300" },
  COLD: { dot: "bg-slate-500", text: "text-slate-400" },
};

export function AudienceSegments({
  apiBase,
  shop,
  apiHeaders,
  topProducts,
}: {
  apiBase: string;
  shop: string;
  apiHeaders: () => HeadersInit;
  topProducts: { product_url?: string; product_name?: string }[];
}) {
  const [products, setProducts] = useState<ProductSegments[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!shop || !apiBase) return;
    let active = true;

    // Take up to 5 products that have a product_url
    const urls = topProducts
      .filter((p) => p.product_url)
      .map((p) => p.product_url!)
      .slice(0, 5);

    if (urls.length === 0) {
      setProducts([]);
      setLoading(false);
      return;
    }

    // Initialize loading state
    setProducts(urls.map((u) => ({
      product_url: u,
      segments: [],
      total_active: 0,
      loading: true,
      error: false,
    })));
    setLoading(false);

    // Fetch segments for each product in parallel
    Promise.all(
      urls.map(async (productUrl) => {
        try {
          const res = await fetch(
            `${apiBase}/segments?product_url=${encodeURIComponent(productUrl)}&hours=72&shop=${encodeURIComponent(shop)}`,
            { headers: apiHeaders(), credentials: "include", cache: "no-store" }
          );
          if (!res.ok) return { product_url: productUrl, segments: [], total_active: 0, error: true };
          const json = await res.json();
          const segs: Segment[] = (json.segments || []).map((s: Record<string, unknown>) => ({
            level: String(s.level || "COLD"),
            visitor_count: Number(s.visitor_count || 0),
            cvr_estimate: Number(s.cvr_estimate || 0),
            revenue_window: Number(s.revenue_window || 0),
            behavioral_index_avg: Number(s.behavioral_index_avg || 0),
          }));
          return {
            product_url: productUrl,
            segments: segs,
            total_active: Number(json.total_active_visitors || 0),
            error: false,
          };
        } catch {
          return { product_url: productUrl, segments: [], total_active: 0, error: true };
        }
      })
    ).then((results) => {
      if (!active) return;
      setProducts(results.map((r) => ({ ...r, loading: false })));
    });

    return () => { active = false; };
  }, [shop, apiBase, topProducts.length]);

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
        <div className="h-3 w-40 rounded bg-white/[0.06]" />
        <div className="mt-3 h-24 rounded bg-white/[0.04]" />
      </div>
    );
  }

  if (products.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-white/[0.08] bg-white/[0.02] px-6 py-8 text-center">
        <div className="text-sm text-slate-500">
          Audience segments appear once products have active visitor traffic. Each product gets its own hot / warm / cold breakdown based on real behavioral data.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {products.map((p) => (
        <div
          key={p.product_url}
          className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4"
        >
          {/* Product header */}
          <div className="mb-3 flex items-center justify-between">
            <span className="text-[13px] font-medium capitalize text-white">
              {shortProduct(p.product_url)}
            </span>
            {p.total_active > 0 && (
              <span className="text-[11px] tabular-nums text-slate-500">
                {p.total_active} active visitor{p.total_active !== 1 ? "s" : ""}
              </span>
            )}
          </div>

          {p.loading ? (
            <div className="h-12 animate-pulse rounded bg-white/[0.04]" />
          ) : p.error ? (
            <div className="text-[12px] text-slate-600">Segment data unavailable</div>
          ) : p.segments.length === 0 ? (
            <div className="text-[12px] text-slate-600">No active visitors in the last 72 hours</div>
          ) : (
            <div className="space-y-2">
              {p.segments.map((seg) => {
                const style = LEVEL_STYLES[seg.level] || LEVEL_STYLES.COLD;
                const barWidth = p.total_active > 0
                  ? Math.max(4, Math.round((seg.visitor_count / p.total_active) * 100))
                  : 0;
                return (
                  <div key={seg.level} className="flex items-center gap-3">
                    <span className={`h-2 w-2 flex-shrink-0 rounded-full ${style.dot}`} />
                    <span className={`w-12 text-[11px] font-semibold ${style.text}`}>
                      {seg.level}
                    </span>
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
                          <div
                            className={`h-full rounded-full ${
                              seg.level === "HOT" ? "bg-rose-400/60" :
                              seg.level === "WARM" ? "bg-amber-300/60" :
                              "bg-slate-600/60"
                            }`}
                            style={{ width: `${barWidth}%` }}
                          />
                        </div>
                        <span className="w-8 text-right text-[11px] tabular-nums text-slate-400">
                          {seg.visitor_count}
                        </span>
                      </div>
                    </div>
                    <span className="w-16 text-right text-[11px] tabular-nums text-slate-500">
                      {seg.revenue_window > 0
                        ? `$${Math.round(seg.revenue_window)}`
                        : "—"}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
