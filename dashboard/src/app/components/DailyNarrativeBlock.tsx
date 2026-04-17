"use client";

/**
 * DailyNarrativeBlock — the storytelling heart of the dashboard.
 *
 * 3 sentences. Human voice. No jargon. The feeling of a founder getting
 * a quick text from a smart friend who watched their store all day.
 *
 * Data: GET /pro/daily-narrative
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";

type NarrativeData = {
  shop_domain: string;
  headline: string;
  paragraphs: string[];
  stats: {
    visitors_today: number;
    intent_signals_today: number;
    nudges_fired_today: number;
    orders_today: number;
    revenue_today_eur: number;
  };
  top_next_action: string | null;
  // Shop's native currency — `revenue_today_eur` is denominated here.
  currency?: string;
  generated_at: string;
};

export function DailyNarrativeBlock({ apiBase, isProUser }: { apiBase: string; isProUser: boolean }) {
  const [data, setData] = useState<NarrativeData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isProUser) {
      setLoading(false);
      return;
    }
    apiClient
      .GET("/pro/daily-narrative")
      .then(({ data: j, error }) => {
        if (!error && j) setData(j as unknown as NarrativeData);
      })
      .finally(() => setLoading(false));
  }, [apiBase, isProUser]);

  if (!isProUser || loading || !data) return null;

  return (
    <div
      style={{
        marginBottom: "24px",
        padding: "28px 32px",
        borderRadius: "20px",
        background:
          "radial-gradient(circle at 80% 0%, rgba(139,92,246,0.1) 0%, transparent 55%), linear-gradient(135deg, #0b1220 0%, #13162b 100%)",
        border: "1px solid rgba(139,92,246,0.25)",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          color: "#a78bfa",
          fontSize: "11px",
          fontWeight: 700,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          marginBottom: "12px",
        }}
      >
        📖 {data.headline}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "10px", maxWidth: "720px" }}>
        {data.paragraphs.map((p, i) => (
          <p
            key={i}
            style={{
              color: "#e2e8f0",
              fontSize: "17px",
              lineHeight: 1.55,
              margin: 0,
              fontWeight: 500,
            }}
          >
            {p}
          </p>
        ))}
      </div>

      {data.top_next_action && (
        <div
          style={{
            marginTop: "18px",
            padding: "12px 16px",
            borderRadius: "10px",
            background: "rgba(232,160,78,0.08)",
            border: "1px solid rgba(232,160,78,0.25)",
            display: "inline-flex",
            alignItems: "center",
            gap: "10px",
          }}
        >
          <span style={{ fontSize: "16px" }}>🎯</span>
          <span style={{ color: "#e8a04e", fontSize: "13px", fontWeight: 600 }}>
            Next opportunity: <span style={{ color: "#fcd34d" }}>{data.top_next_action}</span>
          </span>
        </div>
      )}

      <div
        style={{
          marginTop: "18px",
          paddingTop: "14px",
          borderTop: "1px solid rgba(148,163,184,0.1)",
          display: "flex",
          gap: "20px",
          flexWrap: "wrap",
          fontSize: "12px",
          color: "#94a3b8",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        <span>
          <b style={{ color: "#cbd5e1" }}>{data.stats.visitors_today}</b> visitors
        </span>
        <span>·</span>
        <span>
          <b style={{ color: "#cbd5e1" }}>{data.stats.intent_signals_today}</b> intent
        </span>
        <span>·</span>
        <span>
          <b style={{ color: "#cbd5e1" }}>{data.stats.nudges_fired_today}</b> nudges fired
        </span>
        <span>·</span>
        <span>
          <b style={{ color: "#10b981" }}>{data.stats.orders_today}</b> orders ·{" "}
          {formatMoneyCompact(data.stats.revenue_today_eur, data.currency || "USD")}
        </span>
      </div>
    </div>
  );
}
