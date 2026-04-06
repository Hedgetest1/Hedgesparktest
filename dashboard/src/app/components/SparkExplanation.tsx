"use client";

/**
 * SparkExplanation — the trust engine.
 *
 * Every insight the system surfaces MUST be explainable in human language.
 * This component renders the same four-line structure for every insight,
 * every time, so merchants learn to scan it instantly:
 *
 *   WHAT   happened
 *   WHY    it matters
 *   WHAT   caused it (best guess)
 *   WHAT   to do
 *
 * It also carries the two trust signals that determine whether a merchant
 * will act: confidence (how sure are we) and data quality (is the data we
 * based this on actually any good). Both use the same 3-level scale
 * (High / Medium / Low) so the merchant never has to learn a new vocabulary.
 *
 * No technical terms. No z-scores. No acronyms. If it can't be said to a
 * shop owner, it doesn't belong in this component.
 */

export type TrustLevel = "High" | "Medium" | "Low";

export type SparkExplanationProps = {
  /** Plain-English description of WHAT happened. 1 sentence. */
  what: string;
  /** WHY it matters to the merchant's business. 1 sentence. */
  why: string;
  /** The best explanation of the CAUSE. 1 sentence. Prefix with "Usually…". */
  cause?: string;
  /** The ONE action to take. Short, imperative. */
  suggestion: string;

  /** Trust signals — merchant sees these to decide whether to act. */
  confidence?: TrustLevel;
  dataQuality?: TrustLevel;

  /** If set, clicking the apply button fires this callback. */
  onApply?: () => void;
  /** Copy on the primary CTA. Defaults to "Apply fix". */
  applyLabel?: string;
  /** When true we tell the merchant they can undo this anytime. */
  reversible?: boolean;

  /** Compact variant for dense lists (smaller padding, no Apply button). */
  compact?: boolean;
};

// ---------------------------------------------------------------------------
// Trust pill
// ---------------------------------------------------------------------------

const TRUST_TONE: Record<TrustLevel, { dot: string; text: string }> = {
  High: { dot: "bg-emerald-400", text: "text-emerald-300" },
  Medium: { dot: "bg-amber-400", text: "text-amber-300" },
  Low: { dot: "bg-slate-400", text: "text-slate-400" },
};

function TrustPill({ label, level }: { label: string; level: TrustLevel }) {
  const t = TRUST_TONE[level];
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.03] px-2.5 py-1">
      <span className={`h-1.5 w-1.5 rounded-full ${t.dot}`} aria-hidden />
      <span className="text-[11px] font-medium text-slate-400">{label}:</span>
      <span className={`text-[11px] font-semibold ${t.text}`}>{level}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function SparkExplanation({
  what,
  why,
  cause,
  suggestion,
  confidence,
  dataQuality,
  onApply,
  applyLabel = "Apply fix",
  reversible = true,
  compact = false,
}: SparkExplanationProps) {
  const pad = compact ? "p-4" : "p-5";
  const gap = compact ? "space-y-2" : "space-y-3";
  return (
    <article
      className={`rounded-2xl border border-white/[0.07] bg-white/[0.02] ${pad} shadow-[0_0_32px_rgba(0,0,0,0.3)]`}
    >
      <div className={gap}>
        {/* Line 1 — WHAT */}
        <div className="flex gap-3">
          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-violet-500/15 text-[11px] font-semibold text-violet-300">
            1
          </span>
          <p className="text-[14px] leading-snug text-slate-100">{what}</p>
        </div>
        {/* Line 2 — WHY */}
        <div className="flex gap-3">
          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-violet-500/15 text-[11px] font-semibold text-violet-300">
            2
          </span>
          <p className="text-[13px] leading-snug text-slate-300">{why}</p>
        </div>
        {/* Line 3 — CAUSE (optional) */}
        {cause && (
          <div className="flex gap-3">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-slate-500/15 text-[11px] font-semibold text-slate-400">
              3
            </span>
            <p className="text-[13px] leading-snug text-slate-400">{cause}</p>
          </div>
        )}
        {/* Line 4 — SUGGESTION */}
        <div className="flex gap-3">
          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-emerald-500/15 text-[11px] font-semibold text-emerald-300">
            →
          </span>
          <p className="text-[13px] font-medium leading-snug text-emerald-200">{suggestion}</p>
        </div>
      </div>

      {/* ---------------- Trust footer ---------------- */}
      {(confidence || dataQuality || onApply || reversible) && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-3">
          <div className="flex flex-wrap items-center gap-2">
            {confidence && <TrustPill label="Confidence" level={confidence} />}
            {dataQuality && <TrustPill label="Data quality" level={dataQuality} />}
            {reversible && (
              <span className="inline-flex items-center gap-1.5 text-[11px] text-slate-500">
                <svg className="h-3 w-3" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M3 6 L5 4 L5 5.5 A2.5 2.5 0 1 1 3.5 8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                You can undo this anytime
              </span>
            )}
          </div>
          {!compact && onApply && (
            <button
              onClick={onApply}
              className="rounded-lg bg-violet-600 px-4 py-2 text-[13px] font-semibold text-white transition-all hover:bg-violet-500 hover:shadow-[0_0_28px_rgba(124,58,237,0.4)] focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/40"
            >
              {applyLabel}
            </button>
          )}
        </div>
      )}
    </article>
  );
}
