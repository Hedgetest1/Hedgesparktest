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
import { apiClient, getHeaders, type paths } from "../lib/api-client";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";

// Generated response type — single source of truth for /pro/segments.
// Regenerate via `npm run api:types` after backend Pydantic changes.
type SegmentsResponse =
  paths["/pro/segments"]["get"]["responses"]["200"]["content"]["application/json"];

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
  // Shop's native currency — each segment's revenue_window is
  // denominated in this currency.
  currency?: string;
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
  isPro = true,
}: {
  apiBase: string;
  shop: string;
  apiHeaders: () => HeadersInit;
  topProducts: { product_url?: string; product_name?: string }[];
  isPro?: boolean;
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
          const headers = getHeaders(apiHeaders);
          const res = isPro
            ? await apiClient.GET("/pro/segments", {
                params: { query: { product_url: productUrl, hours: 72 } },
                headers,
              })
            : await apiClient.GET("/analytics/segments", {
                params: { query: { product_url: productUrl, hours: 72 } },
                headers,
              });
          const json: SegmentsResponse | undefined = res.data as SegmentsResponse | undefined;
          if (json == null) {
            return { product_url: productUrl, segments: [], total_active: 0, error: true };
          }
          // Backend returns hot/warm/cold as top-level keys — now fully typed
          // via the generated SegmentsResponse. Any rename surfaces as a compile error.
          const toSegment = (
            level: string,
            raw: SegmentsResponse["hot"],
          ): Segment => ({
            level,
            visitor_count: raw.visitor_count || 0,
            cvr_estimate: raw.cvr_estimate ?? 0,
            revenue_window: raw.estimated_revenue_window || 0,
            behavioral_index_avg: raw.avg_behavioral_index ?? 0,
          });
          const segs: Segment[] = [
            toSegment("HOT",  json.hot),
            toSegment("WARM", json.warm),
            toSegment("COLD", json.cold),
          ];
          return {
            product_url: productUrl,
            segments: segs,
            total_active: json.total_active_visitors || 0,
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            currency: (json as any).currency as string | undefined,
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
  }, [shop, apiBase, topProducts.length, isPro]);

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
        <div className="h-3 w-40 rounded bg-white/[0.06]" />
        <div className="mt-3 h-24 rounded bg-white/[0.04]" />
      </div>
    );
  }

  // ── Aggregate hot/warm/cold across all products (the hero number) ──
  const rollup = products.reduce(
    (acc, p) => {
      for (const seg of p.segments) {
        if (seg.level === "HOT") acc.hot += seg.visitor_count;
        if (seg.level === "WARM") acc.warm += seg.visitor_count;
        if (seg.level === "COLD") acc.cold += seg.visitor_count;
        acc.revenueWindow += seg.revenue_window;
      }
      acc.totalActive += p.total_active;
      return acc;
    },
    { hot: 0, warm: 0, cold: 0, totalActive: 0, revenueWindow: 0 },
  );

  if (products.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-white/[0.10] bg-white/[0.02] p-6">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#f87171]">
            Live Audience
          </span>
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/[0.08] px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide text-emerald-300">
            <span className="relative inline-flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/60" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
            </span>
            Sample
          </span>
        </div>
        <h3 className="text-[15px] font-bold leading-tight text-slate-200">
          Who&apos;s browsing right now, classified by buying intent
        </h3>
        {/* Sample preview — 3 tier counts at 50% opacity */}
        <div className="mt-4 grid grid-cols-3 gap-3 opacity-50">
          <div className="rounded-xl border px-4 py-3" style={{ borderColor: "rgba(248, 113, 113, 0.25)", backgroundColor: "rgba(248, 113, 113, 0.06)" }}>
            <div className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-rose-400" />
              <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-rose-400">HOT</span>
            </div>
            <div className="mt-1.5 text-[26px] font-extrabold tabular-nums leading-none text-rose-300">12</div>
            <div className="mt-1 text-[10px] text-slate-400">ready to buy</div>
          </div>
          <div className="rounded-xl border px-4 py-3" style={{ borderColor: "rgba(252, 211, 77, 0.25)", backgroundColor: "rgba(252, 211, 77, 0.06)" }}>
            <div className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-300" />
              <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-amber-300">WARM</span>
            </div>
            <div className="mt-1.5 text-[26px] font-extrabold tabular-nums leading-none text-amber-200">48</div>
            <div className="mt-1 text-[10px] text-slate-400">comparing</div>
          </div>
          <div className="rounded-xl border px-4 py-3" style={{ borderColor: "rgba(148, 163, 184, 0.20)", backgroundColor: "rgba(148, 163, 184, 0.04)" }}>
            <div className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
              <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">COLD</span>
            </div>
            <div className="mt-1.5 text-[26px] font-extrabold tabular-nums leading-none text-slate-300">29</div>
            <div className="mt-1 text-[10px] text-slate-400">passing through</div>
          </div>
        </div>
        <p className="mt-4 text-[12px] leading-relaxed text-slate-400">
          Real intent classification activates as visitors browse — every scroll, dwell, and revisit feeds the HOT/WARM/COLD scorer in real time. No cookies, no guessing.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
      {/* Header */}
      <div className="mb-5">
        <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#f87171]">
          Live Audience
        </div>
        <h3 className="mt-1 text-[15px] font-bold leading-tight text-white">
          Who&apos;s browsing right now, classified by buying intent
        </h3>
        <p className="mt-1.5 text-[12px] leading-relaxed text-slate-400">
          {rollup.totalActive > 0
            ? `${rollup.totalActive} active visitors across ${products.length} product${products.length !== 1 ? "s" : ""} right now. Behavioral intent scored in real time from scroll, dwell, and visit patterns — no cookies, no guessing.`
            : `Intent scoring activates as visitors browse. Every scroll, dwell, and revisit feeds the HOT/WARM/COLD classification.`}
        </p>
      </div>

      {/* Aggregate hero — 3 big tier numbers */}
      {rollup.totalActive > 0 && (
        <div className="mb-6 grid grid-cols-3 gap-3">
          <div className="rounded-xl border px-4 py-3" style={{ borderColor: "rgba(248, 113, 113, 0.25)", backgroundColor: "rgba(248, 113, 113, 0.06)" }}>
            <div className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-rose-400 shadow-[0_0_6px_rgba(248,113,113,0.7)]" />
              <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-rose-400">HOT</span>
            </div>
            <div className="mt-1.5 text-[26px] font-extrabold tabular-nums leading-none text-rose-300">
              {rollup.hot}
            </div>
            <div className="mt-1 text-[10px] text-slate-400">ready to buy</div>
          </div>
          <div className="rounded-xl border px-4 py-3" style={{ borderColor: "rgba(252, 211, 77, 0.25)", backgroundColor: "rgba(252, 211, 77, 0.06)" }}>
            <div className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-300 shadow-[0_0_6px_rgba(252,211,77,0.6)]" />
              <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-amber-300">WARM</span>
            </div>
            <div className="mt-1.5 text-[26px] font-extrabold tabular-nums leading-none text-amber-200">
              {rollup.warm}
            </div>
            <div className="mt-1 text-[10px] text-slate-400">considering</div>
          </div>
          <div className="rounded-xl border px-4 py-3" style={{ borderColor: "rgba(148, 163, 184, 0.18)", backgroundColor: "rgba(148, 163, 184, 0.04)" }}>
            <div className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-slate-500" />
              <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">COLD</span>
            </div>
            <div className="mt-1.5 text-[26px] font-extrabold tabular-nums leading-none text-slate-300">
              {rollup.cold}
            </div>
            <div className="mt-1 text-[10px] text-slate-400">browsing casually</div>
          </div>
        </div>
      )}

      {/* Per-product breakdown */}
      <div className="space-y-3">
        <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
          By product
        </div>
        {products.map((p) => (
          <div
            key={p.product_url}
            className="rounded-xl border border-white/[0.05] bg-white/[0.015] p-3.5 transition-colors hover:border-white/[0.1] hover:bg-white/[0.025]"
          >
            {/* Product header */}
            <div className="mb-3 flex items-center justify-between">
              <span className="text-[13px] font-semibold capitalize text-white">
                {shortProduct(p.product_url)}
              </span>
              {p.total_active > 0 && (
                <span className="text-[11px] tabular-nums text-slate-400">
                  {p.total_active} visitor{p.total_active !== 1 ? "s" : ""}
                </span>
              )}
            </div>

            {p.loading ? (
              <div className="h-12 animate-pulse rounded bg-white/[0.04]" />
            ) : p.error ? (
              <div className="text-[12px] text-slate-400">Segment data unavailable</div>
            ) : p.segments.length === 0 ? (
              <div className="text-[12px] text-slate-400">No active visitors in the last 72 hours</div>
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
                                seg.level === "HOT" ? "bg-rose-400/70" :
                                seg.level === "WARM" ? "bg-amber-300/70" :
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
                      <span className="w-16 text-right text-[11px] tabular-nums text-slate-400">
                        {seg.revenue_window > 0
                          ? formatMoneyCompact(seg.revenue_window, p.currency || "USD")
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

      {/* Trust footer */}
      <div className="mt-5 inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3 py-1">
        <span className="h-1.5 w-1.5 rounded-full bg-[#f87171] shadow-[0_0_8px_rgba(248,113,113,0.6)]" />
        <span className="text-[10px] text-slate-400">
          Live intent scoring · behavioral signals only · no personal data
        </span>
      </div>
    </div>
  );
}
