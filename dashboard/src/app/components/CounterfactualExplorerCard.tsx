"use client";

/**
 * CounterfactualExplorerCard — Pro moat, rich exploration pattern.
 *
 * "If you'd acted N days ago, you'd have saved €X." Counterfactual
 * projection over every open opportunity signal: per-day loss × days
 * already open × AOV-weighted recovery factor for each lag scenario
 * (act now / 7d ago / 14d ago / 30d ago).
 *
 * Three sections (matches the VisitorIntentExplorer pattern restored
 * 2026-04-29 per founder mandate "Pro must be top-1-in-the-world deep,
 * not a downgrade from the old Lite cassettone"):
 *
 *   1. What you're seeing — title + data-driven subtitle + mechanics +
 *      stakes prose.
 *   2. The data (violet section) — hero stat (total max-save, rose
 *      because it's loss-to-delay), per-lag bar chart (act now / 7d /
 *      14d / 30d), key metrics, methodology with formula + inputs +
 *      note. Per-signal expandable list below.
 *   3. Your next moves (rose-amber gradient) — primary action with 5
 *      data-driven cases (no signals / 1 fresh / many fresh / mixed
 *      lag / aged crisis), supporting actions.
 *
 * Source: GET /pro/counterfactual/signals (require_pro_session).
 */

import { useState } from "react";
import { CardError, CardSkeleton, useCardFetch } from "./_CardStates";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";

const ACCENT = {
  eyebrow: "#d4893a",
  hero: "#fb923c",
  bg: "rgba(217,119,6,0.08)",
  border: "rgba(217,119,6,0.25)",
};

type Scenario = {
  days_ago: number;
  saved_eur: number;
  label: string;
};

type CfEntry = {
  signal_id: number;
  signal_type: string;
  product_url: string | null;
  detected_at: string | null;
  days_open: number;
  per_day_loss_eur: number;
  scenarios: Scenario[];
  max_save_eur: number;
  aov_used_eur: number;
  aov_is_real: boolean;
  headline: string;
};

type CfResponse = {
  shop_domain: string;
  aov_eur: number;
  aov_is_real: boolean;
  total_open_signals: number;
  total_max_save_eur: number;
  entries: CfEntry[];
  headline: string;
  currency?: string;
  generated_at: string;
};

type SupportingAction = {
  label: string;
  description: string;
};

type PrimaryAction = {
  headline: string;
  label: string;
  description: string;
};

const fmtMoney = (n: number, currency?: string): string =>
  formatMoneyCompact(n, currency || "USD");

