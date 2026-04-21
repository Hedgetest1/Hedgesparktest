"use client";

/**
 * PeerBenchmarksCard — "You vs. Similar Shops"
 *
 * Pilot of the /app/lite light-palette direction per
 * /docs/LITE_LIGHT_PALETTE.md (founder-locked 2026-04-21 "B"):
 * cream card on dark canvas, concentric-ring chart for peer
 * percentiles. No radar (LiveRadarMap already owns that visual).
 *
 * Data source: GET /analytics/benchmarks (Lite-accessible). Privacy:
 * minimum 10 peers per band, insufficient-data note below threshold.
 * Tier-agnostic since 2026-04-20: peer benchmarks are €39 Lite.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";

// ─── Types (unchanged — data contract preserved) ─────────────────

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
  currency?: string;
  generated_at: string | null;
  note?: string | null;
  error?: string | null;
  product_concentration?: {
    total_products: number;
    products_for_80pct_revenue: number;
    concentration_ratio: number;
    narrative: string;
  } | null;
};

// ─── Light-palette tokens (colonna direction) ────────────────────

const CARD_BG = "#FAF5EB";
const CARD_BG_SOFT = "#F5EDD8";
const CARD_BORDER = "#BCA8D9";
const INK = "#2A2438";
const INK_SECONDARY = "#5C5478";
const INK_MUTED = "#8B85A0";
const LILAC = "#9177B8";
const LILAC_SOFT = "#C5B5DB";
const PEACH = "#E88A6E";
const GOOD = "#6FC5A0";
const PROBLEM = "#E88888";
const STALL = "#D4B342";

const METRIC_LABELS: Record<string, string> = {
  monthly_revenue: "Monthly revenue",
  aov: "Average order value",
  orders_per_day: "Orders per day",
  revenue_growth_30d_pct: "Revenue growth",
  cvr: "Conversion rate",
};

const METRIC_SHORT: Record<string, string> = {
  monthly_revenue: "Rev",
  aov: "AOV",
  orders_per_day: "Orders",
  revenue_growth_30d_pct: "Growth",
  cvr: "CVR",
};

function fmtMoney(n: number, currency?: string): string {
  return formatMoneyCompact(n, currency || "USD");
}

function fmtMetricValue(
  metric: string,
  v: number,
  currency?: string,
): string {
  if (metric === "revenue_growth_30d_pct") return v.toFixed(0) + "%";
  if (metric === "cvr") return v.toFixed(2) + "%";
  if (metric === "orders_per_day") return v.toFixed(1);
  if (metric === "monthly_revenue" || metric === "aov")
    return fmtMoney(v, currency);
  return String(Math.round(v));
}

function statusColor(status: string): string {
  switch (status) {
    case "top_decile":
    case "top_quartile":
      return GOOD;
    case "above_median":
      return STALL;
    case "below_median":
      return PROBLEM;
    default:
      return LILAC;
  }
}

// ─────────────────────────────────────────────────────────────────

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
    if (!apiBase || !shop) {
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    apiClient
      .GET("/analytics/benchmarks")
      .then(({ data: j, error: err }) => {
        if (!active) return;
        if (err || !j) setData(null);
        else setData(j as unknown as BenchmarkData);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [apiBase, shop]);

  // Prop retained for call-site back-compat (Lite-tier feature now).
  void isProUser;

  const cardShell: React.CSSProperties = {
    backgroundColor: CARD_BG,
    border: `1px solid ${CARD_BORDER}`,
    color: INK,
  };

  if (loading) {
    return (
      <div
        className="relative overflow-hidden rounded-2xl p-5"
        style={cardShell}
      >
        <div
          className="h-3 w-32 rounded"
          style={{ backgroundColor: LILAC_SOFT, opacity: 0.4 }}
        />
        <div className="mt-4 space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-10 rounded"
              style={{ backgroundColor: CARD_BG_SOFT }}
            />
          ))}
        </div>
      </div>
    );
  }

  if (!data || data.error || data.note) {
    return (
      <div
        className="relative overflow-hidden rounded-2xl p-5"
        style={cardShell}
      >
        <PeerOrnament />
        <div
          className="mb-1 text-[10px] font-bold uppercase tracking-[0.18em]"
          style={{ color: LILAC }}
        >
          You vs. Similar Shops
        </div>
        <h3
          className="text-[17px] font-bold"
          style={{ color: INK }}
        >
          How you compare to peers
        </h3>
        <p
          className="mt-2 max-w-xl text-[12.5px] leading-relaxed"
          style={{ color: INK_SECONDARY }}
        >
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
      className="relative overflow-hidden rounded-2xl p-5 sm:p-6"
      style={cardShell}
    >
      <PeerOrnament />

      <div className="relative mb-5 flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0 flex-1">
          <div
            className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em]"
            style={{ color: LILAC }}
          >
            You vs. Similar Shops
          </div>
          <h3
            className="text-[17px] font-bold leading-[1.2]"
            style={{ color: INK }}
          >
            How you compare to peers
          </h3>
          <p
            className="mt-1 text-[11.5px]"
            style={{ color: INK_MUTED }}
          >
            {data.peer_count} shops in the{" "}
            <span
              className="font-semibold"
              style={{ color: INK_SECONDARY }}
            >
              {data.band}
            </span>{" "}
            revenue band
          </p>

          {totalRecovery > 0 && (
            <div
              className="mt-3 inline-flex items-center gap-2 rounded-full px-3.5 py-1.5"
              style={{
                backgroundColor: `${PEACH}20`,
                border: `1px solid ${PEACH}66`,
              }}
            >
              <span
                aria-hidden="true"
                className="h-1.5 w-1.5 rounded-full"
                style={{ backgroundColor: PEACH }}
              />
              <span
                className="text-[9.5px] font-bold uppercase tracking-[0.14em]"
                style={{ color: PEACH }}
              >
                Could recover
              </span>
              <span
                className="text-[13px] font-extrabold tabular-nums"
                style={{ color: INK }}
              >
                {fmtMoney(totalRecovery, data?.currency)}/mo
              </span>
            </div>
          )}
        </div>

        {/* Concentric-ring chart — Apple Watch activity style, but
            boutique pastel. Each ring = one metric, fill length =
            percentile rank. */}
        <div className="flex-shrink-0 self-center md:self-start">
          <PeerRingsChart entries={entries} />
        </div>
      </div>

      {/* Metric list — kept, repainted for light card */}
      <div className="relative space-y-2">
        {entries.map(([metric, m]) => {
          const color = statusColor(m.status);
          const rank = Math.round(m.percentile_rank);
          return (
            <div
              key={metric}
              className="rounded-xl p-3"
              style={{
                backgroundColor: CARD_BG_SOFT,
                border: `1px solid ${LILAC_SOFT}66`,
              }}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span
                      className="text-[12.5px] font-semibold"
                      style={{ color: INK }}
                    >
                      {METRIC_LABELS[metric] || metric}
                    </span>
                    <span
                      className="text-[10.5px]"
                      style={{ color: INK_MUTED }}
                    >
                      you:{" "}
                      <span
                        className="font-mono tabular-nums"
                        style={{ color: INK_SECONDARY, fontWeight: 600 }}
                      >
                        {fmtMetricValue(metric, m.value, data?.currency)}
                      </span>
                    </span>
                  </div>
                  <div
                    className="mt-1 text-[10px]"
                    style={{ color: INK_MUTED }}
                  >
                    p25 {fmtMetricValue(metric, m.p25, data?.currency)} · p50{" "}
                    {fmtMetricValue(metric, m.p50, data?.currency)} · p75{" "}
                    {fmtMetricValue(metric, m.p75, data?.currency)}
                  </div>
                </div>
                <div
                  className="flex-shrink-0 rounded-full px-2.5 py-1 text-[10.5px] font-bold tabular-nums"
                  style={{
                    color: "#fff",
                    background: color,
                    border: `1px solid ${color}`,
                  }}
                >
                  p{rank}
                </div>
              </div>
              {/* Rank bar */}
              <div
                className="mt-2 h-1.5 overflow-hidden rounded-full"
                style={{ backgroundColor: `${LILAC_SOFT}60` }}
              >
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{
                    width: `${Math.min(100, rank)}%`,
                    background: color,
                  }}
                />
              </div>
              {m.recovery_to_p75_eur > 0 && (
                <div
                  className="mt-1.5 text-[10.5px]"
                  style={{ color: PEACH, fontWeight: 600 }}
                >
                  → moving to p75 ={" "}
                  <span className="font-bold">
                    +{fmtMoney(m.recovery_to_p75_eur, data?.currency)}/mo
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Pareto 80/20 block */}
      {data.product_concentration && (
        <div
          className="relative mt-4 rounded-xl px-4 py-3"
          style={{
            backgroundColor: `${PEACH}14`,
            border: `1px solid ${PEACH}66`,
          }}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div
                className="text-[10px] font-bold uppercase tracking-wider"
                style={{ color: PEACH }}
              >
                Catalog concentration (Pareto 80/20)
              </div>
              <p
                className="mt-1 text-[12.5px] leading-relaxed"
                style={{ color: INK_SECONDARY }}
              >
                {data.product_concentration.narrative}
              </p>
            </div>
            <div className="flex-shrink-0 text-right">
              <div
                className="text-[24px] font-extrabold tabular-nums"
                style={{ color: INK }}
              >
                {data.product_concentration.concentration_ratio.toFixed(0)}%
              </div>
              <div
                className="text-[9.5px] uppercase tracking-wider"
                style={{ color: INK_MUTED }}
              >
                of catalog
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// PeerRingsChart — concentric percentile rings, boutique pastel.
// ─────────────────────────────────────────────────────────────────
// Apple Watch activity rings conceptually, but softer. Each ring is
// one metric; ring arc length = percentile_rank / 100. Two-layer per
// ring: track (lilac-soft) + fill (colored by status). Outer = Rev,
// inner = CVR (or whatever 5th metric is last in backend order).
// Center: dominant-metric % for at-a-glance.

function PeerRingsChart({
  entries,
}: {
  entries: Array<[string, BenchmarkMetric]>;
}) {
  const size = 200;
  const cx = size / 2;
  const cy = size / 2;
  const rings = entries.slice(0, 5);
  const ringCount = rings.length;

  // Outer radius 84, innermost 28, step computed.
  const rOuter = 84;
  const rInner = 28;
  const step = ringCount > 1 ? (rOuter - rInner) / (ringCount - 1) : 0;
  const strokeW = Math.max(6, Math.min(12, step * 0.7));

  // Find dominant metric for center label (the best percentile).
  const best = rings.reduce(
    (acc, [m, data]) =>
      data.percentile_rank > acc.rank
        ? { metric: m, rank: data.percentile_rank }
        : acc,
    { metric: "", rank: -1 },
  );

  return (
    <div className="flex flex-col items-center">
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={`Peer ring chart — ${ringCount} metrics, best rank ${Math.round(best.rank)}`}
      >
        {rings.map(([metric, m], i) => {
          const r = rOuter - i * step;
          const circumference = 2 * Math.PI * r;
          const pct = Math.max(0, Math.min(100, m.percentile_rank));
          const dash = (pct / 100) * circumference;
          const color = statusColor(m.status);
          return (
            <g key={metric}>
              {/* Track */}
              <circle
                cx={cx}
                cy={cy}
                r={r}
                fill="none"
                stroke={LILAC_SOFT}
                strokeWidth={strokeW}
                strokeOpacity={0.55}
              />
              {/* Fill arc — starts at top (-90deg) */}
              <g transform={`rotate(-90 ${cx} ${cy})`}>
                <circle
                  cx={cx}
                  cy={cy}
                  r={r}
                  fill="none"
                  stroke={color}
                  strokeWidth={strokeW}
                  strokeLinecap="round"
                  strokeDasharray={`${dash} ${circumference}`}
                />
              </g>
            </g>
          );
        })}

        {/* Center label — best metric */}
        <text
          x={cx}
          y={cy - 4}
          textAnchor="middle"
          style={{
            fontSize: "24px",
            fontWeight: 800,
            fill: INK,
          }}
        >
          p{Math.round(best.rank)}
        </text>
        <text
          x={cx}
          y={cy + 14}
          textAnchor="middle"
          style={{
            fontSize: "8.5px",
            fontWeight: 700,
            letterSpacing: "0.14em",
            fill: INK_MUTED,
          }}
        >
          {(METRIC_SHORT[best.metric] || "").toUpperCase()}
        </text>
      </svg>

      {/* Legend — map each ring to metric */}
      <div className="mt-2 flex flex-wrap justify-center gap-x-3 gap-y-1 text-[9.5px]">
        {rings.map(([metric, m], i) => (
          <div
            key={metric}
            className="flex items-center gap-1.5"
          >
            <span
              aria-hidden="true"
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: statusColor(m.status) }}
            />
            <span style={{ color: INK_SECONDARY, fontWeight: 600 }}>
              {METRIC_SHORT[metric] || metric}
            </span>
            <span
              className="tabular-nums"
              style={{ color: INK_MUTED }}
            >
              p{Math.round(m.percentile_rank)}
            </span>
            {/* Ring position hint */}
            <span
              className="text-[8.5px]"
              style={{ color: INK_MUTED, opacity: 0.7 }}
            >
              {i === 0
                ? "(outer)"
                : i === rings.length - 1
                  ? "(inner)"
                  : ""}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// PeerOrnament — 5-petal soft flower bottom-right, colonna motif.
// ─────────────────────────────────────────────────────────────────

function PeerOrnament() {
  const petalCount = 5;
  const cx = 28;
  const cy = 28;
  const petalR = 10;
  const petalDistance = 11;
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute right-4 bottom-4"
      style={{
        animation: "peer-ornament-breathe 8s ease-in-out infinite",
        opacity: 0.45,
      }}
    >
      <svg width={56} height={56} viewBox="0 0 56 56">
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
              fill={PEACH}
              fillOpacity={0.75}
              transform={`rotate(${deg} ${px} ${py})`}
            />
          );
        })}
        <circle cx={cx} cy={cy} r={4.5} fill={CARD_BG} />
        <circle cx={cx} cy={cy} r={2.4} fill={LILAC} fillOpacity={0.9} />
      </svg>
      <style jsx>{`
        @keyframes peer-ornament-breathe {
          0%,
          100% {
            opacity: 0.38;
            transform: scale(1);
          }
          50% {
            opacity: 0.55;
            transform: scale(1.04);
          }
        }
      `}</style>
    </div>
  );
}
