"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";
import { FloorLayout } from "../../../components/FloorLayout";
import { CardSkeleton, CardError } from "../../../components/_CardStates";
import { apiClient } from "../../../lib/api-client";

const API_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "";

type DataRow = {
  label: string;
  value: number;
  pct_of_total: number | null;
  forecast_low: number | null;
  forecast_high: number | null;
  holdout_lift_eur: number | null;
  holdout_p_value: number | null;
  peer_percentile: number | null;
};

type ReportData = {
  report_id: number;
  metric: string;
  metric_label: string;
  metric_unit: string;
  dimensions: string[];
  range_label: string;
  rows: DataRow[];
  total: number;
  chart_type: "bar" | "pivot" | "scalar" | "line";
  forecast_horizon: number | null;
  notes: string[];
};

type SavedReport = {
  id: number;
  name: string;
  scheduled: boolean;
  scheduled_cadence: string | null;
};

export default function ReportViewerPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolved = use(params);
  const reportId = parseInt(resolved.id, 10);

  return (
    <FloorLayout floor="reports">
      {() => <ViewerSurface reportId={reportId} />}
    </FloorLayout>
  );
}

function fmtMoney(n: number, currency: string = "USD"): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(n);
  } catch {
    return `${currency} ${n.toFixed(0)}`;
  }
}

function fmtValue(n: number, unit: string): string {
  if (unit === "money") return fmtMoney(n);
  if (unit === "pct") return `${n.toFixed(1)}%`;
  return n.toFixed(0);
}

