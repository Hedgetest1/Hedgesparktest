"use client";

import { useEffect, useState } from "react";
import { apiClient, getHeaders, type paths } from "../lib/api-client";

// HeatmapCard — Scroll depth visualization from real behavioral data.
//
// HedgeSpark captures scroll_depth per visitor per product page.
// This component aggregates that into a visual scroll map.
//
// Attacks Microsoft Clarity on their core proposition — but our data
// is connected to conversion outcomes, not just visual session replay.
//
// Source of truth: GET /pro/heatmap/top → HeatmapTopResponse (fully typed).

type HeatmapTopData =
  paths["/pro/heatmap/top"]["get"]["responses"]["200"]["content"]["application/json"];
type ScrollBucket = HeatmapTopData["products"][number]["buckets"][number];

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
      <p className="text-[11px] text-slate-400">No scroll data yet for this product.</p>
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
              <span className="text-[10px] text-slate-400">{b.label}</span>
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
  apiBase: _apiBase,
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
        const res = await apiClient.GET("/pro/heatmap/top", {
          params: { query: { hours: 72 } },
          headers: getHeaders(apiHeaders),
        });
        if (active && res.data != null) setData(res.data);
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
      {/* Header — internal Pro badge removed. Pro context is owned by the
          parent SectionHeading / Pro Intelligence zone. */}
      <div className="mb-4">
        <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">
          Scroll Intelligence
        </div>
        <h3 className="text-[14px] font-semibold text-white">Where visitors stop reading</h3>
        <p className="mt-0.5 text-[11px] text-slate-400">
          Real scroll depth per product — no session replay needed
        </p>
      </div>

      {products.length === 0 ? (
        <div className="rounded-xl border border-dashed border-white/[0.10] bg-white/[0.015] p-4">
          <div className="mb-2 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.14em] text-slate-400">
            <span className="relative inline-flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-violet-400/50" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-violet-400" />
            </span>
            Sample preview
          </div>
          <div className="opacity-50">
            <div className="mb-2 text-[11px] text-slate-400">
              Sample product · 86 sessions
            </div>
            <div className="space-y-1">
              {[
                { pct: "0–25%", count: 86, intensity: 100 },
                { pct: "25–50%", count: 64, intensity: 74 },
                { pct: "50–75%", count: 41, intensity: 48 },
                { pct: "75–100%", count: 18, intensity: 21 },
              ].map((band) => (
                <div key={band.pct} className="flex items-center gap-2">
                  <div className="w-[60px] flex-shrink-0 text-[11px] text-slate-400">{band.pct}</div>
                  <div className="relative flex-1 overflow-hidden rounded bg-white/[0.04]">
                    <div
                      className="h-5 rounded"
                      style={{
                        width: `${band.intensity}%`,
                        background: "linear-gradient(90deg, #a78bfacc 0%, #a78bfa66 100%)",
                      }}
                    />
                  </div>
                  <div className="w-[40px] flex-shrink-0 text-right text-[11px] tabular-nums text-slate-300">
                    {band.count}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <p className="mt-3 text-[12px] leading-relaxed text-slate-400">
            Scroll depth per product — no session replay needed. Real numbers populate the moment your first visitor scrolls a product page.
          </p>
        </div>
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
                  <div className="text-[10px] uppercase text-slate-400">Viewers</div>
                  <div className="mt-0.5 text-[13px] font-semibold text-white">
                    {current.total_viewers?.toLocaleString() ?? "—"}
                  </div>
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
                  <div className="text-[10px] uppercase text-slate-400">Avg Scroll</div>
                  <div className="mt-0.5 text-[13px] font-semibold text-white">
                    {current.avg_scroll_depth != null ? `${current.avg_scroll_depth.toFixed(0)}%` : "—"}
                  </div>
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
                  <div className="text-[10px] uppercase text-slate-400">Full Page</div>
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
