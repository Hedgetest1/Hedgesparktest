"use client";

import { useEffect, useState } from "react";

// HeatmapCard — Scroll depth visualization from real behavioral data.
//
// WishSpark captures scroll_depth per visitor per product page.
// This component aggregates that into a visual scroll map.
//
// Attacks Microsoft Clarity on their core proposition — but our data
// is connected to conversion outcomes, not just visual session replay.
//
// Sources from: GET /pro/heatmap/top?shop=&hours=

type ScrollBucket = {
  label?: string;
  range?: [number, number];
  visitor_count?: number;
  pct_of_viewers?: number;
};

type ScrollData = {
  total_viewers?: number;
  avg_scroll_depth?: number;
  median_scroll_depth?: number;
  buckets?: ScrollBucket[];
  insight?: string;
};

type ProductHeatmap = {
  product_url?: string;
  total_viewers?: number;
  avg_scroll_depth?: number;
  deep_reader_pct?: number;
  insight?: string;
  buckets?: ScrollBucket[];
};

type HeatmapTopData = {
  products?: ProductHeatmap[];
  window_hours?: number;
  generated_at?: string;
};

const BUCKET_COLORS = [
  { bg: "bg-sky-500/70",     text: "text-sky-300"    },
  { bg: "bg-blue-500/60",    text: "text-blue-300"   },
  { bg: "bg-violet-500/60",  text: "text-violet-300" },
  { bg: "bg-purple-500/70",  text: "text-purple-300" },
];

function shortUrl(url: string | undefined): string {
  if (!url) return "—";
  const slug = url.split("/").filter(Boolean).pop() || url;
  return slug.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()).slice(0, 32);
}

function ScrollDepthBar({ buckets, totalViewers }: { buckets: ScrollBucket[]; totalViewers: number }) {
  if (!buckets || buckets.length === 0 || totalViewers === 0) {
    return (
      <p className="text-[11px] text-slate-600">No scroll data yet for this product.</p>
    );
  }

  return (
    <div className="space-y-2">
      {buckets.map((b, i) => {
        const pct = b.pct_of_viewers ?? 0;
        const colors = BUCKET_COLORS[i % BUCKET_COLORS.length];
        return (
          <div key={`b-${i}`}>
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

export function HeatmapCard({
  apiBase,
  shop,
  apiHeaders,
}: {
  apiBase: string;
  shop: string;
  apiHeaders: () => HeadersInit;
}) {
  const [data, setData] = useState<HeatmapTopData | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<number>(0);

  useEffect(() => {
    if (!shop) return;
    let active = true;

    async function load() {
      try {
        setLoading(true);
        const res = await fetch(
          `${apiBase}/pro/heatmap/top?shop=${encodeURIComponent(shop)}&hours=72`,
          { headers: apiHeaders(), credentials: "include", cache: "no-store" }
        );
        if (!res.ok) return;
        const json = await res.json();
        if (active) setData(json as HeatmapTopData);
      } catch { /* silent */ }
      finally { if (active) setLoading(false); }
    }

    load();
    return () => { active = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop]);

  if (loading) {
    return (
      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5 animate-pulse">
        <div className="h-4 w-32 rounded bg-white/[0.05]" />
        <div className="mt-3 space-y-2">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-6 rounded bg-white/[0.03]" />
          ))}
        </div>
      </div>
    );
  }

  const products = data?.products ?? [];
  const current = products[selected];

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      {/* Header */}
      <div className="mb-4 flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
            Scroll Intelligence
          </div>
          <h3 className="text-[14px] font-semibold text-white">Where visitors stop reading</h3>
          <p className="mt-0.5 text-[11px] text-slate-500">
            Real scroll depth per product — no session replay needed
          </p>
        </div>
        <span className="rounded-full border border-violet-400/30 bg-violet-500/15 px-2 py-0.5 text-[10px] font-semibold text-violet-300">
          Pro
        </span>
      </div>

      {products.length === 0 ? (
        <p className="text-[12px] text-slate-600">
          Scroll data will appear here once visitors start browsing your product pages.
        </p>
      ) : (
        <>
          {/* Product selector tabs */}
          {products.length > 1 && (
            <div className="mb-4 flex gap-1.5 overflow-x-auto pb-1">
              {products.map((p, i) => (
                <button
                  key={`tab-${i}`}
                  onClick={() => setSelected(i)}
                  className={`flex-shrink-0 rounded-lg px-3 py-1.5 text-[11px] font-medium transition-colors ${
                    i === selected
                      ? "bg-violet-600/80 text-white"
                      : "bg-white/[0.04] text-slate-500 hover:bg-white/[0.07] hover:text-slate-300"
                  }`}
                >
                  {shortUrl(p.product_url)}
                </button>
              ))}
            </div>
          )}

          {/* Current product stats */}
          {current && (
            <>
              <div className="mb-3 grid grid-cols-3 gap-2">
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
                  <div className="text-[10px] uppercase text-slate-600">Viewers</div>
                  <div className="mt-0.5 text-[13px] font-semibold text-white">
                    {current.total_viewers?.toLocaleString() ?? "—"}
                  </div>
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
                  <div className="text-[10px] uppercase text-slate-600">Avg Scroll</div>
                  <div className="mt-0.5 text-[13px] font-semibold text-white">
                    {current.avg_scroll_depth != null ? `${current.avg_scroll_depth.toFixed(0)}%` : "—"}
                  </div>
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
                  <div className="text-[10px] uppercase text-slate-600">Full Page</div>
                  <div className="mt-0.5 text-[13px] font-semibold text-white">
                    {current.deep_reader_pct != null ? `${current.deep_reader_pct.toFixed(0)}%` : "—"}
                  </div>
                </div>
              </div>

              {/* Scroll depth bars */}
              <ScrollDepthBar
                buckets={current.buckets ?? []}
                totalViewers={current.total_viewers ?? 0}
              />

              {/* Insight */}
              {current.insight && (
                <div className="mt-3 rounded-xl border border-violet-400/[0.1] bg-violet-500/[0.04] px-3.5 py-3">
                  <p className="text-[12px] leading-[1.6] text-slate-300">{current.insight}</p>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
