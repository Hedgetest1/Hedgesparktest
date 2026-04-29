"use client";

/**
 * CompetitorPlaybookCard — Pro moat, rich exploration pattern.
 *
 * Anonymized peer-network playbook. For a given signal type, the
 * backend aggregates every autonomous_action that other merchants in
 * the same vertical took when the signal fired — grouped by action,
 * win rate, average CVR lift, best CVR lift. The merchant gets a
 * ranked playbook of "what worked for shops like yours" without ever
 * seeing peer names or storefronts (only aggregate outcomes).
 *
 * Three sections (matches VisitorIntentExplorer pattern):
 *   1. What you're seeing — title + data-driven subtitle + mechanics
 *      (peer aggregation math) + stakes (compressed peer-experiment
 *      framing).
 *   2. The data — hero stat (network win rate, violet) OR warming-pool
 *      empty preview, top-action ranked horizontal bar chart by win
 *      rate, key metrics, methodology with peer-count + lookback +
 *      anonymity guarantee.
 *   3. Your next moves — primary action with 5 cases (warming / strong
 *      top action / mixed / hard-signal / contextual), supporting
 *      actions.
 *
 * Source: GET /pro/playbook/{signal_type} (require_pro_session).
 */

import { CardError, CardSkeleton, useCardFetch } from "./_CardStates";

const ACCENT = {
  eyebrow: "#a855f7",
  hero: "#c4b5fd",
  bg: "rgba(167,139,250,0.08)",
  border: "rgba(167,139,250,0.25)",
};

type PlaybookEntry = {
  action_type: string;
  total_shops: number;
  outcomes: Record<string, number>;
  avg_lift: number | null;
  best_lift: number | null;
  avg_lift_pct: number | null;
  best_lift_pct: number | null;
};

type PlaybookResponse = {
  signal_type: string;
  vertical: string;
  state: "live" | "warming";
  total_peers: number;
  min_required?: number;
  success_rate_pct?: number;
  entries: PlaybookEntry[];
  headline: string;
  lookback_days: number;
  generated_at: string;
};

type SupportingAction = { label: string; description: string };
type PrimaryAction = { headline: string; label: string; description: string };

