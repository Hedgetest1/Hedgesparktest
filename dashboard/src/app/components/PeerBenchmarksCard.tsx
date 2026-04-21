"use client";

/**
 * PeerBenchmarksCard — "You vs. Similar Shops"
 *
 * Shows the merchant's percentile rank against peers in their revenue band
 * for 4 metrics (revenue, AOV, orders/day, growth). Loss-framed: every
 * row has a "recover by moving to p75" € estimate.
 *
 * Data source: GET /analytics/benchmarks (Lite-accessible, same data
 * as the old /pro/benchmarks). Privacy: minimum 10 peers per band,
 * below that an explicit insufficient-data note.
 *
 * Tier-agnostic since 2026-04-20: per founder directive "strada 2 —
 * completista", peer benchmarks become part of the €39 Lite surface.
 * The `isProUser` prop is retained for call-site back-compat but no
 * longer gates rendering.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";

type BenchmarkMetric = {
  value: number;
  band: string;
  peer_count: number;
  percentile_rank: number;
  p25: number;
  p50: number;
  p75: number;
  p90: number;
  recovery_to_p75_eur: number;
  status: string;
  narrative: string;
};

type BenchmarkData = {
  shop_domain: string;
  band: string | null;
  peer_count: number;
  metrics: Record<string, BenchmarkMetric>;
  total_recovery_potential_eur: number;
  // Shop's native currency — `_eur` fields are native.
  currency?: string;
  generated_at: string | null;
  note?: string | null;
  error?: string | null;
  // Strada 4 (dominate): extended benchmark fields.
  product_concentration?: {
    total_products: number;
    products_for_80pct_revenue: number;
    concentration_ratio: number;
    narrative: string;
  } | null;
};

const METRIC_LABELS: Record<string, string> = {
  monthly_revenue: "Monthly revenue",
  aov: "Average order value",
  orders_per_day: "Orders per day",
  revenue_growth_30d_pct: "Revenue growth",
  cvr: "Conversion rate",
};

// Short axis labels for the radar — 2-3 chars max so the SVG stays
// readable at 220px. Order here locks the radar axis sequence.
const METRIC_SHORT_LABELS: Record<string, string> = {
  monthly_revenue: "Rev",
  aov: "AOV",
  orders_per_day: "Orders",
  revenue_growth_30d_pct: "Growth",
  cvr: "CVR",
};

// ─── Lite palette — "colonna" reference 2026-04-21 ──────────────
// Cream dominant, lilac accent, peach for warm highlights. Soft-
// ceramic mood; no sharp tech-gradient, no urgency saturation.
const C_CREAM = "#F5E6CC";
const C_CREAM_SOFT = "#F0DEB6";
const C_LILAC = "#C5B5DB";
const C_LILAC_SOFT = "#B8A5D0";
const C_PEACH = "#F5A58B";
// Semantic pastels — softened versions of the canonical semantics so
// they sit next to cream/lilac/peach without stridency.
const C_EMERALD_SOFT = "#9FE3C5";
const C_ROSE_SOFT = "#F4A5A5";
const C_YELLOW_SOFT = "#F0D97D";

import { formatMoneyCompact } from "@/app/app/_lib/formatters";

function fmtMoney(n: number, currency?: string): string {
  return formatMoneyCompact(n, currency || "USD");
}

function fmtMetricValue(metric: string, v: number, currency?: string): string {
  if (metric === "revenue_growth_30d_pct") return v.toFixed(0) + "%";
  if (metric === "cvr") return v.toFixed(2) + "%";
  if (metric === "orders_per_day") return v.toFixed(1);
  if (metric === "monthly_revenue" || metric === "aov") return fmtMoney(v, currency);
  return String(Math.round(v));
}

function statusColor(status: string): string {
  // Pastel mapped against the colonna palette — cream/lilac/peach-
  // compatible. Emerald=good, rose=problem, yellow=stall per
  // founder directive 2026-04-21.
  switch (status) {
    case "top_decile":   return C_EMERALD_SOFT;
    case "top_quartile": return C_EMERALD_SOFT;
    case "above_median": return C_YELLOW_SOFT;
    case "below_median": return C_ROSE_SOFT;
    default:             return C_LILAC;
  }
}

export function PeerBenchmarksCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [data, setData] = useState<BenchmarkData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) { setLoading(false); return; }
    let active = true;
    setLoading(true);
    apiClient
      .GET("/analytics/benchmarks")
      .then(({ data: j, error: err }) => {
        if (!active) return;
        if (err || !j) setData(null);
        else setData(j as unknown as BenchmarkData);
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiBase, shop]);

  // `isProUser` retained in signature for back-compat (many callers
  // still pass it) but no longer affects rendering — benchmarks are
  // a Lite-tier feature since 2026-04-20.
  void isProUser;

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
        <div className="h-3 w-32 rounded bg-white/[0.06]" />
        <div className="mt-3 space-y-2">
          {[0, 1, 2, 3].map((i) => (<div key={i} className="h-10 rounded bg-white/[0.04]" />))}
        </div>
      </div>
    );
  }

  if (!data || data.error || data.note) {
    return (
      <div
        className="rounded-2xl p-5"
        style={{
          border: `1px solid ${C_LILAC}22`,
          background:
            "linear-gradient(135deg, rgba(197,181,219,0.04), rgba(245,230,204,0.02))",
        }}
      >
        <div
          className="mb-1 text-[10px] font-bold uppercase tracking-[0.18em]"
          style={{ color: C_LILAC }}
        >
          You vs. Similar Shops
        </div>
        <h3 className="text-[15px] font-bold" style={{ color: C_CREAM }}>
          How you compare to peers
        </h3>
        <PeerRadarChart
          entries={[]}
          ghost
        />
        <p className="mt-2 text-[12px] leading-relaxed text-slate-400">
          {data?.note ||
            "Comparison not available yet — we need at least 10 similar shops in your revenue band. Keep running — this activates automatically."}
        </p>
      </div>
    );
  }

  const entries = Object.entries(data.metrics);
  const totalRecovery = data.total_recovery_potential_eur || 0;

  return (
    <div
      className="relative overflow-hidden rounded-2xl p-5"
      style={{
        border: `1px solid ${C_LILAC}28`,
        background:
          "linear-gradient(135deg, rgba(197,181,219,0.05), rgba(245,230,204,0.025) 60%, rgba(245,165,139,0.02))",
      }}
    >
      {/* Corner ornament — soft 5-petal flower, colonna-ref motif.
          Pure decoration, pointer-events none, bottom-right. */}
      <PeerOrnament />

      <div className="relative mb-4 flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0 flex-1">
          <div
            className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em]"
            style={{ color: C_LILAC }}
          >
            You vs. Similar Shops
          </div>
          <h3
            className="text-[15px] font-bold"
            style={{ color: C_CREAM }}
          >
            How you compare to peers
          </h3>
          <p className="mt-1 text-[11px] text-slate-500">
            {data.peer_count} shops in the{" "}
            <span
              className="font-semibold"
              style={{ color: C_CREAM_SOFT }}
            >
              {data.band}
            </span>{" "}
            revenue band
          </p>
          {totalRecovery > 0 && (
            <div
              className="mt-3 inline-flex items-center gap-2 rounded-full px-3.5 py-1.5"
              style={{
                border: `1px solid ${C_PEACH}40`,
                backgroundColor: `${C_PEACH}12`,
              }}
            >
              <span
                aria-hidden="true"
                className="h-1.5 w-1.5 rounded-full"
                style={{ backgroundColor: C_PEACH }}
              />
              <span
                className="text-[9.5px] font-bold uppercase tracking-[0.14em]"
                style={{ color: C_PEACH }}
              >
                Could recover
              </span>
              <span
                className="text-[13px] font-extrabold tabular-nums"
                style={{ color: C_CREAM }}
              >
                {fmtMoney(totalRecovery, data?.currency)}/mo
              </span>
            </div>
          )}
        </div>

        {/* Radar — 4-5 metrics peer comparison visual. */}
        <div className="flex-shrink-0 self-center md:self-start">
          <PeerRadarChart entries={entries} />
        </div>
      </div>

      <div className="space-y-2">
        {entries.map(([metric, m]) => {
          const color = statusColor(m.status);
          const rank = Math.round(m.percentile_rank);
          return (
            <div
              key={metric}
              className="rounded-xl p-3"
              style={{
                border: `1px solid ${C_CREAM}10`,
                backgroundColor: `${C_CREAM}06`,
              }}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span
                      className="text-[12px] font-semibold"
                      style={{ color: C_CREAM }}
                    >
                      {METRIC_LABELS[metric] || metric}
                    </span>
                    <span className="text-[10px] text-slate-500">
                      you:{" "}
                      <span
                        className="font-mono tabular-nums"
                        style={{ color: C_CREAM_SOFT }}
                      >
                        {fmtMetricValue(metric, m.value, data?.currency)}
                      </span>
                    </span>
                  </div>
                  <div className="mt-1 text-[10px] text-slate-500">
                    p25 {fmtMetricValue(metric, m.p25, data?.currency)} · p50{" "}
                    {fmtMetricValue(metric, m.p50, data?.currency)} · p75{" "}
                    {fmtMetricValue(metric, m.p75, data?.currency)}
                  </div>
                </div>
                <div
                  className="flex-shrink-0 rounded-full px-2.5 py-1 text-[10px] font-bold tabular-nums"
                  style={{
                    color,
                    background: color + "1f",
                    border: `1px solid ${color}48`,
                  }}
                >
                  p{rank}
                </div>
              </div>
              {/* Rank bar */}
              <div
                className="mt-2 h-1.5 overflow-hidden rounded-full"
                style={{ backgroundColor: `${C_CREAM}0a` }}
              >
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{
                    width: `${Math.min(100, rank)}%`,
                    background: color,
                    opacity: 0.85,
                  }}
                />
              </div>
              {m.recovery_to_p75_eur > 0 && (
                <div
                  className="mt-1.5 text-[10px]"
                  style={{ color: C_PEACH }}
                >
                  → moving to p75 ={" "}
                  <span className="font-semibold">
                    +{fmtMoney(m.recovery_to_p75_eur, data?.currency)}/mo
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Product concentration — Strada 4 (dominate). Pareto 80/20
          signal complementing the peer metrics above. */}
      {data.product_concentration && (
        <div
          className="relative mt-4 rounded-xl px-4 py-3"
          style={{
            border: `1px solid ${C_PEACH}28`,
            backgroundColor: `${C_PEACH}0a`,
          }}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div
                className="text-[10px] font-bold uppercase tracking-wider"
                style={{ color: C_PEACH }}
              >
                Catalog concentration (Pareto 80/20)
              </div>
              <p
                className="mt-1 text-[12px] leading-relaxed"
                style={{ color: C_CREAM_SOFT }}
              >
                {data.product_concentration.narrative}
              </p>
            </div>
            <div className="flex-shrink-0 text-right">
              <div
                className="text-[22px] font-extrabold tabular-nums"
                style={{ color: C_PEACH }}
              >
                {data.product_concentration.concentration_ratio.toFixed(0)}%
              </div>
              <div className="text-[9.5px] uppercase tracking-wider text-slate-500">
                of catalog
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// PeerRadarChart — the visual companion to the numeric list.
// ─────────────────────────────────────────────────────────────────────
// SVG radar (spider) 220×220 with up to 5 axes — one per metric the
// backend returned. Two overlapping polygons:
//   - peer median (p50) → soft lilac filled stroke, dashed
//   - you (percentile)  → peach filled stroke, solid
// Axis ticks at 25/50/75/100. Cream grid at very low opacity.
// Ghost mode (no entries) → only grid + dashed polygon placeholder +
// "Watching" copy in the center. Zero fabricated metrics.

function PeerRadarChart({
  entries,
  ghost = false,
}: {
  entries: Array<[string, { percentile_rank: number }]>;
  ghost?: boolean;
}) {
  const size = 220;
  const cx = size / 2;
  const cy = size / 2;
  const rOuter = 82;
  // Ticks at 25/50/75/100 — visual calibration rings.
  const ticks = [25, 50, 75, 100];

  // Cap to 5 axes for readability; prefer the 4 canonical metrics.
  const axes = entries.slice(0, 5).map(([metric, m]) => ({
    metric,
    label: METRIC_SHORT_LABELS[metric] || metric,
    rank: Math.max(0, Math.min(100, m.percentile_rank)),
  }));

  const axisCount = Math.max(4, axes.length || 4);
  // Angles: start at top (−90°), go clockwise.
  const angleFor = (i: number) =>
    -Math.PI / 2 + (i * 2 * Math.PI) / axisCount;

  function pointAt(i: number, ratio: number): [number, number] {
    const a = angleFor(i);
    const r = rOuter * (ratio / 100);
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  }

  // Polygon string from a percent array.
  function polyString(pcts: number[]): string {
    return pcts
      .map((p, i) => {
        const [x, y] = pointAt(i, p);
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");
  }

  const youPoly = axes.length > 0 ? polyString(axes.map((a) => a.rank)) : "";
  const peerPoly = polyString(
    Array.from({ length: axisCount }, () => 50),
  );

  return (
    <div
      className="relative"
      style={{ width: size, height: size }}
      aria-label="Peer benchmarks — radar chart of 4 metrics vs peer median"
    >
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
      >
        {/* Grid rings — concentric, very faint cream */}
        {ticks.map((t) => (
          <circle
            key={`ring-${t}`}
            cx={cx}
            cy={cy}
            r={rOuter * (t / 100)}
            fill="none"
            stroke={C_CREAM}
            strokeWidth={0.5}
            strokeOpacity={t === 100 ? 0.16 : 0.08}
            strokeDasharray={t === 50 ? "2 3" : undefined}
          />
        ))}
        {/* Axes — radial spokes cream 10% */}
        {Array.from({ length: axisCount }, (_, i) => {
          const [x, y] = pointAt(i, 100);
          return (
            <line
              key={`axis-${i}`}
              x1={cx}
              y1={cy}
              x2={x}
              y2={y}
              stroke={C_CREAM}
              strokeWidth={0.5}
              strokeOpacity={0.1}
            />
          );
        })}

        {/* Peer median (p50) polygon — soft lilac, dashed */}
        <polygon
          points={peerPoly}
          fill={C_LILAC}
          fillOpacity={ghost ? 0.05 : 0.08}
          stroke={C_LILAC}
          strokeOpacity={ghost ? 0.35 : 0.5}
          strokeWidth={1.2}
          strokeDasharray="3 3"
        />

        {/* You polygon — peach filled, solid stroke */}
        {!ghost && axes.length > 0 && (
          <g>
            <polygon
              points={youPoly}
              fill={C_PEACH}
              fillOpacity={0.3}
              stroke={C_PEACH}
              strokeWidth={1.6}
              strokeLinejoin="round"
            />
            {/* Node dots — one per axis */}
            {axes.map((a, i) => {
              const [x, y] = pointAt(i, a.rank);
              return (
                <circle
                  key={`node-${i}`}
                  cx={x}
                  cy={y}
                  r={3.2}
                  fill={C_CREAM}
                  stroke={C_PEACH}
                  strokeWidth={1.4}
                />
              );
            })}
          </g>
        )}

        {/* Axis labels — cream, outside the outer ring */}
        {axes.map((a, i) => {
          const [lx, ly] = pointAt(i, 118);
          return (
            <text
              key={`lbl-${i}`}
              x={lx}
              y={ly}
              textAnchor="middle"
              dominantBaseline="middle"
              style={{
                fontSize: "9.5px",
                fontWeight: 700,
                letterSpacing: "0.04em",
                fill: C_CREAM_SOFT,
              }}
            >
              {a.label}
            </text>
          );
        })}

        {/* Ghost overlay — center copy when no data */}
        {ghost && (
          <text
            x={cx}
            y={cy + 4}
            textAnchor="middle"
            style={{
              fontSize: "10px",
              fontWeight: 700,
              letterSpacing: "0.06em",
              fill: C_CREAM,
              opacity: 0.55,
            }}
          >
            Watching…
          </text>
        )}
      </svg>

      {/* Legend — below the radar, compact */}
      <div className="mt-1 flex items-center justify-center gap-3 text-[9.5px] text-slate-500">
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="inline-block h-1.5 w-3 rounded-sm"
            style={{ backgroundColor: C_PEACH }}
          />
          <span style={{ color: C_CREAM_SOFT }}>You</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="inline-block h-1.5 w-3 rounded-sm"
            style={{ backgroundColor: C_LILAC, opacity: 0.65 }}
          />
          <span style={{ color: C_CREAM_SOFT }}>Peer median</span>
        </span>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// PeerOrnament — 5-petal soft flower bottom-right corner.
// ─────────────────────────────────────────────────────────────────────
// Reads as intentional decoration, not cartoon. Derived from the
// "colonna" reference photo (the peach daisy motif). Pure SVG,
// pointer-events none. Breathes slowly so the card feels alive.

function PeerOrnament() {
  const petalCount = 5;
  const cx = 28;
  const cy = 28;
  const petalR = 10;
  const petalDistance = 11;
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute right-4 bottom-4 opacity-30"
      style={{
        animation: "peer-ornament-breathe 8s ease-in-out infinite",
      }}
    >
      <svg width={56} height={56} viewBox="0 0 56 56">
        {/* 5 petals — soft peach ellipses arrayed around a center */}
        {Array.from({ length: petalCount }, (_, i) => {
          const a = -Math.PI / 2 + (i * 2 * Math.PI) / petalCount;
          const px = cx + petalDistance * Math.cos(a);
          const py = cy + petalDistance * Math.sin(a);
          const deg = (a * 180) / Math.PI + 90;
          return (
            <ellipse
              key={i}
              cx={px}
              cy={py}
              rx={petalR * 0.7}
              ry={petalR}
              fill={C_PEACH}
              fillOpacity={0.55}
              transform={`rotate(${deg} ${px} ${py})`}
            />
          );
        })}
        {/* Center */}
        <circle cx={cx} cy={cy} r={4.5} fill={C_CREAM} fillOpacity={0.9} />
        <circle cx={cx} cy={cy} r={2.2} fill={C_LILAC} fillOpacity={0.8} />
      </svg>
      <style jsx>{`
        @keyframes peer-ornament-breathe {
          0%,
          100% {
            opacity: 0.22;
            transform: scale(1);
          }
          50% {
            opacity: 0.36;
            transform: scale(1.04);
          }
        }
      `}</style>
    </div>
  );
}
