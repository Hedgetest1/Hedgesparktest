"use client";

/**
 * LiveRadarMap — Geo-radar + world map for live visitors.
 * Extracted from app/page.tsx (Phase Ω⁶ split).
 *
 * Self-contained: ships with its own demo visitors + LAND_PATHS
 * (Natural Earth 110m) so extracting it never bloats page.tsx again.
 *
 * 2026-04-26 (B-super F5): extended with orders-by-country mode.
 * The radar surface IS the geo surface — no separate Geo tile.
 * Toggle between live-visitors and last-N-days-orders. Both data
 * sources reuse the existing visitor_geo Redis cache; no schema
 * migration on shop_orders. Centroids hardcoded for top 60
 * Shopify-relevant countries.
 */

import { useState } from "react";
import { intentDotClass } from "../_lib/formatters";
import { formatMoneyCompact } from "../_lib/formatters";

export type LiveVisitorShape = {
  visitor_id?: string;
  url?: string;
  intent_level?: string;
  intent_score?: number;
  dwell_seconds?: number;
  country?: string;
  country_code?: string;
  city?: string;
  lat?: number;
  lon?: number;
};

export type OrderCountryAggregate = {
  country_code: string;
  orders: number;
  revenue: number;
};

function geoToMapXY(lat: number, lon: number): { x: number; y: number } {
  const x = ((lon + 180) / 360) * 900;
  const y = ((90 - lat) / 180) * 450;
  return { x, y };
}

// Country centroids — top 60 by Shopify ecom relevance. ISO-3166-1
// alpha-2 → [lat, lon]. Anything outside this set falls back to the
// "Others" pool that renders as a single mid-Atlantic marker so
// 100% of orders still surface visually.
const COUNTRY_CENTROID: Record<string, [number, number]> = {
  US: [39.83, -98.58], CA: [56.13, -106.34], MX: [23.63, -102.55],
  GB: [54.0, -2.0],    DE: [51.16, 10.45],   FR: [46.6, 1.89],
  IT: [42.5, 12.5],    ES: [40.46, -3.74],   NL: [52.13, 5.29],
  BE: [50.5, 4.47],    CH: [46.82, 8.23],    AT: [47.52, 14.55],
  SE: [60.13, 18.64],  NO: [60.47, 8.47],    DK: [56.26, 9.50],
  FI: [61.92, 25.75],  IE: [53.41, -8.24],   PT: [39.4, -8.22],
  PL: [51.92, 19.13],  CZ: [49.82, 15.47],   GR: [39.07, 21.82],
  AU: [-25.27, 133.78], NZ: [-40.9, 174.88], JP: [36.20, 138.25],
  KR: [35.91, 127.77], CN: [35.86, 104.19],  HK: [22.32, 114.17],
  SG: [1.35, 103.82],  TW: [23.69, 120.96],  IN: [20.59, 78.96],
  AE: [23.42, 53.85],  IL: [31.05, 34.85],   TR: [38.96, 35.24],
  SA: [23.88, 45.07],  BR: [-14.23, -51.92], AR: [-38.42, -63.62],
  CL: [-35.67, -71.54], CO: [4.57, -74.30],  PE: [-9.19, -75.01],
  ZA: [-30.56, 22.94], EG: [26.82, 30.80],   NG: [9.08, 8.67],
  MA: [31.79, -7.09],  KE: [-0.02, 37.91],   RU: [61.52, 105.32],
  UA: [48.38, 31.17],  RO: [45.94, 24.97],   HU: [47.16, 19.50],
  BG: [42.73, 25.49],  HR: [45.10, 15.20],   SK: [48.67, 19.70],
  EE: [58.60, 25.01],  LV: [56.88, 24.60],   LT: [55.17, 23.88],
  TH: [15.87, 100.99], VN: [14.06, 108.28],  PH: [12.88, 121.77],
  MY: [4.21, 101.98],  ID: [-0.79, 113.92],
};
const FALLBACK_CENTROID: [number, number] = [25.0, -30.0];  // mid-Atlantic

