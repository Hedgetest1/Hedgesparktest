"use client";

/**
 * VisitorJourneyTimeline — animated real-customer journey playback.
 *
 * KILLER feature (π6): shows 3-5 actual customer paths that ended in
 * a purchase. Each journey is rendered as a horizontal timeline with
 * touchpoint chips spaced by their real time-before-purchase.
 *
 * No competitor can do this without our joined data model.
 * Data: GET /pro/visitor-journeys?source=X&limit=5
 */

import { useEffect, useState } from "react";

type Touch = {
  source: string;
  campaign: string | null;
  hours_before_purchase: number;
  is_first: boolean;
  is_last: boolean;
};

type Journey = {
  visitor_hash: string;
  purchase_at: string;
  revenue_eur: number;
  touch_count: number;
  window_hours: number;
  touches: Touch[];
};

type JourneysResponse = {
  shop_domain: string;
  source_filter: string | null;
  window_days: number;
  total_found: number;
  journeys: Journey[];
};

const SOURCE_ICONS: Record<string, string> = {
  direct: "🔗",
  google: "🔍",
  meta: "📘",
  facebook: "📘",
  instagram: "📸",
  tiktok: "🎵",
  email: "📧",
  klaviyo: "📧",
  newsletter: "📧",
  organic: "🌱",
  referral: "🤝",
};

const SOURCE_COLOR: Record<string, string> = {
  direct: "#94a3b8",
  google: "#ea4335",
  meta: "#1877f2",
  facebook: "#1877f2",
  instagram: "#e1306c",
  tiktok: "#fe2c55",
  email: "#f59e0b",
  klaviyo: "#f59e0b",
  newsletter: "#f59e0b",
  organic: "#10b981",
  referral: "#8b5cf6",
};

const fmtHours = (h: number): string => {
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 24) return `${h.toFixed(h < 4 ? 1 : 0)}h`;
  const days = Math.floor(h / 24);
  const rem = Math.round(h - days * 24);
  return rem > 0 ? `${days}d ${rem}h` : `${days}d`;
};

const fmtEur = (n: number): string => {
  if (n >= 1000) return `€${(n / 1000).toFixed(1)}k`;
  return `€${Math.round(n)}`;
};

