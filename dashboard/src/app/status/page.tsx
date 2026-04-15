"use client";

/**
 * /status — public operational status page.
 *
 * Pulls /public/status (cache-friendly, no auth) and renders a
 * compact status board: overall light + per-component dots + last
 * 7d critical incidents.
 */

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "https://api.hedgesparkhq.com";
const POLL_MS = 30_000;

type Component = {
  name: string;
  status: "operational" | "degraded" | "outage" | "unknown";
  latency_ms?: number | null;
  stale_count?: number;
  total_count?: number;
  critical_24h?: number;
};

type Incident = {
  at: string | null;
  component: string;
  summary: string;
};

type StatusResponse = {
  overall: "operational" | "degraded" | "outage";
  components: Component[];
  incidents: Incident[];
  checked_at: string;
};

const STATUS_COLOR: Record<string, string> = {
  operational: "#34d399",
  degraded: "#fbbf24",
  outage: "#f87171",
  unknown: "#94a3b8",
};

const STATUS_LABEL: Record<string, string> = {
  operational: "All systems operational",
  degraded: "Partial degradation",
  outage: "Active outage",
  unknown: "Status unknown",
};

function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function StatusPage() {
  const [data, setData] = useState<StatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await fetch(`${API_BASE}/public/status`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j: StatusResponse = await r.json();
      setData(j);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, []);

  const overall = data?.overall || "unknown";
  const overallColor = STATUS_COLOR[overall];
  const overallLabel = STATUS_LABEL[overall];

  return (
    <main className="min-h-screen bg-[#0a0a0c] px-6 py-16 text-slate-100">
      <div className="mx-auto max-w-3xl">
        {/* Header */}
        <div className="mb-10">
          <a
            href="/"
            className="text-[12px] font-semibold uppercase tracking-[0.18em] text-[#e8a04e] hover:text-[#f0b56b]"
          >
            Hedge Spark
          </a>
          <h1 className="mt-2 text-[28px] font-extrabold tracking-tight text-white sm:text-[34px]">
            System Status
          </h1>
          <p className="mt-1 text-[14px] text-slate-400">
            Real-time operational health of all Hedge Spark services. Updated every 30 seconds.
          </p>
        </div>

        {/* Overall banner */}
        <div
          className="mb-8 rounded-2xl border p-6"
          style={{ borderColor: overallColor + "40", background: overallColor + "0c" }}
        >
          <div className="flex items-center gap-4">
            <div
              className="h-4 w-4 flex-shrink-0 rounded-full"
              style={{ background: overallColor, boxShadow: `0 0 12px ${overallColor}80` }}
            />
            <div className="flex-1">
              <div className="text-[20px] font-extrabold text-white">{overallLabel}</div>
              <div className="mt-0.5 text-[11px] text-slate-400">
                last checked {relativeTime(data?.checked_at || null)}
              </div>
            </div>
          </div>
        </div>

        {/* Components */}
        <section className="mb-10">
          <h2 className="mb-3 text-[12px] font-bold uppercase tracking-[0.18em] text-[#e8a04e]">
            Components
          </h2>
          {loading && (
            <div className="space-y-2">
              {[0, 1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="h-14 animate-pulse rounded-xl border border-white/[0.06] bg-white/[0.02]"
                />
              ))}
            </div>
          )}
          {error && (
            <div className="rounded-xl border border-rose-400/20 bg-rose-500/[0.05] p-4 text-[13px] text-rose-300">
              {error}
            </div>
          )}
          {data && (
            <div className="space-y-2">
              {data.components.map((c) => {
                const color = STATUS_COLOR[c.status];
                return (
                  <div
                    key={c.name}
                    className="flex items-center justify-between rounded-xl border border-white/[0.07] bg-white/[0.02] px-4 py-3.5"
                  >
                    <div className="flex items-center gap-3">
                      <div
                        className="h-2.5 w-2.5 flex-shrink-0 rounded-full"
                        style={{ background: color, boxShadow: `0 0 6px ${color}80` }}
                      />
                      <div>
                        <div className="text-[14px] font-semibold text-slate-200">{c.name}</div>
                        {c.latency_ms != null && (
                          <div className="text-[11px] text-slate-400">{c.latency_ms} ms response</div>
                        )}
                        {c.stale_count != null && c.total_count != null && (
                          <div className="text-[11px] text-slate-400">
                            {c.total_count - c.stale_count}/{c.total_count} workers fresh
                          </div>
                        )}
                        {c.critical_24h != null && (
                          <div className="text-[11px] text-slate-400">
                            {c.critical_24h} critical alert{c.critical_24h === 1 ? "" : "s"} (24h)
                          </div>
                        )}
                      </div>
                    </div>
                    <span
                      className="rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide tabular-nums"
                      style={{ color, background: color + "15", border: `1px solid ${color}30` }}
                    >
                      {c.status}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* Incidents */}
        <section>
          <h2 className="mb-3 text-[12px] font-bold uppercase tracking-[0.18em] text-[#e8a04e]">
            Recent Incidents (7 days)
          </h2>
          {data && data.incidents.length === 0 ? (
            <div className="rounded-xl border border-emerald-400/15 bg-emerald-500/[0.04] p-4 text-[13px] text-emerald-200/90">
              No critical incidents in the last 7 days. ✨
            </div>
          ) : (
            <div className="space-y-2">
              {data?.incidents.map((i, idx) => (
                <div
                  key={idx}
                  className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4"
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[12px] font-semibold uppercase tracking-wide text-rose-300">
                      {i.component}
                    </span>
                    <span className="text-[11px] text-slate-400">{relativeTime(i.at)}</span>
                  </div>
                  <p className="mt-1.5 text-[13px] text-slate-300">{i.summary}</p>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Footer */}
        <footer className="mt-16 text-center text-[11px] text-slate-400">
          Powered by the Hedge Spark self-healing pipeline.
        </footer>
      </div>
    </main>
  );
}
