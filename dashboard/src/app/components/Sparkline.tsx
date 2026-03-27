"use client";

type SparklineProps = {
  values: number[];
  className?: string;
};

export function Sparkline({ values, className = "" }: SparklineProps) {
  if (!values || values.length === 0) return null;
  const max = Math.max(...values, 1);
  return (
    <div
      className={`flex items-end gap-px ${className}`}
      style={{ height: 28 }}
      aria-hidden="true"
    >
      {values.map((v, i) => {
        const heightPct = Math.max(8, Math.round((v / max) * 100));
        return (
          <div
            key={i}
            className="flex-1 rounded-[2px] bg-violet-400/50 transition-all duration-300"
            style={{ height: `${heightPct}%` }}
            title={String(v)}
          />
        );
      })}
    </div>
  );
}
