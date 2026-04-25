"use client";

/**
 * CompetitorPlaybookCard — Phase Ω⁷ killer #3.
 *
 * "Here's what merchants like you did when this signal fired."
 *
 * Anonymized peer ledger: network-wide aggregation of autonomous_actions
 * for a given signal type, grouped by action_type and outcome. Shows
 * success rate, most common moves, and measured CVR uplift percentiles.
 *
 * Source: GET /pro/playbook/{signal_type}
 */

import { CardError, CardSkeleton, useCardFetch } from "./_CardStates";

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

function prettyAction(t: string): string {
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
  if (!data) return null;

  const isWarming = data.state === "warming";

  return (
    <section
      className="rounded-2xl border border-violet-400/15 bg-violet-500/[0.03] p-5"
      aria-labelledby="playbook-heading"
      role="region"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#a855f7]">
            Peer Playbook · {data.vertical}
          </div>
          <h3 id="playbook-heading" className="text-[15px] font-bold leading-snug text-white">
            What merchants like you did
          </h3>
          <p className="mt-1 text-[11px] text-slate-400">
            Anonymized network-wide aggregation · last {data.lookback_days} days
          </p>
        </div>
        {!isWarming && data.success_rate_pct != null && (
          <div
            className="flex-shrink-0 rounded-xl border border-violet-400/25 bg-violet-500/[0.08] px-3 py-2 text-right"
            aria-label={`Network win rate ${data.success_rate_pct}%`}
          >
            <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-violet-300">
              Network win rate
            </div>
            <div className="text-[22px] font-extrabold tabular-nums text-violet-200">
              {data.success_rate_pct}%
            </div>
          </div>
        )}
      </div>

      <p className="mb-4 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3 text-[12px] leading-relaxed text-slate-300">
        {data.headline}
      </p>

      {isWarming ? (
        <div className="rounded-xl border border-dashed border-violet-400/20 bg-violet-500/[0.02] px-4 py-6 text-center">
          <div className="text-[12px] font-semibold text-violet-200">
            Peer pool warming up
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
            Tracking {data.total_peers} peer merchant{data.total_peers === 1 ? "" : "s"} so far ·
            need {data.min_required} for a reliable playbook.
          </p>
          <div className="mt-2 inline-block rounded-full bg-violet-500/10 px-2 py-0.5 text-[10px] font-semibold text-violet-300">
            New peer data every 24h — come back tomorrow
          </div>
        </div>
      ) : (
        <ul className="space-y-2" aria-label="Peer action playbook entries">
          {data.entries.slice(0, 6).map((entry) => {
            const wins = entry.outcomes["win"] || 0;
            const total = entry.total_shops || 1;
            const winRate = Math.round((wins / total) * 100);
            return (
              <li
                key={entry.action_type}
                className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3"
                aria-label={`${prettyAction(entry.action_type)} — ${entry.total_shops} peers, ${winRate}% win rate`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-[12px] font-semibold text-slate-200">
                      {prettyAction(entry.action_type)}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-400">
                      <span className="rounded bg-white/[0.04] px-1.5 py-px">{entry.total_shops} peers</span>
                      {wins > 0 && (
                        <span className="rounded bg-emerald-500/10 px-1.5 py-px text-emerald-300">{wins} wins</span>
                      )}
                      {entry.avg_lift_pct != null && (
                        <span className="rounded bg-violet-500/10 px-1.5 py-px text-violet-300">
                          +{entry.avg_lift_pct.toFixed(1)}% avg CVR lift
                        </span>
                      )}
                      {entry.best_lift_pct != null && entry.best_lift_pct !== entry.avg_lift_pct && (
                        <span className="rounded bg-violet-500/10 px-1.5 py-px text-violet-300">
                          best +{entry.best_lift_pct.toFixed(1)}%
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex-shrink-0 text-right">
                    <div className="text-[14px] font-bold tabular-nums text-violet-300">
                      {winRate}%
                    </div>
                    <div className="text-[9px] text-slate-400">win rate</div>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
