"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { apiClient, type paths } from "../lib/api-client";
import { reportFrontendError } from "../lib/error-reporter";

// Source of truth: GET /merchant/sip-status → MerchantSipStatusResponse.
type StatusData =
  paths["/merchant/sip-status"]["get"]["responses"]["200"]["content"]["application/json"];

const CONFIDENCE_TARGET = 500;

const SPARK_MESSAGES: Record<string, string> = {
  bootstrapping: "Watching your store. Building behavioral baselines for every product.",
  first_signals: "Found patterns in your traffic. Signals are appearing.",
  learning: "Intelligence is building. Signals get sharper every week.",
  active: "Running autonomously. Detecting, acting, and proving results.",
};

/**
 * SystemStatusBar — shows intelligence progress and system aliveness
 * during the first days of a merchant's experience.
 *
 * Visible when SIP confidence < "high" OR when < 7 days since first data.
 * Fetches from /ops/signal-count-week (public) and /merchant/sip-status (authenticated).
 */
export function SystemStatusBar({
  apiBase: _apiBase,
  shop,
}: {
  apiBase: string;
  shop: string;
}) {
  const [status, setStatus] = useState<StatusData | null>(null);

  useEffect(() => {
    if (!shop) return;

    apiClient
      .GET("/merchant/sip-status", { params: { query: { shop } } })
      .then((res) => {
        if (res.data != null) setStatus(res.data);
      })
      .catch((err: unknown) => {
        // Warming-progress bar is decorative; degrade silently for the user
        // but report to the self-healing pipeline so a broken sip-status
        // endpoint gets caught instead of rotting.
        const e = err as { name?: string; message?: string } | null;
        reportFrontendError({
          component: "SystemStatusBar",
          error_type: e?.name ?? "FetchError",
          message: e?.message ?? "Failed to fetch /merchant/sip-status",
          severity: "info",
        });
      });
  }, [shop]);

  if (!status) return null;

  // Hide when system is fully mature
  if (status.confidence === "high" && status.data_points > 5000) return null;

  const progress = Math.min(100, Math.round((status.data_points / CONFIDENCE_TARGET) * 100));
  const phase =
    status.data_points < 50
      ? "bootstrapping"
      : status.signals_active === 0
      ? "bootstrapping"
      : status.nudges_active > 0
      ? "active"
      : status.data_points >= CONFIDENCE_TARGET
      ? "learning"
      : "first_signals";

  const message = SPARK_MESSAGES[phase];

  return (
    <div className="relative mt-8 overflow-hidden rounded-3xl border border-white/[0.06] bg-gradient-to-br from-white/[0.025] via-transparent to-white/[0.02] px-6 py-7 sm:px-10 sm:py-9">
      {/* Brand gradient stripe on top — same visual language as the
          landing's premium zones. Signals "this is the heartbeat of
          your store, and it is alive." */}
      <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-[#7c3aed] via-[#c026d3] to-[#f97316]" />
      {/* Ambient glow in the corner — soft violet to match the
          "intelligence / brand" palette. */}
      <div className="pointer-events-none absolute -right-24 -top-24 h-[320px] w-[320px] rounded-full bg-[#d946ef]/[0.04] blur-[140px]" />
      {/* Live dot in the top-right, always visible. */}
      <div className="absolute right-6 top-6">
        <div className="relative h-2.5 w-2.5">
          <div className="absolute inset-0 rounded-full bg-emerald-400" />
          <div
            className="absolute inset-0 animate-ping rounded-full bg-emerald-400/40"
            style={{ animationDuration: "2.5s" }}
          />
        </div>
      </div>

      <div className="relative flex items-start gap-5">
        {/* Spark mascot — big, floating, brand-forward. */}
        <div className="flex-shrink-0">
          <Image
            src="/branding/hedgespark/spark.png"
            alt="Spark"
            width={72}
            height={72}
            className="hs-float"
          />
        </div>

        <div className="min-w-0 flex-1">
          <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.03] px-3 py-1">
            <span className="h-1.5 w-1.5 rounded-full bg-[#e8a04e] shadow-[0_0_8px_rgba(232,160,78,0.6)]" />
            <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-300">
              Spark status
            </span>
          </div>
          <h2 className="text-[1.5rem] font-extrabold leading-tight text-white sm:text-[1.75rem]">
            {message}
          </h2>

          {/* Progress bar with brand gradient fill — matches the
              landing's CTA gradients. */}
          <div className="mt-5 flex items-center gap-3">
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/[0.05]">
              <div
                className="h-full rounded-full bg-gradient-to-r from-[#7c3aed] via-[#c026d3] to-[#e8a04e] transition-all duration-1000"
                style={{ width: `${progress}%` }}
              />
            </div>
            <span className="flex-shrink-0 text-[11px] tabular-nums text-slate-400">
              {progress}%
            </span>
          </div>

          {/* Status meta line. */}
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
            <span>
              <b className="text-slate-300 tabular-nums">{status.data_points.toLocaleString()}</b>{" "}
              data points
            </span>
            {status.signals_active > 0 && (
              <span className="text-[#e8a04e]">
                <b className="tabular-nums">{status.signals_active}</b> signal
                {status.signals_active !== 1 ? "s" : ""} active
              </span>
            )}
            {status.nudges_active > 0 && (
              <span className="text-emerald-400">
                <b className="tabular-nums">{status.nudges_active}</b> nudge
                {status.nudges_active !== 1 ? "s" : ""} measuring
              </span>
            )}
            <span>
              Confidence:{" "}
              <b className="text-slate-300">{status.confidence}</b>
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
