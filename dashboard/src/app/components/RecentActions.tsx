"use client";

import { type RecentAction } from "./TopSignalCard";

function timeAgo(ts: number): string {
  const diff = Math.floor((Date.now() - ts) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  const days = Math.floor(diff / 86400);
  return `${days}d ago`;
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

type Props = {
  actions: RecentAction[];
};

export function RecentActions({ actions }: Props) {
  if (actions.length === 0) return null;

  const display = actions.slice(0, 3);

  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] px-5 py-4">
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">
        Recent actions
      </div>
      <div className="space-y-2">
        {display.map((a, i) => (
          <div key={`${a.productUrl}-${i}`} className="flex items-center gap-3">
            <svg className="h-3.5 w-3.5 flex-shrink-0 text-emerald-400/60" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
            </svg>
            <div className="min-w-0 flex-1">
              <span className="text-[12px] font-medium text-slate-300">
                {a.product}
              </span>
              <span className="ml-2 text-[11px] text-slate-400">
                {truncate(a.action, 50)}
              </span>
            </div>
            <span className="flex-shrink-0 text-[10px] tabular-nums text-slate-400">
              {timeAgo(a.timestamp)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
