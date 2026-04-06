"use client";

/**
 * FirstValueTimeline — the "you're not alone" empty state.
 *
 * A merchant who lands on a dashboard showing "no data" and no timeline
 * will leave. This component turns an empty state into a promise: here's
 * exactly when value starts arriving, and where you are on that timeline
 * right now.
 *
 * Three milestones, drawn left-to-right:
 *
 *   First visitors          ~minutes
 *   First insights          ~10 minutes
 *   Full analysis           ~24 hours
 *
 * The active milestone glows. Already-completed milestones show a check.
 * Upcoming milestones are muted. The merchant knows *exactly* where they
 * are and *exactly* when the next step lands.
 */

import Image from "next/image";

export type FirstValueStage = "setup" | "visitors" | "insights" | "full";

export type FirstValueTimelineProps = {
  /** Which milestone is currently active. */
  stage: FirstValueStage;
  /** Optional message override — otherwise uses the stage default. */
  message?: string;
};

const STAGES: {
  key: FirstValueStage;
  label: string;
  eta: string;
  detail: string;
}[] = [
  { key: "visitors", label: "First visitors", eta: "~minutes", detail: "Your tracker is active and watching." },
  { key: "insights", label: "First insights", eta: "~10 minutes", detail: "We'll surface the first opportunities." },
  { key: "full", label: "Full analysis", eta: "~24 hours", detail: "Revenue-at-risk, funnels, forecasts live." },
];

const STAGE_MESSAGES: Record<FirstValueStage, string> = {
  setup: "We're getting ready — finish setup to start receiving visitors.",
  visitors: "Your store is live. We're watching for your first visitors.",
  insights: "Visitors arriving. First insights in about 10 minutes.",
  full: "Full analysis is active. Spark is working for your store.",
};

function stageIndex(stage: FirstValueStage): number {
  if (stage === "setup") return -1;
  return STAGES.findIndex((s) => s.key === stage);
}

export default function FirstValueTimeline({ stage, message }: FirstValueTimelineProps) {
  const activeIdx = stageIndex(stage);
  const headline = message ?? STAGE_MESSAGES[stage];

  return (
    <section className="overflow-hidden rounded-2xl border border-white/[0.08] bg-gradient-to-br from-violet-500/[0.05] via-transparent to-transparent p-6 shadow-[0_0_48px_rgba(124,58,237,0.06)]">
      <div className="mb-5 flex items-start gap-4">
        <div className="relative flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500/20 to-violet-600/5 ring-1 ring-violet-400/20">
          <Image
            src="/branding/hedgespark/spark.png"
            alt=""
            width={36}
            height={36}
            className="hs-float"
            priority
          />
        </div>
        <div className="flex-1">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-violet-300/80">
            Collecting data
          </div>
          <p className="text-[15px] leading-relaxed text-slate-100">{headline}</p>
        </div>
      </div>

      {/* Timeline */}
      <div className="relative">
        {/* Connector line */}
        <div className="absolute left-0 right-0 top-3 h-px bg-gradient-to-r from-violet-500/40 via-white/[0.08] to-white/[0.04]" />
        <ol className="relative grid grid-cols-3 gap-4">
          {STAGES.map((s, i) => {
            const state: "done" | "active" | "upcoming" =
              i < activeIdx ? "done" : i === activeIdx ? "active" : "upcoming";
            return (
              <li key={s.key} className="flex flex-col items-start gap-2">
                {/* Dot */}
                <div
                  className={[
                    "relative flex h-6 w-6 items-center justify-center rounded-full border-2 text-[10px] font-bold",
                    state === "done"
                      ? "border-emerald-400/40 bg-emerald-500/20 text-emerald-300"
                      : state === "active"
                      ? "border-violet-400 bg-violet-500/25 text-violet-100 shadow-[0_0_20px_rgba(124,58,237,0.5)]"
                      : "border-white/[0.12] bg-[#0c0c18] text-slate-600",
                  ].join(" ")}
                >
                  {state === "done" ? (
                    <svg className="h-3 w-3" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M2.5 6 L5 8.5 L9.5 3.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  ) : state === "active" ? (
                    <span className="hs-pulse inline-block h-2 w-2 rounded-full bg-violet-300" />
                  ) : (
                    i + 1
                  )}
                </div>
                <div>
                  <div
                    className={[
                      "text-[13px] font-semibold",
                      state === "upcoming" ? "text-slate-500" : "text-slate-100",
                    ].join(" ")}
                  >
                    {s.label}
                  </div>
                  <div
                    className={[
                      "text-[11px] font-medium",
                      state === "active"
                        ? "text-violet-300"
                        : state === "done"
                        ? "text-emerald-300/80"
                        : "text-slate-600",
                    ].join(" ")}
                  >
                    {s.eta}
                  </div>
                  <div className="mt-1 text-[11px] leading-snug text-slate-400">{s.detail}</div>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
