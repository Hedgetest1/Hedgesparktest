"use client";

type SparklineProps = {
  values: number[];
  className?: string;
  width?: number;
  height?: number;
  /** Tailwind background class for the bars (e.g. "bg-violet-400/60"). */
  barClassName?: string;
};

/**
 * Tiny inline sparkline rendered with pixel heights (not CSS percentages)
 * to avoid layout/rounding ambiguity. Defensive against NaN/non-number entries.
 */
export function Sparkline({
  values,
  className = "",
  width = 64,
  height = 28,
  barClassName = "bg-violet-400/60",
}: SparklineProps) {
  if (!Array.isArray(values) || values.length === 0) return null;
  const clean = values.map((v) => (typeof v === "number" && !Number.isNaN(v) ? v : 0));
  const max = Math.max(...clean, 1);
  return (
    <div
      className={`flex items-end gap-px ${className}`}
      style={{ height, width }}
      aria-hidden="true"
    >
      {clean.map((v, i) => {
        const px = Math.max(2, Math.round((v / max) * height));
        return (
          <div
            key={i}
            className={`flex-1 rounded-[2px] ${barClassName}`}
            style={{ height: px }}
            title={String(v)}
          />
        );
      })}
    </div>
  );
}
