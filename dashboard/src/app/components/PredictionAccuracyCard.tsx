"use client";

/**
 * PredictionAccuracyCard — MA-1 moat card.
 *
 * Renders our honest MAPE per forecast metric + the last 8 predictions
 * vs actuals. When we don't have ≥8 matured predictions yet, shows the
 * "locked" state explaining what unlocks it — same honesty pattern as
 * the benchmark engine (MA-4).
 *
 * Why this card is a moat
 * -----------------------
 * Every competitor's marketing copy claims "accurate forecasts". None
 * of them publishes the accuracy of their own predictions because
 * doing so would expose what the forecasts actually do. Ours is a
 * signed receipt: predicted X, observed Y, error Z%. Trust compounds
 * when a merchant can verify our math instead of taking our word.
 */

import {
  CardEmpty,
  CardError,
  CardSkeleton,
  useCardFetch,
} from "./_CardStates";

type PredictionEntry = {
  prediction_date: string | null;
  horizon_date: string | null;
  predicted: number;
  actual: number;
  error_pct: number;
  currency: string;
};

type PerMetricAccuracy = {
  sample_size: number;
  mape_pct: number;
  median_error_pct: number;
  currency: string;
  last_predictions: PredictionEntry[];
};

type Payload = {
  status: "ok" | "insufficient_history" | "error";
  metrics?: Record<string, PerMetricAccuracy>;
  predictions_seen?: number;
  unlock_at?: number;
  message?: string;
};

const METRIC_LABELS: Record<string, string> = {
  forecast_1d_revenue: "Tomorrow's revenue forecast",
  forecast_7d_revenue: "7-day revenue forecast",
  forecast_30d_revenue: "30-day revenue forecast",
};

function accuracyBandColor(mape: number): { text: string; bg: string; border: string } {
  // Brutal honest bands — matches how a data scientist reads MAPE.
  if (mape < 10) return { text: "#34d399", bg: "rgba(52, 211, 153, 0.08)", border: "rgba(52, 211, 153, 0.25)" };
  if (mape < 25) return { text: "#e8a04e", bg: "rgba(232, 160, 78, 0.08)", border: "rgba(232, 160, 78, 0.25)" };
  return { text: "#fb7185", bg: "rgba(251, 113, 133, 0.08)", border: "rgba(251, 113, 133, 0.25)" };
}

function accuracyBandLabel(mape: number): string {
  if (mape < 10) return "high accuracy";
  if (mape < 25) return "moderate accuracy";
  return "low accuracy — investigating";
}

