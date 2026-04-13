"use client";

/**
 * AnomalyFusionCard — Phase Ω killer.
 *
 * Surfaces fused cross-signal anomalies. Each alert is a *pattern*
 * (e.g. demand_softening, paid_efficiency_collapse) computed from
 * multiple weak signals correlating in the same window — the kind
 * of thing single-metric dashboards miss until it's too late.
 *
 * Source: GET /pro/anomalies/fusion
 */

import { useEffect, useState } from "react";

type FusionAlert = {
  pattern: string;
  fusion_score: number;
  severity: "info" | "warning" | "critical";
  contributors: Array<{ name: string; severity: number; delta_pct: number }>;
  window_hours: number;
  recommended_action: string;
  narrative: string;
};

type FusionResponse = {
  shop_domain: string;
  alerts: FusionAlert[];
  atomic_signals: Array<{ name: string; severity: number; delta_pct: number }>;
};

function labelize(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

const SEV_COLOR: Record<string, string> = {
  critical: "#f87171",
  warning: "#fbbf24",
  info: "#94a3b8",
};

export function AnomalyFusionCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [data, setData] = useState<FusionResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) { setLoading(false); return; }
    let active = true;
    setLoading(true);

    // Initial REST fetch — SSE will deliver subsequent updates
    fetch(`${apiBase}/pro/anomalies/fusion`, {
      credentials: "include",
      headers: { "Content-Type": "application/json" },
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((j: FusionResponse) => { if (active) setData(j); })
      .catch(() => { if (active) setData(null); })
      .finally(() => { if (active) setLoading(false); });

    // Phase Ω''' — Server-Sent Events live updates.
    // EventSource auto-reconnects on disconnect. withCredentials sends
    // the session cookie so the backend can resolve the shop.
    let es: EventSource | null = null;
    try {
      es = new EventSource(`${apiBase}/pro/stream/dashboard`, { withCredentials: true });
      es.addEventListener("snapshot", (ev: MessageEvent) => {
        if (!active) return;
        try {
          const snap = JSON.parse(ev.data);
          // Re-pull full fusion only when the alert count actually changes.
          // Heartbeats and stable snapshots avoid extra REST calls.
          if (snap?.fusion?.alert_count !== undefined) {
            const incoming = snap.fusion.alert_count;
            const current = data?.alerts?.length ?? -1;
            if (incoming !== current) {
              fetch(`${apiBase}/pro/anomalies/fusion`, {
                credentials: "include",
                headers: { "Content-Type": "application/json" },
              })
                .then((r) => (r.ok ? r.json() : null))
                .then((j: FusionResponse | null) => { if (active && j) setData(j); })
                .catch(() => {});
            }
          }
        } catch {}
      });
      es.onerror = () => { /* EventSource handles reconnect */ };
    } catch {}

    return () => {
      active = false;
      try { es?.close(); } catch {}
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, shop, isProUser]);

  if (!isProUser) return null;

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
        <div className="h-3 w-40 rounded bg-white/[0.06]" />
        <div className="mt-3 space-y-2">
          {[0, 1].map((i) => (<div key={i} className="h-14 rounded bg-white/[0.04]" />))}
        </div>
      </div>
    );
  }

  if (!data || data.alerts.length === 0) {
    return (
      <div className="rounded-2xl border border-emerald-400/15 bg-emerald-500/[0.04] p-5">
        <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-400">
          Anomaly Radar
        </div>
        <h3 className="text-[15px] font-bold text-white">No correlated anomalies right now.</h3>
        <p className="mt-2 text-[12px] leading-relaxed text-emerald-200/80">
          We monitor 5 independent signals across revenue, refunds, ad efficiency, retention,
          and system health — and only alert when they correlate. Right now: all clear.
        </p>
      </div>
    );
  }

  return (
    <section
      className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5"
      aria-labelledby="anomaly-radar-heading"
      role="region"
    >
      <div className="mb-3">
        <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]" aria-hidden="true">
          Anomaly Radar
        </div>
        <h3 id="anomaly-radar-heading" className="text-[15px] font-bold text-white">
          Cross-signal fusion alerts
        </h3>
        <p className="mt-1 text-[11px] text-slate-500">
          {data.alerts.length} active pattern{data.alerts.length === 1 ? "" : "s"} ·
          {" "}{data.atomic_signals.length} raw signal{data.atomic_signals.length === 1 ? "" : "s"}
        </p>
      </div>

      <ul className="space-y-2.5" role="list">
        {data.alerts.map((a) => {
          const color = SEV_COLOR[a.severity] || SEV_COLOR.info;
          return (
            <li
              key={a.pattern}
              className="rounded-xl border p-3.5"
              style={{ borderColor: color + "30", background: color + "08" }}
              aria-label={`${a.severity} severity alert: ${a.pattern}`}
            >
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span
                    className="rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide"
                    style={{ color, background: color + "15", border: `1px solid ${color}30` }}
                  >
                    {labelize(a.pattern)}
                  </span>
                  <span className="text-[10px] uppercase tracking-wide text-slate-500">
                    {a.severity}
                  </span>
                </div>
                <span className="text-[11px] tabular-nums font-bold" style={{ color }}>
                  {Math.round(a.fusion_score)}/100
                </span>
              </div>
              <p className="text-[13px] leading-relaxed text-slate-200">{a.narrative}</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {a.contributors.map((c, i) => (
                  <span
                    key={i}
                    className="rounded bg-white/[0.04] px-1.5 py-0.5 text-[10px] tabular-nums text-slate-400"
                  >
                    {labelize(c.name)} {c.delta_pct >= 0 ? "+" : ""}{c.delta_pct.toFixed(0)}%
                  </span>
                ))}
              </div>
              {a.recommended_action && (
                <div className="mt-2.5 border-t border-white/[0.05] pt-2 text-[12px] text-emerald-300/80">
                  → {a.recommended_action}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
