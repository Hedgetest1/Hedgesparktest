"use client";

/**
 * MonthlyTargetsCard — "Your Monthly Targets"
 *
 * Merchants set monthly goals (revenue, orders, AOV, CVR). The card shows
 * progress, projected end-of-month values, and flags at-risk/off-track
 * goals. Loss-framed gap.
 *
 * API: GET /pro/goals, POST /pro/goals, GET /pro/goals/progress
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";


type GoalProgress = {
  metric: string;
  target_value: number;
  current_value: number;
  projected_value: number;
  gap_pct: number;
  status: string;
  narrative: string;
};

const METRIC_LABELS: Record<string, string> = {
  monthly_revenue: "Monthly revenue",
  monthly_orders: "Monthly orders",
  aov: "Average order value",
  cvr: "Conversion rate",
};

function fmtForMetric(metric: string, v: number, currency?: string): string {
  if (metric === "monthly_revenue" || metric === "aov") {
    return formatMoneyCompact(v, currency || "USD");
  }
  if (metric === "cvr") return v.toFixed(1) + "%";
  return Math.round(v).toLocaleString();
}

function statusColor(status: string): string {
  if (status === "on_track") return "#34d399";
  if (status === "at_risk") return "#fbbf24";
  return "#f87171";
}

export function MonthlyTargetsCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [progress, setProgress] = useState<GoalProgress[]>([]);
  const [currency, setCurrency] = useState<string>("USD");
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [newMetric, setNewMetric] = useState<string>("monthly_revenue");
  const [newTarget, setNewTarget] = useState<string>("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function loadProgress() {
    setLoading(true);
    try {
      const { data: j, error: err } = await apiClient.GET("/pro/goals/progress");
      if (!err && j) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const body = j as any;
        setProgress((body.progress as GoalProgress[]) || []);
        if (body.currency) setCurrency(body.currency);
      }
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) { setLoading(false); return; }
    loadProgress();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, shop, isProUser]);

  async function handleSave() {
    setSaveError(null);
    const numeric = parseFloat(newTarget);
    if (!Number.isFinite(numeric) || numeric <= 0) {
      setSaveError("Enter a positive number.");
      return;
    }
    setSaving(true);
    try {
      const { error: err } = await apiClient.POST("/pro/goals", {
        body: { metric: newMetric, target_value: numeric, period: "monthly", note: "" },
      });
      if (err) {
        setSaveError("Save failed.");
        return;
      }
      setNewTarget("");
      setAdding(false);
      await loadProgress();
    } catch {
      setSaveError("Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(metric: string) {
    try {
      await apiClient.DELETE("/pro/goals/{metric}", {
        params: { path: { metric } },
      });
      await loadProgress();
    } catch {
      // silent
    }
  }

  if (!isProUser) return null;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
            Your Monthly Targets
          </div>
          <h3 className="text-[15px] font-bold text-white">
            What you want to hit this month
          </h3>
        </div>
        {!adding && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-1 text-[11px] font-semibold text-slate-300 transition-colors hover:border-white/[0.2] hover:text-white"
          >
            + Add target
          </button>
        )}
      </div>

      {adding && (
        <div className="mb-4 rounded-xl border border-white/[0.06] bg-white/[0.03] p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <select
              value={newMetric}
              onChange={(e) => setNewMetric(e.target.value)}
              className="rounded-md border border-white/[0.08] bg-[#0b0b14] px-2 py-1 text-[12px] text-slate-200"
            >
              <option value="monthly_revenue">Monthly revenue</option>
              <option value="monthly_orders">Monthly orders</option>
              <option value="aov">Average order value</option>
              <option value="cvr">Conversion rate</option>
            </select>
            <input
              type="number"
              value={newTarget}
              onChange={(e) => setNewTarget(e.target.value)}
              placeholder="Target value"
              className="w-32 rounded-md border border-white/[0.08] bg-[#0b0b14] px-2 py-1 text-[12px] text-slate-200 placeholder-slate-600"
            />
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="rounded-md bg-[#d4893a] px-3 py-1 text-[11px] font-bold text-white transition-colors hover:bg-[#e8a04e] disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              onClick={() => { setAdding(false); setSaveError(null); }}
              className="rounded-md px-3 py-1 text-[11px] text-slate-400 hover:text-slate-200"
            >
              Cancel
            </button>
          </div>
          {saveError && <div className="text-[10px] text-rose-400">{saveError}</div>}
        </div>
      )}

      {loading ? (
        <div className="animate-pulse space-y-2">
          <div className="h-10 rounded bg-white/[0.04]" />
          <div className="h-10 rounded bg-white/[0.04]" />
        </div>
      ) : progress.length === 0 ? (
        <div className="rounded-xl border border-dashed border-white/[0.10] bg-white/[0.015] p-4">
          <div className="mb-2 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.14em] text-slate-400">
            <span className="relative inline-flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/50" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
            </span>
            Sample preview
          </div>
          <div className="space-y-2 opacity-50">
            {[
              { label: "Monthly revenue", current: "$28.4K", target: "$35K", pct: 81, color: "#34d399" },
              { label: "Monthly orders", current: "412", target: "500", pct: 82, color: "#34d399" },
              { label: "Conversion rate", current: "2.1%", target: "3.0%", pct: 70, color: "#fbbf24" },
            ].map((p) => (
              <div key={p.label} className="rounded-xl border border-white/[0.04] bg-white/[0.015] p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-[12px] font-semibold text-slate-200">{p.label}</div>
                    <div className="mt-0.5 text-[10px] text-slate-400">
                      now <span className="font-mono tabular-nums text-slate-300">{p.current}</span>
                      <span className="mx-1">·</span>
                      target <span className="font-mono tabular-nums text-slate-300">{p.target}</span>
                    </div>
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                      <div className="h-full rounded-full" style={{ width: `${p.pct}%`, background: p.color }} />
                    </div>
                  </div>
                  <div className="text-[14px] font-bold tabular-nums" style={{ color: p.color }}>{p.pct}%</div>
                </div>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[12px] leading-relaxed text-slate-400">
            Add your first KPI target above to start tracking real progress with at-risk alerts and projected end-of-month values.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {progress.map((p) => {
            const color = statusColor(p.status);
            const pct = p.target_value > 0 ? Math.min(100, (p.current_value / p.target_value) * 100) : 0;
            return (
              <div key={p.metric} className="rounded-xl border border-white/[0.04] bg-white/[0.015] p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-[12px] font-semibold text-slate-200">
                      {METRIC_LABELS[p.metric] || p.metric}
                    </div>
                    <div className="mt-0.5 text-[10px] text-slate-400">
                      now <span className="font-mono tabular-nums text-slate-300">{fmtForMetric(p.metric, p.current_value, currency)}</span>
                      <span className="mx-1">·</span>
                      projected <span className="font-mono tabular-nums text-slate-300">{fmtForMetric(p.metric, p.projected_value, currency)}</span>
                      <span className="mx-1">·</span>
                      target <span className="font-mono tabular-nums text-slate-300">{fmtForMetric(p.metric, p.target_value, currency)}</span>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleDelete(p.metric)}
                    className="flex-shrink-0 text-[10px] text-slate-400 hover:text-rose-400"
                    title="Remove this target"
                  >
                    ✕
                  </button>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{ width: pct + "%", background: color }}
                  />
                </div>
                <div className="mt-1 text-[10px]" style={{ color }}>
                  {p.narrative}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