const DEMO_VISITORS: LiveVisitorShape[] = [
  { visitor_id: "demo-1", url: "/products/silk-pillowcase", intent_level: "HOT", dwell_seconds: 45, country: "United States", country_code: "US", city: "New York", lat: 40.71, lon: -74.0 },
  { visitor_id: "demo-2", url: "/products/ceramic-mug", intent_level: "WARM", dwell_seconds: 22, country: "United Kingdom", country_code: "GB", city: "London", lat: 51.51, lon: -0.13 },
  { visitor_id: "demo-3", url: "/products/candle-trio", intent_level: "HOT", dwell_seconds: 38, country: "Germany", country_code: "DE", city: "Berlin", lat: 52.52, lon: 13.41 },
  { visitor_id: "demo-4", url: "/collections/home", intent_level: "COLD", dwell_seconds: 8, country: "Japan", country_code: "JP", city: "Tokyo", lat: 35.68, lon: 139.69 },
  { visitor_id: "demo-5", url: "/products/throw-blanket", intent_level: "WARM", dwell_seconds: 18, country: "Australia", country_code: "AU", city: "Sydney", lat: -33.87, lon: 151.21 },
  { visitor_id: "demo-6", url: "/products/linen-shirt", intent_level: "HOT", dwell_seconds: 52, country: "Italy", country_code: "IT", city: "Milan", lat: 45.46, lon: 9.19 },
];

// LAND_PATHS (Natural Earth 110m) is imported from a separate data file to
// keep this component under 300 lines. The file is ~40KB of polygon data.
import { LAND_PATHS } from "./LiveRadarMap.data";

