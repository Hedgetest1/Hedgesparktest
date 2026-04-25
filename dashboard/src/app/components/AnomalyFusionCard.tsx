"use client";

/**
 * AnomalyFusionCard — Phase Ω killer.
 *
 * Surfaces fused cross-signal anomalies. Each alert is a *pattern*
 * (e.g. demand_softening, paid_efficiency_collapse) computed from
 * multiple weak signals correlating in the same window — the kind
 * of thing single-metric dashboards miss until it's too late.
 *
 * This card keeps a manual state machine because it subscribes to
 * /pro/stream/dashboard via SSE for live updates. The shared
 * useCardFetch hook is pull-only and doesn't know about SSE pushes,
 * so we use the inline CardSkeleton / CardError / CardEmpty
 * primitives directly instead.
 *
 * Source: GET /pro/anomalies/fusion  (initial REST pull)
 *         GET /pro/stream/dashboard  (SSE push updates)
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { CardSkeleton, CardError, CardEmpty, type CardFetchState } from "./_CardStates";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
  DrawerHowCalculated,
} from "./DetailDrawer";

type Contributor = { name: string; severity: number; delta_pct: number };

type FusionAlert = {
  pattern: string;
  fusion_score: number;
  severity: "info" | "warning" | "critical";
  contributors: Contributor[];
  window_hours: number;
  recommended_action: string;
  narrative: string;
};

type FusionResponse = {
  shop_domain: string;
  alerts: FusionAlert[];
  atomic_signals: Contributor[];
};

function labelize(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

const SEV_COLOR: Record<string, string> = {
  critical: "#f87171",
  warning: "#fbbf24",
  info: "#94a3b8",
};

const SEV_LABEL: Record<string, string> = {
  critical: "Critical",
  warning: "Warning",
  info: "Info",
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
  const [state, setState] = useState<CardFetchState>("loading");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) {
      setState("empty");
      return;
    }
    let active = true;
    setState("loading");

    const pullFusion = async () => {
      try {
        const { data: j, error } = await apiClient.GET("/pro/anomalies/fusion");
        if (error || !j) throw new Error("fetch failed");
        if (!active) return;
        const fusion = j as unknown as FusionResponse;
        setData(fusion);
        setState(fusion.alerts.length === 0 ? "empty" : "ready");
      } catch {
        if (active) setState("error");
      }
    };

    void pullFusion();

    // Phase Ω''' — Server-Sent Events live updates.
    // EventSource auto-reconnects on disconnect. withCredentials sends
    // the session cookie so the backend can resolve the shop.
    let es: EventSource | null = null;
    try {
      es = new EventSource(`${apiBase}/pro/stream/dashboard`, {
        withCredentials: true,
      });
      es.addEventListener("snapshot", (ev: MessageEvent) => {
        if (!active) return;
        try {
          const snap = JSON.parse(ev.data);
          if (snap?.fusion?.alert_count !== undefined) {
            const incoming = snap.fusion.alert_count;
            const current = data?.alerts?.length ?? -1;
            if (incoming !== current) {
              void pullFusion();
            }
          }
        } catch {
          // swallow parse errors — the next heartbeat will bring us a clean snapshot
        }
      });
      es.onerror = () => {
        /* EventSource handles reconnect */
      };
    } catch {
      // EventSource may not exist (older browsers) — initial REST pull still works
    }

    return () => {
      active = false;
      try {
        es?.close();
      } catch {
        /* best-effort close */
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, shop, isProUser, refreshToken]);

  const retry = () => setRefreshToken((n) => n + 1);

  if (!isProUser) return null;

  if (state === "loading") {
    return <CardSkeleton label="Loading anomaly radar" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Anomaly radar unavailable"
        message="We couldn't load this week's cross-signal alerts. The underlying signals are still being collected — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data || data.alerts.length === 0) {
    return (
      <CardEmpty
        accent="emerald"
        title="No correlated anomalies right now"
        body="We monitor 5 independent signals across revenue, refunds, ad efficiency, retention, and system health — and only alert when they correlate. Right now: all clear."
      />
    );
  }

  const topAlert = data.alerts[0];
  const topColor = SEV_COLOR[topAlert.severity] || SEV_COLOR.info;
  const criticalCount = data.alerts.filter((a) => a.severity === "critical").length;
  const warningCount = data.alerts.filter((a) => a.severity === "warning").length;

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open anomaly radar details — ${data.alerts.length} active patterns, top severity ${topAlert.severity}`}
        onClick={() => setDrawerOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setDrawerOpen(true);
          }
        }}
        className="group cursor-pointer rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6 transition-shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-[#e8a04e] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220] hover:border-white/[0.12]"
      >
        <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-[#e8a04e]">
          Anomaly radar
        </div>
        <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
          Cross-signal fusion alerts
        </h3>
        <p className="mt-2 text-[14px] leading-relaxed text-slate-400">
          {data.alerts.length} active pattern{data.alerts.length === 1 ? "" : "s"} detected across{" "}
          {data.atomic_signals.length} raw signal{data.atomic_signals.length === 1 ? "" : "s"}.
          Single-metric dashboards miss these until it&apos;s too late.
        </p>

        <ul className="mt-5 space-y-3" role="list">
          {data.alerts.map((a) => {
            const color = SEV_COLOR[a.severity] || SEV_COLOR.info;
            return (
              <li
                key={a.pattern}
                className="rounded-xl border p-4"
                style={{ borderColor: color + "35", background: color + "08" }}
              >
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span
                      className="rounded-md px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide"
                      style={{
                        color,
                        background: color + "15",
                        border: `1px solid ${color}40`,
                      }}
                    >
                      {labelize(a.pattern)}
                    </span>
                    <span
                      className="text-[10px] uppercase tracking-wider text-slate-400"
                      style={{ color }}
                    >
                      {SEV_LABEL[a.severity]}
                    </span>
                  </div>
                  <span
                    className="text-[14px] font-extrabold tabular-nums"
                    style={{ color }}
                  >
                    {Math.round(a.fusion_score)}/100
                  </span>
                </div>
                <p className="text-[13px] leading-relaxed text-slate-200">{a.narrative}</p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {a.contributors.map((contrib, i) => (
                    <span
                      key={i}
                      className="rounded bg-white/[0.04] px-2 py-0.5 text-[10px] tabular-nums text-slate-400"
                    >
                      {labelize(contrib.name)} {contrib.delta_pct >= 0 ? "+" : ""}
                      {contrib.delta_pct.toFixed(0)}%
                    </span>
                  ))}
                </div>
                {a.recommended_action && (
                  <div className="mt-3 border-t border-white/[0.05] pt-2 text-[12px] font-medium text-emerald-300/80">
                    → {a.recommended_action}
                  </div>
                )}
              </li>
            );
          })}
        </ul>

        <div className="mt-4 text-[11px] font-semibold text-slate-400">
          Click for the full pattern breakdown and method →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="📡"
        title="Cross-signal anomaly radar"
        subtitle={`${data.alerts.length} fused pattern${data.alerts.length === 1 ? "" : "s"} · ${data.atomic_signals.length} raw signals`}
      >
        <DrawerExplainer
          body={
            "Most dashboards alert you when one metric misbehaves. The radar watches five independent " +
            "signals — revenue, refunds, ad efficiency, retention, system health — and only alerts " +
            "when several of them drift in the same window. A single signal can be noise. Several " +
            "moving together is almost always a real pattern."
          }
          why={
            "By the time a single-metric alert fires, the damage is usually done. Correlated weak " +
            "signals show up days earlier and give you time to react before the headline number " +
            "moves. Think of it as an early-warning radar rather than a rear-view mirror."
          }
        />

        <DrawerBigStat
          label="Top pattern severity"
          value={SEV_LABEL[topAlert.severity]}
          sublabel={`${labelize(topAlert.pattern)} · fusion score ${Math.round(
            topAlert.fusion_score,
          )}/100`}
          color={topColor}
        />

        <DrawerKeyValueList
          items={[
            {
              label: "Active patterns",
              value: `${data.alerts.length}`,
            },
            {
              label: "Critical",
              value: `${criticalCount}`,
              color: criticalCount > 0 ? "#f87171" : "#94a3b8",
            },
            {
              label: "Warning",
              value: `${warningCount}`,
              color: warningCount > 0 ? "#fbbf24" : "#94a3b8",
            },
            {
              label: "Raw signals monitored",
              value: `${data.atomic_signals.length}`,
            },
          ]}
        />

        <DrawerSectionHeading>All active patterns</DrawerSectionHeading>
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          {data.alerts.map((a) => {
            const color = SEV_COLOR[a.severity] || SEV_COLOR.info;
            return (
              <div
                key={a.pattern}
                style={{
                  padding: "14px 16px",
                  borderRadius: "12px",
                  background: color + "0d",
                  border: `1px solid ${color}35`,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "8px",
                  }}
                >
                  <div style={{ color, fontWeight: 700, fontSize: "13px" }}>
                    {labelize(a.pattern)}
                  </div>
                  <div
                    style={{
                      color,
                      fontWeight: 800,
                      fontSize: "14px",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {Math.round(a.fusion_score)}/100
                  </div>
                </div>
                <p
                  style={{
                    color: "#e2e8f0",
                    fontSize: "13px",
                    lineHeight: 1.55,
                    margin: 0,
                  }}
                >
                  {a.narrative}
                </p>
                <div
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: "6px",
                    marginTop: "10px",
                  }}
                >
                  {a.contributors.map((contrib, i) => (
                    <span
                      key={i}
                      style={{
                        padding: "3px 8px",
                        borderRadius: "6px",
                        background: "rgba(148,163,184,0.08)",
                        color: "#94a3b8",
                        fontSize: "11px",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {labelize(contrib.name)} {contrib.delta_pct >= 0 ? "+" : ""}
                      {contrib.delta_pct.toFixed(0)}%
                    </span>
                  ))}
                </div>
                {a.recommended_action && (
                  <div
                    style={{
                      marginTop: "12px",
                      paddingTop: "10px",
                      borderTop: "1px solid rgba(148,163,184,0.1)",
                      color: "#6ee7b7",
                      fontSize: "12px",
                      fontWeight: 600,
                    }}
                  >
                    → {a.recommended_action}
                  </div>
                )}
                <div
                  style={{
                    marginTop: "8px",
                    color: "#94a3b8",
                    fontSize: "10px",
                  }}
                >
                  Window: last {a.window_hours} hours
                </div>
              </div>
            );
          })}
        </div>

        <DrawerHowCalculated
          formula="Each raw signal is normalized into a 0–100 severity score. A pattern fires when two or more signals exceed their individual thresholds inside the same time window AND the pattern-specific correlation rule is satisfied. Fusion score is the combined confidence of the contributors, not a simple average."
          inputs={[
            { label: "Raw signals monitored", value: `${data.atomic_signals.length}` },
            { label: "Patterns tracked", value: "5 core fusion patterns" },
            { label: "Window", value: `${topAlert.window_hours} hours` },
          ]}
          note="Single-signal alerts are easy to build but noisy — they fire on every legitimate fluctuation. Fusion alerts are intentionally slower to fire, which means fewer false alarms and more trust. If the radar says something, it's almost certainly worth looking at."
        />
      </DetailDrawer>
    </>
  );
}
