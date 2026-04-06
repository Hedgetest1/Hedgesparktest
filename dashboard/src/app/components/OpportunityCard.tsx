"use client";

/**
 * OpportunityCard — the merchant's "apply fix" moment.
 *
 * This is THE card the whole dashboard leads merchants towards. Everything
 * else — the brief, the cards, the explanations — exists so that when the
 * merchant arrives here, they trust what they see and feel safe clicking
 * the button.
 *
 * Structure (top-to-bottom):
 *   • Kicker           "Spark found an opportunity"
 *   • Title            short product / situation name
 *   • Impact chip      "Potential +€120/day" — front and centre
 *   • Description      one paragraph of plain English
 *   • Trust line       confidence + data quality + "you can undo"
 *   • Actions          [Apply fix]  [What will happen?]
 *
 * Clicking "What will happen?" expands an inline preview of the change
 * before the merchant commits. The Apply button calls onApply and shows
 * a brief optimistic success state.
 */

import { useState } from "react";

export type OpportunityCardProps = {
  /** Short noun — e.g. product name, "Cart page", "Checkout step 2". */
  title: string;
  /** Euro impact estimate already computed by the backend. */
  impactEur?: number;
  /** Timeframe the impact applies to: "/day" or "/week". */
  impactWindow?: "day" | "week";
  /** Plain-English description of the opportunity. 1–2 sentences. */
  description: string;
  /** What will happen when the merchant clicks Apply fix. 1–2 sentences. */
  preview: string;
  /** Trust signals. */
  confidence?: "High" | "Medium" | "Low";
  dataQuality?: "High" | "Medium" | "Low";
  /** Fires when the merchant clicks Apply fix. */
  onApply: () => void | Promise<void>;
  /** Optional dismiss callback — hides card + records the dismissal. */
  onDismiss?: () => void;
};

const TRUST_TONE = {
  High: "text-emerald-300",
  Medium: "text-amber-300",
  Low: "text-slate-400",
} as const;

export default function OpportunityCard({
  title,
  impactEur,
  impactWindow = "day",
  description,
  preview,
  confidence = "High",
  dataQuality = "High",
  onApply,
  onDismiss,
}: OpportunityCardProps) {
  const [showPreview, setShowPreview] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState(false);

  const impactLabel = (() => {
    if (impactEur == null || !Number.isFinite(impactEur)) return null;
    const rounded = impactEur >= 1000 ? `€${Math.round(impactEur / 100) / 10}k` : `€${Math.round(impactEur)}`;
    return `Potential +${rounded}/${impactWindow}`;
  })();

  async function handleApply() {
    if (applying || applied) return;
    setApplying(true);
    try {
      await onApply();
      setApplied(true);
    } finally {
      setApplying(false);
    }
  }

  return (
    <article className="relative overflow-hidden rounded-2xl border border-violet-500/15 bg-gradient-to-br from-violet-500/[0.06] via-transparent to-transparent p-5 shadow-[0_0_40px_rgba(124,58,237,0.08)]">
      {/* Dismiss (top-right) */}
      {onDismiss && !applied && (
        <button
          onClick={onDismiss}
          aria-label="Dismiss this opportunity"
          className="absolute right-3 top-3 rounded-md p-1 text-slate-500 transition hover:bg-white/[0.05] hover:text-slate-300"
        >
          <svg className="h-3.5 w-3.5" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M3 3 L9 9 M9 3 L3 9" strokeLinecap="round" />
          </svg>
        </button>
      )}

      {/* Kicker */}
      <div className="mb-2 inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-violet-300">
        <svg className="h-3 w-3" viewBox="0 0 12 12" fill="currentColor" aria-hidden>
          <path d="M6 1 L7 4.5 L10.5 6 L7 7.5 L6 11 L5 7.5 L1.5 6 L5 4.5 Z" />
        </svg>
        Spark found an opportunity
      </div>

      {/* Title */}
      <h3 className="mb-3 text-[16px] font-semibold leading-snug text-slate-50">{title}</h3>

      {/* Impact chip */}
      {impactLabel && (
        <div className="mb-3 inline-flex items-center rounded-lg border border-emerald-400/20 bg-emerald-500/[0.08] px-3 py-1.5">
          <span className="text-[14px] font-semibold text-emerald-300">{impactLabel}</span>
        </div>
      )}

      {/* Description */}
      <p className="mb-4 text-[13px] leading-relaxed text-slate-300">{description}</p>

      {/* Inline preview (collapsible) */}
      {showPreview && (
        <div className="mb-4 rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            What will happen
          </div>
          <p className="text-[13px] leading-relaxed text-slate-200">{preview}</p>
        </div>
      )}

      {/* Trust line */}
      <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
        <span className="text-slate-500">
          Confidence:{" "}
          <span className={`font-semibold ${TRUST_TONE[confidence]}`}>{confidence}</span>
        </span>
        <span className="text-slate-600">·</span>
        <span className="text-slate-500">
          Data quality:{" "}
          <span className={`font-semibold ${TRUST_TONE[dataQuality]}`}>{dataQuality}</span>
        </span>
        <span className="text-slate-600">·</span>
        <span className="text-slate-500">You can undo this anytime</span>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2">
        {applied ? (
          <span className="inline-flex items-center gap-2 rounded-lg border border-emerald-400/25 bg-emerald-500/[0.08] px-4 py-2 text-[13px] font-semibold text-emerald-300">
            <svg className="h-3.5 w-3.5" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M2.5 6 L5 8.5 L9.5 3.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Applied — watching for impact
          </span>
        ) : (
          <>
            <button
              onClick={handleApply}
              disabled={applying}
              className="rounded-lg bg-violet-600 px-5 py-2 text-[13px] font-semibold text-white transition-all hover:bg-violet-500 hover:shadow-[0_0_28px_rgba(124,58,237,0.4)] focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/40 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {applying ? "Applying…" : "Apply fix"}
            </button>
            <button
              onClick={() => setShowPreview((v) => !v)}
              className="rounded-lg border border-white/[0.10] px-4 py-2 text-[13px] font-medium text-slate-300 transition hover:border-white/[0.18] hover:bg-white/[0.03] hover:text-slate-100"
            >
              {showPreview ? "Hide details" : "What will happen?"}
            </button>
          </>
        )}
      </div>
    </article>
  );
}
