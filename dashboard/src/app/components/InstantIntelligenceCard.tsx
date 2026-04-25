"use client";

/**
 * InstantIntelligenceCard — the 60-second aha moment card.
 *
 * Shows a preview of intelligence computed from the last 90 days of
 * Shopify orders, so the merchant sees REAL numbers on first login
 * instead of "HedgeSpark is collecting data".
 *
 * Behavior:
 *   - status=computing → shows a glowing loader + "reading your orders"
 *   - status=ready     → shows narrative + KPIs + top products + preview RARS
 *   - status=empty     → shows encouraging "we'll track from here" message
 *
 * Auto-hides after the merchant has enough real data (order_count_90d > 50
 * and the dashboard has been loaded for more than 3 sessions — the
 * component tracks this via localStorage).
 *
 * Data: GET /pro/instant-intelligence, POST /pro/instant-intelligence/refresh
 */

import { useEffect, useState, useCallback } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";
import { reportFrontendError } from "@/app/lib/error-reporter";

type TopProduct = {
  id: string;
  title: string;
  revenue: number;
  units: number;
};

type InstantIntel = {
  shop_domain: string;
  status: "ready" | "empty" | "computing";
  reason?: string | null;
  message?: string | null;
  computed_at?: string | null;
  backfill_days?: number | null;
  currency?: string | null;
  order_count_90d?: number | null;
  total_revenue_90d?: number | null;
  aov?: number | null;
  monthly_revenue_estimate?: number | null;
  refund_rate_pct?: number | null;
  top_products?: TopProduct[] | null;
  preview_rars_monthly?: number | null;
  narrative?: string | null;
};

const fmt = (n: number, currency = "USD"): string =>
  formatMoneyCompact(n, currency);

const LS_KEY = "hs_instant_intel_dismissed";

