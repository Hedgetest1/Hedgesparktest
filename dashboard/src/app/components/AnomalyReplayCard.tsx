"use client";

/**
 * AnomalyReplayCard — Pro moat, rich exploration pattern.
 *
 * Watch what actually happened. When an anomaly pattern (cross-signal
 * fusion, conversion drop, traffic spike) is detected, this card freezes
 * the event window and replays it minute-by-minute: every visitor,
 * source, device, URL. Nobody else in SMB Shopify ships this — needs
 * event-level granularity that only first-party trackers have.
 *
 * Three sections (matches VisitorIntentExplorer + Counterfactual +
 * CompetitorPlaybook):
 *   1. Mechanics + stakes — event-level reconstruction framing.
 *   2. Data — hero stat (total events vs unique visitors), minute-by-
 *      minute timeline sparkbar (color-coded by intensity), 3-column
 *      breakdown (source/device/type), key metrics, methodology with
 *      bucket size + cap + truncation note.
 *   3. Actions — primary action with 5 cases (empty / single-source
 *      spike / coordinated / quiet / truncated), supporting actions
 *      (cross-check Anomaly Fusion, expand window).
 *
 * Source: GET /pro/anomalies/{pattern}/replay?minutes=N (Pro-gated).
 */

import { useEffect, useState } from "react";
import { CardError } from "./_CardStates";

const ACCENT = {
  eyebrow: "#d4893a",
  hero: "#fbbf24",
  bg: "rgba(217,119,6,0.08)",
  border: "rgba(217,119,6,0.25)",
};

type ReplayEvent = {
  ts_ms: number;
  type: string;
  visitor: string;
  source: string;
  device: string;
  url: string;
};

type ReplayResponse = {
  pattern: string;
  window: { start_ms: number; end_ms: number; minutes: number };
  events: ReplayEvent[];
  timeline: { ts_ms: number; count: number }[];
  summary: {
    total_events: number;
    unique_visitors: number;
    by_source: { source: string; count: number }[];
    by_device: { device: string; count: number }[];
    by_type: { type: string; count: number }[];
  };
  narrative: string;
  truncated: boolean;
};

type SupportingAction = { label: string; description: string };
type PrimaryAction = { headline: string; label: string; description: string };

