"use client";

/**
 * ScaleFloorPreview — locked-cassettoni grid shown on /app/scale to
 * non-Scale-tier merchants. Lists the 10 Northbeam-class moats that
 * Scale unlocks (Causal Lift+Why, MTA Compare, Anomaly Fusion+Replay,
 * Counterfactual, Competitor Playbook, Revenue Autopsy+Genome,
 * Nudge DNA, Lift Report, Night Shift+Timeline) plus the agency
 * white-label + API rows. Each card has a title, body, and a lock
 * icon — matches the cassettone-with-lock pattern the founder asked
 * for ("i cassettoni come li vedevo prima con il titolo, messi in
 * 'serie' con il lucchetto").
 *
 * Replaces the previous static-feature operations/page.tsx which
 * lived in a separate FloorLayout (causing session-pipeline
 * duplication and the "Loading your plan" bounce). This component
 * renders inline on the shared /app/page.tsx Scale floor — single
 * source of truth.
 */

const SCALE_FEATURES: Array<{ title: string; body: string; accent: string }> = [
  {
    title: "Causal Lift + Why engine",
    body: "Real A/B holdout measurement of every action you take. The 'why' Bayesian inference engine ranks the underlying causes when a metric shifts. Closest competitor: Northbeam ($1k+/mo).",
    accent: "#34d399",
  },
  {
    title: "Night Shift Agent + Timeline",
    body: "Overnight 24h Bayesian inference loop reads every signal, picks the single most-impactful lever, leaves a visible reasoning journal. Timeline shows 30-day retrospective + outcome-tracked recommendations. Nobody under $1k ships this.",
    accent: "#fbbf24",
  },
  {
    title: "Competitor Playbook",
    body: "Anonymized peer-network playbook — for every signal class fired on your store, see what merchants in your vertical did about it (ranked by win rate + average CVR lift). Aggregate-only; never peer names. Unique to HedgeSpark.",
    accent: "#a855f7",
  },
  {
    title: "Anomaly Fusion + Replay",
    body: "Cross-signal correlation that fires when 3+ signal classes co-spike. Replay reconstructs the event window minute-by-minute (every visitor, source, device). Event-level granularity competitors can't reach.",
    accent: "#f87171",
  },
  {
    title: "Counterfactual Explorer",
    body: "What-if math over every open opportunity signal: per-day loss × days-open × AOV, projected back along 4 lag horizons (now / 7d / 14d / 30d). Turns 'oh well, missed it' into a hard cost-of-delay number.",
    accent: "#fb923c",
  },
  {
    title: "Revenue Autopsy + Revenue Genome",
    body: "Autopsy: post-mortem on lost revenue with per-cause attribution. Genome: source-vs-segment DNA decomposition. Northbeam-class diagnostics nobody under $1k ships.",
    accent: "#fb7185",
  },
  {
    title: "Nudge DNA + Lift Report",
    body: "Nudge DNA: which copy patterns (length, urgency, social proof) are pulling weight on YOUR audience. Lift Report: holdout-measured CVR delta with statistical significance — the only number you don't have to trust the vendor on.",
    accent: "#10b981",
  },
  {
    title: "MTA model compare",
    body: "Multi-touch attribution model side-by-side: first-touch vs last-touch vs linear vs time-decay vs data-driven. See which model reads your store best, switch when the data pattern changes. Northbeam $1k+ territory.",
    accent: "#c4b5fd",
  },
  {
    title: "Agency white-label console",
    body: "Branded reports, sub-client management, per-client margin dashboards. Triple Whale Agency starts at $1.5k/mo for comparable feature-set.",
    accent: "#3b82f6",
  },
  {
    title: "API access + outbound webhooks",
    body: "Pull every HedgeSpark metric into your stack via REST + webhooks + full OpenAPI spec. Real-time event streaming. Custom integrations.",
    accent: "#60a5fa",
  },
];

export function ScaleFloorPreview({
  onUpgrade,
}: {
  onUpgrade?: () => void;
}) {
  return (
    <section
      role="region"
      aria-label="Scale floor preview — features locked behind Scale tier"
      className="space-y-6"
    >
      <div className="rounded-3xl border border-[#3b82f6]/25 bg-gradient-to-br from-[#3b82f6]/[0.05] to-transparent p-7 sm:p-9">
        <div className="text-[11px] font-bold uppercase tracking-[0.2em] text-[#3b82f6]">
          Scale tier · €239/mo
        </div>
        <h1 className="mt-3 text-[2rem] font-extrabold leading-[1.1] text-white sm:text-[2.5rem]">
          Northbeam-class moats. At 1/4 the price.
        </h1>
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-slate-400">
          Scale unlocks {SCALE_FEATURES.length} features no competitor
          ships under $1k/mo: Causal Lift, holdout-measured Lift Report,
          MTA Compare, Anomaly Fusion, the Night Shift overnight agent,
          and the rest of the moat intelligence layer. Triple Whale
          Agency starts at $1,500/mo for a comparable feature-set.
        </p>
        {onUpgrade && (
          <button
            type="button"
            onClick={onUpgrade}
            className="mt-5 rounded-xl bg-[#3b82f6] px-6 py-3 text-[13px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-[#60a5fa]"
          >
            Upgrade to Scale
          </button>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {SCALE_FEATURES.map((f) => (
          <div
            key={f.title}
            className="relative overflow-hidden rounded-2xl border border-white/[0.06] bg-[#0e0e1a] p-5 transition-colors hover:border-white/[0.12]"
            style={{
              background: `linear-gradient(135deg, ${f.accent}08 0%, transparent 80%)`,
            }}
          >
            <div className="absolute right-4 top-4">
              <span
                className="inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.1em]"
                style={{
                  color: f.accent,
                  borderColor: `${f.accent}40`,
                  background: `${f.accent}10`,
                }}
              >
                <svg
                  className="h-3 w-3"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z"
                  />
                </svg>
                Scale
              </span>
            </div>
            <h3
              className="pr-16 text-[15px] font-bold"
              style={{ color: f.accent }}
            >
              {f.title}
            </h3>
            <p className="mt-2 text-[13px] leading-[1.55] text-slate-300">
              {f.body}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}
