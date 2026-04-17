"use client";

/**
 * NightShiftCard — Phase Ω⁵ killer.
 *
 * The first thing a Pro merchant sees every morning. While they slept,
 * HedgeSpark read every signal, picked the single most impactful lever,
 * and left a visible reasoning journal explaining WHY that lever and not
 * the others. One click applies the suggested action.
 *
 * Sister component: NightShiftTimeline (retrospective proof-of-work).
 *
 * Source: GET /pro/night-shift/latest
 * Action: POST /pro/night-shift/apply
 *
 * Storytelling (passes the 4 filters):
 *   - narrative: "Quiet night. HedgeSpark prevented €840 while you slept."
 *   - plain language: no jargon in the headline
 *   - visual hierarchy: headline > action > confidence > journal
 *   - loss framing: top action shows €/mo impact
 */

import { useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import { reportFrontendError } from "../lib/error-reporter";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";

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
  // Shop's native currency — `top_action.estimated_impact_eur` and
  // `metrics.*_eur` fields are denominated in this currency.
  currency?: string;
};

const STATUS_ACCENT: Record<string, { bar: string; glow: string; pill: string; pillBg: string }> = {
  quiet:  { bar: "#34d399", glow: "rgba(52,211,153,0.12)", pill: "#34d399", pillBg: "rgba(52,211,153,0.12)" },
  active: { bar: "#fbbf24", glow: "rgba(251,191,36,0.14)", pill: "#fbbf24", pillBg: "rgba(251,191,36,0.12)" },
  alarm:  { bar: "#f87171", glow: "rgba(248,113,113,0.16)", pill: "#f87171", pillBg: "rgba(248,113,113,0.12)" },
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
  });

  if (!isProUser) return null;

  if (state === "loading") {
    return <CardSkeleton label="Loading tonight's night-shift report" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Night shift report unavailable"
        message="We couldn't load the latest night-shift report. Your signals are safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="emerald"
        title="First night-shift report is on its way"
        body="Every night we read every signal on your store, pick the single most impactful lever, and leave a reasoning journal. The first report lands after we've watched your store for a full overnight cycle."
        eta="First report after the next overnight run"
      />
    );
  }

  const accent = STATUS_ACCENT[data.status] || STATUS_ACCENT.quiet;
  const confidencePct = Math.max(0, Math.min(100, data.sleep_confidence));
  const prov = data.metrics?.sleep_confidence_provenance;
  const isCalibrated = prov?.calibrated ?? false;

  const applyAction = async () => {
    if (!data.top_action || applying || applied) return;
    setApplying(true);
    try {
      const { error } = await apiClient.POST("/pro/night-shift/apply");
      if (!error) {
        setApplied(true);
      } else {
        reportFrontendError({
          component: "NightShiftCard.applyAction",
          error_type: "HttpError",
          message: "POST /pro/night-shift/apply failed",
          severity: "warning",
        });
      }
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

  return (
    <section
      className="relative overflow-hidden rounded-2xl border border-white/[0.07] p-6"
      style={{ background: `linear-gradient(135deg, ${accent.glow} 0%, rgba(255,255,255,0.02) 60%)` }}
      aria-labelledby="night-shift-heading"
      role="region"
    >
      <div
        aria-hidden="true"
        className="absolute inset-x-0 top-0 h-0.5"
        style={{ background: accent.bar }}
      />

      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-2 flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.16em] text-[#e8a04e]">
            <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
            </svg>
            Night shift · {data.day}
          </div>
          <h3
            id="night-shift-heading"
            className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]"
          >
            {data.headline}
          </h3>
          <p className="mt-1 text-[11px] text-slate-500">
            Generated {relativeTime(data.generated_at)}
          </p>
        </div>

        <div className="flex-shrink-0 text-right">
          <div
            className="rounded-full px-3 py-1 text-[10px] font-bold uppercase tracking-wide tabular-nums"
            style={{ color: accent.pill, background: accent.pillBg, border: `1px solid ${accent.pill}40` }}
            aria-label={`Sleep confidence ${confidencePct} out of 100`}
            title={prov?.cap_reason || undefined}
          >
            {confidencePct}/100 · {data.sleep_confidence_label}
          </div>
          {!isCalibrated && prov && (
            <div
              className="mt-1 text-right text-[9px] font-semibold uppercase tracking-wide text-amber-300/80"
              title="Calibration collects 30 matched observations before we trust a 'full autonomy' label. This is the honest default."
            >
              Score uncalibrated · {prov.observations} obs
            </div>
          )}
          <div className="mt-2 h-1 w-32 overflow-hidden rounded-full bg-white/[0.05]" aria-hidden="true">
            <div
              className="h-full rounded-full transition-all duration-700"
              style={{ width: `${confidencePct}%`, background: accent.pill }}
            />
          </div>
        </div>
      </div>

      <p className="mb-4 text-[14px] leading-relaxed text-slate-200">{data.narrative}</p>

      {/* Top action */}
      {data.top_action && (
        <div className="mb-3 rounded-xl border border-white/[0.08] bg-white/[0.025] p-4">
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-[#e8a04e]">
              Suggested first move
            </span>
            {data.top_action.estimated_impact_eur > 0 && (
              <span className="rounded-md border border-amber-400/30 bg-amber-500/[0.08] px-2 py-0.5 text-[11px] font-extrabold tabular-nums text-amber-300">
                +{fmtMoney(data.top_action.estimated_impact_eur, data.currency)}/mo
              </span>
            )}
          </div>
          <div className="text-[15px] font-semibold text-white">{data.top_action.label}</div>
          <p className="mt-1 text-[12px] leading-relaxed text-slate-400">{data.top_action.detail}</p>
          <button
            type="button"
            onClick={applyAction}
            disabled={applying || applied}
            className="mt-3 rounded-lg border px-4 py-2 text-[11px] font-bold uppercase tracking-wide transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[#e8a04e] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220] disabled:cursor-not-allowed"
            style={{
              color: applied ? "#34d399" : accent.pill,
              borderColor: (applied ? "#34d399" : accent.pill) + "40",
              background: (applied ? "#34d399" : accent.pill) + "12",
            }}
            aria-label={applied ? "Action applied" : "Apply suggested action"}
          >
            {applied ? "✓ Applied" : applying ? "Applying…" : "Apply with 1 click"}
          </button>
        </div>
      )}

      {/* Journal — visible reasoning chain */}
      {data.journal && data.journal.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setJournalOpen((v) => !v)}
            className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400 hover:text-slate-200 focus:outline-none focus-visible:text-[#e8a04e]"
            aria-expanded={journalOpen}
            aria-controls="night-shift-journal"
          >
            <span>Reasoning journal ({data.journal.length})</span>
            <svg
              className={`h-3 w-3 transition-transform ${journalOpen ? "rotate-180" : ""}`}
              viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {journalOpen && (
            <ul
              id="night-shift-journal"
              className="mt-2 space-y-1.5 border-t border-white/[0.05] pt-2.5"
              role="list"
            >
              {data.journal.map((j, i) => (
                <li key={i} className="flex items-start gap-2 text-[11px]">
                  <span
                    className="mt-0.5 inline-block rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide"
                    style={{
                      color: VERDICT_COLOR[j.verdict] || "#94a3b8",
                      background: (VERDICT_COLOR[j.verdict] || "#94a3b8") + "15",
                      border: `1px solid ${(VERDICT_COLOR[j.verdict] || "#94a3b8")}30`,
                    }}
                  >
                    {j.verdict}
                  </span>
                  <div className="min-w-0 flex-1">
                    <span className="font-mono text-[10px] text-slate-500">{j.signal}</span>
                    <span className="ml-1.5 text-slate-300">{j.reason}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
