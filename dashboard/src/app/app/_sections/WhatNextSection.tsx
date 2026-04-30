"use client";

/**
 * WhatNextSection — Spark Action Engine "what to do next" queue.
 * Extracted from app/page.tsx PageInner (Phase Ω⁷ split).
 */

import Image from "next/image";

import { SectionHeading } from "../_components/SectionHeading";
import { formatDisplayMoney, type DisplayCurrency } from "../../lib/currency";

/* eslint-disable @typescript-eslint/no-explicit-any */
export interface WhatNextSectionProps {
  sparkActions: any[];
  isProUser: boolean;
  displayCurrency: DisplayCurrency;
  setUpgradeModalOpen: (v: boolean) => void;
}

export function WhatNextSection(p: WhatNextSectionProps) {
  const { sparkActions, isProUser, displayCurrency, setUpgradeModalOpen } = p;

  return (
    <section id="section-what-next">
      <h2 className="mb-6 text-[2.25rem] font-extrabold leading-[1.05] tracking-tight text-[#e8a04e] sm:text-[2.75rem]">
        What to do next
      </h2>
      <SectionHeading
        eyebrow="Actions"
        title="What to do next"
        description="Ranked by revenue impact. Each action is derived from your real store data."
      />
      <div className="space-y-3">
        {sparkActions.slice(0, isProUser ? 5 : 2).map((act: any, i: number) => (
          <div
            key={act.id}
            className={`group overflow-hidden rounded-2xl border transition-all duration-150 hover:shadow-[0_2px_16px_rgba(0,0,0,0.15)] ${
              act.priority === "CRITICAL"
                ? "border-rose-400/20 bg-gradient-to-r from-rose-500/[0.04] to-transparent"
                : act.priority === "HIGH"
                ? "border-amber-400/15 bg-white/[0.02]"
                : "border-white/[0.07] bg-white/[0.02]"
            }`}
          >
            <div className="px-5 py-4">
              <div className="mb-2.5 flex items-start justify-between gap-3">
                <div className="flex items-center gap-2.5">
                  <span className={`flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] ${
                    act.priority === "CRITICAL"
                      ? "bg-rose-500/20 text-rose-300 ring-1 ring-rose-400/30"
                      : act.priority === "HIGH"
                      ? "bg-amber-500/15 text-amber-300 ring-1 ring-amber-400/25"
                      : "bg-white/5 text-slate-400 ring-1 ring-white/10"
                  }`}>
                    {i === 0 ? "#1 Priority" : act.priority}
                  </span>
                  {act.isPattern && (
                    <span className="flex-shrink-0 rounded-full bg-violet-500/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-violet-300 ring-1 ring-violet-400/25">
                      Store-wide
                    </span>
                  )}
                  {act.proofStatus === "improving" && (
                    <span className="flex-shrink-0 rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-emerald-300 ring-1 ring-emerald-400/25">
                      Improving
                    </span>
                  )}
                  {act.proofStatus === "worsening" && (
                    <span className="flex-shrink-0 rounded-full bg-rose-500/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-rose-300 ring-1 ring-rose-400/25">
                      Worsening
                    </span>
                  )}
                  {act.trend === "falling" && !act.isPattern && (
                    <span className="flex-shrink-0 rounded-full bg-rose-500/10 px-2 py-0.5 text-[10px] font-medium text-rose-300/70">
                      ↓ Traffic falling
                    </span>
                  )}
                  {act.trend === "rising" && !act.isPattern && (
                    <span className="flex-shrink-0 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300/70">
                      ↑ Traffic rising
                    </span>
                  )}
                  {act.segment && !act.isPattern && (
                    <span className={`flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${
                      act.segment === "Mobile issue" || act.segment === "Desktop issue"
                        ? "bg-pink-500/10 text-pink-300/80 ring-pink-400/20"
                        : act.segment === "Paid traffic"
                        ? "bg-yellow-500/10 text-yellow-300/80 ring-yellow-400/20"
                        : act.segment === "Worsening"
                        ? "bg-red-500/10 text-red-300/80 ring-red-400/20"
                        : act.segment === "Improving"
                        ? "bg-emerald-500/10 text-emerald-300/80 ring-emerald-400/20"
                        : act.segment === "Landing page issue"
                        ? "bg-orange-500/10 text-orange-300/80 ring-orange-400/20"
                        : act.segment === "Timing mismatch"
                        ? "bg-indigo-500/10 text-indigo-300/80 ring-indigo-400/20"
                        : act.segment === "Upsell opportunity"
                        ? "bg-violet-500/10 text-violet-300/80 ring-violet-400/20"
                        : act.segment === "Bundle opportunity"
                        ? "bg-cyan-500/10 text-cyan-300/80 ring-cyan-400/20"
                        : act.segment === "Revenue concentration"
                        ? "bg-amber-500/10 text-amber-200/80 ring-amber-400/20"
                        : "bg-white/[0.04] text-slate-500 ring-white/[0.06]"
                    }`}>
                      {act.segment}
                    </span>
                  )}
                  <h3 className="text-[14px] font-semibold text-white">{act.title}</h3>
                </div>
                {act.impactValue > 0 && (
                  <span className="flex-shrink-0 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[11px] font-semibold tabular-nums text-emerald-300">
                    ~{formatDisplayMoney(act.impactValue, "USD", displayCurrency)}/wk
                  </span>
                )}
              </div>

              <p className="mb-2 text-[12px] leading-[1.6] text-slate-400">
                {act.context}
              </p>

              {isProUser ? (
                <div className="mb-2 rounded-lg border border-violet-400/15 bg-violet-500/[0.04] px-3 py-2">
                  <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-[0.14em] text-violet-300/60">What to do</div>
                  <p className="text-[12px] leading-[1.55] text-slate-300">{act.action}</p>
                </div>
              ) : (
                <div
                  className="mb-2 cursor-pointer rounded-lg border border-violet-400/10 bg-violet-500/[0.03] px-3 py-2 transition hover:border-violet-400/25"
                  onClick={() => setUpgradeModalOpen(true)}
                >
                  <p className="text-[12px] text-slate-400">Specific action available <span className="text-violet-400/70">in Pro</span></p>
                </div>
              )}

              {act.proofDetail && (
                <div className={`mb-2 flex items-center gap-2 rounded-lg px-3 py-1.5 text-[11px] ${
                  act.proofStatus === "improving"
                    ? "border border-emerald-400/15 bg-emerald-500/[0.05] text-emerald-300/80"
                    : "border border-rose-400/15 bg-rose-500/[0.05] text-rose-300/80"
                }`}>
                  <span>{act.proofStatus === "improving" ? "↑" : "↓"}</span>
                  <span>{act.proofDetail}</span>
                </div>
              )}

              <div className="flex items-start gap-2">
                <Image src="/branding/hedgespark/spark.png" alt="" width={16} height={16} className="mt-0.5 flex-shrink-0" />
                <p className="text-[11px] leading-[1.5] text-emerald-300/60">{act.impact}</p>
              </div>
            </div>
          </div>
        ))}

        {!isProUser && sparkActions.length > 2 && (
          <div
            className="flex cursor-pointer items-center justify-between rounded-xl border border-violet-400/15 bg-violet-500/[0.04] px-5 py-3 transition hover:border-violet-400/25"
            onClick={() => setUpgradeModalOpen(true)}
          >
            <span className="text-[12px] text-slate-400">
              + {sparkActions.length - 2} more action{sparkActions.length - 2 !== 1 ? "s" : ""} identified
            </span>
            <span className="text-[11px] text-violet-400 transition hover:text-violet-300">Unlock with Pro →</span>
          </div>
        )}
      </div>
    </section>
  );
}
