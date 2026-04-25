"use client";

/**
 * SessionsSection — Pro session replay + click insights.
 * Extracted from app/page.tsx PageInner (Phase Ω⁷ split).
 */

import { SectionHeading } from "../_components/SectionHeading";
import { SectionErrorBoundary } from "../../components/SectionErrorBoundary";

/* eslint-disable @typescript-eslint/no-explicit-any */
export interface SessionsSectionProps {
  isProUser: boolean;
  sessions: any[];
  clicks: any[];
  formatDuration: (seconds: number) => string;
  shortUrl: (url: string) => string;
}

export function SessionsSection(p: SessionsSectionProps) {
  const { isProUser, sessions, clicks, formatDuration, shortUrl } = p;
  if (!isProUser) return <section id="section-sessions" />;

  return (
    <section id="section-sessions">
      <div className="grid gap-4 xl:grid-cols-2">

        {/* Left — Session Replay */}
        <SectionErrorBoundary name="Recent Visitor Journeys">
        <div>
          <SectionHeading eyebrow="Sessions" title="Recent visitor journeys" />
          {sessions.length === 0 ? (
            <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-400">
              No session data yet.
            </p>
          ) : (
            <div className="overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.02]">
              <table className="min-w-full text-left text-[13px]">
                <thead>
                  <tr className="border-b border-white/[0.06] text-[11px] uppercase tracking-wide text-slate-400">
                    <th className="px-4 py-2.5 font-medium">Visitor</th>
                    <th className="px-4 py-2.5 font-medium">Pages</th>
                    <th className="px-4 py-2.5 font-medium">Duration</th>
                    <th className="px-4 py-2.5 font-medium">Last Page</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((s: any, i: number) => (
                    <tr
                      key={`sess-${s.visitor_id}-${i}`}
                      className="border-t border-white/[0.04] transition-colors hover:bg-white/[0.02]"
                    >
                      <td className="px-4 py-2.5">
                        <span className="font-mono text-[11px] text-slate-400">
                          {s.visitor_id.slice(0, 8)}
                        </span>
                      </td>
                      <td className="px-4 py-2.5">
                        <span className="tabular-nums text-slate-300">
                          {s.pages_visited.length}
                        </span>
                        <span className="ml-1 text-[10px] text-slate-400">pg</span>
                      </td>
                      <td className="px-4 py-2.5 tabular-nums text-slate-400">
                        {formatDuration(s.total_duration_seconds)}
                      </td>
                      <td className="max-w-[160px] px-4 py-2.5">
                        <span
                          className="block truncate text-[11px] text-slate-400"
                          title={s.last_page || "—"}
                        >
                          {s.last_page ? shortUrl(s.last_page) : "—"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        </SectionErrorBoundary>

        {/* Right — Click Insights */}
        <SectionErrorBoundary name="Click Insights">
        <div>
          <SectionHeading eyebrow="Clicks" title="What visitors click" />
          {clicks.length === 0 ? (
            <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-400">
              No click data yet — track click events to see this.
            </p>
          ) : (
            <div className="space-y-1.5">
              {clicks.map((row: any, i: number) => {
                const maxClicks = clicks[0]?.clicks || 1;
                const barWidth = Math.round((row.clicks / maxClicks) * 100);
                return (
                  <div
                    key={`click-${i}`}
                    className="flex items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2"
                  >
                    <span className="w-4 flex-shrink-0 text-center text-[11px] tabular-nums text-slate-700">
                      {i + 1}
                    </span>
                    <span
                      className="min-w-0 flex-1 truncate text-[12px] text-slate-300"
                      title={row.url}
                    >
                      {shortUrl(row.url)}
                    </span>
                    <div className="w-14 flex-shrink-0">
                      <div className="h-1 w-full overflow-hidden rounded-full bg-white/[0.07]">
                        <div
                          className="h-full rounded-full bg-cyan-400/50"
                          style={{ width: `${barWidth}%` }}
                        />
                      </div>
                    </div>
                    <span className="w-8 flex-shrink-0 text-right text-[11px] tabular-nums text-slate-400">
                      {row.clicks}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
        </SectionErrorBoundary>

      </div>
    </section>
  );
}
