"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { FloorLayout } from "../../../../components/FloorLayout";
import { CardError, CardSkeleton } from "../../../../components/_CardStates";
import { apiClient } from "../../../../lib/api-client";
import {
  ReportBuilderForm,
  type ReportBuilderInitial,
} from "../../../../components/ReportBuilderForm";

export default function EditReportPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolved = use(params);
  const reportId = parseInt(resolved.id, 10);

  return (
    <FloorLayout floor="reports">
      {() => <EditSurface reportId={reportId} />}
    </FloorLayout>
  );
}

function EditSurface({ reportId }: { reportId: number }) {
  const [initial, setInitial] = useState<ReportBuilderInitial | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .GET("/merchant/reports/{report_id}", {
        params: { path: { report_id: reportId } },
      })
      .then(({ data, error: err }) => {
        if (cancelled) return;
        if (err || !data) {
          setError(true);
          return;
        }
        const d = data as {
          id: number;
          name: string;
          metric: string;
          dimensions: string[];
          date_range_preset: string;
          formula: string | null;
          forecast_horizon: number | null;
        };
        setInitial({
          id: d.id,
          name: d.name,
          metric: d.metric,
          dimensions: d.dimensions || [],
          date_range_preset: d.date_range_preset,
          formula: d.formula,
          forecast_horizon: d.forecast_horizon,
        });
      })
      .catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, [reportId]);

  if (error) return <CardError label="Edit report" message="We couldn't load this report to edit." />;
  if (!initial) return <CardSkeleton label="Loading the report editor" />;

  return (
    <>
      <div className="mb-8">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] font-semibold text-slate-400">
          <Link href="/app" className="text-slate-400 hover:text-[#e8a04e]">
            ← Dashboard
          </Link>
          <span className="text-slate-600">/</span>
          <Link href="/app/reports" className="text-slate-400 hover:text-[#e8a04e]">
            Reports
          </Link>
          <span className="text-slate-600">/</span>
          <Link href={`/app/reports/${reportId}`} className="text-slate-400 hover:text-[#e8a04e]">
            {initial.name}
          </Link>
          <span className="text-slate-600">/</span>
          <span className="text-slate-300">Edit</span>
        </div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
          Edit report
        </h1>
      </div>

      <ReportBuilderForm initial={initial} />
    </>
  );
}
