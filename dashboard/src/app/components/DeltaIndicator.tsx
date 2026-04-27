"use client";

/**
 * DeltaIndicator — single source of truth for "vs previous period"
 * comparison badges across every range-aware tile.
 *
 * Born 2026-04-27 from Phase 3B comparison-toggle wiring. Factors the
 * delta-badge logic out of `app/_components/KpiCard.tsx:33-41` so the
 * 11 Lite analytics tiles can drop in a consistent visual without each
 * re-implementing the math.
 *
 * Palette per CLAUDE.md §4:
 *   emerald = up (good direction)
 *   rose    = down (bad direction)
 *   slate   = neutral / sub-threshold
 *   amber   = warning (special use, not auto-applied here)
 *
 * Inverse semantics: for metrics where DOWN is good (cart abandonment %,
 * refund rate, churn), pass `inverse={true}` so a decrease renders as
 * emerald (good) and an increase renders as rose (bad).
 *
 * Threshold default 1 matches the existing KpiCard behavior — sub-1%
 * deltas don't render to avoid noise from rounding artifacts.
 */

type DeltaFormat = "pct" | "currency" | "count";

export function DeltaIndicator({
  value,
  prevValue,
  format = "count",
  inverse = false,
  threshold = 1,
  className = "",
}: {
  value: number;
  prevValue: number | null | undefined;
  format?: DeltaFormat;
  inverse?: boolean;
  threshold?: number;
  className?: string;
}) {
  if (prevValue == null) return null;

  // Both zero → no change to indicate
  if (prevValue === 0 && value === 0) return null;

  // Brand-new (was zero, now non-zero) — show "new" badge with neutral
  // styling, no pct (would be Infinity).
  if (prevValue === 0) {
    return (
      <span
        className={`inline-flex items-center gap-0.5 rounded-full bg-slate-500/15 px-2 py-0.5 text-[12px] font-bold tabular-nums text-slate-300 ${className}`}
        aria-label="New this period"
      >
        new
      </span>
    );
  }

  const deltaPct = ((value - prevValue) / Math.abs(prevValue)) * 100;
  if (Math.abs(deltaPct) < threshold) return null;

  const isUp = deltaPct > 0;
  // "good" = up unless inverse, in which case "good" = down
  const isGood = inverse ? !isUp : isUp;

  const colorClass = isGood
    ? "bg-emerald-500/15 text-emerald-300"
    : "bg-rose-500/15 text-rose-300";

  const arrow = isUp ? "↑" : "↓";
  const magnitude = Math.abs(Math.round(deltaPct));

  return (
    <span
      className={`inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[12px] font-bold tabular-nums ${colorClass} ${className}`}
      aria-label={`${isUp ? "Up" : "Down"} ${magnitude}% vs previous period`}
      title={formatTitle(value, prevValue, format)}
    >
      {arrow}{magnitude}%
    </span>
  );
}

function formatTitle(value: number, prev: number, format: DeltaFormat): string {
  if (format === "pct") {
    return `${value.toFixed(1)}% (was ${prev.toFixed(1)}%)`;
  }
  if (format === "currency") {
    // Caller typically pre-formats; we keep a numeric fallback here.
    return `${value.toFixed(2)} (was ${prev.toFixed(2)})`;
  }
  return `${value.toLocaleString()} (was ${prev.toLocaleString()})`;
}
