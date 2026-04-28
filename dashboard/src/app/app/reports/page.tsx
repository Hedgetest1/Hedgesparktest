"use client";

/**
 * /app/reports — Reports Hub (Lite + Pro identical, all tiers).
 *
 * Two stacked sections:
 *   1. Standard reports — 6 fixed surfaces with reused ExportButton
 *   2. Custom reports   — saved reports list + "+ New report" CTA
 *
 * Voice: calm, merchant-friendly per founder direction 2026-04-28.
 * Surface: tier-agnostic chrome per
 *   `feedback_settings_is_tier_agnostic_chrome.md`. The same hub
 * renders for every tier; the only thing that varies is the user's
 * own list of saved reports.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { FloorLayout } from "../../components/FloorLayout";
import { ExportButton } from "../../components/ExportButton";
import { CardSkeleton, CardError, CardEmpty } from "../../components/_CardStates";
import { apiClient } from "../../lib/api-client";

type StandardSurface = {
  surface: string;
  title: string;
  description: string;
};

type SavedReport = {
  id: number;
  name: string;
  metric: string;
  dimensions: string[];
  date_range_preset: string;
  scheduled: boolean;
  scheduled_cadence: string | null;
  last_run_at: string | null;
  updated_at: string;
};

const SURFACE_TO_EXPORT_KEY: Record<string, string> = {
  rars: "rars",
  benchmarks: "benchmarks",
  benchmarks_vertical: "benchmarks_vertical",
  pnl: "pnl",
  cohorts_monthly: "cohorts_monthly",
  attribution: "attribution",
};

export default function ReportsHubPage() {
  return (
    <FloorLayout floor="reports">
      {() => <ReportsHubSurface />}
    </FloorLayout>
  );
}

function ReportsHubSurface() {
  return (
    <>
      <div className="mb-8">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] font-semibold text-slate-400">
          <Link href="/app" className="text-slate-400 hover:text-[#e8a04e]">
            ← Dashboard
          </Link>
          <span className="text-slate-600">/</span>
          <span className="text-slate-300">Reports</span>
        </div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
          Reports
        </h1>
        <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-slate-400">
          Every number from your store, exactly the way you need it.
          Use the prebuilt reports below or build your own from any
          metric and dimension.
        </p>
      </div>

      <StandardReportsSection />

      <div className="mt-10">
        <CustomReportsSection />
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Standard reports section
// ---------------------------------------------------------------------------

function StandardReportsSection() {
  const [data, setData] = useState<StandardSurface[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .GET("/merchant/reports/standard")
      .then(({ data, error: err }) => {
        if (cancelled) return;
        if (err || !data) {
          setError(true);
          return;
        }
        const d = data as { surfaces?: StandardSurface[] };
        setData(d.surfaces || []);
      })
      .catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, []);

  return (
    <section>
      <h2 className="mb-4 text-[18px] font-bold tracking-tight text-slate-200">
        Prebuilt reports
      </h2>
      {error ? (
        <CardError
          label="Reports list"
          message="We couldn't load the prebuilt reports list. The data behind each report is safe; this list will recover automatically."
        />
      ) : data === null ? (
        <CardSkeleton label="Loading prebuilt reports" />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.map((s) => (
            <div
              key={s.surface}
              className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5"
            >
              <div className="text-[15px] font-bold text-[#e8a04e]">
                {s.title}
              </div>
              <p className="mt-1 text-[12.5px] leading-relaxed text-slate-400">
                {s.description}
              </p>
              <div className="mt-4 flex items-center gap-2">
                <ExportButton
                  surface={SURFACE_TO_EXPORT_KEY[s.surface] as ExportSurface}
                  format="csv"
                  accentColor="#e8a04e"
                />
                <ExportButton
                  surface={SURFACE_TO_EXPORT_KEY[s.surface] as ExportSurface}
                  format="pdf"
                  accentColor="#e8a04e"
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// Inline type alias matching ExportButton's accepted surfaces. Kept
// here rather than imported because ExportButton's type is internal.
type ExportSurface =
  | "rars"
  | "benchmarks"
  | "benchmarks_vertical"
  | "pnl"
  | "cohorts_monthly"
  | "attribution";

// ---------------------------------------------------------------------------
// Custom reports section
// ---------------------------------------------------------------------------

function CustomReportsSection() {
  const [reports, setReports] = useState<SavedReport[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .GET("/merchant/reports")
      .then(({ data, error: err }) => {
        if (cancelled) return;
        if (err || !data) {
          setError(true);
          return;
        }
        const d = data as { reports?: SavedReport[] };
        setReports(d.reports || []);
      })
      .catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, []);

  return (
    <section>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-[18px] font-bold tracking-tight text-slate-200">
          Your custom reports
        </h2>
        <Link
          href="/app/reports/new"
          className="rounded-lg bg-[#e8a04e] px-4 py-2 text-[12px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-[#fbbf24]"
        >
          + New report
        </Link>
      </div>

      {error ? (
        <CardError
          label="Custom reports"
          message="We couldn't load your saved reports. Try the retry button or come back in a minute."
        />
      ) : reports === null ? (
        <CardSkeleton label="Loading custom reports" />
      ) : reports.length === 0 ? (
        <CardEmpty
          accent="amber"
          title="Build your first custom report"
          body="Pick a metric and a dimension, save it, and run it whenever you need."
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {reports.map((r) => (
            <div
              key={r.id}
              className="flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5"
            >
              <div className="text-[15px] font-bold text-slate-200">
                {r.name}
              </div>
              <div className="mt-1 text-[11.5px] text-slate-400">
                {r.metric} · {(r.dimensions || []).join(" × ") || "summary"} ·{" "}
                {r.date_range_preset.replace(/_/g, " ")}
                {r.scheduled && (
                  <>
                    {" · "}
                    <span className="font-bold text-emerald-300">
                      scheduled {r.scheduled_cadence}
                    </span>
                  </>
                )}
              </div>
              <div className="mt-3 text-[10.5px] text-slate-400">
                {r.last_run_at
                  ? `Last run ${new Date(r.last_run_at).toLocaleDateString()}`
                  : "Not run yet"}
              </div>
              <div className="mt-auto flex items-center gap-2 pt-4">
                <Link
                  href={`/app/reports/${r.id}`}
                  className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-1.5 text-[11px] font-semibold text-slate-200 hover:border-[#e8a04e]/40 hover:text-[#e8a04e]"
                >
                  View
                </Link>
                <Link
                  href={`/app/reports/${r.id}/edit`}
                  className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-1.5 text-[11px] font-semibold text-slate-200 hover:border-[#e8a04e]/40 hover:text-[#e8a04e]"
                >
                  Edit
                </Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