// Exported for testability — `[id]/__tests__/page.test.tsx` mounts this
// component directly with a known reportId so we can cover the 4
// chart_type render branches without going through the Next.js
// `use(params)` async unwrap (which is awkward to drive in vitest).
export function ViewerSurface({ reportId }: { reportId: number }) {
  const router = useRouter();
  const [meta, setMeta] = useState<SavedReport | null>(null);
  const [data, setData] = useState<ReportData | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiClient.GET("/merchant/reports/{report_id}", {
        params: { path: { report_id: reportId } },
      }),
      apiClient.GET("/merchant/reports/{report_id}/data", {
        params: { path: { report_id: reportId } },
      }),
    ])
      .then(([metaRes, dataRes]) => {
        if (cancelled) return;
        if (metaRes.error || !metaRes.data) {
          setError(true);
          return;
        }
        setMeta(metaRes.data as SavedReport);
        if (dataRes.data) {
          setData(dataRes.data as ReportData);
        }
      })
      .catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, [reportId]);

  async function handleDelete() {
    if (!confirm("Delete this report? You can undo within 30 days.")) return;
    setBusy(true);
    try {
      await apiClient.DELETE("/merchant/reports/{report_id}", {
        params: { path: { report_id: reportId } },
      });
      router.push("/app/reports");
    } catch {
      setBusy(false);
    }
  }

  async function handleSchedule(cadence: "daily" | "weekly" | null) {
    setBusy(true);
    try {
      const { data: updated } = await apiClient.POST(
        "/merchant/reports/{report_id}/schedule",
        {
          params: { path: { report_id: reportId } },
          body: { scheduled: cadence !== null, scheduled_cadence: cadence },
        }
      );
      if (updated) setMeta(updated as SavedReport);
    } finally {
      setBusy(false);
    }
  }

  function downloadCsv() {
    window.open(
      `${API_BASE}/analytics/export?surface=custom&report_id=${reportId}&format=csv`,
      "_blank"
    );
  }

  if (error) {
    return (
      <CardError
        label="Report"
        message="We couldn't load this report. It may have been deleted, or the data is taking a moment to come back."
        onRetry={() => router.refresh()}
      />
    );
  }
  if (!meta || !data) {
    return <CardSkeleton label="Loading your report" />;
  }

  return (
    <>
      <div className="mb-6">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] font-semibold text-slate-400">
          <Link href="/app" className="text-slate-400 hover:text-[#e8a04e]">
            ← Dashboard
          </Link>
          <span className="text-slate-600">/</span>
          <Link href="/app/reports" className="text-slate-400 hover:text-[#e8a04e]">
            Reports
          </Link>
          <span className="text-slate-600">/</span>
          <span className="text-slate-300">{meta.name}</span>
        </div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
          {meta.name}
        </h1>
        <p className="mt-2 text-[13.5px] leading-relaxed text-slate-400">
          {data.metric_label} · {data.dimensions.join(" × ") || "summary"} ·{" "}
          {data.range_label}
        </p>
      </div>

      {/* Summary card */}
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        {data.chart_type === "scalar" ? (
          <div className="text-center">
            <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
              {data.metric_label} · {data.range_label}
            </div>
            <div className="mt-3 text-[44px] font-extrabold leading-none tracking-tight text-[#e8a04e]">
              {fmtValue(data.total, data.metric_unit)}
            </div>
          </div>
        ) : (
          <div>
            <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
              {data.metric_label} · total in window
            </div>
            <div className="mt-1 text-[28px] font-extrabold tracking-tight text-[#e8a04e]">
              {fmtValue(data.total, data.metric_unit)}
            </div>

            <div className="mt-5 space-y-2">
              {data.rows.map((row, i) => {
                // Strict numeric check — the API may omit these keys
                // entirely on non-forecast rows. `!== null` would let
                // `undefined` through and crash `fmtValue` downstream.
                const isForecastRow =
                  typeof row.forecast_low === "number"
                  && typeof row.forecast_high === "number";
                const barColor = i === 0 && !isForecastRow ? "#e8a04e" : "rgba(148,163,184,0.55)";
                const pct = row.pct_of_total ?? 0;
                return (
                  <div
                    key={`${row.label}-${i}`}
                    className="rounded-xl border border-white/[0.05] bg-white/[0.015] px-4 py-3"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="truncate text-[13px] font-semibold text-slate-200">
                        {row.label}
                      </span>
                      <span className="flex-shrink-0 text-[12px] tabular-nums text-slate-300">
                        {fmtValue(row.value, data.metric_unit)}
                        {row.pct_of_total !== null && (
                          <span className="ml-2 text-slate-400">({pct.toFixed(0)}%)</span>
                        )}
                      </span>
                    </div>
                    {!isForecastRow && (
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.04]">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${Math.min(100, pct)}%`,
                            background: barColor,
                          }}
                        />
                      </div>
                    )}
                    {isForecastRow && (
                      <div className="mt-2 text-[11px] text-slate-400">
                        Range: {fmtValue(row.forecast_low!, data.metric_unit)} –{" "}
                        {fmtValue(row.forecast_high!, data.metric_unit)}
                      </div>
                    )}
                    {typeof row.holdout_lift_eur === "number" && typeof row.holdout_p_value === "number" && (
                      <div className="mt-2 text-[11px] text-emerald-300">
                        Holdout-measured lift:{" "}
                        {fmtMoney(row.holdout_lift_eur)} (p={row.holdout_p_value.toFixed(3)})
                      </div>
                    )}
                    {typeof row.peer_percentile === "number" && (
                      <div className="mt-1 text-[11px] text-violet-300">
                        Peer percentile: top {100 - row.peer_percentile}% of stores in your category
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Notes */}
      {data.notes.length > 0 && (
        <div className="mt-4 rounded-2xl border border-white/[0.05] bg-white/[0.01] p-4">
          <ul className="list-inside list-disc space-y-1 text-[12px] text-slate-400">
            {data.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Actions */}
      <div className="mt-6 flex flex-wrap items-center gap-2">
        <Link
          href={`/app/reports/${reportId}/edit`}
          className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-4 py-2 text-[12px] font-semibold text-slate-200 hover:border-[#e8a04e]/40 hover:text-[#e8a04e]"
        >
          Edit
        </Link>
        <button
          onClick={downloadCsv}
          className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-4 py-2 text-[12px] font-semibold text-slate-200 hover:border-[#e8a04e]/40 hover:text-[#e8a04e]"
        >
          Export CSV
        </button>
        {meta.scheduled ? (
          <button
            disabled={busy}
            onClick={() => handleSchedule(null)}
            className="rounded-lg border border-emerald-400/30 bg-emerald-500/[0.08] px-4 py-2 text-[12px] font-semibold text-emerald-300 hover:border-emerald-400/50 disabled:opacity-50"
          >
            Scheduled {meta.scheduled_cadence} — unschedule
          </button>
        ) : (
          <>
            <button
              disabled={busy}
              onClick={() => handleSchedule("daily")}
              className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-4 py-2 text-[12px] font-semibold text-slate-200 hover:border-[#e8a04e]/40 hover:text-[#e8a04e] disabled:opacity-50"
            >
              Schedule daily
            </button>
            <button
              disabled={busy}
              onClick={() => handleSchedule("weekly")}
              className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-4 py-2 text-[12px] font-semibold text-slate-200 hover:border-[#e8a04e]/40 hover:text-[#e8a04e] disabled:opacity-50"
            >
              Schedule weekly
            </button>
          </>
        )}
        <button
          disabled={busy}
          onClick={handleDelete}
          className="ml-auto rounded-lg border border-rose-400/30 bg-rose-500/[0.05] px-4 py-2 text-[12px] font-semibold text-rose-300 hover:border-rose-400/50 hover:bg-rose-500/[0.10] disabled:opacity-50"
        >
          Delete
        </button>
      </div>
    </>
  );
}
