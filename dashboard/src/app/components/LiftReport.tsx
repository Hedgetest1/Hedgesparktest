"use client";

/**
 * LiftReport — Pro moat, rich exploration pattern.
 *
 * Holdout-measured lift — HedgeSpark's most defensible claim. Every
 * nudge that ships goes live for 80% of visitors; the other 20% (the
 * holdout) sees nothing. After enough traffic, the conversion-rate
 * difference is the real lift. No model, no inflation, no cherry-pick.
 *
 * Three sections (matches VisitorIntentExplorer + Counterfactual +
 * Playbook + AnomalyReplay):
 *   1. Mechanics + stakes — holdout-vs-observation framing + why this
 *      math is the only one you don't have to trust the vendor on.
 *   2. Data — hero stat (lift % color-coded by tier), exposed vs
 *      holdout CVR side-by-side bars, attributed revenue, per-nudge
 *      breakdown (collapsible), key metrics, methodology.
 *   3. Actions — primary action with 5 cases (no data / strong winner
 *      / modest / negative / small sample), supporting actions
 *      (cross-check Causal Lift, watch sample-size threshold, share).
 *
 * Source: GET /pro/lift?window_hours=168 (require_pro_session).
 */

import { useEffect, useState } from "react";
import { createMoneyFormatter, type DisplayCurrency } from "../lib/currency";
import { apiClient, getHeaders, type paths } from "../lib/api-client";

const ACCENT = {
  eyebrow: "#34d399",
  hero: "#10b981",
  bg: "rgba(16,185,129,0.08)",
  border: "rgba(16,185,129,0.25)",
};

type LiftData =
  paths["/pro/lift"]["get"]["responses"]["200"]["content"]["application/json"];
type SupportingAction = { label: string; description: string };
type PrimaryAction = { headline: string; label: string; description: string };

function formatPct(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

function shortUrl(url: string | undefined): string {
  if (!url) return "—";
  const slug = url.split("/").filter(Boolean).pop() || url;
  return slug.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()).slice(0, 28);
}

function liftColor(lift: number | null | undefined): string {
  if (lift == null) return "#94a3b8";
  if (lift > 5) return "#34d399";
  if (lift >= 0) return "#fbbf24";
  return "#fb7185";
}

