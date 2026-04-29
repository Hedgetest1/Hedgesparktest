"use client";

/**
 * NightShiftCard — Pro moat, rich exploration pattern.
 *
 * The first thing a Pro merchant sees every morning. While they slept,
 * I read every signal on the store, ran 24h Bayesian inference, picked
 * the SINGLE most impactful lever, and wrote a visible reasoning
 * journal explaining WHY that lever and not the others. One click
 * applies the suggested action.
 *
 * Three sections (matches all other rich Pro moats):
 *   1. Mechanics + stakes — overnight inference loop framing, why
 *      "what's wrong" without "where to start" wastes mornings.
 *   2. Data — narrative + headline, suggested-action card with
 *      one-click apply (preserved interaction), sleep confidence with
 *      calibration provenance, reasoning journal with verdict tags,
 *      key metrics, methodology.
 *   3. Actions — primary action derived from top_action +
 *      sleep_confidence tier (5 cases), supporting actions cross-
 *      ref Causal Why + Anomaly Fusion + Timeline.
 *
 * Source: GET /pro/night-shift/latest, POST /pro/night-shift/apply.
 */

import { useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import { reportFrontendError } from "../lib/error-reporter";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";

const ACCENT = {
  eyebrow: "#e8a04e",
  hero: "#fbbf24",
  bg: "rgba(232,160,78,0.08)",
  border: "rgba(232,160,78,0.25)",
};

type TopAction = {
  kind: string;
  label: string;
  detail: string;
  estimated_impact_eur: number;
  source?: string;
};

type JournalEntry = {
  signal: string;
  verdict: "kept" | "rejected" | "watched" | string;
  reason: string;
  weight: number;
};

type Provenance = {
  raw_score: number;
  capped_score: number;
  calibrated: boolean;
  observations: number;
  cap_reason: string | null;
  contributions: Array<{ name: string; points: number; reason: string }>;
};

type NightShiftReport = {
  shop_domain: string;
  day: string;
  generated_at: string;
  narrative: string;
  headline: string;
  top_action: TopAction | null;
  sleep_confidence: number;
  sleep_confidence_label: string;
  journal: JournalEntry[];
  metrics: {
    rars_total_eur: number;
    prevented_24h_eur: number;
    fusion_alert_count: number;
    critical_alerts: number;
    sleep_confidence_provenance?: Provenance;
  };
  status: "quiet" | "active" | "alarm" | string;
  currency?: string;
};

type SupportingAction = { label: string; description: string };
type PrimaryActionShape = { headline: string; label: string; description: string };

const STATUS_COLOR: Record<string, string> = {
  quiet: "#34d399",
  active: "#fbbf24",
  alarm: "#f87171",
};

const VERDICT_COLOR: Record<string, string> = {
  kept: "#34d399",
  watched: "#94a3b8",
  rejected: "#f87171",
};

const fmtMoney = (n: number, currency?: string): string =>
  formatMoneyCompact(n, currency || "USD");

function relativeTime(iso: string): string {
  try {
    const then = new Date(iso).getTime();
    const diff = Math.max(0, Date.now() - then);
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch {
    return "";
  }
}

export function NightShiftCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [journalOpen, setJournalOpen] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState(false);

  const { data, state, retry } = useCardFetch<NightShiftReport>({
    url: `${apiBase}/pro/night-shift/latest`,
    enabled: !!apiBase && !!shop && isProUser,
    isEmpty: () => false,
    component: "NightShiftCard",
  });

  if (!isProUser) return null;
  if (state === "loading")
    return <CardSkeleton label="Loading tonight's night-shift report" />;
  if (state === "error")
    return (
      <CardError
        label="Night shift report unavailable"
        message="We couldn't load the latest night-shift report. Your signals are safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  if (state === "empty" || !data)
    return (
      <CardEmpty
        accent="emerald"
        title="First night-shift report is on its way"
        body="Every night I read every signal on your store, run 24h Bayesian inference, pick the single most impactful lever, and write a reasoning journal. The first report lands after I've watched your store for a full overnight cycle."
        eta="First report after the next overnight run"
      />
    );

  const status = data.status;
  const statusColor = STATUS_COLOR[status] || STATUS_COLOR.quiet;
  const confidencePct = Math.max(0, Math.min(100, data.sleep_confidence));
  const prov = data.metrics?.sleep_confidence_provenance;
  const isCalibrated = prov?.calibrated ?? false;
  const journalKept = data.journal.filter((j) => j.verdict === "kept").length;
  const journalRejected = data.journal.filter((j) => j.verdict === "rejected").length;
  const journalWatched = data.journal.filter((j) => j.verdict === "watched").length;

  const subtitle = `${data.day} · ${data.headline}`;

  const applyAction = async () => {
    if (!data.top_action || applying || applied) return;
    setApplying(true);
    try {
      const { error } = await apiClient.POST("/pro/night-shift/apply");
      if (!error) setApplied(true);
      else
        reportFrontendError({
          component: "NightShiftCard.applyAction",
          error_type: "HttpError",
          message: "POST /pro/night-shift/apply failed",
          severity: "warning",
        });
    } catch (err: unknown) {
      const e = err as { name?: string; message?: string } | null;
      reportFrontendError({
        component: "NightShiftCard.applyAction",
        error_type: e?.name ?? "FetchError",
        message: e?.message ?? "Failed to POST /pro/night-shift/apply",
        severity: "warning",
      });
    } finally {
      setApplying(false);
    }
  };

  const primaryAction = computePrimaryAction(data, applied, applying);
  const supportingActions = computeSupportingActions(data);

  return (
    <section
      role="region"
      aria-label="Night shift — Pro exploration"
      className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-[#0e0e1a] p-7 shadow-[0_20px_80px_-20px_rgba(0,0,0,0.6)] sm:p-9"
    >
      <div
        aria-hidden="true"
        className="absolute inset-x-0 top-0 h-0.5"
        style={{ background: statusColor }}
      />
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div
            className="mb-3 flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.18em]"
            style={{ color: ACCENT.eyebrow }}
          >
            <MoonIcon />
            Night shift · {data.day} · {relativeTime(data.generated_at)}
          </div>
          <h2
            className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight sm:text-[2rem]"
            style={{ color: ACCENT.hero }}
          >
            Night Shift Agent
          </h2>
          <p className="mt-2 text-[15px] font-semibold text-white">{subtitle}</p>
        </div>
        <div className="flex-shrink-0 text-right">
          <div
            className="rounded-full px-3 py-1 text-[10px] font-bold uppercase tracking-wide tabular-nums"
            style={{
              color: statusColor,
              background: statusColor + "15",
              border: `1px solid ${statusColor}40`,
            }}
            aria-label={`Sleep confidence ${confidencePct} out of 100`}
            title={prov?.cap_reason || undefined}
          >
            {confidencePct}/100 · {data.sleep_confidence_label}
          </div>
          {!isCalibrated && prov && (
            <div
              className="mt-1 text-[9px] font-semibold uppercase tracking-wide text-amber-300/80"
              title="Calibration collects 30 matched observations before we trust a 'full autonomy' label."
            >
              Uncalibrated · {prov.observations} obs
            </div>
          )}
          <div className="mt-2 h-1 w-32 overflow-hidden rounded-full bg-white/[0.05]">
            <div
              className="h-full rounded-full transition-all duration-700"
              style={{ width: `${confidencePct}%`, background: statusColor }}
            />
          </div>
        </div>
      </div>

      {/* ── Section 1: mechanics + stakes ── */}
      <div className="mt-5 max-w-3xl space-y-5">
        <p className="text-[14px] leading-relaxed text-slate-300">
          Every night, while you sleep, I run the same Bayesian
          inference the Causal Why engine runs in real time — but with
          a 24-hour lookback. I read every signal on your store, score
          the impact-weighted opportunity space, and pick the SINGLE
          most impactful lever. The reasoning journal captures every
          signal I considered + my verdict on each (kept / watched /
          rejected) so you can audit the recommendation.
        </p>
        <div>
          <div
            className="mb-1.5 text-[10.5px] font-bold uppercase tracking-[0.18em]"
            style={{ color: ACCENT.eyebrow }}
          >
            Why this matters
          </div>
          <p className="text-[14px] leading-relaxed text-slate-300">
            Most merchants wake up to a dashboard full of red metrics
            and no obvious where-to-start. NightShift collapses
            &ldquo;all of last night&rdquo; into one suggested move with
            the visible reasoning behind it — so you start your morning
            fixing what matters most, not browsing what&apos;s wrong.
            Sleep confidence is calibrated against your own historical
            outcomes — we don&apos;t claim &ldquo;full autonomy&rdquo;
            until 30 matched observations validate the claim.
          </p>
        </div>
      </div>

      {/* ── Section 2: data ── */}
      <div className="mt-8 rounded-2xl border border-violet-400/15 bg-violet-500/[0.025] p-5 sm:p-6">
        <div className="mb-5 flex items-center gap-2.5">
          <ChartIcon />
          <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-violet-300">
            The data · what you&apos;re looking at
          </div>
        </div>

        {/* Narrative */}
        <div className="mb-6 rounded-xl border border-white/[0.06] bg-[#0b0b14]/70 p-5">
          <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
            Last night&apos;s read
          </div>
          <p className="mt-2 text-[14.5px] leading-relaxed text-slate-200">
            {data.narrative}
          </p>
        </div>

        {/* Top action with apply button */}
        {data.top_action && (
          <div className="mb-6 rounded-xl border border-amber-400/20 bg-amber-500/[0.04] p-5">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span
                className="text-[10.5px] font-bold uppercase tracking-[0.14em]"
                style={{ color: ACCENT.eyebrow }}
              >
                Suggested first move
              </span>
              {data.top_action.estimated_impact_eur > 0 && (
                <span className="rounded-md border border-amber-400/30 bg-amber-500/[0.08] px-2 py-0.5 text-[12px] font-extrabold tabular-nums text-amber-300">
                  +{fmtMoney(data.top_action.estimated_impact_eur, data.currency)}/mo
                </span>
              )}
            </div>
            <div className="text-[16px] font-bold text-white">{data.top_action.label}</div>
            <p className="mt-1.5 text-[13px] leading-relaxed text-slate-300">
              {data.top_action.detail}
            </p>
            <button
              type="button"
              onClick={applyAction}
              disabled={applying || applied}
              className="mt-3 rounded-lg border px-4 py-2 text-[11px] font-bold uppercase tracking-wide transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[#e8a04e] disabled:cursor-not-allowed"
              style={{
                color: applied ? "#34d399" : ACCENT.hero,
                borderColor: (applied ? "#34d399" : ACCENT.hero) + "50",
                background: (applied ? "#34d399" : ACCENT.hero) + "12",
              }}
              aria-label={applied ? "Action applied" : "Apply suggested action"}
            >
              {applied ? "✓ Applied" : applying ? "Applying…" : "Apply with 1 click"}
            </button>
          </div>
        )}

        {/* Reasoning journal */}
        {data.journal && data.journal.length > 0 && (
          <div className="mb-6 rounded-xl border border-white/[0.05] bg-[#0b0b14]/60 p-5">
            <button
              type="button"
              onClick={() => setJournalOpen((v) => !v)}
              className="flex w-full items-center justify-between gap-2 text-left"
              aria-expanded={journalOpen}
              aria-controls="night-shift-journal"
            >
              <div>
                <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                  Reasoning journal
                </div>
                <div className="mt-1 text-[12px] text-slate-400">
                  {journalKept} kept · {journalWatched} watching · {journalRejected} rejected
                  {" "}({data.journal.length} signal{data.journal.length === 1 ? "" : "s"} considered)
                </div>
              </div>
              <svg
                className={`h-4 w-4 flex-shrink-0 text-slate-400 transition-transform ${journalOpen ? "rotate-180" : ""}`}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2.5}
                aria-hidden="true"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            {journalOpen && (
              <ul
                id="night-shift-journal"
                className="mt-4 space-y-2 border-t border-white/[0.05] pt-4"
                role="list"
              >
                {data.journal.map((j, i) => {
                  const color = VERDICT_COLOR[j.verdict] || "#94a3b8";
                  return (
                    <li
                      key={i}
                      className="flex items-start gap-3 rounded-lg border border-white/[0.04] bg-white/[0.015] px-3 py-2"
                    >
                      <span
                        className="mt-0.5 inline-block flex-shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide"
                        style={{
                          color,
                          background: color + "15",
                          border: `1px solid ${color}30`,
                        }}
                      >
                        {j.verdict}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="font-mono text-[10.5px] text-slate-400">{j.signal}</div>
                        <div className="mt-0.5 text-[12.5px] text-slate-300">{j.reason}</div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}

        {/* Key metrics */}
        <div className="mb-6">
          <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
            Key metrics
          </div>
          <div className="divide-y divide-white/[0.04] rounded-xl border border-white/[0.05] bg-[#0b0b14]/50">
            <KvRow
              label="Sleep confidence"
              value={`${confidencePct}/100 · ${data.sleep_confidence_label}`}
              color={statusColor}
            />
            <KvRow
              label="RARS total (this month)"
              value={fmtMoney(data.metrics.rars_total_eur, data.currency)}
              color={data.metrics.rars_total_eur > 0 ? "#fb7185" : undefined}
            />
            <KvRow
              label="Prevented (last 24h)"
              value={fmtMoney(data.metrics.prevented_24h_eur, data.currency)}
              color={data.metrics.prevented_24h_eur > 0 ? "#34d399" : undefined}
            />
            <KvRow
              label="Fusion alerts"
              value={data.metrics.fusion_alert_count.toString()}
              color={data.metrics.fusion_alert_count > 0 ? "#fbbf24" : undefined}
            />
            <KvRow
              label="Critical alerts"
              value={data.metrics.critical_alerts.toString()}
              color={data.metrics.critical_alerts > 0 ? "#f87171" : undefined}
            />
            <KvRow
              label="Calibration status"
              value={
                isCalibrated
                  ? "Calibrated against shop history"
                  : `Uncalibrated · ${prov?.observations ?? 0} of 30 obs`
              }
              color={isCalibrated ? "#34d399" : "#fbbf24"}
            />
          </div>
        </div>

        {/* Methodology */}
        <div>
          <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
            How this is calculated
          </div>
          <div className="rounded-xl border border-white/[0.05] bg-[#0b0b14]/50 p-5">
            <p className="text-[13px] leading-relaxed text-slate-300">
              Overnight (00:00 UTC + shop offset), I aggregate every
              signal fired in the last 24h and run vertical-tuned
              Bayesian inference over the action space. Each candidate
              action gets an impact-weighted score: prior × likelihood ×
              recency-boost. The top-ranked action becomes &ldquo;suggested
              first move&rdquo;. Sleep confidence = calibrated agreement
              between my recommendation and what the shop&apos;s
              historical winning actions look like (calibration requires
              30 matched obs).
            </p>
            <ul className="mt-4 space-y-1.5 text-[12.5px]">
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-400">Run cadence</span>
                <span className="tabular-nums text-slate-300">Nightly · 24h lookback</span>
              </li>
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-400">Inference engine</span>
                <span className="tabular-nums text-slate-300">Bayesian · vertical-tuned priors</span>
              </li>
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-400">Calibration target</span>
                <span className="tabular-nums text-slate-300">30 matched obs</span>
              </li>
              <li className="flex justify-between gap-3 last:border-0">
                <span className="text-slate-400">Status palette</span>
                <span className="tabular-nums text-slate-300">Quiet / Active / Alarm</span>
              </li>
            </ul>
            <p className="mt-4 text-[12.5px] leading-relaxed italic text-slate-400">
              The reasoning journal is the audit trail — every signal I considered, every verdict, every reason. Nothing about NightShift&apos;s pick is opaque.
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
  data: NightShiftReport,
  applied: boolean,
  applying: boolean,
): PrimaryActionShape {
  if (applied) {
    return {
      headline: "Action applied",
      label: "Suggested first move shipped",
      description:
        "The recommendation is live. Lift Report (and Causal Lift on a longer window) will measure whether it moved the needle. Re-check tomorrow's NightShift to see whether this fix shifted the next bottleneck into the top spot.",
    };
  }
  if (applying) {
    return {
      headline: "Applying…",
      label: "Recommendation deploying",
      description:
        "The action is being committed to your storefront. Once live, it auto-launches with a 20% holdout — Lift Report will populate measured outcomes within the 7-day window.",
    };
  }
  if (!data.top_action) {
    return {
      headline: "No action needed",
      label: "Quiet night — no high-impact lever surfaced",
      description:
        "The overnight Bayesian read found no opportunity scoring above the action threshold. That's a healthy signal. Use the quiet morning to invest in what's working — Causal Lift highlights your strongest current variants.",
    };
  }
  if (data.status === "alarm") {
    return {
      headline: "Alarm · act first",
      label: data.top_action.label,
      description: `Status escalated overnight. ${data.metrics.critical_alerts} critical alert${data.metrics.critical_alerts === 1 ? "" : "s"} triggered + ${data.metrics.fusion_alert_count} fusion alert${data.metrics.fusion_alert_count === 1 ? "" : "s"}. The suggested move is the highest-impact lever I found. One-click apply runs it; cross-check Anomaly Replay if you want the underlying minute-by-minute first.`,
    };
  }
  if (data.status === "active") {
    return {
      headline: "Active · primary lever ready",
      label: data.top_action.label,
      description: `Status is active — meaningful signal activity overnight. The suggested action is +${fmtMoney(data.top_action.estimated_impact_eur, data.currency)}/mo impact. Run it, watch the next 24h cycle to see whether the next-ranked lever takes its place.`,
    };
  }
  return {
    headline: "Quiet · light-touch",
    label: data.top_action.label,
    description:
      "Quiet overnight, but the system found a low-friction optimization worth shipping. Apply if you have 5 minutes; otherwise skip — quiet nights are good and don't require action.",
  };
}

function computeSupportingActions(data: NightShiftReport): SupportingAction[] {
  const out: SupportingAction[] = [];
  if (data.top_action) {
    out.push({
      label: "Audit the reasoning",
      description:
        "Open the journal above to see every signal I considered + my verdict on each. If you disagree with a verdict, the reasoning is in plain English — that's the audit trail for trusting (or overriding) the recommendation.",
    });
  }
  out.push({
    label: "Cross-check Causal Why",
    description:
      "Causal Why runs the same kind of inference but in real time. NightShift's overnight pick + Causal Why's live read should agree most mornings. When they diverge, the divergence itself is information — likely a fast-moving signal that fired after the overnight cut.",
  });
  out.push({
    label: "Open Night Shift Timeline",
    description:
      "The Timeline is the retrospective view — every overnight read of the past 30 days, with whether the suggested action was applied and the lift it produced. Use it to calibrate trust in the system before flipping to higher autonomy levels.",
  });
  return out;
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

function MoonIcon() {
  return (
    <svg
      className="h-3 w-3"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.2}
      aria-hidden="true"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"
      />
    </svg>
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