function prettyType(t: string): string {
  return t.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

export function CounterfactualExplorerCard({
  apiBase,
  isProUser,
}: {
  apiBase: string;
  isProUser: boolean;
}) {
  const { data, state, retry } = useCardFetch<CfResponse>({
    url: `${apiBase}/pro/counterfactual/signals`,
    enabled: isProUser && !!apiBase,
    isEmpty: () => false,
    component: "CounterfactualExplorerCard",
  });
  const [expandedId, setExpandedId] = useState<number | null>(null);

  if (!isProUser) return null;
  if (state === "loading") return <CardSkeleton label="Loading counterfactual explorer" />;
  if (state === "error")
    return (
      <CardError
        label="Counterfactual explorer failed to load"
        message="Couldn't reach the signal loss projection — your revenue tracking is unaffected."
        onRetry={retry}
      />
    );

  const totalSignals = data?.total_open_signals ?? 0;
  const totalMaxSave = data?.total_max_save_eur ?? 0;
  const entries = data?.entries ?? [];
  const aov = data?.aov_eur ?? 0;
  const aovIsReal = data?.aov_is_real ?? false;
  const currency = data?.currency || "USD";
  const oldestDays = entries.reduce((m, e) => Math.max(m, e.days_open), 0);
  const avgLag = entries.length > 0
    ? Math.round(entries.reduce((s, e) => s + e.days_open, 0) / entries.length)
    : 0;
  const totalPerDay = entries.reduce((s, e) => s + e.per_day_loss_eur, 0);

  const lagAggregate = computeLagAggregate(entries);
  const subtitle = totalSignals === 0
    ? "No open signals — nothing to simulate. The card warms up on the first opportunity."
    : `${totalSignals} open signal${totalSignals !== 1 ? "s" : ""} — ${fmtMoney(totalMaxSave, currency)} already lost to delay.`;

  const primaryAction = computePrimaryAction(totalSignals, totalMaxSave, oldestDays, currency);
  const supportingActions = computeSupportingActions(totalSignals);

  return (
    <section
      role="region"
      aria-label="Counterfactual explorer — Pro exploration"
      className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-[#0e0e1a] p-7 shadow-[0_20px_80px_-20px_rgba(0,0,0,0.6)] sm:p-9"
    >
      <div
        className="mb-3 text-[10px] font-bold uppercase tracking-[0.18em]"
        style={{ color: ACCENT.eyebrow }}
      >
        What-if math
      </div>
      <h2
        className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight sm:text-[2rem]"
        style={{ color: ACCENT.hero }}
      >
        Counterfactual Explorer
      </h2>
      <p className="mt-2 text-[15px] font-semibold text-white">{subtitle}</p>

      {/* ── Section 1: mechanics + stakes ── */}
      <div className="mt-5 max-w-3xl space-y-5">
        <p className="text-[14px] leading-relaxed text-slate-300">
          For every open opportunity signal on your store, I project what
          your revenue would look like if you&apos;d acted N days ago.
          The math is your real AOV ({fmtMoney(aov, currency)}
          {!aovIsReal && " — estimated until you have 30 orders"})
          multiplied by each signal&apos;s per-day loss rate, projected
          back along four lag horizons (now, 7d, 14d, 30d). No
          fabrication — every number traces to a real signal-detection
          timestamp and your live order data.
        </p>
        <div>
          <div
            className="mb-1.5 text-[10.5px] font-bold uppercase tracking-[0.18em]"
            style={{ color: ACCENT.eyebrow }}
          >
            Why this matters
          </div>
          <p className="text-[14px] leading-relaxed text-slate-300">
            Counterfactuals turn &ldquo;oh well, missed it&rdquo; into a
            hard number. A 14-day-old signal bleeding {fmtMoney(15, currency)}/day is
            {" "}{fmtMoney(15 * 14, currency)} already gone — money that
            won&apos;t recover. Once you internalize that lag costs the same on
            every signal class, you start fixing same-day. Compound that
            across every signal type for a quarter and the difference is
            in the thousands.
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

        {/* Hero stat */}
        {totalSignals > 0 ? (
          <div className="mb-6 rounded-xl border border-white/[0.06] bg-[#0b0b14]/70 p-5">
            <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
              Money already lost to delay
            </div>
            <div
              className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums"
              style={{ color: "#fb7185" }}
            >
              {fmtMoney(totalMaxSave, currency)}
            </div>
            <div className="mt-2.5 text-[12.5px] leading-relaxed text-slate-400">
              {totalSignals} open signal{totalSignals !== 1 ? "s" : ""} bleeding ~{fmtMoney(totalPerDay, currency)}/day. Acting today caps the loss; every day of delay adds another ~{fmtMoney(totalPerDay, currency)}.
            </div>
          </div>
        ) : (
          <EmptyPreview accentHero={ACCENT.hero} currency={currency} />
        )}

        {/* Per-lag bar chart */}
        {totalSignals > 0 && lagAggregate.length > 0 && (
          <div className="mb-6 rounded-xl border border-white/[0.04] bg-[#0b0b14]/60 p-5">
            <div className="mb-4 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
              Recovery by acting-window — what each lag costs
            </div>
            <div className="space-y-2.5">
              {lagAggregate.map((row) => {
                const pct = totalMaxSave > 0
                  ? Math.min(100, Math.round((row.value / totalMaxSave) * 100))
                  : 0;
                return (
                  <div key={row.label} className="flex items-center gap-3">
                    <div className="w-[120px] flex-shrink-0 text-[12px] text-slate-400">
                      {row.label}
                    </div>
                    <div className="relative flex-1 overflow-hidden rounded bg-white/[0.04]">
                      <div
                        className="h-6 rounded"
                        style={{
                          width: `${pct}%`,
                          background: `linear-gradient(90deg, ${row.color}cc 0%, ${row.color}66 100%)`,
                        }}
                      />
                    </div>
                    <div
                      className="w-[90px] flex-shrink-0 text-right text-[13px] font-bold tabular-nums"
                      style={{ color: row.color }}
                    >
                      {fmtMoney(row.value, currency)}
                    </div>
                  </div>
                );
              })}
            </div>
            <p className="mt-4 text-[11.5px] leading-relaxed text-slate-400">
              Each bar is the sum of recoverable loss across all open
              signals if you&apos;d acted at that lag. The gap between
              &ldquo;Now&rdquo; and &ldquo;30 days ago&rdquo; is your
              cost of delay, in real money.
            </p>
          </div>
        )}

        {/* Key metrics */}
        {totalSignals > 0 && (
          <div className="mb-6">
            <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
              Key metrics
            </div>
            <div className="divide-y divide-white/[0.04] rounded-xl border border-white/[0.05] bg-[#0b0b14]/50">
              <KvRow label="Open signals" value={totalSignals.toLocaleString()} />
              <KvRow
                label="Total max-save (act now)"
                value={fmtMoney(totalMaxSave, currency)}
                color="#fb7185"
              />
              <KvRow
                label="Bleed rate"
                value={`${fmtMoney(totalPerDay, currency)}/day`}
                color="#fb923c"
              />
              <KvRow
                label="Average signal age"
                value={`${avgLag} day${avgLag !== 1 ? "s" : ""}`}
              />
              <KvRow
                label="Oldest open signal"
                value={`${oldestDays} day${oldestDays !== 1 ? "s" : ""}`}
                color={oldestDays >= 30 ? "#f87171" : oldestDays >= 14 ? "#fbbf24" : "#cbd5e1"}
              />
            </div>
          </div>
        )}

        {/* Per-signal expandable list */}
        {entries.length > 0 && (
          <div className="mb-6">
            <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
              Per-signal breakdown
            </div>
            <ul className="space-y-2">
              {entries.slice(0, 5).map((entry) => {
                const isExpanded = expandedId === entry.signal_id;
                return (
                  <li
                    key={entry.signal_id}
                    className="rounded-xl border border-white/[0.05] bg-[#0b0b14]/60"
                  >
                    <button
                      type="button"
                      onClick={() => setExpandedId(isExpanded ? null : entry.signal_id)}
                      className="flex w-full items-start justify-between gap-3 p-3.5 text-left transition-colors hover:bg-white/[0.02]"
                      aria-expanded={isExpanded}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="rounded-md bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-amber-300">
                            {entry.days_open}d open
                          </span>
                          <span className="truncate text-[12.5px] font-semibold text-slate-200">
                            {prettyType(entry.signal_type)}
                          </span>
                        </div>
                        {entry.product_url && (
                          <p
                            className="mt-1 truncate text-[10.5px] text-slate-400"
                            title={entry.product_url}
                          >
                            {entry.product_url.replace(/^\/products\//, "")}
                          </p>
                        )}
                      </div>
                      <div className="flex-shrink-0 text-right">
                        <div className="text-[13px] font-bold tabular-nums text-amber-300">
                          {fmtMoney(entry.max_save_eur, currency)}
                        </div>
                        <div className="text-[9.5px] text-slate-400">
                          ~{fmtMoney(entry.per_day_loss_eur, currency)}/day
                        </div>
                      </div>
                    </button>

                    {isExpanded && (
                      <div className="border-t border-white/[0.05] px-3.5 pb-3.5 pt-3">
                        <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                          What-if scenarios
                        </div>
                        <div className="mt-2 grid grid-cols-4 gap-2">
                          {entry.scenarios.map((s) => (
                            <div
                              key={s.days_ago}
                              className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-2 py-2 text-center"
                            >
                              <div className="text-[9px] text-slate-400">{s.label}</div>
                              <div className="mt-1 text-[12px] font-bold tabular-nums text-amber-300">
                                {fmtMoney(s.saved_eur, currency)}
                              </div>
                            </div>
                          ))}
                        </div>
                        <p className="mt-3 text-[11px] leading-relaxed text-slate-400">
                          {entry.headline}
                        </p>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {/* Methodology */}
        <div>
          <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
            How this is calculated
          </div>
          <div className="rounded-xl border border-white/[0.05] bg-[#0b0b14]/50 p-5">
            <p className="text-[13px] leading-relaxed text-slate-300">
              max_save_eur = days_open × per_day_loss_rate × AOV. The
              per-day loss rate comes from the signal type (abandoned-
              high-intent uses last-30d intent volume; refund-decline
              uses last-14d refund delta; nudge-gap uses missed-impression
              opportunity cost). For the 4-lag projection, we cap
              days_open at the lag horizon (so a 90d signal at the
              &ldquo;7d&rdquo; bucket projects 7d × per_day_loss × AOV).
            </p>
            <ul className="mt-4 space-y-1.5 text-[12.5px]">
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-400">AOV used</span>
                <span className="tabular-nums text-slate-300">
                  {fmtMoney(aov, currency)}
                  {!aovIsReal && " (estimated)"}
                </span>
              </li>
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-400">Open signals</span>
                <span className="tabular-nums text-slate-300">{totalSignals}</span>
              </li>
              <li className="flex justify-between gap-3 last:border-0">
                <span className="text-slate-400">Lag horizons projected</span>
                <span className="tabular-nums text-slate-300">Now · 7d · 14d · 30d</span>
              </li>
            </ul>
            <p className="mt-4 text-[12.5px] leading-relaxed italic text-slate-400">
              The 30-day projection caps at the signal&apos;s actual age
              when older signals exist, so the math never invents
              synthetic loss for signals that didn&apos;t exist yet.
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

type LagRow = { label: string; value: number; color: string };

function computeLagAggregate(entries: CfEntry[]): LagRow[] {
  if (entries.length === 0) return [];
  const aggBy: Record<number, number> = {};
  for (const e of entries) {
    for (const s of e.scenarios) {
      aggBy[s.days_ago] = (aggBy[s.days_ago] || 0) + s.saved_eur;
    }
  }
  const palette: Record<number, string> = {
    0: "#34d399",
    7: "#a78bfa",
    14: "#fbbf24",
    30: "#fb7185",
  };
  const labels: Record<number, string> = {
    0: "Act now",
    7: "If acted 7d ago",
    14: "If acted 14d ago",
    30: "If acted 30d ago",
  };
  return [0, 7, 14, 30]
    .filter((d) => aggBy[d] !== undefined)
    .map((d) => ({
      label: labels[d],
      value: aggBy[d],
      color: palette[d],
    }));
}

function computePrimaryAction(
  totalSignals: number,
  totalMaxSave: number,
  oldestDays: number,
  currency: string,
): PrimaryAction {
  if (totalSignals === 0) {
    return {
      headline: "Counterfactual idle",
      label: "No open signals to project",
      description:
        "The system catches signals as they fire — abandoned-intent, refund-decline, nudge-gap, below-benchmark, goal-gap. Once your first signal opens, this card starts the projection.",
    };
  }
  if (totalSignals === 1 && oldestDays < 7) {
    return {
      headline: "Single fresh signal",
      label: `Strike on the open signal — ${fmtMoney(totalMaxSave, currency)} recoverable`,
      description:
        "One opportunity, recent, fixable in a single move. Open the per-signal breakdown above, click into the row, and act today before the lag cost compounds.",
    };
  }
  if (oldestDays < 7) {
    return {
      headline: "Quick window",
      label: `${totalSignals} open signals, all under a week — ${fmtMoney(totalMaxSave, currency)} recoverable`,
      description:
        "All your open signals are recent. A single batched-fix afternoon recovers the headline number. The 7d / 14d / 30d projections show what happens if you don't.",
    };
  }
  if (oldestDays < 30) {
    return {
      headline: "Compound lag",
      label: `Oldest signal is ${oldestDays} days open — bleeding ~${fmtMoney(totalMaxSave / Math.max(1, oldestDays), currency)}/day`,
      description:
        "Signals are aging. Triage by per-day loss rate (per-signal breakdown), fix the bleeders first. Every day of delay on the oldest signal costs the same as one day on a fresh one — but you can never recover what's already gone.",
    };
  }
  return {
    headline: "Aged crisis",
    label: `Oldest open signal is ${oldestDays} days — process change needed`,
    description:
      "A signal this old is a process problem, not a one-shot fix. Beyond closing the open signals, set up a daily review (or trigger a Slack notification on signal-fired) so future signals never reach 30 days again.",
  };
}

function computeSupportingActions(totalSignals: number): SupportingAction[] {
  if (totalSignals === 0) {
    return [
      {
        label: "Open Live Opportunities",
        description:
          "Live Opportunities is the parent — it lists pages leaking intent right now. Counterfactual flips that into projected recovery once a signal opens.",
      },
      {
        label: "Set up signal alerts",
        description:
          "If you connect Slack (Settings → Slack), every new signal posts to your channel — the projection in this card is then most useful for the post-mortem.",
      },
    ];
  }
  return [
    {
      label: "Triage by per-day loss",
      description:
        "Open the per-signal breakdown and sort by per-day loss rate — that's the bleeding rate, not the total. Highest bleed-rate signal is fix #1.",
    },
    {
      label: "Re-check after each action",
      description:
        "When you close a signal, the projection updates within 5 minutes. The shrinking total at the top is the proof.",
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

function EmptyPreview({
  accentHero,
  currency,
}: {
  accentHero: string;
  currency: string;
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
        Once your first opportunity-signal fires, the system projects
        what you&apos;d have saved by acting now vs 7d / 14d / 30d ago.
        The 30d projection shows you the cost-of-delay you&apos;ve
        already paid, in real money.
      </p>
      <div className="pointer-events-none mb-4 rounded-xl border border-white/[0.05] bg-[#0b0b14]/60 p-5 opacity-50">
        <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Money already lost to delay
        </div>
        <div
          className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums"
          style={{ color: "#fb7185" }}
        >
          {formatMoneyCompact(420, currency)}
        </div>
        <div className="mt-2.5 text-[12.5px] leading-relaxed text-slate-400">
          3 open signals bleeding ~{formatMoneyCompact(28, currency)}/day. Acting today caps the loss.
        </div>
      </div>
      <div className="flex items-center gap-2 rounded-lg border border-emerald-400/15 bg-emerald-500/[0.05] px-3 py-2 text-[11.5px] font-semibold text-emerald-300">
        <span
          className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400"
          aria-hidden="true"
        />
        Watching your signals — projection populates with the first opportunity.
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