export function LiftReport({
  apiBase: _apiBase,
  shop,
  apiHeaders,
  displayCurrency = "USD",
}: {
  apiBase: string;
  shop: string;
  apiHeaders: () => HeadersInit;
  displayCurrency?: DisplayCurrency;
}) {
  const [data, setData] = useState<LiftData | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!shop) return;
    let active = true;
    async function load() {
      try {
        setLoading(true);
        const res = await apiClient.GET("/pro/lift", {
          params: { query: { window_hours: 168 } },
          headers: getHeaders(apiHeaders),
        });
        if (active && res.data != null) setData(res.data);
      } catch {
        /* silent */
      } finally {
        if (active) setLoading(false);
      }
    }
    load();
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop]);

  if (loading) {
    return (
      <section className="rounded-3xl border border-white/[0.08] bg-[#0e0e1a] p-7 sm:p-9">
        <div className="h-6 w-48 animate-pulse rounded bg-white/[0.05]" />
        <div className="mt-3 h-4 w-full animate-pulse rounded bg-white/[0.03]" />
        <div className="mt-2 h-4 w-4/5 animate-pulse rounded bg-white/[0.03]" />
      </section>
    );
  }
  if (!data) return null;

  const hasData = data.has_experiment_data;
  const lift = data.lift_pct;
  const revenue = data.attributed_revenue ?? 0;
  const breakdown = data.nudge_breakdown ?? [];
  const nativeCurrency = data.currency ?? "USD";
  const formatDollars = createMoneyFormatter(displayCurrency, nativeCurrency);
  const exposedCvr = data.exposed_cvr ?? 0;
  const holdoutCvr = data.holdout_cvr ?? 0;
  const totalExposed = data.total_exposed ?? 0;
  const totalHoldout = data.total_holdout ?? 0;
  const sampleHealthy = totalExposed >= 500 && totalHoldout >= 100;

  const subtitle = !hasData
    ? "No experiments running yet — every nudge you deploy automatically launches with a 20% control group."
    : `${formatPct(lift)} conversion lift over ${(totalExposed + totalHoldout).toLocaleString()} visitors · last 7 days.`;

  const primaryAction = computePrimaryAction(hasData, lift, sampleHealthy, revenue, formatDollars);
  const supportingActions = computeSupportingActions(hasData, lift, sampleHealthy);

  return (
    <section
      role="region"
      aria-label="Holdout-measured lift report"
      className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-[#0e0e1a] p-7 shadow-[0_20px_80px_-20px_rgba(0,0,0,0.6)] sm:p-9"
    >
      <div
        className="mb-3 text-[10px] font-bold uppercase tracking-[0.18em]"
        style={{ color: ACCENT.eyebrow }}
      >
        Holdout proof · 7-day window
      </div>
      <h2
        className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight sm:text-[2rem]"
        style={{ color: ACCENT.hero }}
      >
        Lift Report
      </h2>
      <p className="mt-2 text-[15px] font-semibold text-white">{subtitle}</p>

      {/* ── Section 1: mechanics + stakes ── */}
      <div className="mt-5 max-w-3xl space-y-5">
        <p className="text-[14px] leading-relaxed text-slate-300">
          Every nudge that ships on your store goes live for 80% of
          visitors. The other 20% — the holdout — sees nothing.
          Assignment is hash-based on visitor_id, deterministic across
          sessions. After enough traffic accumulates, I compare the
          conversion rate of the exposed group vs the holdout. The
          difference is the lift caused by the nudge — with no
          inflation, no cherry-picking, no model assumptions.
        </p>
        <div>
          <div
            className="mb-1.5 text-[10.5px] font-bold uppercase tracking-[0.18em]"
            style={{ color: ACCENT.eyebrow }}
          >
            Why this matters
          </div>
          <p className="text-[14px] leading-relaxed text-slate-300">
            Triple Whale claims lift from observation alone. Northbeam
            runs MMM models. Both can be wrong by 30%+ for any given fix
            because they don&apos;t have a control group. Holdout-
            measured lift is the only number that doesn&apos;t require
            you to trust the vendor&apos;s math — you can re-run the
            comparison yourself from raw events any time. Every &ldquo;+€X
            saved&rdquo; HedgeSpark claims passes through this filter.
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

        {!hasData ? (
          <EmptyPreview accentHero={ACCENT.hero} verdict={data.verdict} />
        ) : (
          <>
            {/* Hero stat — lift % */}
            <div className="mb-6 rounded-xl border border-white/[0.06] bg-[#0b0b14]/70 p-5">
              <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                Conversion lift vs control
              </div>
              <div className="mt-2 flex items-end gap-6">
                <div>
                  <div
                    className="text-[2.75rem] font-extrabold leading-none tabular-nums"
                    style={{ color: liftColor(lift) }}
                  >
                    {formatPct(lift)}
                  </div>
                  <div className="mt-1 text-[12.5px] text-slate-400">
                    over {(totalExposed + totalHoldout).toLocaleString()} visitors
                  </div>
                </div>
                {revenue > 0 && (
                  <div>
                    <div className="text-[1.5rem] font-bold leading-none tabular-nums text-emerald-300">
                      {formatDollars(revenue)}
                    </div>
                    <div className="mt-1 text-[12.5px] text-slate-400">
                      attributed extra revenue
                    </div>
                  </div>
                )}
              </div>
              <p className="mt-3.5 text-[13px] leading-relaxed text-slate-400">
                {data.verdict}
              </p>
            </div>

            {/* Exposed vs holdout — bars */}
            <div className="mb-6 grid gap-3 sm:grid-cols-2">
              <div className="rounded-xl border border-emerald-400/15 bg-emerald-500/[0.025] px-5 py-4">
                <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-emerald-300">
                  Saw your fix
                </div>
                <div className="mt-1.5 text-[24px] font-extrabold leading-none tabular-nums text-white">
                  {(exposedCvr * 100).toFixed(2)}%
                </div>
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.min(100, exposedCvr * 100 * 20)}%`,
                      background: "#34d399",
                    }}
                  />
                </div>
                <div className="mt-2 text-[12px] text-slate-400">
                  {totalExposed.toLocaleString()} exposed visitors
                </div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-5 py-4">
                <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">
                  Control group
                </div>
                <div className="mt-1.5 text-[24px] font-extrabold leading-none tabular-nums text-white">
                  {(holdoutCvr * 100).toFixed(2)}%
                </div>
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className="h-full rounded-full bg-slate-500"
                    style={{ width: `${Math.min(100, holdoutCvr * 100 * 20)}%` }}
                  />
                </div>
                <div className="mt-2 text-[12px] text-slate-400">
                  {totalHoldout.toLocaleString()} holdout visitors
                </div>
              </div>
            </div>

            {/* Per-nudge breakdown — collapsible */}
            {breakdown.length > 0 && (
              <div className="mb-6">
                <button
                  type="button"
                  onClick={() => setExpanded((x) => !x)}
                  className="mb-3 flex items-center gap-2 text-[12.5px] font-semibold text-slate-400 transition-colors hover:text-slate-200"
                  aria-expanded={expanded}
                >
                  <svg
                    className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-90" : ""}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                  {expanded ? "Hide" : "Show"} per-nudge breakdown ({breakdown.length})
                </button>
                {expanded && (
                  <div className="space-y-2">
                    {breakdown.map((n, i) => (
                      <div
                        key={`nb-${n.nudge_id ?? i}`}
                        className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.05] bg-[#0b0b14]/60 px-4 py-3"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[13.5px] font-semibold text-slate-200">
                            {shortUrl(n.product_url)}
                          </div>
                          <div className="mt-0.5 text-[11px] text-slate-400">
                            {n.exposed_count?.toLocaleString()} exposed · {n.holdout_count?.toLocaleString()} control
                          </div>
                        </div>
                        <div className="flex-shrink-0 text-right">
                          <div
                            className="text-[16px] font-bold tabular-nums"
                            style={{ color: liftColor(n.lift_pct) }}
                          >
                            {formatPct(n.lift_pct)}
                          </div>
                          {n.attributed_revenue != null && n.attributed_revenue > 0 && (
                            <div className="text-[11.5px] font-semibold text-emerald-300/80">
                              {formatDollars(n.attributed_revenue)}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Key metrics */}
            <div className="mb-6">
              <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                Key metrics
              </div>
              <div className="divide-y divide-white/[0.04] rounded-xl border border-white/[0.05] bg-[#0b0b14]/50">
                <KvRow label="Lift" value={formatPct(lift)} color={liftColor(lift)} />
                <KvRow
                  label="Exposed CVR"
                  value={`${(exposedCvr * 100).toFixed(2)}%`}
                  color="#34d399"
                />
                <KvRow
                  label="Holdout CVR"
                  value={`${(holdoutCvr * 100).toFixed(2)}%`}
                />
                <KvRow
                  label="Sample size"
                  value={`${totalExposed.toLocaleString()} + ${totalHoldout.toLocaleString()}`}
                  color={sampleHealthy ? "#34d399" : "#fbbf24"}
                />
                <KvRow
                  label="Attributed revenue"
                  value={revenue > 0 ? formatDollars(revenue) : "—"}
                  color={revenue > 0 ? "#34d399" : undefined}
                />
                <KvRow label="Window" value="7 days" />
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
              For every nudge that ships, every incoming visitor is
              hashed (SHA-256 of visitor_id) and assigned to either the
              exposed group (80%) or the holdout (20%). The assignment
              is deterministic — the same visitor on a return session
              lands in the same group, so cookies clearing don&apos;t
              corrupt the experiment. We then track conversion within
              the 7-day attribution window for both groups. Lift % =
              (exposed_cvr − holdout_cvr) / holdout_cvr × 100.
            </p>
            <ul className="mt-4 space-y-1.5 text-[12.5px]">
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-500">Assignment</span>
                <span className="tabular-nums text-slate-300">SHA-256 hash · 80/20 split</span>
              </li>
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-500">Attribution window</span>
                <span className="tabular-nums text-slate-300">7 days post-exposure</span>
              </li>
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-500">Min healthy sample</span>
                <span className="tabular-nums text-slate-300">500 exposed + 100 holdout</span>
              </li>
              <li className="flex justify-between gap-3 last:border-0">
                <span className="text-slate-500">Significance bar</span>
                <span className="tabular-nums text-slate-300">p&lt;0.05 chi-squared</span>
              </li>
            </ul>
            <p className="mt-4 text-[12.5px] leading-relaxed italic text-slate-400">
              Quasi-experimental — visitors aren&apos;t blocked from the site, just from the nudge. The exposed group experiences the storefront identically except for the experimental treatment.
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

      {/* Trust footer */}
      <div className="mt-5 inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3 py-1">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]" />
        <span className="text-[10px] text-slate-400">
          Quasi-experimental · hash-based assignment · 7-day window · re-runnable from raw events
        </span>
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------

function computePrimaryAction(
  hasData: boolean,
  lift: number | null | undefined,
  sampleHealthy: boolean,
  revenue: number,
  formatDollars: (n: number) => string,
): PrimaryAction {
  if (!hasData) {
    return {
      headline: "No experiments yet",
      label: "Deploy your first nudge to start the holdout",
      description:
        "Every nudge auto-launches with a 20% control group. Once enough visitors land in both groups (target: 500 exposed + 100 holdout for healthy stats), this card fills with measured lift instead of vendor estimates.",
    };
  }
  if (!sampleHealthy) {
    return {
      headline: "Small sample",
      label: "Numbers below the healthy-sample bar",
      description:
        "Lift is currently directional but not statistically reliable. Wait until you have 500+ exposed and 100+ holdout visitors before treating this number as a decision input. Until then, prefer Causal Lift's longer window for guidance.",
    };
  }
  if ((lift ?? 0) > 5) {
    return {
      headline: "Strong winner",
      label: `${formatPct(lift)} lift over control — bank the win`,
      description: `Holdout-clean +${(lift ?? 0).toFixed(1)}% with ${revenue > 0 ? formatDollars(revenue) + " attributed extra revenue" : "real visitor count"}. Lock the variant in production, kill the holdout once you've confirmed stability for 14 days, and ship the next experiment in the queue.`,
    };
  }
  if ((lift ?? 0) >= 0) {
    return {
      headline: "Modest lift",
      label: `${formatPct(lift)} — green but not exciting`,
      description:
        "The treatment outperforms control but the margin is thin. Consider iterating on copy/timing/audience before locking. Open Nudge DNA to see which patterns are pulling weight, then A/B against the current variant.",
    };
  }
  return {
    headline: "Roll back",
    label: `${formatPct(lift)} — the treatment is hurting conversion`,
    description:
      "The exposed group converts WORSE than control. Pause the nudge in Settings → Nudges, investigate via Anomaly Replay (the rollback window will show the visitor flow), then re-launch with a fixed variant. Don't ship it long-form just because it has &ldquo;activity&rdquo;.",
  };
}

