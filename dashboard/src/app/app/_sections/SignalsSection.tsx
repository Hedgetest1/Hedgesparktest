"use client";

/**
 * SignalsSection — "Findings" alert cards + Revenue at Risk summary.
 * Extracted from app/page.tsx PageInner (Phase Ω⁷ split).
 */

import { SectionHeading } from "../_components/SectionHeading";
import { impactClass, prettyText } from "../_lib/formatters";

/* eslint-disable @typescript-eslint/no-explicit-any */
export interface SignalsSectionProps {
  alerts: any[];
  strongSignals: any[];
  earlySignals: any[];
  isColdStart: boolean;
}

export function SignalsSection(p: SignalsSectionProps) {
  const { alerts, strongSignals, earlySignals, isColdStart } = p;

  return (
    <section id="section-signals">
      <SectionHeading eyebrow="Findings" title={strongSignals.length > 0 ? "What we found" : "Needs attention"} />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {alerts.length === 0 ? (
          <p className="lg:col-span-3 rounded-xl border border-white/[0.05] bg-white/[0.02] px-5 py-4 text-[15px] text-slate-500">
            {isColdStart && earlySignals.length === 0
              ? "Waiting for first visitors..."
              : isColdStart
              ? "Analyzing behavior — findings shortly."
              : "All clear — store looks healthy."}
          </p>
        ) : (
          <>
            {alerts.slice(0, 2).map((alert: any, i: number) => (
              <div
                key={`${alert.type || "alert"}-${i}`}
                className="flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5"
              >
                <div className="mb-3 flex items-center gap-2.5">
                  <span className={`rounded-lg px-2.5 py-1 text-[12px] font-bold uppercase tracking-wide ${impactClass(alert.priority)}`}>
                    {alert.priority || "Info"}
                  </span>
                  <span className="text-[13px] font-medium text-slate-400">{prettyText(alert.type)}</span>
                </div>
                <p className="flex-1 text-[15px] leading-[1.6] text-slate-300">{alert.message || "—"}</p>
                {alert.action && (
                  <div className="mt-3 rounded-xl border border-emerald-400/15 bg-emerald-500/[0.05] px-4 py-3">
                    <div className="mb-1 flex items-center gap-2">
                      <svg className="h-3.5 w-3.5 text-emerald-400/70" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
                      </svg>
                      <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-emerald-300/70">Fix</span>
                      <span className="rounded border border-[#d4893a]/25 bg-[#d4893a]/10 px-1.5 py-[2px] text-[10px] font-bold text-[#d4893a]/70">PRO</span>
                    </div>
                    <p className="text-[14px] leading-[1.5] text-slate-200">{alert.action}</p>
                  </div>
                )}
              </div>
            ))}

            {/* Summary card */}
            <div className="flex flex-col rounded-2xl border border-[#d4893a]/15 bg-gradient-to-br from-[#d4893a]/[0.04] to-transparent p-5">
              <div className="mb-3 flex items-center gap-2.5">
                <span className="rounded-lg bg-[#d4893a]/15 px-2.5 py-1 text-[12px] font-bold uppercase tracking-wide text-[#e8a04e] ring-1 ring-[#d4893a]/25">
                  Summary
                </span>
              </div>
              <div className="flex-1">
                <div className="text-[2rem] font-extrabold text-[#e8a04e]">
                  {alerts.length}
                </div>
                <div className="text-[15px] font-medium text-slate-300">
                  finding{alerts.length !== 1 ? "s" : ""} on your store
                </div>
                <p className="mt-2 text-[14px] text-slate-500">
                  {strongSignals.length > 0
                    ? `${strongSignals.length} confirmed signal${strongSignals.length !== 1 ? "s" : ""} requiring attention.`
                    : "Monitoring your products for issues."}
                </p>
              </div>
              {strongSignals.length > 0 && (
                <div className="mt-3 flex items-center gap-2">
                  <div className="h-2 w-2 rounded-full bg-[#d4893a] shadow-[0_0_8px_rgba(212,137,58,0.6)]" />
                  <span className="text-[13px] font-semibold text-[#e8a04e]">Active</span>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