function fmtTime(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function prettyPattern(p: string): string {
  return p.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

export function AnomalyReplayCard({
  apiBase,
  isProUser,
  pattern = "cross_signal_fusion",
}: {
  apiBase: string;
  isProUser: boolean;
  pattern?: string;
}) {
  const [data, setData] = useState<ReplayResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [hadError, setHadError] = useState(false);
  const [minutes, setMinutes] = useState(60);

  const load = async (mins: number) => {
    setLoading(true);
    setHadError(false);
    try {
      const r = await fetch(
        `${apiBase}/pro/anomalies/${encodeURIComponent(pattern)}/replay?minutes=${mins}`,
        { credentials: "include" },
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch {
      setHadError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isProUser && apiBase) void load(minutes);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, isProUser, minutes, pattern]);

  if (!isProUser) return null;

  if (hadError && !data) {
    return (
      <CardError
        label="Anomaly replay failed to load"
        message="Couldn't reconstruct the event window right now — try a different time range or retry."
        onRetry={() => void load(minutes)}
      />
    );
  }

  const total = data?.summary.total_events ?? 0;
  const uniqueVisitors = data?.summary.unique_visitors ?? 0;
  const truncated = data?.truncated ?? false;
  const maxCount = data?.timeline.reduce((m, t) => Math.max(m, t.count), 0) || 1;
  const topSource = data?.summary.by_source[0];
  const sourceConcentration = topSource && total > 0
    ? Math.round((topSource.count / total) * 100)
    : 0;

  const subtitle = total === 0
    ? `Window empty — no events captured in the last ${minutes < 60 ? `${minutes}m` : `${minutes / 60}h`}.`
    : `${total.toLocaleString()} events · ${uniqueVisitors.toLocaleString()} visitor${uniqueVisitors !== 1 ? "s" : ""} · pattern ${prettyPattern(pattern)}.`;

  const primaryAction = computePrimaryAction(total, sourceConcentration, topSource, truncated);
  const supportingActions = computeSupportingActions(total, truncated);

  return (
    <section
      role="region"
      aria-label="Anomaly replay — Pro exploration"
      className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-[#0e0e1a] p-7 shadow-[0_20px_80px_-20px_rgba(0,0,0,0.6)] sm:p-9"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div
            className="mb-3 text-[10px] font-bold uppercase tracking-[0.18em]"
            style={{ color: ACCENT.eyebrow }}
          >
            Replay · {prettyPattern(pattern)}
          </div>
          <h2
            className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight sm:text-[2rem]"
            style={{ color: ACCENT.hero }}
          >
            Anomaly Replay
          </h2>
          <p className="mt-2 text-[15px] font-semibold text-white">{subtitle}</p>
        </div>
        {/* Window selector */}
        <div
          className="flex flex-shrink-0 items-center gap-1 rounded-lg border border-white/10 bg-white/[0.02] p-1"
          role="radiogroup"
          aria-label="Replay time window"
        >
          {[30, 60, 120, 240].map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMinutes(m)}
              role="radio"
              aria-checked={minutes === m}
              aria-label={`Show last ${m < 60 ? `${m} minutes` : `${m / 60} hours`}`}
              className={`rounded-md px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/60 ${
                minutes === m
                  ? "bg-amber-500/15 text-amber-300"
                  : "text-slate-400 hover:text-slate-300"
              }`}
            >
              {m < 60 ? `${m}m` : `${m / 60}h`}
            </button>
          ))}
        </div>
      </div>

      {/* ── Section 1: mechanics + stakes ── */}
      <div className="mt-5 max-w-3xl space-y-5">
        <p className="text-[14px] leading-relaxed text-slate-300">
          When an anomaly pattern fires on your store (cross-signal
          fusion, conversion drop, traffic spike, etc.), I freeze the
          event window and replay it minute-by-minute — every visitor,
          every source, every device, every URL hit. The reconstruction
          turns &ldquo;something weird happened&rdquo; into a
          time-stamped narrative you can audit and act on.
        </p>
        <div>
          <div
            className="mb-1.5 text-[10.5px] font-bold uppercase tracking-[0.18em]"
            style={{ color: ACCENT.eyebrow }}
          >
            Why this matters
          </div>
          <p className="text-[14px] leading-relaxed text-slate-300">
            Anomalies without context are noise. Watching the actual
            minute-by-minute reconstruction tells you whether it was a
            campaign that ran (good — capitalize), a price test breaking
            checkout (bad — roll back now), or a bot wave (ignorable —
            but tag the source). Without this you guess; with it you
            decide. No other SMB Shopify tool ships this because nobody
            else captures event-level data.
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

        {loading && !data ? (
          <div className="h-32 animate-pulse rounded-xl bg-white/[0.03]" />
        ) : total === 0 ? (
          <EmptyPreview accentHero={ACCENT.hero} minutes={minutes} />
        ) : (
          <>
            {/* Hero stat */}
            <div className="mb-6 rounded-xl border border-white/[0.06] bg-[#0b0b14]/70 p-5">
              <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                Events captured · last {minutes < 60 ? `${minutes} min` : `${minutes / 60} h`}
              </div>
              <div
                className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums"
                style={{ color: ACCENT.hero }}
              >
                {total.toLocaleString()}
              </div>
              <div className="mt-2.5 text-[12.5px] leading-relaxed text-slate-400">
                {data?.narrative}
              </div>
            </div>

            {/* Minute-by-minute timeline sparkbar */}
            <div className="mb-6 rounded-xl border border-white/[0.04] bg-[#0b0b14]/60 p-5">
              <div className="mb-3 flex items-center justify-between text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                <span>Minute-by-minute timeline</span>
                <span className="font-normal normal-case tracking-normal text-slate-400">
                  Color = intensity (rose=peak, amber=mid, emerald=quiet)
                </span>
              </div>
              <div className="flex h-16 items-end gap-px rounded-lg bg-white/[0.015] p-1">
                {data?.timeline.map((t, i) => {
                  const h = Math.max(3, Math.round((t.count / maxCount) * 56));
                  const color =
                    t.count === 0
                      ? "bg-white/[0.03]"
                      : t.count >= maxCount * 0.7
                        ? "bg-rose-400/80"
                        : t.count >= maxCount * 0.3
                          ? "bg-amber-400/80"
                          : "bg-emerald-400/70";
                  return (
                    <div
                      key={i}
                      className={`flex-1 rounded-sm ${color}`}
                      style={{ height: `${h}px` }}
                      title={`${fmtTime(t.ts_ms)}: ${t.count} events`}
                    />
                  );
                })}
              </div>
              <div className="mt-1 flex items-center justify-between text-[10px] text-slate-400">
                <span>{data && fmtTime(data.window.start_ms)}</span>
                <span>now</span>
              </div>
            </div>

            {/* 3-column breakdown */}
            <div className="mb-6 grid gap-3 sm:grid-cols-3">
              <BreakdownColumn label="By source" rows={data?.summary.by_source ?? []} />
              <BreakdownColumn label="By device" rows={data?.summary.by_device ?? []} />
              <BreakdownColumn label="By event type" rows={data?.summary.by_type ?? []} />
            </div>

            {/* Key metrics */}
            <div className="mb-6">
              <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                Key metrics
              </div>
              <div className="divide-y divide-white/[0.04] rounded-xl border border-white/[0.05] bg-[#0b0b14]/50">
                <KvRow label="Total events" value={total.toLocaleString()} />
                <KvRow label="Unique visitors" value={uniqueVisitors.toLocaleString()} />
                <KvRow
                  label="Top source concentration"
                  value={topSource ? `${topSource.source} (${sourceConcentration}%)` : "—"}
                  color={
                    sourceConcentration >= 70
                      ? "#fb7185"
                      : sourceConcentration >= 40
                        ? "#fbbf24"
                        : "#34d399"
                  }
                />
                <KvRow
                  label="Window length"
                  value={`${minutes < 60 ? `${minutes} min` : `${minutes / 60} h`}`}
                />
                <KvRow
                  label="Truncated"
                  value={truncated ? "Yes (200-event cap)" : "No"}
                  color={truncated ? "#fbbf24" : undefined}
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
              When the {prettyPattern(pattern)} pattern fires, I anchor the
              window at the detection timestamp and pull every event in
              the {minutes < 60 ? `${minutes}-minute` : `${minutes / 60}-hour`} preceding span.
              Timeline buckets are 1-minute aggregates. Source / device /
              type breakdowns are exact counts (no sampling). The event
              stream is capped at 200 to keep render fast — for high-
              volume windows the &ldquo;truncated&rdquo; flag indicates
              there are more events available; expand the window
              selector or filter by pattern.
            </p>
            <ul className="mt-4 space-y-1.5 text-[12.5px]">
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-400">Bucket size</span>
                <span className="tabular-nums text-slate-300">1 minute</span>
              </li>
              <li className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5">
                <span className="text-slate-400">Event cap</span>
                <span className="tabular-nums text-slate-300">200 (truncated flag)</span>
              </li>
              <li className="flex justify-between gap-3 last:border-0">
                <span className="text-slate-400">Anchor</span>
                <span className="tabular-nums text-slate-300">Pattern-detection timestamp</span>
              </li>
            </ul>
            <p className="mt-4 text-[12.5px] leading-relaxed italic text-slate-400">
              Replay is a real reconstruction, not a sampled approximation. Every event in the window is preserved unless the 200-cap kicks in.
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
  total: number,
  sourceConcentration: number,
  topSource: { source: string; count: number } | undefined,
  truncated: boolean,
): PrimaryAction {
  if (total === 0) {
    return {
      headline: "Window empty",
      label: "No events captured in this range",
      description:
        "Either the anomaly is older than your window (try expanding to 4h) or the tracker stopped firing during the period (check Settings → Tracker health). The replay is exact — no events means no events.",
    };
  }
  if (sourceConcentration >= 70 && topSource) {
    return {
      headline: "Source-driven anomaly",
      label: `${sourceConcentration}% from ${topSource.source}`,
      description: `One source dominates the spike — investigate that channel first. If it's a paid campaign, check whether it just launched (good — keep running it). If it's a referrer you don't recognize, consider it bot traffic until proven otherwise.`,
    };
  }
  if (sourceConcentration >= 40) {
    return {
      headline: "Coordinated traffic",
      label: `${sourceConcentration}% from a primary source, rest distributed`,
      description:
        "Multiple sources fired together — usually a campaign launch or a viral moment across channels. Check your scheduled campaigns first, then look at Live Opportunities to capitalize while the wave is fresh.",
    };
  }
  if (total < 30) {
    return {
      headline: "Mostly quiet",
      label: `${total} events — light pattern`,
      description:
        "Low event count for an anomaly window — could be a transient blip rather than a real pattern. Cross-check Anomaly Fusion to see if the same window registered on multiple signal classes; if not, this is probably noise.",
    };
  }
  if (truncated) {
    return {
      headline: "More to see",
      label: "Window truncated at 200 events",
      description:
        "There's more here than the 200-event cap can show. Narrow the window (try 30m) to see the densest minutes, or filter to one source via the by-source breakdown to drill into a specific channel.",
    };
  }
  return {
    headline: "Mixed activity",
    label: `${total} events distributed across sources and devices`,
    description:
      "Healthy variety — no single source/device dominates, no obvious anomaly driver. The pattern fired but the window doesn't show a smoking gun. Check Anomaly Fusion for the cross-signal correlation that triggered it.",
  };
}

function computeSupportingActions(
  total: number,
  truncated: boolean,
): SupportingAction[] {
  const out: SupportingAction[] = [];
  if (total > 0) {
    out.push({
      label: "Cross-check Anomaly Fusion",
      description:
        "Anomaly Fusion shows which signals correlated to fire this pattern in the first place. The replay shows WHAT happened; Fusion shows WHY the system flagged it.",
    });
  }
  if (truncated) {
    out.push({
      label: "Narrow the window",
      description:
        "Switch to 30m to see the densest minutes uncapped. The 200-event cap holds; narrower windows expose the structure better.",
    });
  } else {
    out.push({
      label: "Widen if pattern reaches further back",
      description:
        "If the spike's tail extends past the current window, try 4h to see the full ramp-up or wind-down — most patterns have shape, not point events.",
    });
  }
  out.push({
    label: "Tag the cause in Trust Center",
    description:
      "If you confirm the cause (campaign launch, price test, bot wave), tag it in Trust Center — future similar patterns inherit the tag and the system narrows down faster next time.",
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

function BreakdownColumn({
  label,
  rows,
}: {
  label: string;
  rows: Array<{ source?: string; device?: string; type?: string; count: number }>;
}) {
  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0b0b14]/60 p-4">
      <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
        {label}
      </div>
      <ul className="space-y-1.5">
        {rows.slice(0, 5).map((r, i) => {
          const k = r.source ?? r.device ?? r.type ?? `row-${i}`;
          return (
            <li key={k} className="flex items-center justify-between gap-2 text-[12px]">
              <span className="truncate text-slate-300">{k}</span>
              <span className="flex-shrink-0 tabular-nums text-slate-400">{r.count}</span>
            </li>
          );
        })}
        {rows.length === 0 && <li className="text-[11px] text-slate-400">No data</li>}
      </ul>
    </div>
  );
}

function EmptyPreview({
  accentHero,
  minutes,
}: {
  accentHero: string;
  minutes: number;
}) {
  return (
    <div className="mb-6 rounded-xl border border-dashed border-white/[0.12] bg-[#0b0b14]/40 p-5 sm:p-6">
      <div className="mb-3 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
        <span
          className="inline-block h-1.5 w-1.5 animate-pulse rounded-full"
          style={{ background: accentHero }}
          aria-hidden="true"
        />
        Window empty
      </div>
      <p className="mb-4 text-[13px] leading-relaxed text-slate-400">
        No events in the last {minutes < 60 ? `${minutes} minutes` : `${minutes / 60} hours`}.
        Either the anomaly fired earlier than the window — try expanding to 2h
        or 4h above — or the tracker is paused. Replay is a real reconstruction;
        an empty result means the event store has nothing for this range.
      </p>
      <div className="flex items-center gap-2 rounded-lg border border-emerald-400/15 bg-emerald-500/[0.05] px-3 py-2 text-[11.5px] font-semibold text-emerald-300">
        <span
          className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400"
          aria-hidden="true"
        />
        Tracker active — next anomaly will populate this view automatically.
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