function computeSupportingActions(
  hasData: boolean,
  lift: number | null | undefined,
  sampleHealthy: boolean,
): SupportingAction[] {
  if (!hasData) {
    return [
      {
        label: "Open Live Opportunities",
        description:
          "Live Opportunities is where new nudges seed from. Pick the highest-leakage page and enable the suggested nudge — the holdout starts on the next visitor.",
      },
    ];
  }
  const out: SupportingAction[] = [];
  if (!sampleHealthy) {
    out.push({
      label: "Wait for sample to mature",
      description:
        "The 500/100 healthy threshold is a chi-squared p<0.05 floor. Below it, random variance can drive +/-30% movements that don't replicate. Don't overreact.",
    });
  }
  out.push({
    label: "Cross-check Causal Lift",
    description:
      "Causal Lift uses a longer window (30d) and synthetic-control math for the same comparison. If both agree, you're definitively winning/losing; if they disagree, the disagreement itself is information.",
  });
  if ((lift ?? 0) >= 0) {
    out.push({
      label: "Mine winning patterns via Nudge DNA",
      description:
        "Nudge DNA identifies which copy patterns (length, urgency words, social proof) are pulling the lift. Export the winning ones into your next variant before iterating.",
    });
  }
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

function EmptyPreview({
  accentHero,
  verdict,
}: {
  accentHero: string;
  verdict: string | null | undefined;
}) {
  return (
    <div className="mb-6 rounded-xl border border-dashed border-white/[0.12] bg-[#0b0b14]/40 p-5 sm:p-6">
      <div className="mb-3 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
        <span
          className="inline-block h-1.5 w-1.5 animate-pulse rounded-full"
          style={{ background: accentHero }}
          aria-hidden="true"
        />
        Preview — what this card will show
      </div>
      <p className="mb-5 text-[13px] leading-relaxed text-slate-400">
        {verdict ?? "Deploy a nudge → 20% goes to the holdout → we measure the gap. The card fills with real lift the moment your first nudge has enough exposure."}
      </p>
      <div className="pointer-events-none mb-4 rounded-xl border border-white/[0.05] bg-[#0b0b14]/60 p-5 opacity-50">
        <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Conversion lift vs control
        </div>
        <div className="mt-2 flex items-end gap-6">
          <div>
            <div
              className="text-[2.75rem] font-extrabold leading-none tabular-nums"
              style={{ color: "#34d399" }}
            >
              +6.4%
            </div>
            <div className="mt-1 text-[12.5px] text-slate-400">over 1,840 visitors</div>
          </div>
          <div>
            <div className="text-[1.5rem] font-bold leading-none tabular-nums text-emerald-300">
              €312
            </div>
            <div className="mt-1 text-[12.5px] text-slate-400">attributed extra revenue</div>
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2 rounded-lg border border-emerald-400/15 bg-emerald-500/[0.05] px-3 py-2 text-[11.5px] font-semibold text-emerald-300">
        <span
          className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400"
          aria-hidden="true"
        />
        Holdout active — first nudge populates this view automatically.
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