export function VisitorJourneyTimeline({
  apiBase,
  source,
}: {
  apiBase: string;
  source: string;
}) {
  const [data, setData] = useState<JourneysResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [animated, setAnimated] = useState(false);

  useEffect(() => {
    setLoading(true);
    setAnimated(false);
    fetch(`${apiBase}/pro/visitor-journeys?source=${source}&limit=5`, {
      credentials: "include",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .finally(() => {
        setLoading(false);
        // Kick off animation on next frame
        setTimeout(() => setAnimated(true), 80);
      });
  }, [apiBase, source]);

  if (loading) {
    return (
      <div style={{ padding: "20px", textAlign: "center", color: "#94a3b8", fontSize: "13px" }}>
        Loading real customer journeys…
      </div>
    );
  }

  if (!data || data.journeys.length === 0) {
    return (
      <div
        style={{
          padding: "16px",
          background: "rgba(15,23,42,0.5)",
          borderRadius: "10px",
          border: "1px dashed rgba(148,163,184,0.2)",
          color: "#94a3b8",
          fontSize: "13px",
          textAlign: "center",
        }}
      >
        No completed journeys for this source yet. Come back after a few more orders.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
      {data.journeys.map((j, journeyIdx) => (
        <div
          key={j.visitor_hash}
          style={{
            padding: "16px 18px",
            borderRadius: "12px",
            background: "linear-gradient(135deg, rgba(15,23,42,0.8) 0%, rgba(30,41,59,0.4) 100%)",
            border: "1px solid rgba(148,163,184,0.12)",
            opacity: animated ? 1 : 0,
            transform: animated ? "translateY(0)" : "translateY(12px)",
            transition: `opacity 0.5s ease ${journeyIdx * 0.1}s, transform 0.5s cubic-bezier(0.16,1,0.3,1) ${journeyIdx * 0.1}s`,
          }}
        >
          {/* Header */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "14px",
            }}
          >
            <div>
              <div
                style={{
                  color: "#94a3b8",
                  fontSize: "11px",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  fontWeight: 700,
                }}
              >
                Customer {j.visitor_hash}
              </div>
              <div style={{ color: "#cbd5e1", fontSize: "13px", marginTop: "2px" }}>
                {j.touch_count} touches over {fmtHours(j.window_hours)}
              </div>
            </div>
            <div
              style={{
                color: "#10b981",
                fontSize: "18px",
                fontWeight: 800,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              +{fmtEur(j.revenue_eur)}
            </div>
          </div>

          {/* Timeline */}
          <div style={{ position: "relative", paddingBottom: "28px" }}>
            {/* Track */}
            <div
              style={{
                position: "absolute",
                top: "14px",
                left: 0,
                right: 0,
                height: "2px",
                background: "rgba(148,163,184,0.15)",
                borderRadius: "1px",
              }}
            />

            {/* Touch chips */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                position: "relative",
                gap: "6px",
              }}
            >
              {j.touches.map((t, i) => {
                const color = SOURCE_COLOR[t.source] || "#94a3b8";
                const icon = SOURCE_ICONS[t.source] || "•";
                const isPurchase = t.is_last;
                return (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      minWidth: 0,
                      flex: "0 1 auto",
                      opacity: animated ? 1 : 0,
                      transform: animated ? "scale(1)" : "scale(0.7)",
                      transition: `opacity 0.3s ease ${journeyIdx * 0.1 + i * 0.08 + 0.2}s, transform 0.4s cubic-bezier(0.34,1.56,0.64,1) ${journeyIdx * 0.1 + i * 0.08 + 0.2}s`,
                    }}
                  >
                    <div
                      style={{
                        width: "28px",
                        height: "28px",
                        borderRadius: "50%",
                        background: `${color}20`,
                        border: `2px solid ${color}`,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: "14px",
                        position: "relative",
                        zIndex: 1,
                      }}
                    >
                      {icon}
                    </div>
                    <div
                      style={{
                        marginTop: "6px",
                        fontSize: "10px",
                        color,
                        fontWeight: 700,
                        textTransform: "capitalize",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {t.source}
                    </div>
                    <div
                      style={{
                        fontSize: "9px",
                        color: "#64748b",
                        fontVariantNumeric: "tabular-nums",
                        marginTop: "1px",
                      }}
                    >
                      {t.hours_before_purchase > 0 ? `-${fmtHours(t.hours_before_purchase)}` : "bought"}
                    </div>
                    {t.is_first && (
                      <div
                        style={{
                          fontSize: "8px",
                          color: "#a78bfa",
                          marginTop: "1px",
                          textTransform: "uppercase",
                          letterSpacing: "0.05em",
                          fontWeight: 700,
                        }}
                      >
                        FIRST
                      </div>
                    )}
                  </div>
                );
              })}
              {/* Purchase marker at the end */}
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  opacity: animated ? 1 : 0,
                  transition: `opacity 0.3s ease ${journeyIdx * 0.1 + j.touches.length * 0.08 + 0.3}s`,
                }}
              >
                <div
                  style={{
                    width: "28px",
                    height: "28px",
                    borderRadius: "50%",
                    background: "#10b981",
                    border: "2px solid #10b981",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: "14px",
                    position: "relative",
                    zIndex: 1,
                    boxShadow: "0 0 0 4px rgba(16,185,129,0.2)",
                  }}
                >
                  ✓
                </div>
                <div
                  style={{
                    marginTop: "6px",
                    fontSize: "10px",
                    color: "#10b981",
                    fontWeight: 700,
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                  }}
                >
                  PURCHASE
                </div>
              </div>
            </div>
          </div>
        </div>
      ))}

      <div
        style={{
          padding: "10px 14px",
          fontSize: "11px",
          color: "#64748b",
          fontStyle: "italic",
          lineHeight: 1.5,
        }}
      >
        These are real customers from your store (visitor IDs hashed for privacy). The icons show
        where they came from; the gap below each icon shows how long before the purchase. "FIRST"
        marks the very first time they visited your store.
      </div>
    </div>
  );
}
