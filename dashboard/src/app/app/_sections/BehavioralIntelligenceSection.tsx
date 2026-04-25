"use client";

/**
 * BehavioralIntelligenceSection — the Pro "moat" cassettone.
 *
 * Links pre-purchase behavioral signals (scroll / dwell / visit pattern /
 * source) to post-purchase LTV. The single killer number (high-engagement
 * ratio vs low-engagement) is the visual differentiator vs every other
 * Shopify analytics tool.
 *
 * Extracted from app/page.tsx PageInner (Phase Ω⁷ split). Takes the raw
 * behavioralData shape; all derived state is computed in-component.
 */

import { SectionHeading } from "../_components/SectionHeading";
import { formatDisplayMoney, type DisplayCurrency } from "../../lib/currency";

/* eslint-disable @typescript-eslint/no-explicit-any */
export interface BehavioralIntelligenceSectionProps {
  data: any;   // BehavioralCohortsData from /pro/cohorts/behavioral
  displayCurrency: DisplayCurrency;
}

export function BehavioralIntelligenceSection({
  data: behavioralData,
  displayCurrency,
}: BehavioralIntelligenceSectionProps) {
  const insights = behavioralData.insights;
  const byEngagement = behavioralData.segments.by_engagement;
  const byVisit = behavioralData.segments.by_visit_pattern;
  const bySource = behavioralData.segments.by_source;
  const coverage = behavioralData.data_coverage;

  type BehSegment = { avg_revenue: number; [k: string]: any };
  const maxRev = (arr: readonly BehSegment[]) =>
    arr.length > 0 ? Math.max(...arr.map((s) => s.avg_revenue), 1) : 1;
  const maxEng = maxRev(byEngagement);
  const maxVis = maxRev(byVisit);
  const maxSrc = maxRev(bySource);

  const engagementColor = (level: string) => {
    if (level === "HIGH") return "#34d399";
    if (level === "MEDIUM") return "#e8a04e";
    if (level === "LOW") return "#f87171";
    return "#94a3b8";
  };
  const visitColor = (level: string) =>
    level === "REPEAT_VISITOR" ? "#34d399" : "#e8a04e";

  const sourceLabel = (code: string) => {
    const map: Record<string, string> = {
      SEARCH: "Search",
      SOCIAL: "Social",
      DIRECT: "Direct",
      EMAIL_SMS: "Email / SMS",
      REFERRAL: "Referral",
      PAID: "Paid ads",
      ORGANIC: "Organic",
      UNKNOWN: "Unknown",
      OTHER: "Other",
    };
    return map[code] ?? code;
  };

  type SegmentRow = {
    label: string;
    customers: number;
    avg_revenue: number;
    repeat_rate: number;
    color: string;
  };

  const renderSegmentCassettone = (
    eyebrow: string,
    headline: string,
    rows: SegmentRow[],
    maxR: number,
    emptyMsg: string,
  ) => (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-4">
        <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-[#d946ef]">
          {eyebrow}
        </div>
        <h4 className="mt-1 text-[13px] font-semibold text-white">{headline}</h4>
      </div>
      {rows.length === 0 ? (
        <p className="text-[11px] text-slate-400">{emptyMsg}</p>
      ) : (
        <div className="space-y-3">
          {rows.map((row, i) => {
            const barWidth = Math.max(6, Math.round((row.avg_revenue / maxR) * 100));
            return (
              <div key={i}>
                <div className="mb-1.5 flex items-center justify-between text-[11px]">
                  <div className="flex items-center gap-2">
                    <span
                      className="h-2 w-2 flex-shrink-0 rounded-full"
                      style={{ backgroundColor: row.color, boxShadow: `0 0 6px ${row.color}66` }}
                    />
                    <span className="font-semibold" style={{ color: row.color }}>
                      {row.label}
                    </span>
                    <span className="text-slate-600">{row.customers} cust</span>
                  </div>
                  <div className="tabular-nums">
                    <span className="font-bold text-white">
                      {formatDisplayMoney(row.avg_revenue, "USD", displayCurrency)}
                    </span>
                    <span className="ml-2 text-slate-500">
                      {Math.round(row.repeat_rate * 100)}% repeat
                    </span>
                  </div>
                </div>
                <div className="h-1 overflow-hidden rounded-full bg-white/[0.04]">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${barWidth}%`,
                      background: `linear-gradient(90deg, ${row.color} 0%, ${row.color}99 100%)`,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  const engagementRows: SegmentRow[] = byEngagement.map((s: any) => ({
    label: s.segment === "HIGH" ? "High engagement" : s.segment === "MEDIUM" ? "Medium engagement" : "Low engagement",
    customers: s.customers,
    avg_revenue: s.avg_revenue,
    repeat_rate: s.repeat_rate,
    color: engagementColor(s.segment),
  }));
  const visitRows: SegmentRow[] = byVisit.map((s: any) => ({
    label: s.segment === "REPEAT_VISITOR" ? "Repeat visitors" : "Single visit",
    customers: s.customers,
    avg_revenue: s.avg_revenue,
    repeat_rate: s.repeat_rate,
    color: visitColor(s.segment),
  }));
  const sourceRows: SegmentRow[] = bySource.map((s: any) => ({
    label: sourceLabel(s.segment),
    customers: s.customers,
    avg_revenue: s.avg_revenue,
    repeat_rate: s.repeat_rate,
    color: "#c4b5fd",
  }));

  // Moat hero — the differentiator visualization
  const findTier = (name: string): any =>
    byEngagement.find((s: any) => s.segment === name);
  const highTier = findTier("HIGH");
  const lowTier = findTier("LOW") || findTier("MEDIUM");

  const moatIsLive =
    highTier != null &&
    lowTier != null &&
    highTier.segment !== lowTier.segment &&
    highTier.avg_revenue > 0 &&
    lowTier.avg_revenue > 0 &&
    highTier.customers >= 2 &&
    lowTier.customers >= 2;
  const moatRatio = moatIsLive ? highTier!.avg_revenue / lowTier!.avg_revenue : 0;
  const moatTierLabel = lowTier
    ? lowTier.segment === "MEDIUM" ? "medium-engagement" : "low-engagement"
    : "low-engagement";

  return (
    <section id="section-behavioral-intelligence">
      <SectionHeading
        eyebrow="Behavioral DNA"
        title="What separates your buyers from your browsers"
      />

      {/* Moat hero */}
      <div
        className="mb-5 overflow-hidden rounded-2xl border"
        style={{
          borderColor: "rgba(217, 70, 239, 0.22)",
          background:
            "linear-gradient(135deg, rgba(217, 70, 239, 0.08) 0%, rgba(124, 58, 237, 0.04) 45%, rgba(217, 70, 239, 0.02) 100%)",
        }}
      >
        {moatIsLive ? (
          <div className="p-6">
            <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#d946ef]">
              The HedgeSpark moat
            </div>

            <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="text-[13px] font-medium text-slate-300">
                High-engagement buyers are worth
              </span>
              <span
                className="text-[44px] font-extrabold leading-none tabular-nums"
                style={{ color: "#d946ef", textShadow: "0 0 28px rgba(217, 70, 239, 0.4)" }}
              >
                {moatRatio.toFixed(1)}×
              </span>
              <span className="text-[13px] font-medium text-slate-300">
                more than {moatTierLabel} buyers.
              </span>
            </div>

            <div className="mt-5 space-y-3">
              <div>
                <div className="mb-1.5 flex items-center justify-between text-[11px]">
                  <div className="flex items-center gap-2">
                    <span
                      className="h-2 w-2 rounded-full"
                      style={{ backgroundColor: "#d946ef", boxShadow: "0 0 10px rgba(217, 70, 239, 0.7)" }}
                    />
                    <span className="font-bold uppercase tracking-[0.08em] text-[#d946ef]">
                      High engagement
                    </span>
                    <span className="text-slate-500">{highTier!.customers} customers</span>
                  </div>
                  <span className="text-[16px] font-extrabold tabular-nums text-white">
                    {formatDisplayMoney(highTier!.avg_revenue, "USD", displayCurrency)}
                  </span>
                </div>
                <div className="h-2.5 overflow-hidden rounded-full bg-white/[0.04]">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: "100%",
                      background: "linear-gradient(90deg, #d946ef 0%, #a855f7 100%)",
                      boxShadow: "0 0 14px -2px rgba(217, 70, 239, 0.6)",
                    }}
                  />
                </div>
              </div>

              <div>
                <div className="mb-1.5 flex items-center justify-between text-[11px]">
                  <div className="flex items-center gap-2">
                    <span className="h-2 w-2 rounded-full bg-slate-500" />
                    <span className="font-bold uppercase tracking-[0.08em] text-slate-400">
                      {lowTier!.segment === "MEDIUM" ? "Medium engagement" : "Low engagement"}
                    </span>
                    <span className="text-slate-500">{lowTier!.customers} customers</span>
                  </div>
                  <span className="text-[16px] font-extrabold tabular-nums text-slate-400">
                    {formatDisplayMoney(lowTier!.avg_revenue, "USD", displayCurrency)}
                  </span>
                </div>
                <div className="h-2.5 overflow-hidden rounded-full bg-white/[0.04]">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.max(6, Math.round((lowTier!.avg_revenue / highTier!.avg_revenue) * 100))}%`,
                      background: "linear-gradient(90deg, #64748b 0%, #475569 100%)",
                    }}
                  />
                </div>
              </div>
            </div>

            <div className="mt-5 border-t border-white/[0.07] pt-4">
              <p className="text-[12px] leading-relaxed text-slate-300">
                <strong className="text-white">This is the HedgeSpark moat.</strong>{" "}
                Every other Shopify analytics tool segments customers by
                <em> what</em> they bought. We segment them by
                <strong className="text-white"> how they behaved before buying</strong> —
                linking scroll depth, dwell time, and visit pattern to real
                lifetime value. Structurally impossible to replicate without
                first-party behavioral tracking joined to order attribution.
              </p>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[10px] text-slate-400">
              <div className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.02] px-2.5 py-1">
                <span className="h-1 w-1 rounded-full bg-[#d946ef]" />
                <span>Measured from {coverage.segmentable_customers} identified customers</span>
              </div>
              <div className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.02] px-2.5 py-1">
                <span className="h-1 w-1 rounded-full bg-[#d946ef]" />
                <span>{behavioralData.window_days}-day window</span>
              </div>
              <div className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.02] px-2.5 py-1">
                <span className="h-1 w-1 rounded-full bg-[#d946ef]" />
                <span>First-party behavioral data, zero third-party cookies</span>
              </div>
            </div>

            {insights.length > 0 && (
              <div className="mt-4 space-y-1.5 border-t border-white/[0.06] pt-4">
                {insights.map((insight: string, i: number) => (
                  <p key={i} className="text-[12px] leading-relaxed text-slate-400">
                    <span className="mr-2 text-[#d946ef]">›</span>
                    {insight}
                  </p>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="p-5">
            <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#d946ef]">
              The moat — what only HedgeSpark can show you
            </div>
            <p className="mt-1.5 text-[13px] leading-relaxed text-slate-300">
              Competitors segment customers by <em>what</em> they bought.
              HedgeSpark segments them by{" "}
              <strong className="text-white">how they behaved before buying</strong> —
              linking scroll depth, dwell time, and visit pattern to actual
              revenue outcomes. The killer ratio becomes visible here once we
              have 2+ customers in both a high-engagement and a low-engagement
              tier with real revenue on both sides.
            </p>
            {insights.length > 0 && (
              <div className="mt-4 space-y-1.5 border-t border-white/[0.06] pt-4">
                {insights.map((insight: string, i: number) => (
                  <p key={i} className="text-[12px] leading-relaxed text-slate-400">
                    <span className="mr-2 text-[#d946ef]">›</span>
                    {insight}
                  </p>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        {renderSegmentCassettone("By Engagement", "Scroll + dwell + visits", engagementRows, maxEng, "Needs visitor behavior data to segment.")}
        {renderSegmentCassettone("By Visit Pattern", "Browsing before purchase", visitRows, maxVis, "Needs visitor session data.")}
        {renderSegmentCassettone("By Traffic Source", "Channel buyer quality", sourceRows, maxSrc, "Needs attributed orders.")}
      </div>

      {coverage.total_customers > 0 && (
        <div className="mt-4 inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3 py-1">
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ backgroundColor: coverage.coverage_rate > 0.7 ? "#34d399" : "#fb923c" }}
          />
          <span className="text-[10px] text-slate-400">
            {coverage.segmentable_customers} of {coverage.total_customers} customers have behavioral data
            ({Math.round(coverage.coverage_rate * 100)}% coverage)
          </span>
        </div>
      )}
    </section>
  );
}