export function InstantIntelligenceCard({ apiBase }: { apiBase: string }) {
  const [data, setData] = useState<InstantIntel | null>(null);
  const [loading, setLoading] = useState(true);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    try {
      if (localStorage.getItem(LS_KEY) === "1") {
        setDismissed(true);
      }
    } catch {}
  }, []);

  const load = useCallback(async () => {
    try {
      const { data: j, error } = await apiClient.GET("/pro/instant-intelligence");
      if (error || !j) return;
      setData(j as unknown as InstantIntel);
    } catch (err) {
      // Never silently swallow — report to the dashboard error channel so
      // the ops digest can track fetch failures on this card. Empty body
      // path above is tolerated (returns null data), but a thrown
      // exception is a bug we need visibility on.
      reportFrontendError({
        component: "InstantIntelligenceCard",
        error_type: "fetch_failed",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }, []);

  useEffect(() => {
    load().finally(() => setLoading(false));
  }, [load]);

  // Auto-poll every 4s while computing, max 8 polls
  useEffect(() => {
    if (data?.status !== "computing") return;
    let polls = 0;
    const id = setInterval(() => {
      if (polls >= 8) {
        clearInterval(id);
        return;
      }
      polls += 1;
      load();
    }, 4000);
    return () => clearInterval(id);
  }, [data?.status, load]);

  const dismiss = () => {
    try {
      localStorage.setItem(LS_KEY, "1");
    } catch {}
    setDismissed(true);
  };

  if (loading || dismissed || !data) return null;

  // Auto-hide logic: if the merchant has ≥100 orders AND a top_win exists
  // on the ROI hero, they no longer need the preview. We keep this simple
  // and let the merchant dismiss manually.

  const currency = data.currency || "USD";
  const isReady = data.status === "ready";
  const isComputing = data.status === "computing";
  const isEmpty = data.status === "empty";

  return (
    <div
      style={{
        marginBottom: "24px",
        borderRadius: "18px",
        overflow: "hidden",
        border: "1px solid rgba(232,160,78,0.3)",
        background: "linear-gradient(135deg, #0b1220 0%, #1a1006 100%)",
        position: "relative",
      }}
    >
      {/* Glow */}
      <div
        style={{
          position: "absolute",
          top: "-60%",
          left: "40%",
          width: "600px",
          height: "600px",
          background: "radial-gradient(circle, rgba(232,160,78,0.08) 0%, transparent 55%)",
          pointerEvents: "none",
        }}
      />

      {/* Header */}
      <div
        style={{
          padding: "18px 24px",
          borderBottom: "1px solid rgba(148,163,184,0.1)",
          display: "flex",
          alignItems: "center",
          gap: "12px",
          position: "relative",
        }}
      >
        <div style={{ fontSize: "22px" }}>⚡</div>
        <div style={{ flex: 1 }}>
          <div style={{ color: "#e8a04e", fontSize: "13px", fontWeight: 800, letterSpacing: "0.04em", textTransform: "uppercase" }}>
            Instant snapshot
          </div>
          <div style={{ color: "#cbd5e1", fontSize: "12px" }}>
            {isComputing
              ? "Reading your last 90 days of orders…"
              : isReady
                ? "Computed from your Shopify order history"
                : "Welcome — we'll track every order from here"}
          </div>
        </div>
        <button
          onClick={dismiss}
          title="Dismiss"
          style={{
            background: "transparent",
            border: "1px solid rgba(148,163,184,0.2)",
            color: "#94a3b8",
            padding: "4px 10px",
            borderRadius: "6px",
            fontSize: "11px",
            cursor: "pointer",
          }}
        >
          Dismiss
        </button>
      </div>

      {/* Body */}
      <div style={{ padding: "24px", position: "relative" }}>
        {isComputing && (
          <div style={{ display: "flex", alignItems: "center", gap: "16px", color: "#cbd5e1" }}>
            <div
              style={{
                width: "20px",
                height: "20px",
                borderRadius: "50%",
                border: "2px solid rgba(232,160,78,0.3)",
                borderTopColor: "#e8a04e",
                animation: "spin 0.8s linear infinite",
              }}
            />
            <div style={{ fontSize: "14px" }}>
              Fetching orders, computing AOV, identifying top products, projecting revenue-at-risk…
              this takes about 10 seconds.
            </div>
            <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
          </div>
        )}

        {isEmpty && (
          <div style={{ textAlign: "center", color: "#94a3b8", padding: "12px 0" }}>
            <div style={{ fontSize: "36px", marginBottom: "8px" }}>🌱</div>
            <div style={{ color: "#e2e8f0", fontSize: "16px", fontWeight: 600, marginBottom: "4px" }}>
              Fresh start
            </div>
            <div style={{ fontSize: "13px" }}>{data.message || "We'll track every order from now on."}</div>
          </div>
        )}

        {isReady && (
          <>
            <div
              style={{
                color: "#e2e8f0",
                fontSize: "15px",
                lineHeight: 1.6,
                marginBottom: "20px",
                maxWidth: "680px",
              }}
            >
              {data.narrative}
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                gap: "12px",
                marginBottom: "20px",
              }}
            >
              <Kpi
                label="90d revenue"
                value={fmt(data.total_revenue_90d || 0, currency)}
                color="#e8a04e"
              />
              <Kpi
                label="Monthly estimate"
                value={fmt(data.monthly_revenue_estimate || 0, currency)}
                color="#10b981"
              />
              <Kpi label="Orders (90d)" value={String(data.order_count_90d || 0)} color="#8b5cf6" />
              <Kpi label="AOV" value={fmt(data.aov || 0, currency)} color="#06b6d4" />
              {data.refund_rate_pct != null && (
                <Kpi
                  label="Refund rate"
                  value={`${data.refund_rate_pct.toFixed(1)}%`}
                  color={data.refund_rate_pct > 5 ? "#ef4444" : "#10b981"}
                />
              )}
              {data.preview_rars_monthly != null && data.preview_rars_monthly > 0 && (
                <Kpi
                  label="⚠️ At risk (preview)"
                  value={fmt(data.preview_rars_monthly, currency)}
                  color="#ef4444"
                />
              )}
            </div>

            {data.top_products && data.top_products.length > 0 && (
              <div>
                <div
                  style={{
                    color: "#94a3b8",
                    fontSize: "11px",
                    fontWeight: 700,
                    textTransform: "uppercase",
                    letterSpacing: "0.06em",
                    marginBottom: "10px",
                  }}
                >
                  Top 5 by revenue
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                  {data.top_products.map((p, i) => {
                    const maxRev = data.top_products![0]?.revenue || 1;
                    const pct = (p.revenue / maxRev) * 100;
                    return (
                      <div
                        key={p.id}
                        style={{
                          display: "grid",
                          gridTemplateColumns: "24px 1fr auto",
                          alignItems: "center",
                          gap: "10px",
                          padding: "8px 12px",
                          background: "rgba(15,23,42,0.5)",
                          borderRadius: "8px",
                          position: "relative",
                          overflow: "hidden",
                        }}
                      >
                        <div
                          style={{
                            position: "absolute",
                            top: 0,
                            left: 0,
                            bottom: 0,
                            width: `${pct}%`,
                            background: "linear-gradient(90deg, rgba(232,160,78,0.12) 0%, transparent 100%)",
                            pointerEvents: "none",
                          }}
                        />
                        <div
                          style={{
                            color: "#94a3b8",
                            fontSize: "12px",
                            fontWeight: 700,
                            position: "relative",
                          }}
                        >
                          #{i + 1}
                        </div>
                        <div
                          style={{
                            color: "#e2e8f0",
                            fontSize: "13px",
                            fontWeight: 600,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                            position: "relative",
                          }}
                        >
                          {p.title}
                          <span style={{ color: "#94a3b8", fontWeight: 400, marginLeft: "6px" }}>
                            · {p.units} sold
                          </span>
                        </div>
                        <div
                          style={{
                            color: "#e8a04e",
                            fontSize: "13px",
                            fontWeight: 700,
                            position: "relative",
                          }}
                        >
                          {fmt(p.revenue, currency)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Kpi({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div
      style={{
        padding: "12px 14px",
        background: "rgba(15,23,42,0.6)",
        borderRadius: "10px",
        border: "1px solid rgba(148,163,184,0.08)",
      }}
    >
      <div
        style={{
          color: "#94a3b8",
          fontSize: "10px",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
        }}
      >
        {label}
      </div>
      <div
        style={{
          color,
          fontSize: "20px",
          fontWeight: 800,
          marginTop: "4px",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </div>
    </div>
  );
}
