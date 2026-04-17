"use client";

/**
 * RevenueForecastCard (α6 + δ3 surface) — compact 14-day revenue projection
 * with 80% and 95% prediction intervals.
 *
 * Built from GET /pro/forecast/revenue (Holt double exponential smoothing).
 * Idiot-proof copy: no "residual std error", no "R²", no jargon.
 *
 * Click → drawer with the math + "how sure are we?" explainer.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
  DrawerSparkline,
} from "./DetailDrawer";

import { formatMoneyCompact } from "@/app/app/_lib/formatters";

type ForecastData = {
  shop_domain: string;
  method: string;
  metric: string;
  horizon_days: number;
  window_days: number;
  dates?: string[];
  observed_values?: number[];
  fitted_values?: number[];
  forecast_values?: number[];
  forecast_point: number;
  forecast_lower_80: number;
  forecast_upper_80: number;
  forecast_lower_95: number;
  forecast_upper_95: number;
  residual_std: number;
  r_squared: number;
  direction: string;
  confidence: string;
  headline: string;
  status?: string;
  // Shop's native currency — forecast/observed values are native.
  currency?: string;
};

const makeFmt = (currency?: string) =>
  (n: number) => formatMoneyCompact(n, currency || "USD");

const DIRECTION_META: Record<string, { color: string; arrow: string; label: string }> = {
  rising: { color: "#10b981", arrow: "↑", label: "Trending up" },
  falling: { color: "#f43f5e", arrow: "↓", label: "Cooling down" },
  stable: { color: "#e8a04e", arrow: "→", label: "Stable" },
};

const CONFIDENCE_META: Record<string, string> = {
  high: "Solid — lots of recent data",
  medium: "Decent — the trend is clear",
  low: "Rough — still collecting data",
  insufficient: "Too early — need a few more days",
};

export function RevenueForecastCard({ apiBase, isProUser }: { apiBase: string; isProUser: boolean }) {
  const [data, setData] = useState<ForecastData | null>(null);
  const [loading, setLoading] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    if (!isProUser) {
      setLoading(false);
      return;
    }
    apiClient
      .GET("/pro/forecast/revenue", { params: { query: { horizon_days: 14 } } })
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      .then(({ data: j, error: err }) => { if (!err && j) setData(j as any); })
      .finally(() => setLoading(false));
  }, [apiBase, isProUser]);

  if (!isProUser || loading || !data) return null;
  if (data.status === "insufficient_data") return null;
  const fmt = makeFmt(data.currency);

  const dir = DIRECTION_META[data.direction] || DIRECTION_META.stable;

  return (
    <>
      <div
        onClick={() => setDrawerOpen(true)}
        style={{
          padding: "22px 24px",
          borderRadius: "16px",
          background: "linear-gradient(135deg, #0b1220 0%, #141d33 100%)",
          border: `1px solid ${dir.color}33`,
          cursor: "pointer",
          transition: "transform 0.2s ease",
          marginBottom: "16px",
          position: "relative",
          overflow: "hidden",
        }}
        onMouseEnter={(e) => (e.currentTarget.style.transform = "translateY(-2px)")}
        onMouseLeave={(e) => (e.currentTarget.style.transform = "translateY(0)")}
      >
        <div
          style={{
            position: "absolute",
            top: "-40%",
            right: "-10%",
            width: "320px",
            height: "320px",
            background: `radial-gradient(circle, ${dir.color}12 0%, transparent 60%)`,
            pointerEvents: "none",
          }}
        />

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "10px",
            marginBottom: "8px",
            position: "relative",
          }}
        >
          <span style={{ fontSize: "18px" }}>🔮</span>
          <div
            style={{
              color: "#e8a04e",
              fontSize: "11px",
              fontWeight: 700,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}
          >
            Next 14 days revenue
          </div>
          <span
            style={{
              marginLeft: "auto",
              padding: "3px 10px",
              borderRadius: "6px",
              background: `${dir.color}20`,
              color: dir.color,
              fontSize: "10px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            {dir.arrow} {dir.label}
          </span>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: "10px",
            position: "relative",
          }}
        >
          <div
            style={{
              color: dir.color,
              fontSize: "36px",
              fontWeight: 900,
              fontVariantNumeric: "tabular-nums",
              letterSpacing: "-0.03em",
              lineHeight: 1,
            }}
          >
            {fmt(data.forecast_point)}
          </div>
          <div style={{ color: "#94a3b8", fontSize: "13px" }}>per day on average</div>
        </div>

        <div
          style={{
            color: "#cbd5e1",
            fontSize: "13px",
            marginTop: "10px",
            position: "relative",
          }}
        >
          Likely between{" "}
          <b style={{ color: "#e8a04e" }}>{fmt(data.forecast_lower_80)}</b> and{" "}
          <b style={{ color: "#e8a04e" }}>{fmt(data.forecast_upper_80)}</b> — 80% of the time.
        </div>

        <div
          style={{
            marginTop: "12px",
            paddingTop: "12px",
            borderTop: "1px solid rgba(148,163,184,0.08)",
            fontSize: "11px",
            color: "#64748b",
            position: "relative",
          }}
        >
          {CONFIDENCE_META[data.confidence] || data.confidence} · click for details
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="🔮"
        title="Next 14 days revenue"
        subtitle="Projected from the last 60 days of orders"
      >
        <DrawerExplainer
          body={
            "We look at your daily revenue for the last 60 days, notice the trend and rhythm, " +
            "then project it 14 days forward. The big number is the average. The two ranges " +
            "show where revenue is likely to land — we're 80% sure it stays in the tight range, " +
            "and 95% sure it stays in the wider one."
          }
          why={
            "One bad day can make the dashboard look like the sky is falling. A forecast range " +
            "tells you what a normal amount of variation looks like, so you only panic when it matters."
          }
        />

        <DrawerBigStat
          label="Best guess (daily average)"
          value={fmt(data.forecast_point)}
          sublabel={data.headline}
          color={dir.color}
        />

        <DrawerKeyValueList
          items={[
            {
              label: "Tight range (80% confidence)",
              value: `${fmt(data.forecast_lower_80)} — ${fmt(data.forecast_upper_80)}`,
              color: "#e8a04e",
            },
            {
              label: "Wide range (95% confidence)",
              value: `${fmt(data.forecast_lower_95)} — ${fmt(data.forecast_upper_95)}`,
              color: "#94a3b8",
            },
            {
              label: "How much revenue jumps around",
              value: `± ${fmt(data.residual_std)}/day`,
            },
            {
              label: "Confidence in the trend",
              value: CONFIDENCE_META[data.confidence] || data.confidence,
            },
          ]}
        />

        {data.observed_values && data.observed_values.length > 1 && (
          <>
            <DrawerSectionHeading>Recent daily revenue</DrawerSectionHeading>
            <DrawerSparkline
              values={data.observed_values.slice(-30)}
              color={dir.color}
              height={60}
            />
          </>
        )}

        <DrawerSectionHeading>How to read the numbers</DrawerSectionHeading>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "10px",
            fontSize: "13px",
            color: "#cbd5e1",
          }}
        >
          <div>
            <b style={{ color: "#e8a04e" }}>Average</b>: the most likely daily revenue over the next 2 weeks.
          </div>
          <div>
            <b style={{ color: "#e8a04e" }}>Tight range (80%)</b>: four days out of five, revenue will land in here. If it goes outside, something unusual happened.
          </div>
          <div>
            <b style={{ color: "#e8a04e" }}>Wide range (95%)</b>: only very unusual days go outside this range. If you see revenue below the bottom of this range, investigate immediately.
          </div>
        </div>
      </DetailDrawer>
    </>
  );
}
