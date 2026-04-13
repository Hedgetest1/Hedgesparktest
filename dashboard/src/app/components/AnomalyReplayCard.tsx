"use client";

/**
 * AnomalyReplayCard — Phase Ω⁷ killer #1.
 *
 * "Watch what actually happened."
 *
 * When an anomaly is detected, the merchant clicks Replay and gets a
 * minute-by-minute reconstruction of the event window: visitor sessions,
 * source mix, device mix, URL stream. No other SMB Shopify tool ships
 * this because nobody else has our event-level granularity.
 *
 * Source: GET /pro/anomalies/{pattern}/replay?minutes=60
 */

import { useEffect, useState } from "react";

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

function fmtTime(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
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
  const [minutes, setMinutes] = useState(60);

  const load = async (mins: number) => {
    setLoading(true);
    try {
      const r = await fetch(
        `${apiBase}/pro/anomalies/${encodeURIComponent(pattern)}/replay?minutes=${mins}`,
        { credentials: "include" },
      );
      if (r.ok) setData(await r.json());
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isProUser && apiBase) void load(minutes);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, isProUser, minutes, pattern]);

  if (!isProUser) return null;

  const maxCount = data?.timeline.reduce((m, t) => Math.max(m, t.count), 0) || 1;

  return (
    <section
      className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5"
      aria-labelledby="anomaly-replay-heading"
      role="region"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
            Anomaly Replay
          </div>
          <h3 id="anomaly-replay-heading" className="text-[15px] font-bold text-white">
            Watch what actually happened
          </h3>
          <p className="mt-1 text-[11px] text-slate-500">
            The event window around the most recent detected pattern — every visitor, every source.
          </p>
        </div>
        <div className="flex flex-shrink-0 items-center gap-1 rounded-lg border border-white/10 bg-white/[0.02] p-1">
          {[30, 60, 120, 240].map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMinutes(m)}
              className={`rounded-md px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide transition-colors ${
                minutes === m
                  ? "bg-amber-500/15 text-amber-300"
                  : "text-slate-500 hover:text-slate-300"
              }`}
            >
              {m < 60 ? `${m}m` : `${m / 60}h`}
            </button>
          ))}
        </div>
      </div>

      {loading && !data ? (
        <div className="h-20 animate-pulse rounded-xl bg-white/[0.03]" />
      ) : !data || data.summary.total_events === 0 ? (
        <div className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-6 text-center text-[12px] text-slate-500">
          No events captured in the selected window.
        </div>
      ) : (
        <>
          {/* Narrative */}
          <p className="mb-4 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3 text-[12px] leading-relaxed text-slate-300">
            {data.narrative}
          </p>

          {/* Minute-by-minute timeline — sparkbar */}
          <div className="mb-4">
            <div className="mb-1.5 flex items-center justify-between text-[10px] uppercase tracking-wide text-slate-500">
              <span>Minute-by-minute</span>
              <span>{data.summary.total_events} events · {data.summary.unique_visitors} visitors</span>
            </div>
            <div className="flex h-12 items-end gap-px rounded-lg bg-white/[0.015] p-1">
              {data.timeline.map((t, i) => {
                const h = Math.max(2, Math.round((t.count / maxCount) * 40));
                const color = t.count === 0
                  ? "bg-white/[0.03]"
                  : t.count >= maxCount * 0.7
                  ? "bg-rose-400/70"
                  : t.count >= maxCount * 0.3
                  ? "bg-amber-400/70"
                  : "bg-emerald-400/60";
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
            <div className="mt-1 flex items-center justify-between text-[9px] text-slate-600">
              <span>{fmtTime(data.window.start_ms)}</span>
              <span>now</span>
            </div>
          </div>

          {/* 3-column summary */}
          <div className="grid grid-cols-3 gap-2">
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
              <div className="text-[9px] font-semibold uppercase tracking-[0.12em] text-slate-600">By source</div>
              <ul className="mt-1 space-y-0.5">
                {data.summary.by_source.slice(0, 4).map((s) => (
                  <li key={s.source} className="flex justify-between text-[11px]">
                    <span className="truncate text-slate-400">{s.source}</span>
                    <span className="tabular-nums text-slate-300">{s.count}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
              <div className="text-[9px] font-semibold uppercase tracking-[0.12em] text-slate-600">By device</div>
              <ul className="mt-1 space-y-0.5">
                {data.summary.by_device.slice(0, 4).map((d) => (
                  <li key={d.device} className="flex justify-between text-[11px]">
                    <span className="truncate text-slate-400">{d.device}</span>
                    <span className="tabular-nums text-slate-300">{d.count}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
              <div className="text-[9px] font-semibold uppercase tracking-[0.12em] text-slate-600">By event type</div>
              <ul className="mt-1 space-y-0.5">
                {data.summary.by_type.slice(0, 4).map((t) => (
                  <li key={t.type} className="flex justify-between text-[11px]">
                    <span className="truncate text-slate-400">{t.type}</span>
                    <span className="tabular-nums text-slate-300">{t.count}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          {data.truncated && (
            <p className="mt-3 text-[10px] text-slate-600">
              Showing first 200 events out of a capped window — expand the range for more context.
            </p>
          )}
        </>
      )}
    </section>
  );
}