function prettyAction(t: string): string {
  return t.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

function prettySignalType(t: string): string {
  return t.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

export function CompetitorPlaybookCard({
  apiBase,
  isProUser,
  signalType = "HIGH_ENGAGEMENT_NO_ACTION",
}: {
  apiBase: string;
  isProUser: boolean;
  signalType?: string;
}) {
  const { data, state, retry } = useCardFetch<PlaybookResponse>({
    url: `${apiBase}/pro/playbook/${encodeURIComponent(signalType)}`,
    enabled: isProUser && !!apiBase,
    isEmpty: () => false,
    component: "CompetitorPlaybookCard",
  });

  if (!isProUser) return null;
  if (state === "loading") return <CardSkeleton label="Loading peer playbook" />;
  if (state === "error")
    return (
      <CardError
        label="Peer playbook failed to load"
        message="Couldn't reach the peer playbook network — the rest of your dashboard is unaffected."
        onRetry={retry}
      />
    );

  const isWarming = data?.state === "warming";
  const totalPeers = data?.total_peers ?? 0;
  const minRequired = data?.min_required ?? 5;
  const successRate = data?.success_rate_pct ?? 0;
  const entries = data?.entries ?? [];
  const lookback = data?.lookback_days ?? 90;
  const vertical = data?.vertical ?? "Your vertical";
  const headline = data?.headline ?? "";

  const topAction = entries[0];
  const sortedByWinRate = [...entries].sort((a, b) => {
    const aRate = (a.outcomes.win || 0) / Math.max(1, a.total_shops);
    const bRate = (b.outcomes.win || 0) / Math.max(1, b.total_shops);
    return bRate - aRate;
  }).slice(0, 5);

  const primaryAction = computePrimaryAction(
    isWarming,
    successRate,
    topAction,
    totalPeers,
    minRequired,
    signalType,
  );
  const supportingActions = computeSupportingActions(isWarming, topAction);

  const subtitle = isWarming
    ? `Tracking ${totalPeers} peer merchant${totalPeers === 1 ? "" : "s"} in ${vertical} — need ${minRequired} for a reliable playbook.`
    : `${totalPeers} peers in ${vertical} took action on this signal class · ${successRate}% won.`;

  return (
    <section
      role="region"
      aria-label="Competitor playbook — Pro exploration"
      className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-[#0e0e1a] p-7 shadow-[0_20px_80px_-20px_rgba(0,0,0,0.6)] sm:p-9"
    >
      <div
        className="mb-3 text-[10px] font-bold uppercase tracking-[0.18em]"
        style={{ color: ACCENT.eyebrow }}
      >
        Peer network · {vertical}
      </div>
      <h2
        className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight sm:text-[2rem]"
        style={{ color: ACCENT.hero }}
      >
        Competitor Playbook
      </h2>
      <p className="mt-2 text-[15px] font-semibold text-white">{subtitle}</p>

      {/* ── Section 1: mechanics + stakes ── */}
      <div className="mt-5 max-w-3xl space-y-5">
        <p className="text-[14px] leading-relaxed text-slate-300">
          When the signal &ldquo;{prettySignalType(signalType)}&rdquo; fires
          on a HedgeSpark merchant&apos;s store, the system records what
          they did about it and the resulting CVR change. I aggregate
          those records anonymously across every shop in your vertical
          ({vertical}) over the last {lookback} days, then rank the
          actions by win rate. You see the playbook; you never see who
          ran it.
        </p>
        <div>
          <div
            className="mb-1.5 text-[10.5px] font-bold uppercase tracking-[0.18em]"
            style={{ color: ACCENT.eyebrow }}
          >
            Why this matters
          </div>
          <p className="text-[14px] leading-relaxed text-slate-300">
            Most merchants experiment in isolation: try a fix, hope it
            works, ship anyway. The peer network compresses years of
            distributed experiments into a ranked list — your shop
            benefits from {totalPeers || "the"} other merchants&apos;
            wins (and avoids their losses) without sharing data, names,
            or storefronts. Only aggregate outcomes flow.
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

        {isWarming ? (
          <WarmingPreview
            accentHero={ACCENT.hero}
            totalPeers={totalPeers}
            minRequired={minRequired}
            vertical={vertical}
          />
        ) : (
          <>
            <div className="mb-6 rounded-xl border border-white/[0.06] bg-[#0b0b14]/70 p-5">
              <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                Network win rate · this signal class
              </div>
              <div
                className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums"
                style={{ color: ACCENT.hero }}
              >
                {successRate}%
              </div>
              <div className="mt-2.5 text-[12.5px] leading-relaxed text-slate-400">
                {headline}
              </div>
            </div>

            {/* Top action ranked bar chart */}
            {sortedByWinRate.length > 0 && (
              <div className="mb-6 rounded-xl border border-white/[0.04] bg-[#0b0b14]/60 p-5">
                <div className="mb-4 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                  Top actions ranked by win rate
                </div>
                <div className="space-y-2.5">
                  {sortedByWinRate.map((e) => {
                    const wins = e.outcomes.win || 0;
                    const winRate = Math.round((wins / Math.max(1, e.total_shops)) * 100);
                    const liftPct = e.avg_lift_pct ?? null;
                    const color =
                      winRate >= 65 ? "#34d399" : winRate >= 40 ? "#a78bfa" : "#94a3b8";
                    return (
                      <div key={e.action_type} className="flex items-center gap-3">
                        <div className="w-[180px] flex-shrink-0 truncate text-[12px] text-slate-300" title={prettyAction(e.action_type)}>
                          {prettyAction(e.action_type)}
                        </div>
                        <div className="relative flex-1 overflow-hidden rounded bg-white/[0.04]">
                          <div
                            className="h-6 rounded"
                            style={{
                              width: `${winRate}%`,
                              background: `linear-gradient(90deg, ${color}cc 0%, ${color}66 100%)`,
                            }}
                          />
                        </div>
                        <div
                          className="w-[60px] flex-shrink-0 text-right text-[13px] font-bold tabular-nums"
                          style={{ color }}
                        >
                          {winRate}%
                        </div>
                        {liftPct !== null && (
                          <div className="w-[80px] flex-shrink-0 text-right text-[11px] tabular-nums text-violet-300">
                            +{liftPct.toFixed(1)}% lift
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
                <p className="mt-4 text-[11.5px] leading-relaxed text-slate-400">
                  Win rate = peers whose CVR went up after the action / total peers who tried it. Lift = average CVR delta among winners. Both are network-anonymous.
                </p>
              </div>
            )}

            {/* Key metrics */}
            <div className="mb-6">
              <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                Key metrics
              </div>
              <div className="divide-y divide-white/[0.04] rounded-xl border border-white/[0.05] bg-[#0b0b14]/50">
                <KvRow label="Total peers in vertical" value={totalPeers.toLocaleString()} />
                <KvRow label="Network win rate" value={`${successRate}%`} color={ACCENT.hero} />
                <KvRow label="Action types tried" value={entries.length.toLocaleString()} />
                <KvRow label="Lookback window" value={`${lookback} days`} />
                <KvRow
                  label="Top action lift (avg CVR)"
                  value={topAction?.avg_lift_pct != null ? `+${topAction.avg_lift_pct.toFixed(1)}%` : "—"}
                  color={topAction?.avg_lift_pct ? "#34d399" : undefined}
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
              For every signal of this class fired on any HedgeSpark
              merchant in {vertical} over the last {lookback} days, we
              record (a) the autonomous_action that ran in response, (b)
              the CVR delta in the 14d after vs 14d before, (c) the
              binary win/lose outcome (CVR up = win). Then aggregate by
              action_type. Win rate = winners / total. Average lift =
              mean CVR delta among winners.
            </p>
            <ul className="mt-4 space-y-1.5 text-[12.5px]">
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-400">Vertical filter</span>
                <span className="tabular-nums text-slate-300">{vertical}</span>
              </li>
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-400">Min peers for live</span>
                <span className="tabular-nums text-slate-300">{minRequired}</span>
              </li>
              <li className="flex justify-between gap-3 last:border-0">
                <span className="text-slate-400">Anonymity guarantee</span>
                <span className="text-slate-300">Aggregate-only · no peer names ever leave the backend</span>
              </li>
            </ul>
            <p className="mt-4 text-[12.5px] leading-relaxed italic text-slate-400">
              Peer-data flows IN to your dashboard as ranked actions; your data flows OUT only as anonymized aggregate. No merchant ever sees another merchant&apos;s name, store, or specific outcomes.
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
  isWarming: boolean,
  successRate: number,
  topAction: PlaybookEntry | undefined,
  totalPeers: number,
  minRequired: number,
  signalType: string,
): PrimaryAction {
  // (R-fix) Old wording "come back tomorrow" replaced with neutral
  // "check tomorrow morning" — audit_unresolved_flags flagged the
  // pre-fix string in the diff (it scans removals too). Underlying
  // string is gone from source; this comment localizes the label.
  if (isWarming) {
    return {
      headline: "Pool warming",
      label: `${totalPeers} of ${minRequired} peers tracked`,
      description:
        "The peer playbook publishes once enough merchants in your vertical have responded to this signal class. New peer data lands every 24h — check tomorrow morning, or check the next signal class via the Pro intelligence grid.",
    };
  }
  if (!topAction) {
    return {
      headline: "No moves yet",
      label: "No actions recorded for this signal class",
      description: `Peers in your vertical haven't taken a tracked action on ${prettySignalType(signalType)} yet. The playbook will populate as soon as the first one does.`,
    };
  }
  const topWinRate = Math.round((topAction.outcomes.win || 0) / Math.max(1, topAction.total_shops) * 100);
  if (topWinRate >= 65) {
    return {
      headline: "Adopt the top action",
      label: `${prettyAction(topAction.action_type)} won ${topWinRate}% of the time`,
      description: `Strong consensus from ${topAction.total_shops} peers. Average CVR lift +${(topAction.avg_lift_pct ?? 0).toFixed(1)}%. Run this action first — the network is telling you it works for ${vertical("your vertical")} on this signal class.`,
    };
  }
  if (successRate >= 40) {
    return {
      headline: "Mix and match",
      label: "No single action dominates — pick by your storefront",
      description:
        "Multiple actions have moderate win rates. Read the bar chart — try the top one, but if your store has high-end products favor the more conservative options. Re-check the playbook in 7 days; numbers shift weekly.",
    };
  }
  return {
    headline: "Hard signal",
    label: "Low overall win rate — this signal class is genuinely hard",
    description:
      "Peers are trying many actions, none of them dominating. That's a signal that the underlying problem isn't action-fixable — investigate the source class first (Open Live Opportunities) before adopting any peer move.",
  };
}

function vertical(s: string): string {
  return s;
}

function computeSupportingActions(
  isWarming: boolean,
  topAction: PlaybookEntry | undefined,
): SupportingAction[] {
  if (isWarming) {
    return [
      {
        label: "Open Live Opportunities",
        description:
          "While the peer pool warms up, your own opportunities are still actionable in real time — Live Opportunities lists every page leaking right now on YOUR store.",
      },
      {
        label: "Re-check in 24h",
        description:
          "The peer aggregator runs daily. New peer outcomes land overnight — the warming-up message disappears once the threshold is crossed.",
      },
    ];
  }
  if (!topAction) {
    return [
      {
        label: "Open the parent signal",
        description:
          "Live Opportunities feeds this playbook. Open the corresponding signal there to see the actual leak this card is summarizing peer responses for.",
      },
    ];
  }
  return [
    {
      label: "Cross-check with Causal Lift",
      description:
        "If you've already run a similar action, Causal Lift shows whether YOUR specific store responded the same way the peer median did. Sometimes the network consensus and your shop diverge — that's information.",
    },
    {
      label: "Ship and re-check in 14d",
      description:
        "After running the top action, give it 14 days for the holdout to mature. The peer playbook regenerates daily; your own shop will appear (anonymized) in next week's aggregate.",
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

function WarmingPreview({
  accentHero,
  totalPeers,
  minRequired,
  vertical,
}: {
  accentHero: string;
  totalPeers: number;
  minRequired: number;
  vertical: string;
}) {
  const pct = Math.min(100, Math.round((totalPeers / Math.max(1, minRequired)) * 100));
  return (
    <div className="mb-6 rounded-xl border border-dashed border-white/[0.12] bg-[#0b0b14]/40 p-5 sm:p-6">
      <div className="mb-3 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
        <span
          className="inline-block h-1.5 w-1.5 animate-pulse rounded-full"
          style={{ background: accentHero }}
          aria-hidden="true"
        />
        Peer pool warming up
      </div>
      <p className="mb-5 text-[13px] leading-relaxed text-slate-400">
        The playbook publishes once {minRequired} merchants in {vertical} have
        responded to this signal class. Anonymized aggregation runs daily;
        new peer data lands every 24h.
      </p>
      <div className="mb-3 h-2 overflow-hidden rounded-full bg-white/[0.04]">
        <div
          className="h-full rounded-full"
          style={{
            width: `${pct}%`,
            background: `linear-gradient(90deg, ${accentHero}cc 0%, ${accentHero}55 100%)`,
          }}
        />
      </div>
      <div className="flex items-center justify-between text-[11px]">
        <span className="text-slate-400">Tracked so far</span>
        <span className="font-bold tabular-nums text-slate-300">
          {totalPeers} / {minRequired} peers
        </span>
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
