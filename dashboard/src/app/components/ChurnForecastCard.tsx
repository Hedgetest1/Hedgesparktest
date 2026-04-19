"use client";

/**
 * ChurnForecastCard — "Who's about to go silent?"
 *
 * Forward-looking view of customer churn based on Holt double-exponential
 * smoothing of the daily "newly silent customer" count (customers whose
 * most-recent order has just crossed the 30-day silence line). Returns a
 * point estimate plus 80% and 95% prediction intervals for the next
 * `horizon_days` (default 30).
 *
 * Competitive positioning: Lifetimely / BeProfit / Peel are retrospective
 * ("you lost X customers last month"). HedgeSpark is forward-looking
 * ("X customers about to go silent in the next 30 days, confidence band
 * Y–Z"). That prediction is actionable before the loss happens.
 *
 * Data source: GET /pro/forecast/churn
 */

import { useState } from "react";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
  DrawerHowCalculated,
  DrawerNextAction,
} from "./DetailDrawer";
import type { components } from "@/app/lib/api-types";

type ChurnData = components["schemas"]["ChurnForecastResponse"];

function directionBadge(direction: string) {
  if (direction === "worsening") return { label: "Trending up", color: "#f43f5e" };
  if (direction === "improving") return { label: "Cooling off", color: "#10b981" };
  return { label: "Steady", color: "#94a3b8" };
}

function confidenceBadge(c: string) {
  if (c === "high") return { label: "High confidence", color: "#10b981" };
  if (c === "medium") return { label: "Medium confidence", color: "#e8a04e" };
  if (c === "low") return { label: "Low confidence", color: "#f59e0b" };
  return { label: "Insufficient data", color: "#94a3b8" };
}

/**
 * ForecastChart — inline SVG line chart with observed history +
 * forecast extension + 80% confidence ribbon. Zero dependencies.
 *
 * Design: past values solid line, future values dashed, shaded ribbon
 * around the forecast spanning lower_80 to upper_80. Compact enough for
 * a dashboard card (h~120px); readable at 320px wide.
 */
function ForecastChart({
  observed,
  forecastValues,
  lower80,
  upper80,
}: {
  observed: number[];
  forecastValues: number[];
  lower80: number;
  upper80: number;
}) {
  if (observed.length < 2 || forecastValues.length < 1) return null;

  const width = 320;
  const height = 120;
  const padTop = 8;
  const padBottom = 18;
  const padLeft = 4;
  const padRight = 4;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const all = [...observed, ...forecastValues, lower80, upper80].filter(
    (v) => Number.isFinite(v),
  );
  const max = Math.max(...all, 1);
  const min = Math.max(0, Math.min(...all));
  const range = max - min || 1;

  const nObs = observed.length;
  const nFc = forecastValues.length;
  const nTotal = nObs + nFc;

  const toX = (idx: number): number =>
    padLeft + (idx / Math.max(nTotal - 1, 1)) * plotW;
  const toY = (v: number): number =>
    padTop + plotH - ((v - min) / range) * plotH;

  const observedPoints = observed
    .map((v, i) => `${toX(i)},${toY(v)}`)
    .join(" ");
  const forecastPoints = forecastValues
    .map((v, i) => `${toX(nObs + i)},${toY(v)}`)
    .join(" ");

  // Ribbon: rectangle over forecast region bounded by lower80..upper80.
  // Simple but effective when the forecast is roughly stationary (which
  // Holt's flat forecast assumption implies within the horizon window).
  const ribbonX1 = toX(nObs);
  const ribbonX2 = toX(nTotal - 1);
  const ribbonYTop = toY(upper80);
  const ribbonYBot = toY(lower80);

  const forecastStartX = toX(nObs - 0.5);
  const lastObserved = observed[nObs - 1];

  return (
    <svg
      width={width}
      height={height}
      role="img"
      aria-label={`Forecast chart: ${nObs} days observed, ${nFc} days forecast with 80% confidence band`}
      style={{ maxWidth: "100%", display: "block" }}
    >
      {/* Confidence ribbon (80% band over forecast region) */}
      <rect
        x={ribbonX1}
        y={ribbonYTop}
        width={ribbonX2 - ribbonX1}
        height={Math.max(ribbonYBot - ribbonYTop, 1)}
        fill="#f43f5e"
        fillOpacity="0.12"
      />
      {/* Vertical "now" marker */}
      <line
        x1={forecastStartX}
        y1={padTop}
        x2={forecastStartX}
        y2={padTop + plotH}
        stroke="#94a3b8"
        strokeDasharray="2 3"
        strokeOpacity="0.4"
        strokeWidth="1"
      />
      {/* Observed line */}
      <polyline
        points={observedPoints}
        fill="none"
        stroke="#94a3b8"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Forecast line (dashed) */}
      <polyline
        points={`${toX(nObs - 1)},${toY(lastObserved)} ${forecastPoints}`}
        fill="none"
        stroke="#f43f5e"
        strokeWidth="2"
        strokeDasharray="4 3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Axis caption */}
      <text
        x={padLeft}
        y={height - 4}
        fontSize="9"
        fill="#64748b"
        fontWeight="600"
      >
        PAST
      </text>
      <text
        x={width - padRight - 40}
        y={height - 4}
        fontSize="9"
        fill="#f43f5e"
        fontWeight="600"
        textAnchor="start"
      >
        FORECAST
      </text>
    </svg>
  );
}

