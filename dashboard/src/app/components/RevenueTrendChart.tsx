"use client";

import { useEffect, useMemo, useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
type DailyPoint = {
  day: string;
  revenue: number;
  orders: number;
};

type DailyRevenueResponse = {
  points: DailyPoint[];
  currency: string;
  days: number;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function fmtShortDay(iso: string): string {
  try {
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString("en-US", { weekday: "short" });
  } catch {
    return iso.slice(-2);
  }
}

function fmtCurrencyCompact(value: number, currency: string): string {
  try {
    if (value >= 1000) {
      return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
        notation: "compact",
      }).format(value);
    }
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    return `${Math.round(value)}`;
  }
}

// ---------------------------------------------------------------------------
// SVG area chart — smooth cubic Bezier path with gradient fill
// ---------------------------------------------------------------------------
const CHART_W = 400;
const CHART_H = 100;
const PAD_X = 0;
const PAD_TOP = 8;
const PAD_BOT = 0;

function buildPath(
  points: number[],
  w: number,
  h: number,
): { line: string; area: string } {
  if (points.length === 0) return { line: "", area: "" };

  const max = Math.max(...points, 1);
  const n = points.length;
  const stepX = n > 1 ? (w - PAD_X * 2) / (n - 1) : 0;

  function toXY(i: number): [number, number] {
    const x = PAD_X + i * stepX;
    const y = PAD_TOP + (1 - points[i] / max) * (h - PAD_TOP - PAD_BOT);
    return [x, y];
  }

  // Single point — draw a flat line spanning the chart
  if (n === 1) {
    const [, y] = toXY(0);
    return {
      line: `M${PAD_X},${y} L${w - PAD_X},${y}`,
      area: `M${PAD_X},${y} L${w - PAD_X},${y} L${w - PAD_X},${h} L${PAD_X},${h} Z`,
    };
  }

  // Build smooth cubic Bezier
  const coords = Array.from({ length: n }, (_, i) => toXY(i));
  let linePath = `M${coords[0][0]},${coords[0][1]}`;

  for (let i = 0; i < coords.length - 1; i++) {
    const [x0, y0] = coords[i];
    const [x1, y1] = coords[i + 1];
    const tension = stepX * 0.35;
    linePath += ` C${x0 + tension},${y0} ${x1 - tension},${y1} ${x1},${y1}`;
  }

  const lastX = coords[coords.length - 1][0];
  const firstX = coords[0][0];
  const areaPath = `${linePath} L${lastX},${h} L${firstX},${h} Z`;

  return { line: linePath, area: areaPath };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export function RevenueTrendChart({
  apiBase,
  shop,
  currency: fallbackCurrency = "USD",
}: {
  apiBase: string;
  shop: string;
  currency?: string;
}) {
  const [data, setData] = useState<DailyRevenueResponse | null>(null);
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;

    fetch(`${apiBase}/orders/daily-revenue?shop=${encodeURIComponent(shop)}&days=7`, {
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      cache: "no-store",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => {
        if (active && json?.points) setData(json);
      })
      .catch(() => {});

    return () => { active = false; };
  }, [apiBase, shop]);

  const points = data?.points ?? [];
  const currency = data?.currency ?? fallbackCurrency;
  const values = useMemo(() => points.map((p) => p.revenue), [points]);
  const hasRevenue = values.some((v) => v > 0);

  // Don't render if no data at all
  if (!data || points.length === 0) return null;

  const { line, area } = buildPath(values, CHART_W, CHART_H);
  const maxVal = Math.max(...values, 1);
  const n = points.length;
  const stepX = n > 1 ? (CHART_W - PAD_X * 2) / (n - 1) : 0;

  // Compute week-over-week delta if we have enough data
  const totalRevenue = values.reduce((s, v) => s + v, 0);
  const firstHalf = values.slice(0, Math.floor(n / 2));
  const secondHalf = values.slice(Math.floor(n / 2));
  const sumFirst = firstHalf.reduce((s, v) => s + v, 0);
  const sumSecond = secondHalf.reduce((s, v) => s + v, 0);
  const momentum = sumFirst > 0 ? ((sumSecond - sumFirst) / sumFirst) * 100 : null;

  if (!hasRevenue) {
    // All zeros — show minimal placeholder
    return (
      <div className="mt-4 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
        <div className="flex items-center gap-2">
          <div className="h-px flex-1 bg-white/[0.06]" />
          <span className="text-[11px] text-slate-600">Revenue trend will appear once orders arrive</span>
          <div className="h-px flex-1 bg-white/[0.06]" />
        </div>
      </div>
    );
  }

  return (
    <div className="mt-4 overflow-hidden rounded-xl border border-white/[0.06] bg-white/[0.02]">
      {/* Header row */}
      <div className="flex items-center justify-between px-4 pt-3 pb-1">
        <div className="text-[11px] font-medium text-slate-500">7-day revenue</div>
        <div className="flex items-center gap-2">
          {momentum !== null && Math.abs(momentum) >= 1 && (
            <span
              className={`flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                momentum >= 0
                  ? "bg-emerald-500/15 text-emerald-300"
                  : "bg-rose-500/15 text-rose-300"
              }`}
            >
              {momentum >= 0 ? "↑" : "↓"} {Math.abs(Math.round(momentum))}%
            </span>
          )}
          <span className="text-[11px] tabular-nums text-slate-400">
            {fmtCurrencyCompact(totalRevenue, currency)} total
          </span>
        </div>
      </div>

      {/* SVG Chart */}
      <div className="relative px-2 pb-1">
        <svg
          viewBox={`0 0 ${CHART_W} ${CHART_H}`}
          preserveAspectRatio="none"
          className="h-[80px] w-full"
          onMouseLeave={() => setHoveredIdx(null)}
        >
          <defs>
            <linearGradient id="hs-rev-gradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgb(139, 92, 246)" stopOpacity="0.3" />
              <stop offset="100%" stopColor="rgb(139, 92, 246)" stopOpacity="0.02" />
            </linearGradient>
          </defs>

          {/* Area fill */}
          <path d={area} fill="url(#hs-rev-gradient)" />

          {/* Line */}
          <path
            d={line}
            fill="none"
            stroke="rgb(139, 92, 246)"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Hover hit areas + dots */}
          {points.map((_, i) => {
            const x = PAD_X + i * stepX;
            const y = PAD_TOP + (1 - values[i] / maxVal) * (CHART_H - PAD_TOP - PAD_BOT);
            const isHovered = hoveredIdx === i;
            return (
              <g key={i}>
                {/* Invisible wide hit area */}
                <rect
                  x={x - stepX / 2}
                  y={0}
                  width={stepX}
                  height={CHART_H}
                  fill="transparent"
                  onMouseEnter={() => setHoveredIdx(i)}
                />
                {/* Vertical guide line */}
                {isHovered && (
                  <line
                    x1={x} y1={PAD_TOP} x2={x} y2={CHART_H}
                    stroke="rgba(139, 92, 246, 0.2)"
                    strokeWidth="1"
                    strokeDasharray="3 3"
                  />
                )}
                {/* Dot */}
                <circle
                  cx={x}
                  cy={y}
                  r={isHovered ? 4 : 2}
                  fill={isHovered ? "rgb(139, 92, 246)" : "rgba(139, 92, 246, 0.6)"}
                  stroke={isHovered ? "rgba(255,255,255,0.3)" : "none"}
                  strokeWidth={isHovered ? 2 : 0}
                  className="transition-all duration-150"
                />
              </g>
            );
          })}
        </svg>

        {/* Hover tooltip */}
        {hoveredIdx !== null && points[hoveredIdx] && (
          <div
            className="pointer-events-none absolute top-0 z-10 rounded-lg border border-white/[0.1] bg-[#0d0d1e]/95 px-2.5 py-1.5 text-[11px] shadow-lg backdrop-blur-sm"
            style={{
              left: `${((PAD_X + hoveredIdx * stepX) / CHART_W) * 100}%`,
              transform: "translateX(-50%)",
            }}
          >
            <div className="font-medium tabular-nums text-white">
              {fmtCurrencyCompact(points[hoveredIdx].revenue, currency)}
            </div>
            <div className="text-slate-500">
              {fmtShortDay(points[hoveredIdx].day)} · {points[hoveredIdx].orders} order{points[hoveredIdx].orders !== 1 ? "s" : ""}
            </div>
          </div>
        )}
      </div>

      {/* Day labels */}
      <div className="flex justify-between px-4 pb-2.5">
        {points.map((p, i) => (
          <span
            key={p.day}
            className={`text-[10px] tabular-nums ${
              hoveredIdx === i ? "font-medium text-violet-300" : "text-slate-600"
            }`}
          >
            {fmtShortDay(p.day)}
          </span>
        ))}
      </div>
    </div>
  );
}
