"use client";

/**
 * SparkBrief — First-screen focus component.
 *
 * The top of the dashboard must answer three questions in one glance:
 *   1. What is happening in my store?
 *   2. Is this good or bad?
 *   3. What should I do?
 *
 * SparkBrief renders ONE summary sentence + exactly THREE focus cards:
 *   - Revenue at risk
 *   - Conversion opportunities
 *   - High-intent visitors
 *
 * No tables. No clutter. No more than 3 cards. The merchant gets the whole
 * state of their store in under 30 seconds.
 *
 * All text is designed to read like a smart colleague told them, not an
 * engineer dumped metrics on them. Numbers are contextualised ("€320/day",
 * not "loss: 320"). Tone leads with the opportunity, never the alarm.
 */

import Image from "next/image";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type SparkBriefProps = {
  /** Summary headline — 1 sentence in plain English. */
  summary: string;
  /** Optional sub-line with the ONE suggested next step. */
  nextStep?: string;

  /** Euro amount estimated at risk across the measured window. */
  revenueAtRisk?: number;
  /** Number of products showing an actionable opportunity right now. */
  opportunityCount?: number;
  /** Number of high-intent visitors active in the current window. */
  highIntentVisitors?: number;

  /** When true the 3 cards collapse into a "warming up" state. */
  isColdStart?: boolean;
  /** When true we show the loading skeleton instead of data. */
  loading?: boolean;

  /** Optional click handlers — if unset, cards are non-interactive. */
  onRevenueClick?: () => void;
  onOpportunitiesClick?: () => void;
  onVisitorsClick?: () => void;
};

// ---------------------------------------------------------------------------
// Number formatting — short, human
// ---------------------------------------------------------------------------

function formatCurrency(v: number | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (v >= 1000) return `€${Math.round(v / 100) / 10}k`;
  return `€${Math.round(v)}`;
}

function formatCount(v: number | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (v >= 1000) return `${Math.round(v / 100) / 10}k`;
  return String(Math.round(v));
}

// ---------------------------------------------------------------------------
// Card primitive — small, consistent, non-clickable by default
// ---------------------------------------------------------------------------

type CardTone = "risk" | "opportunity" | "intent";

const TONE: Record<CardTone, { ring: string; glow: string; accent: string; label: string }> = {
  risk: {
    ring: "border-rose-500/20",
    glow: "shadow-[0_0_40px_rgba(244,63,94,0.08)]",
    accent: "text-rose-300",
    label: "text-rose-200/70",
  },
  opportunity: {
    ring: "border-violet-500/20",
    glow: "shadow-[0_0_40px_rgba(124,58,237,0.10)]",
    accent: "text-violet-300",
    label: "text-violet-200/70",
  },
  intent: {
    ring: "border-emerald-500/20",
    glow: "shadow-[0_0_40px_rgba(16,185,129,0.08)]",
    accent: "text-emerald-300",
    label: "text-emerald-200/70",
  },
};

function FocusCard({
  tone,
  label,
  value,
  caption,
  onClick,
  loading,
}: {
  tone: CardTone;
  label: string;
  value: string;
  caption: string;
  onClick?: () => void;
  loading?: boolean;
}) {
  const t = TONE[tone];
  const interactive = typeof onClick === "function";
  const Tag = interactive ? "button" : "div";
  return (
    <Tag
      onClick={onClick}
      className={[
        "group relative overflow-hidden rounded-2xl border p-5 text-left transition-all",
        "bg-white/[0.02]",
        t.ring,
        t.glow,
        interactive
          ? "hover:border-white/20 hover:bg-white/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/40"
          : "",
      ].join(" ")}
    >
      <div className={`mb-2 text-[11px] font-semibold uppercase tracking-wider ${t.label}`}>
        {label}
      </div>
      {loading ? (
        <>
          <div className="mb-2 h-9 w-24 animate-pulse rounded bg-white/[0.06]" />
          <div className="h-4 w-40 animate-pulse rounded bg-white/[0.04]" />
        </>
      ) : (
        <>
          <div className={`text-[32px] font-semibold leading-none ${t.accent}`}>{value}</div>
          <div className="mt-2 text-[13px] leading-snug text-slate-300/80">{caption}</div>
        </>
      )}
      {interactive && (
        <div className="mt-3 flex items-center gap-1 text-[12px] font-medium text-slate-400 opacity-0 transition-opacity group-hover:opacity-100">
          View details
          <svg className="h-3 w-3" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M4 3 L8 6 L4 9" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      )}
    </Tag>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function SparkBrief({
  summary,
  nextStep,
  revenueAtRisk,
  opportunityCount,
  highIntentVisitors,
  isColdStart,
  loading,
  onRevenueClick,
  onOpportunitiesClick,
  onVisitorsClick,
}: SparkBriefProps) {
  return (
    <section className="hs-fade-up">
      {/* ---------------- Summary strip ---------------- */}
      <div className="relative mb-5 overflow-hidden rounded-2xl border border-white/[0.08] bg-gradient-to-br from-violet-500/[0.06] via-transparent to-transparent p-5 shadow-[0_0_48px_rgba(124,58,237,0.06)]">
        <div className="flex items-start gap-4">
          {/* Spark mascot */}
          <div className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500/20 to-violet-600/5 ring-1 ring-violet-400/20">
            <Image
              src="/branding/hedgespark/spark.png"
              alt=""
              width={28}
              height={28}
              className="hs-float"
              priority
            />
          </div>
          <div className="flex-1 min-w-0">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-violet-300/80">
              Spark Brief
            </div>
            <p className="text-[15px] leading-relaxed text-slate-100">
              {loading ? (
                <span className="inline-block h-4 w-96 max-w-full animate-pulse rounded bg-white/[0.06]" />
              ) : (
                summary
              )}
            </p>
            {!loading && nextStep && (
              <p className="mt-2 text-[13px] leading-relaxed text-slate-400">
                <span className="text-violet-300">Suggested next:</span> {nextStep}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* ---------------- Three focus cards ---------------- */}
      <div className="grid gap-3 sm:grid-cols-3">
        <FocusCard
          tone="risk"
          label="Revenue at risk"
          value={isColdStart ? "—" : formatCurrency(revenueAtRisk)}
          caption={
            isColdStart
              ? "Watching your store. We'll spot leaks as they appear."
              : "Estimated loss this week if nothing changes."
          }
          onClick={onRevenueClick}
          loading={loading}
        />
        <FocusCard
          tone="opportunity"
          label="Opportunities"
          value={isColdStart ? "—" : formatCount(opportunityCount)}
          caption={
            isColdStart
              ? "We'll surface fixes once visitors arrive."
              : "Actions ready to apply right now."
          }
          onClick={onOpportunitiesClick}
          loading={loading}
        />
        <FocusCard
          tone="intent"
          label="High-intent visitors"
          value={isColdStart ? "—" : formatCount(highIntentVisitors)}
          caption={
            isColdStart
              ? "Real-time intent tracking is on."
              : "Shoppers browsing with buying signals today."
          }
          onClick={onVisitorsClick}
          loading={loading}
        />
      </div>
    </section>
  );
}
