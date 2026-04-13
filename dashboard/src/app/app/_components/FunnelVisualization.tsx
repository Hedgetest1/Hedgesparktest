/**
 * FunnelVisualization — tapered horizontal funnel flow.
 * Extracted from app/page.tsx (Phase Ω⁶ split).
 */

import Image from "next/image";
import { formatNumber } from "../_lib/formatters";

export type FunnelStepShape = {
  step: string;
  label: string;
  count: number;
  pct?: number | null;
  drop_off?: number | null;
};

const FUNNEL_COLORS = [
  { bar: "bg-violet-400/70", text: "text-violet-300", glow: "shadow-[0_0_12px_rgba(139,92,246,0.15)]" },
  { bar: "bg-cyan-400/60",   text: "text-cyan-300",   glow: "shadow-[0_0_12px_rgba(34,211,238,0.12)]" },
  { bar: "bg-amber-400/60",  text: "text-amber-300",  glow: "shadow-[0_0_12px_rgba(251,191,36,0.12)]" },
  { bar: "bg-emerald-400/60",text: "text-emerald-300", glow: "shadow-[0_0_12px_rgba(52,211,153,0.12)]" },
];

export function FunnelVisualization({ steps }: { steps: FunnelStepShape[] }) {
  const topCount = steps[0]?.count ?? 1;

  let worstDropIdx = -1;
  let worstDrop = 0;
  steps.forEach((s, i) => {
    if (i > 0 && s.drop_off != null && s.drop_off > worstDrop) {
      worstDrop = s.drop_off;
      worstDropIdx = i;
    }
  });

  return (
    <div className="overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.02]">
      <div className="space-y-0 px-5 pt-5 pb-3">
        {steps.map((step, i) => {
          const widthPct = topCount > 0 ? Math.max(8, (step.count / topCount) * 100) : 8;
          const colors = FUNNEL_COLORS[i % FUNNEL_COLORS.length];
          const isWorst = i === worstDropIdx;

          return (
            <div key={step.step}>
              {i > 0 && step.drop_off != null && (
                <div className="flex items-center gap-2.5 py-1.5 pl-3">
                  <div className="flex flex-col items-center">
                    <div className="h-3 w-px bg-white/[0.08]" />
                    <svg className="h-2 w-2 text-slate-700" viewBox="0 0 8 8" fill="currentColor">
                      <path d="M4 8L0 2h8L4 8z" />
                    </svg>
                  </div>
                  <span className={`text-[11px] tabular-nums ${isWorst ? "font-semibold text-rose-400" : "text-rose-400/50"}`}>
                    ↓ {step.drop_off}% lost
                    {isWorst && (
                      <span className="ml-1.5 rounded bg-rose-500/15 px-1.5 py-px text-[9px] font-semibold uppercase tracking-[0.08em] text-rose-300">
                        biggest drop
                      </span>
                    )}
                  </span>
                </div>
              )}

              <div className="flex items-center gap-4">
                <div className="relative min-w-0 flex-1">
                  <div
                    className={`relative flex items-center rounded-lg px-4 py-3 transition-all duration-300 ${colors.glow}`}
                    style={{ width: `${widthPct}%`, minWidth: "140px" }}
                  >
                    <div className={`absolute inset-0 rounded-lg ${colors.bar}`} />
                    <div className="relative z-10 flex w-full items-center justify-between gap-3">
                      <span className="text-[12px] font-semibold uppercase tracking-[0.1em] text-white/90">
                        {step.label}
                      </span>
                      <span className="text-[15px] font-bold tabular-nums text-white">
                        {formatNumber(step.count)}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="w-16 flex-shrink-0 text-right">
                  <span className={`text-[13px] font-semibold tabular-nums ${i === 0 ? "text-slate-400" : colors.text}`}>
                    {step.pct != null ? `${step.pct}%` : "—"}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {worstDropIdx > 0 && (
        <div className="border-t border-white/[0.06] px-5 py-3">
          <div className="flex items-start gap-2.5">
            <Image src="/branding/hedgespark/spark.png" alt="" width={18} height={18} className="mt-0.5 flex-shrink-0 opacity-80" />
            <p className="text-[12px] leading-[1.55] text-slate-400">
              Your biggest leak is between{" "}
              <span className="font-medium text-slate-300">{steps[worstDropIdx - 1]?.label}</span>
              {" "}and{" "}
              <span className="font-medium text-slate-300">{steps[worstDropIdx]?.label}</span>
              {" "}— {worstDrop}% of visitors don&apos;t make it through.
              {worstDropIdx === 1 && " Reducing friction at add-to-cart would have the biggest impact."}
              {worstDropIdx === 2 && " Checkout friction or trust signals may need attention."}
              {worstDropIdx === 3 && " Payment or shipping costs may be causing abandonment."}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
