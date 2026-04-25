"use client";

/**
 * RiskForecastCard — "Where is revenue-at-risk heading?"
 *
 * 7-day projection of the Revenue-at-Risk Score (RARS). Uses
 * least-squares linear regression over a rolling RARS history
 * (1 snapshot per day, capped at 60 days) plus 80/95% prediction
 * intervals derived from the residual standard error.
 *
 * Competitive positioning: the RARS hero already shows "money at
 * risk right now". This card shows the trajectory — "at this rate
 * you'll be down another €420 next week". No competitor does this
 * because no competitor accumulates per-shop RARS history.
 *
 * Data source: GET /pro/risk-forecast
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
import { formatMoneyCompact } from "@/app/app/_lib/formatters";
import type { components } from "@/app/lib/api-types";

type RiskData = components["schemas"]["RiskForecastResponse"];

function directionTheme(direction: string | null | undefined) {
  if (direction === "rising") return { label: "Risk rising", color: "#f43f5e" };
  if (direction === "falling") return { label: "Risk falling", color: "#10b981" };
  return { label: "Risk stable", color: "#94a3b8" };
}

function confidenceTheme(c: string | null | undefined) {
  if (c === "high") return { label: "High confidence", color: "#10b981" };
  if (c === "medium") return { label: "Medium confidence", color: "#e8a04e" };
  if (c === "low") return { label: "Low confidence", color: "#f59e0b" };
  return { label: "Not yet reliable", color: "#94a3b8" };
}

function HistorySparkline({
  history,
  forecast,
  lower80,
  upper80,
}: {
  history: { ts: string; total_at_risk_eur: number }[];
  forecast: number;
  lower80: number;
  upper80: number;
}) {
  if (history.length < 2) return null;

  const width = 320;
  const height = 110;
  const padTop = 6;
  const padBottom = 16;
  const padLeft = 4;
  const padRight = 4;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const observed = history.map((h) => h.total_at_risk_eur);
  const all = [...observed, forecast, lower80, upper80].filter((v) => Number.isFinite(v));
  const max = Math.max(...all, 1);
  const min = Math.max(0, Math.min(...all, 0));
  const range = max - min || 1;

  const nObs = observed.length;
  const nTotal = nObs + 1;

  const toX = (idx: number): number =>
    padLeft + (idx / Math.max(nTotal - 1, 1)) * plotW;
  const toY = (v: number): number =>
    padTop + plotH - ((v - min) / range) * plotH;

  const observedPoints = observed.map((v, i) => `${toX(i)},${toY(v)}`).join(" ");
  const lastObservedY = toY(observed[nObs - 1]);
  const forecastX = toX(nObs);

  const ribbonYTop = toY(upper80);
  const ribbonYBot = toY(lower80);

  return (
    <svg
      width={width}
      height={height}
      role="img"
      aria-label={`RARS trajectory: ${nObs} daily snapshots observed, 7-day forecast with 80% confidence band`}
      style={{ maxWidth: "100%", display: "block" }}
    >
      {/* 80% confidence band around forecast point — thin vertical strip */}
      <rect
        x={forecastX - 4}
        y={ribbonYTop}
        width={8}
        height={Math.max(ribbonYBot - ribbonYTop, 1)}
        fill="#f43f5e"
        fillOpacity="0.2"
        rx={2}
      />
      {/* Observed history line */}
      <polyline
        points={observedPoints}
        fill="none"
        stroke="#94a3b8"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Forecast segment (dashed) from last observed to forecast */}
      <line
        x1={toX(nObs - 1)}
        y1={lastObservedY}
        x2={forecastX}
        y2={toY(forecast)}
        stroke="#f43f5e"
        strokeWidth="2"
        strokeDasharray="4 3"
        strokeLinecap="round"
      />
      {/* Forecast endpoint marker */}
      <circle cx={forecastX} cy={toY(forecast)} r="3.5" fill="#f43f5e" />
      {/* Vertical "+7d" label */}
      <text
        x={forecastX - 2}
        y={height - 4}
        fontSize="9"
        fill="#f43f5e"
        fontWeight="600"
        textAnchor="end"
      >
        +7d
      </text>
      <text
        x={padLeft}
        y={height - 4}
        fontSize="9"
        fill="#64748b"
        fontWeight="600"
      >
        {nObs}d HISTORY
      </text>
    </svg>
  );
}

