"use client";

import Link from "next/link";
import { FloorLayout } from "../../../components/FloorLayout";
import { ReportBuilderForm, EMPTY_REPORT } from "../../../components/ReportBuilderForm";

export default function NewReportPage() {
  return (
    <FloorLayout floor="reports">
      {() => (
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
              <span className="text-slate-300">New report</span>
            </div>
            <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
              Build a report
            </h1>
            <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-slate-400">
              Pick a metric, group it the way you want, and save it. You
              can edit, schedule, or export it any time after.
            </p>
          </div>

          <ReportBuilderForm initial={EMPTY_REPORT} />
        </>
      )}
    </FloorLayout>
  );
}
