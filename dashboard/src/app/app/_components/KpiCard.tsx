"use client";

/**
 * KpiCard — a single KPI tile with label, value, hint, and optional
 * trend delta. Extracted from app/page.tsx (Phase Ω⁶ split).
 */

import { CountUp } from "./CountUp";

export function KpiCard({
  label,
  value,
  hint,
  numeric,
  onClick,
  delta,
}: {
  label: string;
  value: string;
  hint: string;
  numeric?: number;
  onClick?: () => void;
  /** Trend percentage delta: positive = up, negative = down, undefined = hidden */
  delta?: number | null;
}) {
  return (
    <div
      className={`hs-fade-up group rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5 transition-all duration-200 hover:border-[#d4893a]/20 hover:bg-white/[0.05] hover:shadow-[0_4px_24px_rgba(212,137,58,0.06)]${onClick ? " cursor-pointer select-none" : ""}`}
      onClick={onClick}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="text-[14px] font-medium text-slate-400">{label}</div>
        {delta != null && Math.abs(delta) >= 1 && (
          <span className={`flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[12px] font-bold tabular-nums ${
            delta > 0
              ? "bg-emerald-500/15 text-emerald-300"
              : "bg-rose-500/15 text-rose-300"
          }`}>
            {delta > 0 ? "↑" : "↓"}{Math.abs(Math.round(delta))}%
          </span>
        )}
      </div>
      <div className="mt-2.5 text-[2rem] font-bold tabular-nums text-white">
        {numeric !== undefined ? (
          <CountUp value={numeric} />
        ) : (
          value
        )}
      </div>
      <div className="mt-1.5 text-[13px] leading-snug text-slate-500">{hint}</div>
    </div>
  );
}