export function RiskForecastCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<RiskData>({
    url: `${apiBase}/pro/risk-forecast`,
    enabled: !!apiBase && !!shop && isProUser,
    isEmpty: (d) => d.status !== "ok",
  });

  if (!isProUser) return null;

  if (state === "loading") {
    return <CardSkeleton label="Projecting the risk trajectory" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Risk forecast unavailable"
        message="We couldn't load the RARS trajectory right now. Your history is safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="amber"
        title="Forecast warming up"
        body="We need at least four days of RARS snapshots before the trajectory is statistically meaningful. Daily snapshots accumulate automatically — check back in a few days."
        eta="Needs ~4 days of daily RARS history"
      />
    );
  }

  const today = data.today_value_eur ?? 0;
  const forecast = data.forecast_7d_eur ?? 0;
  const low80 = data.forecast_7d_lower_80_eur ?? 0;
  const up80 = data.forecast_7d_upper_80_eur ?? 0;
  const low95 = data.forecast_7d_lower_95_eur ?? 0;
  const up95 = data.forecast_7d_upper_95_eur ?? 0;
  const delta = data.week_delta_eur ?? 0;
  const deltaPct = data.week_delta_pct ?? 0;
  const dTheme = directionTheme(data.direction);
  const cTheme = confidenceTheme(data.confidence);
  const history = data.history ?? [];

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open risk forecast details — ${dTheme.label}, 7-day projection ${forecast} with ${cTheme.label}`}
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
              Risk forecast
            </div>
            <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
              Where is revenue-at-risk heading?
            </h3>
            <p className="mt-2 text-[14px] leading-relaxed text-slate-400">
              Revenue-at-Risk today vs the 7-day projection, with an honest confidence band. Built
              from your own RARS history — no competitor can reproduce this without it.
            </p>
          </div>
          <div
            className="flex-shrink-0 rounded-xl border px-4 py-2 text-right"
            style={{
              borderColor: dTheme.color + "55",
              background: dTheme.color + "14",
            }}
          >
            <div
              className="text-[10px] font-bold uppercase tracking-wider"
              style={{ color: dTheme.color }}
            >
              Trajectory
            </div>
            <div className="mt-0.5 text-[13px] font-extrabold" style={{ color: dTheme.color }}>
              {dTheme.label}
            </div>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="rounded-xl border border-slate-400/20 bg-slate-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
              At risk today
            </div>
            <div className="mt-1 text-[28px] font-extrabold tabular-nums text-slate-200">
              {formatMoneyCompact(today, "USD")}
            </div>
            <div className="mt-0.5 text-[10px] text-slate-400">
              current RARS total
            </div>
          </div>

          <div className="rounded-xl border border-rose-400/25 bg-rose-500/[0.06] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-rose-400">
              Projected in 7 days
            </div>
            <div className="mt-1 text-[28px] font-extrabold tabular-nums text-rose-300">
              {formatMoneyCompact(forecast, "USD")}
            </div>
            <div className="mt-0.5 text-[10px] text-rose-400/70">
              {delta > 0 ? "+" : ""}
              {formatMoneyCompact(delta, "USD")} ({deltaPct > 0 ? "+" : ""}
              {deltaPct.toFixed(0)}%)
            </div>
          </div>

          <div
            className="rounded-xl border px-4 py-4 text-center"
            style={{
              borderColor: cTheme.color + "33",
              background: cTheme.color + "0D",
            }}
          >
            <div className="text-[10px] font-bold uppercase tracking-wider" style={{ color: cTheme.color }}>
              Confidence
            </div>
            <div className="mt-1 text-[28px] font-extrabold tabular-nums" style={{ color: cTheme.color }}>
              {((data.r_squared ?? 0) * 100).toFixed(0)}%
            </div>
            <div
              className="mt-0.5 text-[10px]"
              style={{ color: cTheme.color, opacity: 0.75 }}
            >
              {cTheme.label}
            </div>
          </div>
        </div>

        {history.length >= 2 && (
          <div className="mt-5 rounded-lg border border-white/[0.05] bg-white/[0.02] px-4 py-4">
            <div className="mb-2 text-[11px] font-bold uppercase tracking-wider text-slate-400">
              Trajectory
            </div>
            <div className="overflow-hidden rounded-md">
              <HistorySparkline
                history={history}
                forecast={forecast}
                lower80={low80}
                upper80={up80}
              />
            </div>
            <p className="mt-3 text-[12px] text-slate-400">
              Solid line = your daily RARS totals. Dashed = 7-day projection. Rose band = 80%
              prediction interval — we refuse to give you a single number when the math says a
              range.
            </p>
          </div>
        )}

        {data.headline && (
          <div className="mt-3 rounded-md border border-white/[0.05] bg-white/[0.015] px-4 py-2.5 text-[12.5px] text-slate-300">
            {data.headline}
          </div>
        )}

        <div className="mt-3 text-[11px] font-semibold text-slate-400">
          Click for the 95% band, full method, and recommended action →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="📈"
        title="Where revenue-at-risk is heading"
        subtitle="Linear regression over your RARS history"
      >
        <DrawerExplainer
          body={
            "Every time your Revenue-at-Risk Score is computed (several times a day), we store a " +
            "snapshot of the total. After a few days, we fit a least-squares line to the daily " +
            "averages and project it 7 days forward. The 80% band comes from the residual standard " +
            "error of the fit — so a noisier history produces a visibly wider band, not a false " +
            "sense of precision."
          }
          why={
            "The RARS hero tells you how much money is at risk RIGHT NOW. This card tells you " +
            "whether you'll have more or less money at risk next week. It's the difference between " +
            "a status light and a weather forecast. Competitors don't ship this because they don't " +
            "accumulate a continuous per-shop risk series."
          }
        />

        <DrawerBigStat
          label="7-day projection vs today"
          value={`${delta > 0 ? "+" : ""}${formatMoneyCompact(delta, "USD")}`}
          sublabel={data.headline || "Projection extrapolated from daily RARS snapshots."}
          color={dTheme.color}
        />

        <DrawerKeyValueList
          items={[
            {
              label: "At risk today",
              value: formatMoneyCompact(today, "USD"),
            },
            {
              label: "Projected in 7 days",
              value: formatMoneyCompact(forecast, "USD"),
              color: dTheme.color,
            },
            {
              label: "80% prediction interval",
              value: `${formatMoneyCompact(low80, "USD")} – ${formatMoneyCompact(up80, "USD")}`,
            },
            {
              label: "95% prediction interval",
              value: `${formatMoneyCompact(low95, "USD")} – ${formatMoneyCompact(up95, "USD")}`,
            },
            { label: "Confidence", value: cTheme.label, color: cTheme.color },
            { label: "R²", value: `${((data.r_squared ?? 0) * 100).toFixed(1)}%` },
            { label: "Daily slope", value: `${(data.slope_per_day ?? 0) > 0 ? "+" : ""}${formatMoneyCompact(data.slope_per_day ?? 0, "USD")}/day` },
            { label: "History points used", value: `${data.points_used ?? 0}` },
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
          We fit a least-squares linear regression over (day_index, RARS_total) pairs. The slope
          and intercept give us today&apos;s expected value and the 7-day projection. The 80/95%
          bands come from the residual standard error of the fit, widened by √horizon. When the
          band is wider than the point estimate itself, the confidence label reflects it — we
          refuse to present noise as signal.
        </div>

        <DrawerHowCalculated
          formula="point(t) = slope × t + intercept. 80% band = point ± z₀.₈₀ · σ · √horizon. σ = √(Σresiduals² / (n−2)). Point is clamped to zero — RARS can't go negative."
          inputs={[
            { label: "History window", value: "up to 60 days of daily snapshots" },
            { label: "Forecast horizon", value: "7 days" },
            { label: "Residual std error", value: formatMoneyCompact(data.residual_std_error ?? 0, "USD") },
            { label: "Linear regression", value: "OLS (deterministic, no LLM)" },
          ]}
          note="This is a linear forecast — it can't see regime changes (new product launch, promo burst, seasonal shift). When the fit quality (R²) is low the confidence label drops to 'low' or 'insufficient'; use the band as the real signal, not the point."
        />

        <DrawerNextAction
          headline={
            data.direction === "rising"
              ? "Intervene before the trend compounds"
              : data.direction === "falling"
                ? "Investigate what's helping"
                : "Keep monitoring"
          }
          primary={{
            label:
              data.direction === "rising"
                ? "Review what's driving the rise"
                : data.direction === "falling"
                  ? "Double down on the improvement"
                  : "Nothing urgent — watch weekly",
            description:
              data.direction === "rising"
                ? "RARS is trending up. Open the RARS breakdown drawer to see which component (abandoned, churn, price-sensitive) is pushing it. Act on the biggest contributor before next week's number lands."
                : data.direction === "falling"
                  ? "RARS is trending down — something you did in the last two weeks is working. Identify the change and ensure it keeps running. Revisit this card weekly to confirm the trend holds."
                  : "No action required. The projection is within ±5% of today, so any move would be noise-chasing. Keep daily RARS refreshing and let the series breathe.",
            onClick: () => setDrawerOpen(false),
          }}
        />
      </DetailDrawer>
    </>
  );
}