export function ChurnForecastCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<ChurnData>({
    url: `${apiBase}/pro/forecast/churn`,
    enabled: !!apiBase && !!shop && isProUser,
    isEmpty: (d) => d.confidence === "insufficient" || (d.observed_values?.length ?? 0) === 0,
  });

  if (!isProUser) return null;

  if (state === "loading") {
    return <CardSkeleton label="Forecasting customer churn" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Churn forecast unavailable"
        message="We couldn't load the forecast right now. Your customer history is safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="amber"
        title="Not enough history to forecast churn yet"
        body="We need at least two weeks of continuous order history to model the silence curve. The first time a customer goes 30 days without an order counts toward the data — so early stores catch up quickly."
        eta="Needs ~14 days of order history"
      />
    );
  }

  const total = data.total_projected_churn ?? 0;
  const point = data.forecast_point ?? 0;
  const low80 = data.forecast_lower_80 ?? 0;
  const up80 = data.forecast_upper_80 ?? 0;
  const low95 = data.forecast_lower_95 ?? 0;
  const up95 = data.forecast_upper_95 ?? 0;
  const dBadge = directionBadge(data.direction ?? "stable");
  const cBadge = confidenceBadge(data.confidence ?? "insufficient");
  const lastWeekMean =
    (data.observed_values?.slice(-7).reduce((a, b) => a + b, 0) ?? 0) /
    Math.max(Math.min(7, data.observed_values?.length ?? 0), 1);
  const deltaVsLastWeek = lastWeekMean > 0 ? ((point - lastWeekMean) / lastWeekMean) * 100 : 0;

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open churn forecast details — ${total} customers projected to go silent in ${data.horizon_days} days, ${dBadge.label}`}
        onClick={() => setDrawerOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setDrawerOpen(true);
          }
        }}
        className="group cursor-pointer rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6 transition-shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-[#e8a04e] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220] hover:border-white/[0.12]"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-[#e8a04e]">
              Churn forecast
            </div>
            <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
              Who&apos;s about to go silent?
            </h3>
            <p className="mt-2 text-[14px] leading-relaxed text-slate-400">
              Customers whose last order is sliding past the 30-day line. Every competitor reports
              this <em>after</em> it happens. We project it <em>before</em>, so you can intervene.
            </p>
          </div>
          <div
            className="flex-shrink-0 rounded-xl border px-4 py-2 text-right"
            style={{
              borderColor: dBadge.color + "55",
              background: dBadge.color + "14",
            }}
          >
            <div
              className="text-[10px] font-bold uppercase tracking-wider"
              style={{ color: dBadge.color }}
            >
              Trend
            </div>
            <div className="mt-0.5 text-[13px] font-extrabold" style={{ color: dBadge.color }}>
              {dBadge.label}
            </div>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="rounded-xl border border-rose-400/20 bg-rose-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-rose-400">
              Next {data.horizon_days}d at risk
            </div>
            <div className="mt-1 text-[32px] font-extrabold tabular-nums text-rose-300">
              {total}
            </div>
            <div className="mt-0.5 text-[10px] text-rose-400/70">
              customers projected silent
            </div>
          </div>

          <div className="rounded-xl border border-amber-400/20 bg-amber-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-amber-400">
              Daily rate
            </div>
            <div className="mt-1 text-[32px] font-extrabold tabular-nums text-amber-300">
              {point.toFixed(1)}
            </div>
            <div className="mt-0.5 text-[10px] text-amber-400/70">
              {deltaVsLastWeek > 0 ? "+" : ""}
              {deltaVsLastWeek.toFixed(0)}% vs last week
            </div>
          </div>

          <div
            className="rounded-xl border px-4 py-4 text-center"
            style={{
              borderColor: cBadge.color + "33",
              background: cBadge.color + "0D",
            }}
          >
            <div className="text-[10px] font-bold uppercase tracking-wider" style={{ color: cBadge.color }}>
              Confidence
            </div>
            <div className="mt-1 text-[32px] font-extrabold tabular-nums" style={{ color: cBadge.color }}>
              {((data.r_squared ?? 0) * 100).toFixed(0)}%
            </div>
            <div className="mt-0.5 text-[10px]" style={{ color: cBadge.color, opacity: 0.75 }}>
              {cBadge.label}
            </div>
          </div>
        </div>

        <div className="mt-5 rounded-lg border border-white/[0.05] bg-white/[0.02] px-4 py-4">
          <div className="mb-2 text-[11px] font-bold uppercase tracking-wider text-slate-500">
            Forecast window
          </div>
          <div className="overflow-hidden rounded-md">
            <ForecastChart
              observed={data.observed_values ?? []}
              forecastValues={data.forecast_values ?? []}
              lower80={low80}
              upper80={up80}
            />
          </div>
          <p className="mt-3 text-[12px] text-slate-400">
            Solid line = observed. Dashed = forecast. Rose band = 80% prediction interval — the
            forecast could realistically land anywhere inside it.
          </p>
        </div>

        <div className="mt-3 text-[11px] font-semibold text-slate-500">
          Click for the full method, 95% band and recommended action →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="📉"
        title="Customers projected to go silent"
        subtitle={`Next ${data.horizon_days} days · ${data.method} smoothing`}
      >
        <DrawerExplainer
          body={
            "Every day, some customers cross the 30-day-no-order line and join the 'silent' bucket. " +
            "We measure that daily rate over the past " +
            `${data.window_days} days, fit a Holt double-exponential smoother to the curve, and ` +
            "project the next " +
            `${data.horizon_days} days forward. Summing the projected daily counts gives the total ` +
            "customers at risk of going silent over the horizon."
          }
          why={
            "Retrospective churn metrics are comfort food — you see the loss once it's permanent. " +
            "A forward forecast with a confidence band tells you which week to pre-empt with a " +
            "win-back campaign, not just which month to mourn. Competitors don't ship this " +
            "because it requires continuous order history + a time-series model; they settle for " +
            "cohort retention charts."
          }
        />

        <DrawerBigStat
          label={`Projected silent customers — next ${data.horizon_days} days`}
          value={`${total}`}
          sublabel={data.headline || "Holt double-exponential forecast across the horizon."}
          color={dBadge.color}
        />

        <DrawerKeyValueList
          items={[
            {
              label: "Point estimate (per day)",
              value: point.toFixed(2),
              color: dBadge.color,
            },
            {
              label: "80% prediction interval",
              value: `${low80.toFixed(1)} – ${up80.toFixed(1)} per day`,
            },
            {
              label: "95% prediction interval",
              value: `${low95.toFixed(1)} – ${up95.toFixed(1)} per day`,
            },
            { label: "Confidence", value: cBadge.label, color: cBadge.color },
            { label: "R²", value: `${((data.r_squared ?? 0) * 100).toFixed(1)}%` },
            {
              label: "Observed history",
              value: `${data.observed_values?.length ?? 0} days`,
            },
          ]}
        />

        <DrawerSectionHeading>How the method works</DrawerSectionHeading>
        <div
          style={{
            padding: "14px 16px",
            borderRadius: "10px",
            background: "rgba(15,23,42,0.55)",
            border: "1px solid rgba(148,163,184,0.1)",
            color: "#cbd5e1",
            fontSize: "13px",
            lineHeight: 1.6,
          }}
        >
          Holt double-exponential smoothing tracks a <b>level</b> and a <b>trend</b> component and
          updates both with every observation. It handles short daily series better than ARIMA
          because it doesn&apos;t require a stationarity assumption. The prediction interval
          widens with the horizon and with the residual standard deviation of the fit — so a
          noisier history produces a visibly wider band, which is the honest way to communicate
          uncertainty.
        </div>

        <DrawerHowCalculated
          formula="Daily rate (d) = count(customers whose last order falls on day d AND whose next day crosses 30-day silence). Holt smoother with α (level) + β (trend) fit to the series. Forecast = level(n) + k·trend(n) for k = 1..horizon. Prediction interval = point ± z·σ·√k, clamped to zero."
          inputs={[
            { label: "Look-back window", value: `${data.window_days} days` },
            { label: "Horizon", value: `${data.horizon_days} days` },
            { label: "Silence threshold", value: "30 days no order" },
            { label: "Method", value: data.method },
          ]}
          note="We deliberately model the count, not the identity, of at-risk customers. The identity work belongs to the Customer Churn card, which shows the specific emails about to cross the line. The two cards together answer both 'how many' and 'who'."
        />

        <DrawerNextAction
          headline={
            dBadge.label === "Trending up"
              ? "Launch the win-back before they're gone"
              : dBadge.label === "Cooling off"
                ? "Keep the momentum"
                : "Maintain the current rhythm"
          }
          primary={{
            label:
              dBadge.label === "Trending up"
                ? "See the specific customers at risk"
                : "Review the customer churn card",
            description:
              dBadge.label === "Trending up"
                ? "Churn is accelerating. Open the Customer Churn card to see the exact emails crossing the 30-day line this week, and trigger a targeted discount or email sequence before they're unreachable."
                : dBadge.label === "Cooling off"
                  ? "Retention is improving. Look at what changed in the last 2-4 weeks (new product, price change, email cadence) and double down on the causal pattern."
                  : "No alarms, no complacency. Keep monitoring and pre-empt the next rising trend.",
            onClick: () => setDrawerOpen(false),
          }}
        />
      </DetailDrawer>
    </>
  );
}
