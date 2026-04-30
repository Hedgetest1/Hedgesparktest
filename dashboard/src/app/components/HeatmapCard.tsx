"use client";

import { useEffect, useState } from "react";
import { apiClient, getHeaders, type paths } from "../lib/api-client";

// HeatmapCard — Scroll + Click + Move heatmaps from real behavioral data.
//
// Lite slot 13 (per project_current_partition_state.md). Closes Lucky
// Orange Build $39 / Hotjar Free parity gap which ship all three modes.
//
// HedgeSpark captures:
//  - max_scroll_depth per visitor (existing) → /pro/heatmap/top
//  - x_pct/y_pct on every click + sampled mousemove (tracker v16+) →
//    /pro/heatmap/spatial?event_type=click|move (Redis 10×10 buckets)
//
// Source of truth:
//  - GET /pro/heatmap/top    → HeatmapTopResponse  (scroll quartiles)
//  - GET /pro/heatmap/spatial → SpatialHeatmapResponse (10×10 grid)

type HeatmapTopData =
  paths["/pro/heatmap/top"]["get"]["responses"]["200"]["content"]["application/json"];
type ScrollBucket = HeatmapTopData["products"][number]["buckets"][number];

type SpatialData =
  paths["/pro/heatmap/spatial"]["get"]["responses"]["200"]["content"]["application/json"];

type ViewMode = "scroll" | "click" | "move";

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

// 10×10 spatial grid. Each cell rendered as a div with opacity scaled by
// count / max(count). Hue per mode: violet (click) / sky (move).
function SpatialGrid({
  data,
  mode,
}: {
  data: SpatialData | null;
  mode: "click" | "move";
}) {
  const buckets = data?.buckets ?? [];
  const total = data?.total_events ?? 0;

  if (!buckets.length || total === 0) {
    return (
      <div className="rounded-xl border border-dashed border-white/[0.10] bg-white/[0.015] p-4 text-center">
        <p className="text-[11.5px] leading-relaxed text-slate-400">
          {mode === "click"
            ? "No click data yet for this product. Cells light up the moment your first visitor clicks anywhere on the page."
            : "No mouse-move data yet for this product. Cells light up the moment your first visitor moves their cursor on the page."}
        </p>
      </div>
    );
  }

  // Build a 10×10 sparse map.
  const grid: number[][] = Array.from({ length: 10 }, () => Array(10).fill(0));
  let maxCount = 0;
  for (const b of buckets) {
    if (b.x >= 0 && b.x <= 9 && b.y >= 0 && b.y <= 9) {
      grid[b.y][b.x] = b.count;
      if (b.count > maxCount) maxCount = b.count;
    }
  }

  // Hue: violet (#a78bfa) for click, sky (#38bdf8) for move.
  const baseRgb = mode === "click" ? "167, 139, 250" : "56, 189, 248";

  return (
    <div className="space-y-2">
      <div
        className="relative overflow-hidden rounded-xl border border-white/[0.06] bg-white/[0.02] p-1.5"
        style={{ aspectRatio: "16/9" }}
      >
        <div className="grid h-full w-full grid-cols-10 grid-rows-10 gap-px">
          {Array.from({ length: 10 }).map((_, y) =>
            Array.from({ length: 10 }).map((__, x) => {
              const c = grid[y][x];
              const intensity = maxCount > 0 ? c / maxCount : 0;
              return (
                <div
                  key={`${x}-${y}`}
                  className="rounded-[2px]"
                  style={{
                    backgroundColor: `rgba(${baseRgb}, ${0.05 + intensity * 0.85})`,
                  }}
                  title={c > 0 ? `${c} ${mode === "click" ? "clicks" : "samples"}` : ""}
                />
              );
            })
          )}
        </div>
      </div>
      <div className="flex items-center justify-between text-[11px] text-slate-400">
        <span>
          {mode === "click" ? "Top-left → bottom-right" : "Cursor density"}
        </span>
        <span className="tabular-nums">
          {total.toLocaleString()} {mode === "click" ? "clicks" : "samples"}
          {" · "}
          peak {maxCount.toLocaleString()}/cell
        </span>
      </div>
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
  const [mode, setMode] = useState<ViewMode>("scroll");
  const [spatialClick, setSpatialClick] = useState<SpatialData | null>(null);
  const [spatialMove, setSpatialMove] = useState<SpatialData | null>(null);

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

  const products = data?.products ?? [];
  const current = products[selected];
  const currentUrl = current?.product_url ?? "";

  // Lazy-fetch spatial data when the user switches to click/move tab,
  // scoped to the currently-selected product. Avoids paying the cost
  // until the merchant actually opens the tab.
  useEffect(() => {
    if (!shop || !currentUrl) return;
    if (mode === "scroll") return;
    let active = true;

    async function loadSpatial() {
      try {
        const res = await apiClient.GET("/pro/heatmap/spatial", {
          params: { query: { product_url: currentUrl, event_type: mode } },
          headers: getHeaders(apiHeaders),
        });
        if (!active || res.data == null) return;
        if (mode === "click") setSpatialClick(res.data);
        else setSpatialMove(res.data);
      } catch { /* silent */ }
    }

    loadSpatial();
    return () => { active = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop, currentUrl, mode]);

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

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-4">
        <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">
          Behavior Intelligence
        </div>
        <h3 className="text-[14px] font-semibold text-white">Where visitors stop, click & move</h3>
        <p className="mt-0.5 text-[11px] text-slate-400">
          Scroll depth + click + cursor density per product — no session replay
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
            Scroll + click + move heatmaps populate the moment your first visitor lands on a product page.
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

          {/* Mode tabs — Scroll / Click / Move */}
          <div className="mb-4 inline-flex gap-1 rounded-lg border border-white/[0.06] bg-white/[0.02] p-1">
            {(
              [
                { id: "scroll", label: "Scroll" },
                { id: "click",  label: "Clicks" },
                { id: "move",   label: "Move" },
              ] as { id: ViewMode; label: string }[]
            ).map((m) => (
              <button
                key={m.id}
                onClick={() => setMode(m.id)}
                className={`rounded-md px-3 py-1 text-[11px] font-semibold transition-colors ${
                  mode === m.id
                    ? "bg-violet-600/80 text-white"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>

          {current && (
            <>
              {/* Stats — only meaningful for scroll mode */}
              {mode === "scroll" && (
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
              )}

              {/* Body — depends on mode */}
              {mode === "scroll" && (
                <ScrollDepthBar
                  buckets={current.buckets ?? []}
                  totalViewers={current.total_viewers ?? 0}
                />
              )}
              {mode === "click" && (
                <SpatialGrid data={spatialClick} mode="click" />
              )}
              {mode === "move" && (
                <SpatialGrid data={spatialMove} mode="move" />
              )}

              {/* Insight — scroll only (LLM-free, deterministic) */}
              {mode === "scroll" && current.insight && (
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