export function LiveRadarMap({
  visitors: realVisitors,
  radarPositions,
  coldStartPhase,
  orderAggregates,
  ordersCurrency,
  ordersWindowDays,
}: {
  visitors: LiveVisitorShape[];
  radarPositions: string[];
  coldStartPhase: number;
  /** Aggregated orders by country for the orders mode. Optional —
   *  when empty/undefined the toggle is hidden and behavior is
   *  identical to the pre-2026-04-26 version (live visitors only). */
  orderAggregates?: OrderCountryAggregate[];
  ordersCurrency?: string;
  ordersWindowDays?: number;
}) {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [demoMode, setDemoMode] = useState(false);
  const [mode, setMode] = useState<"visitors" | "orders">("visitors");
  const [selectedCountry, setSelectedCountry] = useState<string | null>(null);

  const visitors = realVisitors.length > 0 ? realVisitors : demoMode ? DEMO_VISITORS : [];
  const hasOrders = (orderAggregates?.length ?? 0) > 0;
  const showOrders = mode === "orders" && hasOrders;
  // Orders mode forces the world map to be visible (otherwise it's
  // collapsed unless a visitor is selected — wrong UX for geo aggregate).
  const isExpanded = showOrders || selectedIdx !== null;
  const selectedVisitor = selectedIdx !== null ? visitors[selectedIdx] : null;

  // Compute scale factor for orders dots — sqrt scaling so the
  // largest market doesn't dwarf small ones into invisibility.
  const maxOrders = Math.max(1, ...(orderAggregates ?? []).map(a => a.orders));

  function handleDotClick(i: number) {
    setSelectedIdx((prev) => (prev === i ? null : i));
  }

  return (
    <div className="relative overflow-hidden rounded-3xl border border-cyan-400/10 bg-[#08080f]" style={{ minHeight: 400 }}>
      <style>{`
        @keyframes radar-sweep { from { transform: translate(-50%,-50%) rotate(0deg); } to { transform: translate(-50%,-50%) rotate(360deg); } }
        @keyframes ripple-out { from { r: 5; opacity: 0.6; } to { r: 35; opacity: 0; } }
      `}</style>

      {/* Mode toggle — only visible when order data is available. The
          map IS the geo surface; we simply switch what's plotted. */}
      {hasOrders && (
        <div className="absolute right-3 top-3 z-10 flex rounded-xl border border-white/[0.06] bg-black/60 p-0.5 backdrop-blur-sm">
          <button
            type="button"
            onClick={() => { setMode("visitors"); setSelectedCountry(null); }}
            className={`rounded-lg px-3 py-1.5 text-[11px] font-bold uppercase tracking-[0.08em] transition-all ${
              mode === "visitors" ? "bg-cyan-500/20 text-cyan-300" : "text-slate-400 hover:text-slate-200"
            }`}
            aria-label="Show live visitors"
          >Live visitors</button>
          <button
            type="button"
            onClick={() => { setMode("orders"); setSelectedIdx(null); }}
            className={`rounded-lg px-3 py-1.5 text-[11px] font-bold uppercase tracking-[0.08em] transition-all ${
              mode === "orders" ? "bg-emerald-500/20 text-emerald-300" : "text-slate-400 hover:text-slate-200"
            }`}
            aria-label={`Show orders by country, last ${ordersWindowDays || 30} days`}
          >Orders · {ordersWindowDays || 30}d</button>
        </div>
      )}

      <div className="flex h-full" style={{ minHeight: 400 }}>

        <div
          className="relative overflow-hidden transition-all duration-700 ease-[cubic-bezier(0.16,1,0.3,1)]"
          style={{ width: isExpanded ? (showOrders ? "100%" : "60%") : "0%", opacity: isExpanded ? 1 : 0 }}
        >
          <svg viewBox="0 0 900 450" className="absolute inset-0 h-full w-full" preserveAspectRatio="xMidYMid meet">
            {LAND_PATHS.map((d, i) => (
              <path key={`lp${i}`} d={d} fill="rgba(34,211,238,0.07)" stroke="rgba(34,211,238,0.2)" strokeWidth="0.8" strokeLinejoin="round" />
            ))}

            {showOrders && (orderAggregates ?? []).map((a) => {
              const centroid = COUNTRY_CENTROID[a.country_code] ?? FALLBACK_CENTROID;
              const { x, y } = geoToMapXY(centroid[0], centroid[1]);
              const r = 6 + Math.sqrt(a.orders / maxOrders) * 22;
              const isSel = selectedCountry === a.country_code;
              const color = "#34d399";  // emerald — orders/revenue (good signal)
              return (
                <g key={`oc-${a.country_code}`} className="cursor-pointer"
                   onClick={() => setSelectedCountry(prev => prev === a.country_code ? null : a.country_code)}>
                  <circle cx={x} cy={y} r={r} fill={`${color}33`} stroke={color} strokeWidth={isSel ? 2 : 1}
                          style={{ filter: `drop-shadow(0 0 ${isSel ? 14 : 6}px ${color})`, transition: "all 0.3s" }} />
                  <text x={x} y={y + 4} textAnchor="middle" fill="white" fontSize="10" fontWeight="700"
                        style={{ pointerEvents: "none" }}>{a.country_code}</text>
                  {isSel && (
                    <g style={{ pointerEvents: "none" }}>
                      <rect x={x + r + 6} y={y - 22} width={140} height={42} rx="6"
                            fill="rgba(0,0,0,0.85)" stroke={color} strokeWidth="1" />
                      <text x={x + r + 14} y={y - 6} fill="white" fontSize="11" fontWeight="700">
                        {a.orders} order{a.orders !== 1 ? "s" : ""}
                      </text>
                      <text x={x + r + 14} y={y + 12} fill={color} fontSize="13" fontWeight="700">
                        {formatMoneyCompact(a.revenue, ordersCurrency || "USD")}
                      </text>
                    </g>
                  )}
                </g>
              );
            })}

            {!showOrders && visitors.map((v, i) => {
              if (!v.lat || !v.lon) return null;
              const { x, y } = geoToMapXY(v.lat, v.lon);
              const isSel = selectedIdx === i;
              const color = v.intent_level === "HOT" ? "#fb7185" : v.intent_level === "WARM" ? "#fcd34d" : "#94a3b8";
              return (
                <g key={`mp-${i}`}>
                  {isSel && (
                    <>
                      <circle cx={x} cy={y} fill="none" stroke={color} strokeWidth="1.5" r="5" opacity="0">
                        <animate attributeName="r" values="5;35" dur="2s" repeatCount="indefinite" />
                        <animate attributeName="opacity" values="0.7;0" dur="2s" repeatCount="indefinite" />
                      </circle>
                      <circle cx={x} cy={y} fill="none" stroke={color} strokeWidth="1" r="5" opacity="0">
                        <animate attributeName="r" values="5;35" dur="2s" begin="0.7s" repeatCount="indefinite" />
                        <animate attributeName="opacity" values="0.5;0" dur="2s" begin="0.7s" repeatCount="indefinite" />
                      </circle>
                    </>
                  )}
                  {!isSel && (
                    <circle cx={x} cy={y} fill="none" stroke={color} strokeWidth="0.5" r="4" opacity="0">
                      <animate attributeName="r" values="4;15" dur="3s" repeatCount="indefinite" />
                      <animate attributeName="opacity" values="0.4;0" dur="3s" repeatCount="indefinite" />
                    </circle>
                  )}
                  <circle
                    cx={x} cy={y}
                    r={isSel ? 8 : 4}
                    fill={color}
                    stroke={isSel ? "white" : "none"}
                    strokeWidth={isSel ? 3 : 0}
                    style={{ filter: `drop-shadow(0 0 ${isSel ? 20 : 8}px ${color})`, transition: "all 0.4s ease-out" }}
                    className="cursor-pointer"
                    onClick={() => handleDotClick(i)}
                  />
                  {isSel && v.city && (() => {
                    const label = `${v.city}, ${v.country_code}`;
                    const labelW = label.length * 7.5 + 20;
                    return (
                      <g style={{ transition: "opacity 0.3s", opacity: 1 }}>
                        <rect x={x + 14} y={y - 16} width={labelW} height={28} rx="8" fill="rgba(0,0,0,0.85)" stroke={color} strokeWidth="1" />
                        <text x={x + 24} y={y + 2} fill="white" fontSize="13" fontWeight="700" fontFamily="system-ui,sans-serif">{label}</text>
                      </g>
                    );
                  })()}
                </g>
              );
            })}
          </svg>

          {selectedVisitor && (
            <div className="absolute bottom-4 left-4 right-4 rounded-2xl border border-white/[0.08] bg-black/70 px-5 py-4 backdrop-blur-md">
              <div className="flex items-center gap-3">
                <span className={`h-4 w-4 flex-shrink-0 rounded-full ${intentDotClass(selectedVisitor.intent_level)}`} />
                <div className="min-w-0 flex-1">
                  <div className="text-[16px] font-bold text-white">
                    {selectedVisitor.city ? `${selectedVisitor.city}, ${selectedVisitor.country}` : "Unknown location"}
                  </div>
                  <div className="truncate text-[14px] text-slate-400">{selectedVisitor.url}</div>
                </div>
                <span className={`flex-shrink-0 rounded-lg px-3 py-1.5 text-[13px] font-bold uppercase ${
                  selectedVisitor.intent_level === "HOT" ? "bg-rose-500/20 text-rose-300" :
                  selectedVisitor.intent_level === "WARM" ? "bg-amber-500/20 text-amber-300" :
                  "bg-white/10 text-slate-400"
                }`}>{selectedVisitor.intent_level}</span>
              </div>
            </div>
          )}
        </div>

        <div
          className="relative flex-1 transition-all duration-700 ease-[cubic-bezier(0.16,1,0.3,1)]"
          style={{
            flexBasis: showOrders ? "0%" : (isExpanded ? "40%" : "100%"),
            opacity: showOrders ? 0 : 1,
          }}
        >
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(56,189,248,0.08),transparent_40%)]" />

          <div className="absolute inset-0 flex items-center justify-center">
            <div className={`relative rounded-full border border-cyan-400/12 transition-all duration-700 ${isExpanded ? "h-[180px] w-[180px]" : "h-[240px] w-[240px]"}`}>
              <div className="absolute inset-[22%] rounded-full border border-cyan-400/8" />
              <div className="absolute inset-[44%] rounded-full border border-cyan-400/5" />
              <div className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-cyan-400/[0.04]" />
              <div className="absolute left-0 top-1/2 h-px w-full -translate-y-1/2 bg-cyan-400/[0.04]" />

              <div className="absolute left-1/2 top-1/2 h-1/2 w-px origin-bottom" style={{ animation: "radar-sweep 4s linear infinite", background: "linear-gradient(to top, transparent, rgba(34,211,238,0.35))" }} />

              <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
                <div className="h-3 w-3 rounded-full bg-cyan-400/30" />
                <div className="absolute inset-0 animate-ping rounded-full bg-cyan-400/20" style={{ animationDuration: "3s" }} />
              </div>

              {visitors.slice(0, 8).map((v, i) => (
                <div
                  key={`rd-${v.visitor_id || i}`}
                  className={`absolute ${radarPositions[i % radarPositions.length]} -translate-x-1/2 -translate-y-1/2 cursor-pointer`}
                  onClick={() => handleDotClick(i)}
                >
                  <div className={`rounded-full transition-all duration-300 ${
                    selectedIdx === i ? "h-6 w-6 ring-[3px] ring-white/60" : "h-4 w-4"
                  } ${intentDotClass(v.intent_level)}`} />
                </div>
              ))}
            </div>
          </div>

          <div className="absolute left-4 top-4 flex items-center gap-2 rounded-xl bg-black/40 px-4 py-2 backdrop-blur-sm">
            <div className="relative h-2.5 w-2.5">
              <div className="absolute inset-0 rounded-full bg-cyan-400" />
              <div className="absolute inset-0 animate-ping rounded-full bg-cyan-400/40" style={{ animationDuration: "2s" }} />
            </div>
            <span className="text-[16px] font-bold text-cyan-300">{visitors.length || 0}</span>
            <span className="text-[14px] text-slate-400">{visitors.length > 0 ? "live" : ""}</span>
            {demoMode && <span className="rounded bg-amber-500/20 px-2 py-0.5 text-[10px] font-bold text-amber-300">DEMO</span>}
          </div>

          <div className="absolute right-3 top-3 flex flex-col gap-1.5 rounded-lg bg-black/30 px-2.5 py-2 backdrop-blur-sm">
            <span className="flex items-center gap-1.5 text-[11px]"><span className="h-2.5 w-2.5 rounded-full bg-rose-400 shadow-[0_0_6px_rgba(251,113,133,0.5)]" /><span className="text-slate-400">Hot</span></span>
            <span className="flex items-center gap-1.5 text-[11px]"><span className="h-2.5 w-2.5 rounded-full bg-amber-300 shadow-[0_0_6px_rgba(252,211,77,0.5)]" /><span className="text-slate-400">Warm</span></span>
            <span className="flex items-center gap-1.5 text-[11px]"><span className="h-2.5 w-2.5 rounded-full bg-slate-400" /><span className="text-slate-400">Cold</span></span>
          </div>

          {visitors.length === 0 && (
            <div className="absolute inset-x-0 bottom-5 flex flex-col items-center gap-3 text-center">
              <p className="text-[15px] font-semibold text-slate-400">
                {coldStartPhase <= 1 ? "Scanning..." : "No visitors right now"}
              </p>
              <button
                onClick={() => setDemoMode(true)}
                className="rounded-xl border border-cyan-400/20 bg-cyan-500/10 px-5 py-2 text-[14px] font-semibold text-cyan-300 transition-all hover:bg-cyan-500/20"
              >
                Preview with demo data
              </button>
            </div>
          )}

          {visitors.length > 0 && selectedIdx === null && (
            <div className="absolute inset-x-0 bottom-4 text-center">
              <span className="rounded-lg bg-black/40 px-4 py-1.5 text-[13px] text-cyan-300/60 backdrop-blur-sm">
                Click a dot to locate on map
              </span>
            </div>
          )}

          {isExpanded && (
            <button
              onClick={() => setSelectedIdx(null)}
              className="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-lg bg-black/40 px-4 py-1.5 text-[13px] text-slate-400 backdrop-blur-sm transition hover:text-white"
            >
              Close map
            </button>
          )}
        </div>
      </div>

      {visitors.length > 0 && (
        <div className="border-t border-white/[0.04] px-4 py-3">
          <div className="flex gap-2 overflow-x-auto pb-1">
            {visitors.slice(0, 8).map((v, i) => (
              <button
                key={`vl-${v.visitor_id || i}`}
                className={`flex flex-shrink-0 items-center gap-2 rounded-xl border px-3.5 py-2 text-[13px] transition-all duration-200 ${
                  selectedIdx === i
                    ? "border-cyan-400/30 bg-cyan-500/[0.1] shadow-[0_0_16px_rgba(34,211,238,0.12)]"
                    : "border-white/[0.05] bg-white/[0.02] hover:bg-white/[0.04]"
                }`}
                onClick={() => handleDotClick(i)}
              >
                <span className={`h-2.5 w-2.5 flex-shrink-0 rounded-full ${intentDotClass(v.intent_level)}`} />
                <span className="font-medium text-slate-200">{v.city || "Visitor"}</span>
                {v.country_code && <span className="text-slate-500">{v.country_code}</span>}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
