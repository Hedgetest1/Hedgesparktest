"use client";

/**
 * ReportBuilderForm — shared wizard used by /app/reports/new and
 * /app/reports/[id]/edit. Calm, merchant-friendly voice per founder
 * direction 2026-04-28.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiClient } from "../lib/api-client";

const METRICS: Array<{ value: string; label: string; hint: string }> = [
  { value: "revenue", label: "Revenue", hint: "What you brought in." },
  { value: "orders", label: "Orders", hint: "Number of orders placed." },
  { value: "aov", label: "Average order value", hint: "Revenue ÷ orders." },
  { value: "conversion_rate", label: "Conversion rate", hint: "Visitors who bought." },
  { value: "refund_amount", label: "Refund amount", hint: "Money returned." },
  { value: "discount_amount", label: "Discount amount", hint: "Money given up to discounts." },
  { value: "tax_amount", label: "Tax amount", hint: "Tax collected." },
  { value: "repeat_rate", label: "Repeat-buyer rate", hint: "How many come back." },
  { value: "customer_ltv", label: "Customer LTV", hint: "Lifetime value per customer." },
  { value: "revenue_at_risk", label: "Revenue at Risk", hint: "Money currently leaking." },
  { value: "active_visitors", label: "Active visitors", hint: "Distinct visitors in window." },
  { value: "survey_response_top", label: "Top survey answer", hint: "From your post-purchase survey." },
];

const DIMENSIONS: Array<{ value: string; label: string }> = [
  { value: "time", label: "Time" },
  { value: "channel", label: "Channel" },
  { value: "country", label: "Country" },
  { value: "product", label: "Product" },
  { value: "customer_cohort", label: "First-purchase month" },
  { value: "discount_code", label: "Discount code" },
  { value: "payment_method", label: "Payment method" },
  { value: "hour_of_day", label: "Hour of day" },
  { value: "first_purchase_channel", label: "First-purchase channel" },
  { value: "survey_choice", label: "Survey answer" },
];

const PRESETS: Array<{ value: string; label: string }> = [
  { value: "today", label: "Today" },
  { value: "yesterday", label: "Yesterday" },
  { value: "last_7_days", label: "Last 7 days" },
  { value: "last_30_days", label: "Last 30 days" },
  { value: "last_90_days", label: "Last 90 days" },
  { value: "year_to_date", label: "Year to date" },
];

const FORECAST_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 30, label: "Where this is heading — next 30 days" },
  { value: 60, label: "Where this is heading — next 60 days" },
  { value: 90, label: "Where this is heading — next 90 days" },
];

export type ReportBuilderInitial = {
  id?: number;
  name: string;
  metric: string;
  dimensions: string[];
  date_range_preset: string;
  formula: string | null;
  forecast_horizon: number | null;
};

export const EMPTY_REPORT: ReportBuilderInitial = {
  name: "",
  metric: "revenue",
  dimensions: ["time"],
  date_range_preset: "last_30_days",
  formula: null,
  forecast_horizon: null,
};

export function ReportBuilderForm({ initial }: { initial: ReportBuilderInitial }) {
  const router = useRouter();
  const isEdit = typeof initial.id === "number";
  const [name, setName] = useState(initial.name);
  const [metric, setMetric] = useState(initial.metric);
  const [dimensions, setDimensions] = useState<string[]>(initial.dimensions);
  const [preset, setPreset] = useState(initial.date_range_preset);
  const [formula, setFormula] = useState(initial.formula || "");
  const [forecastHorizon, setForecastHorizon] = useState<number | null>(
    initial.forecast_horizon,
  );
  const [saving, setSaving] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  function toggleDimension(d: string) {
    setDimensions((prev) => {
      if (prev.includes(d)) return prev.filter((x) => x !== d);
      if (prev.length >= 2) return [prev[1], d]; // FIFO 2-cap
      return [...prev, d];
    });
  }

  async function save() {
    setErrorMsg(null);
    if (!name.trim()) {
      setErrorMsg("Give your report a name first.");
      return;
    }
    setSaving(true);
    try {
      const useFormula = formula.trim().length > 0;
      const body = {
        name: name.trim(),
        metric: useFormula ? "formula" : metric,
        dimensions,
        filters: {},
        date_range_preset: preset,
        compare_enabled: false,
        formula: useFormula ? formula.trim() : null,
        forecast_horizon: forecastHorizon,
      };
      let res;
      if (isEdit && initial.id) {
        res = await apiClient.PUT("/merchant/reports/{report_id}", {
          params: { path: { report_id: initial.id } },
          body,
        });
      } else {
        res = await apiClient.POST("/merchant/reports", { body });
      }
      const { data, error } = res;
      if (error || !data) {
        const detail =
          (error as { detail?: string } | null)?.detail ||
          "Save failed. Try again?";
        setErrorMsg(detail);
        setSaving(false);
        return;
      }
      const id = (data as { id: number }).id;
      router.push(`/app/reports/${id}`);
    } catch (err) {
      const e = err as { message?: string };
      setErrorMsg(e?.message || "Save failed. Try again?");
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      {/* Name */}
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <label className="block text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Report name
        </label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value.slice(0, 60))}
          maxLength={60}
          placeholder="e.g. Revenue by channel — last 30 days"
          className="mt-2 w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-2.5 text-[14px] text-slate-200 placeholder:text-slate-600 focus:border-[#e8a04e]/60 focus:outline-none"
        />
      </div>

      {/* Metric — picker; hidden when formula is in use */}
      {!formula.trim() && (
        <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
          <label className="block text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
            Metric
          </label>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {METRICS.map((m) => (
              <button
                key={m.value}
                onClick={() => setMetric(m.value)}
                className={`rounded-lg border px-3 py-2 text-left transition ${
                  metric === m.value
                    ? "border-[#e8a04e]/60 bg-[#e8a04e]/[0.08] text-[#e8a04e]"
                    : "border-white/[0.08] bg-white/[0.02] text-slate-300 hover:border-white/[0.16]"
                }`}
              >
                <div className="text-[13px] font-semibold">{m.label}</div>
                <div className="mt-0.5 text-[11px] text-slate-400">{m.hint}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Dimensions */}
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <label className="block text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Group by — pick up to 2
        </label>
        <div className="mt-3 flex flex-wrap gap-2">
          {DIMENSIONS.map((d) => {
            const selected = dimensions.includes(d.value);
            return (
              <button
                key={d.value}
                onClick={() => toggleDimension(d.value)}
                className={`rounded-full border px-3 py-1.5 text-[12px] transition ${
                  selected
                    ? "border-[#e8a04e]/60 bg-[#e8a04e]/[0.08] text-[#e8a04e]"
                    : "border-white/[0.1] bg-white/[0.03] text-slate-300 hover:border-white/[0.16]"
                }`}
              >
                {d.label}
              </button>
            );
          })}
        </div>
        <p className="mt-3 text-[11.5px] leading-relaxed text-slate-400">
          One group-by gives you a bar chart. Two gives you a pivot.
          Skip group-by entirely for a single big number.
        </p>
      </div>

      {/* Date range */}
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <label className="block text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Date range
        </label>
        <div className="mt-3 flex flex-wrap gap-2">
          {PRESETS.map((p) => (
            <button
              key={p.value}
              onClick={() => setPreset(p.value)}
              className={`rounded-full border px-3 py-1.5 text-[12px] transition ${
                preset === p.value
                  ? "border-[#e8a04e]/60 bg-[#e8a04e]/[0.08] text-[#e8a04e]"
                  : "border-white/[0.1] bg-white/[0.03] text-slate-300 hover:border-white/[0.16]"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Forecast — applies to revenue × time only; the API silently
          omits forecast lines for combinations not yet supported */}
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <label className="block text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Forecast
        </label>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            onClick={() => setForecastHorizon(null)}
            className={`rounded-full border px-3 py-1.5 text-[12px] transition ${
              forecastHorizon === null
                ? "border-[#e8a04e]/60 bg-[#e8a04e]/[0.08] text-[#e8a04e]"
                : "border-white/[0.1] bg-white/[0.03] text-slate-300"
            }`}
          >
            Off
          </button>
          {FORECAST_OPTIONS.map((f) => (
            <button
              key={f.value}
              onClick={() => setForecastHorizon(f.value)}
              className={`rounded-full border px-3 py-1.5 text-[12px] transition ${
                forecastHorizon === f.value
                  ? "border-[#e8a04e]/60 bg-[#e8a04e]/[0.08] text-[#e8a04e]"
                  : "border-white/[0.1] bg-white/[0.03] text-slate-300 hover:border-white/[0.16]"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <p className="mt-3 text-[11.5px] leading-relaxed text-slate-400">
          Forecast lines appear on revenue × time reports based on the
          last 90 days of orders. The shaded range is the 90% confidence
          band.
        </p>
      </div>

      {/* Custom formula */}
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <label className="block text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Custom formula (optional)
        </label>
        <input
          type="text"
          value={formula}
          onChange={(e) => setFormula(e.target.value.slice(0, 240))}
          maxLength={240}
          placeholder="(Revenue * 0.7) / Orders"
          className="mt-2 w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-2.5 font-mono text-[13px] text-slate-200 placeholder:text-slate-600 focus:border-[#e8a04e]/60 focus:outline-none"
        />
        <p className="mt-3 text-[11.5px] leading-relaxed text-slate-400">
          Use any metric name (Revenue, Orders, AOV, Refund_amount,
          Discount_amount, Tax_amount), plus +, -, *, /, and parentheses.
          Example: <span className="font-mono text-slate-300">(Revenue * 0.7) / Orders</span>
        </p>
      </div>

      {/* Save */}
      <div className="flex items-center justify-end gap-3">
        {errorMsg && (
          <span className="text-[12px] text-rose-300">{errorMsg}</span>
        )}
        <button
          onClick={save}
          disabled={saving}
          className="rounded-lg bg-[#e8a04e] px-5 py-2.5 text-[13px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-[#fbbf24] disabled:cursor-not-allowed disabled:opacity-40"
        >
          {saving ? "Saving…" : isEdit ? "Save changes" : "Save & view"}
        </button>
      </div>
    </div>
  );
}
