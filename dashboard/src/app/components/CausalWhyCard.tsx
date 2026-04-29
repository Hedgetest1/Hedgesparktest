"use client";

/**
 * CausalWhyCard — Pro moat, rich exploration pattern.
 *
 * The "why" engine. Every other dashboard surfaces WHAT — a metric
 * dropped, a signal fired, a number turned red. This one surfaces
 * WHY — Bayesian inference over signal classes, vertical-tuned priors,
 * evidence-weighted likelihood, suppressor adjustments. Output is a
 * ranked hypothesis list with confidence + concrete action.
 *
 * Three sections (matches all other rich Pro moats):
 *   1. Mechanics + stakes — Bayesian inference framing, why "what"
 *      without "why" is guessing.
 *   2. Data — hero stat (top hypothesis + confidence color-coded by
 *      tier), evidence + suppressor block, alternate hypotheses ranked,
 *      key metrics, methodology with priors + evidence weights +
 *      vertical tuning.
 *   3. Actions — primary action with 5 cases (healthy / high
 *      confidence / mid / low / contested), supporting actions
 *      (cross-check Anomaly Fusion, watch live SSE, escalate).
 *
 * Source: GET /pro/causal/explain + SSE /pro/stream/dashboard for
 * live label changes.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";

const ACCENT = {
  eyebrow: "#d4893a",
  hero: "#fb923c",
  bg: "rgba(217,119,6,0.08)",
  border: "rgba(217,119,6,0.25)",
};

type Hypothesis = {
  label: string;
  confidence: number;
  score: number;
  prior: number;
  evidence: string[];
  suppressors: string[];
  narrative: string;
  recommended_action: string;
  rank: number;
};

type CausalResponse = {
  shop_domain: string;
  vertical?: string;
  vertical_display?: string;
  hypotheses: Hypothesis[];
  narrative: string;
  next_action?: string | null;
  fusion_alerts?: Array<{ pattern: string; severity: string; fusion_score: number }>;
  raw_signals?: Array<{ name: string; severity: number; delta_pct: number }>;
  generated_at: string;
};

type SupportingAction = { label: string; description: string };
type PrimaryAction = { headline: string; label: string; description: string };

function labelize(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function confidenceColor(c: number): string {
  if (c >= 0.7) return "#f87171";
  if (c >= 0.4) return "#fbbf24";
  return "#94a3b8";
}

function confidenceTier(c: number): "high" | "medium" | "low" {
  if (c >= 0.7) return "high";
  if (c >= 0.4) return "medium";
  return "low";
}

export function CausalWhyCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [data, setData] = useState<CausalResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastLive, setLastLive] = useState<string | null>(null);

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) {
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);

    const refetch = async () => {
      try {
        const { data: j, error } = await apiClient.GET("/pro/causal/explain");
        if (error || !j) throw new Error("fetch failed");
        if (active) {
          setData(j as unknown as CausalResponse);
          setLastLive(new Date().toISOString());
        }
      } catch {
        if (active) setData(null);
      }
    };

    refetch().finally(() => {
      if (active) setLoading(false);
    });

    let es: EventSource | null = null;
    try {
      es = new EventSource(`${apiBase}/pro/stream/dashboard`, { withCredentials: true });
      es.addEventListener("snapshot", (ev: MessageEvent) => {
        if (!active) return;
        try {
          const snap = JSON.parse(ev.data);
          const incomingLabel = snap?.causal_top?.label ?? null;
          const currentLabel = data?.hypotheses?.[0]?.label ?? null;
          setLastLive(new Date().toISOString());
          if (incomingLabel !== currentLabel) {
            refetch();
          }
        } catch {
          /* ignore */
        }
      });
      es.onerror = () => {
        /* auto-reconnect */
      };
    } catch {
      /* ignore */
    }

    return () => {
      active = false;
      try {
        es?.close();
      } catch {
        /* ignore */
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, shop, isProUser]);

  if (!isProUser) return null;

  if (loading) {
    return (
      <section className="rounded-3xl border border-white/[0.08] bg-[#0e0e1a] p-7 sm:p-9">
        <div className="h-6 w-48 animate-pulse rounded bg-white/[0.05]" />
        <div className="mt-3 h-4 w-full animate-pulse rounded bg-white/[0.03]" />
      </section>
    );
  }

  const isHealthy = !data || !data.hypotheses || data.hypotheses.length === 0;
  const top = data?.hypotheses?.[0];
  const conf = top ? Math.round((top.confidence || 0) * 100) : 0;
  const tier = top ? confidenceTier(top.confidence || 0) : "low";
  const color = top ? confidenceColor(top.confidence || 0) : ACCENT.hero;
  const others = data?.hypotheses?.slice(1, 4) ?? [];
  const verticalDisplay = data?.vertical_display ?? "your vertical";

  const subtitle = isHealthy
    ? "Store reads as healthy — every signal class is in normal range."
    : `Leading cause: ${labelize(top!.label)} · ${conf}% confidence · ${top!.evidence.length} supporting signal${top!.evidence.length === 1 ? "" : "s"}.`;

  const primaryAction = computePrimaryAction(isHealthy, top, tier);
  const supportingActions = computeSupportingActions(isHealthy, top);

  return (
    <section
      role="region"
      aria-label="Causal Why engine — Pro exploration"
      className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-[#0e0e1a] p-7 shadow-[0_20px_80px_-20px_rgba(0,0,0,0.6)] sm:p-9"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div
            className="mb-3 text-[10px] font-bold uppercase tracking-[0.18em]"
            style={{ color: ACCENT.eyebrow }}
          >
            Why engine · {verticalDisplay}-tuned
          </div>
          <h2
            className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight sm:text-[2rem]"
            style={{ color: ACCENT.hero }}
          >
            Causal Why
          </h2>
          <p className="mt-2 text-[15px] font-semibold text-white">{subtitle}</p>
        </div>
        {lastLive && (
          <span
            className="inline-flex flex-shrink-0 items-center gap-1.5 rounded-full bg-emerald-500/[0.08] px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-emerald-300"
            title={`Live stream · last update ${new Date(lastLive).toLocaleTimeString()}`}
          >
            <span className="relative inline-flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/60" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
            </span>
            live
          </span>
        )}
      </div>

      {/* ── Section 1: mechanics + stakes ── */}
      <div className="mt-5 max-w-3xl space-y-5">
        <p className="text-[14px] leading-relaxed text-slate-300">
          I run real-time Bayesian inference over every signal class on
          your store — abandoned-intent, refund-decline, nudge-gap,
          below-benchmark, goal-gap, anomaly-fusion. The hypothesis
          space is tuned for {verticalDisplay} stores: each hypothesis
          has a vertical-specific prior, an evidence-weighted likelihood
          updated from your live signals, and suppressor adjustments
          when contradictory signals fire. Output is the ranked list
          with confidence + concrete action.
        </p>
        <div>
          <div
            className="mb-1.5 text-[10.5px] font-bold uppercase tracking-[0.18em]"
            style={{ color: ACCENT.eyebrow }}
          >
            Why this matters
          </div>
          <p className="text-[14px] leading-relaxed text-slate-300">
            Every other dashboard tells you WHAT happened (CVR dropped 8%).
            This card tells you WHY (cart abandonment shifted on mobile
            because checkout took a price-test variant in the last 7d).
            Without &ldquo;why&rdquo; you guess; with it, you fix the
            actual cause and stop the bleeding instead of chasing the
            symptom.
          </p>
        </div>
      </div>

      {/* ── Section 2: the data ── */}
      <div className="mt-8 rounded-2xl border border-violet-400/15 bg-violet-500/[0.025] p-5 sm:p-6">
        <div className="mb-5 flex items-center gap-2.5">
          <ChartIcon />
          <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-violet-300">
            The data · what you&apos;re looking at
          </div>
        </div>

        {isHealthy ? (
          <HealthyPreview accentHero={ACCENT.hero} />
        ) : (
          <>
            {/* Hero — top hypothesis + confidence */}
            <div className="mb-6 rounded-xl border border-white/[0.06] bg-[#0b0b14]/70 p-5">
              <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                Top hypothesis · {tier} confidence
              </div>
              <div className="mt-2 flex items-end gap-6">
                <div>
                  <div
                    className="text-[2rem] font-extrabold leading-tight tracking-tight"
                    style={{ color }}
                  >
                    {labelize(top!.label)}
                  </div>
                  <div className="mt-2 text-[12.5px] text-slate-400">
                    Posterior probability after Bayesian update across {top!.evidence.length} supporting signal{top!.evidence.length === 1 ? "" : "s"}
                    {top!.suppressors.length > 0 && ` and ${top!.suppressors.length} suppressor${top!.suppressors.length === 1 ? "" : "s"}`}.
                  </div>
                </div>
                <div
                  className="rounded-xl px-4 py-3 text-center"
                  style={{
                    background: color + "20",
                    border: `1px solid ${color}40`,
                  }}
                >
                  <div className="text-[10px] font-bold uppercase tracking-[0.14em]" style={{ color }}>
                    Confidence
                  </div>
                  <div className="mt-1 text-[28px] font-extrabold leading-none tabular-nums" style={{ color }}>
                    {conf}%
                  </div>
                </div>
              </div>
              <p className="mt-4 text-[13.5px] leading-relaxed text-slate-200">
                {top!.narrative}
              </p>
            </div>

            {/* Recommended action highlighted */}
            {top!.recommended_action && (
              <div className="mb-6 rounded-xl border border-emerald-400/20 bg-emerald-500/[0.05] p-4">
                <div className="mb-1.5 flex items-center gap-2">
                  <svg
                    className="h-3.5 w-3.5 text-emerald-400"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z"
                    />
                  </svg>
                  <span className="text-[10.5px] font-bold uppercase tracking-[0.14em] text-emerald-300">
                    Recommended action
                  </span>
                </div>
                <p className="text-[13.5px] leading-relaxed text-slate-100">
                  {top!.recommended_action}
                </p>
              </div>
            )}

            {/* Evidence + suppressors */}
            <div className="mb-6 grid gap-3 sm:grid-cols-2">
              {top!.evidence.length > 0 && (
                <div className="rounded-xl border border-amber-400/15 bg-amber-500/[0.04] p-4">
                  <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.14em] text-amber-300">
                    Supporting signals · {top!.evidence.length}
                  </div>
                  <ul className="space-y-1">
                    {top!.evidence.slice(0, 6).map((e, i) => (
                      <li key={i} className="text-[12px] leading-relaxed text-slate-300">
                        • {e}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {top!.suppressors.length > 0 ? (
                <div className="rounded-xl border border-violet-400/15 bg-violet-500/[0.04] p-4">
                  <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.14em] text-violet-300">
                    Suppressors · {top!.suppressors.length}
                  </div>
                  <ul className="space-y-1">
                    {top!.suppressors.slice(0, 4).map((s, i) => (
                      <li key={i} className="text-[12px] leading-relaxed text-slate-300">
                        • {s}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <div className="rounded-xl border border-white/[0.05] bg-white/[0.015] p-4">
                  <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
                    No suppressors firing
                  </div>
                  <p className="text-[12px] leading-relaxed text-slate-400">
                    No contradictory signals — the evidence stack points cleanly to this hypothesis.
                  </p>
                </div>
              )}
            </div>

            {/* Alternate hypotheses */}
            {others.length > 0 && (
              <div className="mb-6">
                <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                  Alternate hypotheses considered
                </div>
                <ul className="space-y-2">
                  {others.map((h) => {
                    const altConf = Math.round((h.confidence || 0) * 100);
                    const altColor = confidenceColor(h.confidence || 0);
                    return (
                      <li
                        key={h.label}
                        className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.05] bg-[#0b0b14]/60 px-4 py-2.5"
                      >
                        <div className="min-w-0">
                          <div className="text-[12.5px] font-semibold text-slate-200">
                            {labelize(h.label)}
                          </div>
                          <div className="mt-0.5 truncate text-[10.5px] text-slate-400">
                            {h.evidence.length} signal{h.evidence.length === 1 ? "" : "s"} · prior {Math.round(h.prior * 100)}%
                          </div>
                        </div>
                        <div
                          className="rounded-md px-2.5 py-1 text-[12px] font-bold tabular-nums"
                          style={{
                            color: altColor,
                            background: altColor + "15",
                            border: `1px solid ${altColor}30`,
                          }}
                        >
                          {altConf}%
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}

            {/* Key metrics */}
            <div className="mb-6">
              <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                Key metrics
              </div>
              <div className="divide-y divide-white/[0.04] rounded-xl border border-white/[0.05] bg-[#0b0b14]/50">
                <KvRow label="Top confidence" value={`${conf}%`} color={color} />
                <KvRow label="Hypotheses ranked" value={(data?.hypotheses?.length ?? 0).toString()} />
                <KvRow label="Supporting signals" value={top!.evidence.length.toString()} color="#fbbf24" />
                <KvRow
                  label="Suppressors"
                  value={top!.suppressors.length.toString()}
                  color={top!.suppressors.length > 0 ? "#a78bfa" : undefined}
                />
                <KvRow
                  label="Vertical tuning"
                  value={verticalDisplay}
                />
              </div>
            </div>
          </>
        )}

        {/* Methodology */}
        <div>
          <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
            How this is calculated
          </div>
          <div className="rounded-xl border border-white/[0.05] bg-[#0b0b14]/50 p-5">
            <p className="text-[13px] leading-relaxed text-slate-300">
              Posterior(H | evidence) = Prior(H | vertical) × Likelihood(evidence | H) × ΠSuppressor_adjustment(s | H). Each hypothesis carries a vertical-specific prior derived from peer-network outcomes. Likelihood accumulates with each supporting signal weighted by signal severity. Suppressors are signals that contradict the hypothesis — they damp the posterior, never zero it. Confidence = normalized posterior across the full hypothesis space.
            </p>
            <ul className="mt-4 space-y-1.5 text-[12.5px]">
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-500">Inference engine</span>
                <span className="tabular-nums text-slate-300">Bayesian · vertical-tuned priors</span>
              </li>
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-500">Update cadence</span>
                <span className="tabular-nums text-slate-300">Live (SSE on snapshot)</span>
              </li>
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-500">Confidence tiers</span>
                <span className="tabular-nums text-slate-300">High &ge;70% · Medium &ge;40% · Low &lt;40%</span>
              </li>
              <li className="flex justify-between gap-3 last:border-0">
                <span className="text-slate-500">Hypothesis space</span>
                <span className="tabular-nums text-slate-300">{(data?.hypotheses?.length ?? 0)} active</span>
              </li>
            </ul>
            <p className="mt-4 text-[12.5px] leading-relaxed italic text-slate-400">
              Live SSE-driven — the leading hypothesis can shift mid-session as new signals fire. Last update: {lastLive ? new Date(lastLive).toLocaleTimeString() : "—"}.
            </p>
          </div>
        </div>
      </div>

      {/* ── Section 3: actions ── */}
      <div
        className="mt-6 rounded-2xl p-5 sm:p-6"
        style={{
          background: `linear-gradient(135deg, ${ACCENT.bg} 0%, transparent 80%)`,
          border: `1px solid ${ACCENT.border}`,
        }}
      >
        <div className="mb-4 flex items-center gap-2.5">
          <BoltIcon stroke={ACCENT.hero} />
          <div
            className="text-[11px] font-bold uppercase tracking-[0.18em]"
            style={{ color: ACCENT.hero }}
          >
            Your next moves
          </div>
        </div>

        {primaryAction && (
          <div className="rounded-xl border border-white/[0.08] bg-[#0e0e1a]/80 p-5">
            <div
              className="text-[10px] font-bold uppercase tracking-[0.2em]"
              style={{ color: ACCENT.hero }}
            >
              {primaryAction.headline}
            </div>
            <div className="mt-2 text-[16px] font-bold leading-snug text-white">
              {primaryAction.label}
            </div>
            <p className="mt-2.5 max-w-3xl text-[13.5px] leading-relaxed text-slate-300">
              {primaryAction.description}
            </p>
          </div>
        )}

        {supportingActions.length > 0 && (
          <ul className="mt-3 space-y-2">
            {supportingActions.map((s, i) => (
              <li
                key={i}
                className="flex items-start gap-3 rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 px-4 py-3"
              >
                <span
                  className="mt-1.5 inline-flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-[10px] font-bold tabular-nums"
                  style={{
                    color: ACCENT.hero,
                    background: ACCENT.bg,
                    border: `1px solid ${ACCENT.border}`,
                  }}
                  aria-hidden="true"
                >
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-[13.5px] font-semibold text-slate-200">
                    {s.label}
                  </div>
                  <p className="mt-1 text-[12.5px] leading-relaxed text-slate-400">
                    {s.description}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------

function computePrimaryAction(
  isHealthy: boolean,
  top: Hypothesis | undefined,
  tier: "high" | "medium" | "low",
): PrimaryAction {
  if (isHealthy || !top) {
    return {
      headline: "Store healthy",
      label: "No causal hypothesis above the floor",
      description:
        "Every signal class reads in normal range — Bayesian posterior over all hypotheses stays below the surfacing threshold. The system is watching live and will surface the cause the moment something drifts. Use the quiet period to invest in what's working (RARS components ranked by recovery).",
    };
  }
  if (tier === "high") {
    return {
      headline: "Strong hypothesis · act on it",
      label: top.recommended_action || `Address ${labelize(top.label)} now`,
      description: `${Math.round(top.confidence * 100)}% confidence with ${top.evidence.length} supporting signals${top.suppressors.length > 0 ? ` and ${top.suppressors.length} suppressors that don't move the needle` : " and no contradictory signals"}. The evidence stack is clean — investigate this cause first, before chasing the symptom.`,
    };
  }
  if (tier === "medium") {
    return {
      headline: "Provisional hypothesis",
      label: `${labelize(top.label)} is the leading guess — but check alternates`,
      description:
        "Confidence is in the medium band — the evidence supports this hypothesis more than any other, but not strongly enough to ignore the alternates listed above. Cross-check the top 2-3 alternate hypotheses before committing to a fix.",
    };
  }
  return {
    headline: "Contested signal",
    label: "Low confidence on every hypothesis",
    description:
      "The system sees noise but no clear cause emerging. Either the underlying signal is genuinely ambiguous, or you have a new failure mode we haven't trained for. Open Anomaly Fusion to see the cross-signal correlation map; if a pattern is visible there but not here, escalate.",
  };
}

function computeSupportingActions(
  isHealthy: boolean,
  top: Hypothesis | undefined,
): SupportingAction[] {
  if (isHealthy || !top) {
    return [
      {
        label: "Watch live updates",
        description:
          "The card auto-updates via SSE. Keep it pinned during a campaign launch or product change — the leading hypothesis flips in real time as new signals fire.",
      },
      {
        label: "Open Causal Lift",
        description:
          "Causal Lift runs the same kind of math but on holdout-measured outcomes. Healthy here + flat lift there = your interventions aren't moving the needle either way.",
      },
    ];
  }
  return [
    {
      label: "Cross-check Anomaly Fusion",
      description:
        "Anomaly Fusion shows which signals correlate to fire together. The Why engine ranks hypotheses; Fusion shows the underlying signal-correlation pattern. Together they explain not just what + why, but the structural mechanism.",
    },
    {
      label: "Run the recommended action",
      description:
        "The action is generated from the leading hypothesis — running it is the fastest way to test whether the system's diagnosis is correct. If lift moves after, the diagnosis was right.",
    },
    {
      label: "Re-check in 24h",
      description:
        "Causal hypotheses shift as evidence accumulates. The same store can read &ldquo;abandonment-driver&rdquo; today and &ldquo;refund-driver&rdquo; next week — fix one, the next bottleneck reveals itself.",
    },
  ];
}

function KvRow({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <span className="text-[13px] text-slate-400">{label}</span>
      <span
        className="text-[14px] font-bold tabular-nums"
        style={{ color: color ?? "#e2e8f0" }}
      >
        {value}
      </span>
    </div>
  );
}

function HealthyPreview({ accentHero }: { accentHero: string }) {
  return (
    <div className="mb-6 rounded-xl border border-emerald-400/15 bg-emerald-500/[0.04] p-5">
      <div className="mb-3 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em] text-emerald-300">
        <span
          className="inline-block h-1.5 w-1.5 animate-pulse rounded-full"
          style={{ background: accentHero }}
          aria-hidden="true"
        />
        Healthy reading
      </div>
      <p className="text-[13px] leading-relaxed text-slate-300">
        Your store reads as healthy. Every signal class is in normal range, no hypothesis crosses the surfacing threshold. The Bayesian engine is watching every signal in real time and will surface the cause — not just the metric — the moment something drifts.
      </p>
      <div className="mt-4 flex items-center gap-2 rounded-lg border border-emerald-400/15 bg-emerald-500/[0.05] px-3 py-2 text-[11.5px] font-semibold text-emerald-300">
        <span
          className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400"
          aria-hidden="true"
        />
        Live monitoring active — first cause will populate this view automatically.
      </div>
    </div>
  );
}

function ChartIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="#c4b5fd"
      strokeWidth={1.8}
      className="h-4 w-4 flex-shrink-0"
      aria-hidden="true"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75c0 .621-.504 1.125-1.125 1.125h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z"
      />
    </svg>
  );
}

function BoltIcon({ stroke }: { stroke: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke={stroke}
      strokeWidth={1.8}
      className="h-4 w-4 flex-shrink-0"
      aria-hidden="true"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M13 10V3L4 14h7v7l9-11h-7z"
      />
    </svg>
  );
}