export function PredictionAccuracyCard({
  apiBase,
  enabled = true,
}: {
  apiBase: string;
  enabled?: boolean;
}) {
  const { data, state, retry } = useCardFetch<Payload>({
    url: `${apiBase}/pro/prediction-accuracy`,
    enabled,
    component: "PredictionAccuracyCard",
    isEmpty: (d) =>
      !d || !d.metrics || Object.keys(d.metrics).length === 0,
  });

  if (state === "loading") return <CardSkeleton label="Loading prediction accuracy" />;
  if (state === "error") {
    return (
      <CardError
        message="Couldn't pull prediction accuracy — pipeline notified."
        onRetry={retry}
        label="Prediction accuracy failed to load"
      />
    );
  }

  // Locked / insufficient history — the honest state.
  if (!data || data.status === "insufficient_history" || state === "empty") {
    const seen = data?.predictions_seen ?? 0;
    const unlockAt = data?.unlock_at ?? 8;
    return (
      <div
        className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-6"
        role="status"
        aria-live="polite"
      >
        <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
          Prediction accuracy
        </div>
        <h3 className="text-[16px] font-semibold text-slate-100">
          🔒 Unlocks at {unlockAt} matured predictions
        </h3>
        <p className="mt-3 text-[13px] leading-relaxed text-slate-400">
          {data?.message ??
            `We don't publish a Mean Absolute Percentage Error computed from too few samples — honest numbers only. Your forecast history has ${seen} matured predictions so far; accuracy unlocks at ${unlockAt}.`}
        </p>
        <div className="mt-4 flex items-center gap-2">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className="h-full rounded-full bg-gradient-to-r from-violet-500/60 to-[#e8a04e]"
              style={{ width: `${Math.min(100, (seen / unlockAt) * 100)}%` }}
            />
          </div>
          <span className="text-[11px] font-semibold tabular-nums text-slate-300">
            {seen}/{unlockAt}
          </span>
        </div>
      </div>
    );
  }

  // OK — render per-metric panels.
  const metrics = data.metrics || {};
  const metricKeys = Object.keys(metrics).sort();

  return (
    <div className="rounded-2xl border border-[#e8a04e]/20 bg-gradient-to-br from-[#e8a04e]/[0.04] via-transparent to-[#7c3aed]/[0.03] p-6">
      <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.16em] text-[#e8a04e]">
        Prediction accuracy — receipts
      </div>
      <h3 className="text-[16px] font-semibold text-slate-100">
        How close our forecasts were, measured
      </h3>
      <p className="mt-1 text-[12px] leading-relaxed text-slate-400">
        Every number below compares what we predicted against what actually
        happened. No competitor publishes this.
      </p>

      <div className="mt-5 space-y-4">
        {metricKeys.map((key) => {
          const m = metrics[key];
          const color = accuracyBandColor(m.mape_pct);
          const label = METRIC_LABELS[key] ?? key;
          return (
            <div
              key={key}
              className="rounded-xl border p-4"
              style={{ borderColor: color.border, backgroundColor: color.bg }}
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">
                    {label}
                  </div>
                  <div
                    className="mt-1 text-[26px] font-extrabold tabular-nums leading-none"
                    style={{ color: color.text }}
                  >
                    {m.mape_pct.toFixed(1)}%
                    <span className="ml-1.5 text-[11px] font-semibold text-slate-400">
                      MAPE
                    </span>
                  </div>
                  <div className="mt-1 text-[11px] text-slate-500">
                    median error {m.median_error_pct.toFixed(1)}% · {m.sample_size} measured
                  </div>
                </div>
                <span
                  className="rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide"
                  style={{
                    color: color.text,
                    backgroundColor: color.bg,
                    border: `1px solid ${color.border}`,
                  }}
                >
                  {accuracyBandLabel(m.mape_pct)}
                </span>
              </div>

              {/* Last predictions mini-table */}
              {m.last_predictions.length > 0 && (
                <div className="mt-4">
                  <div className="mb-1.5 grid grid-cols-4 gap-2 text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-600">
                    <div>Horizon</div>
                    <div className="text-right">Predicted</div>
                    <div className="text-right">Actual</div>
                    <div className="text-right">Error</div>
                  </div>
                  <div className="space-y-1">
                    {m.last_predictions.slice(0, 5).map((p, i) => (
                      <div
                        key={`${p.horizon_date}-${i}`}
                        className="grid grid-cols-4 gap-2 rounded-md px-2 py-1 text-[11px] text-slate-300 even:bg-white/[0.015]"
                      >
                        <div className="tabular-nums text-slate-400">
                          {p.horizon_date ?? "—"}
                        </div>
                        <div className="text-right tabular-nums">
                          {p.predicted.toLocaleString()} {p.currency}
                        </div>
                        <div className="text-right tabular-nums text-slate-100">
                          {p.actual.toLocaleString()} {p.currency}
                        </div>
                        <div
                          className="text-right font-bold tabular-nums"
                          style={{ color: p.error_pct < 10 ? "#34d399" : p.error_pct < 25 ? "#e8a04e" : "#fb7185" }}
                        >
                          {p.error_pct.toFixed(1)}%
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <p className="mt-5 text-[11px] leading-relaxed text-slate-500">
        MAPE = Mean Absolute Percentage Error. Lower is better.
        We keep the last 90 days of matured predictions.
      </p>
    </div>
  );
}
