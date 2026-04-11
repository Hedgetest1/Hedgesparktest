"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { apiClient, type paths } from "../lib/api-client";

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
      .catch(() => {});
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
    <div className="mb-4 rounded-xl border border-white/[0.04] bg-[#0e0e1a] p-4">
      <div className="flex items-start gap-3">
        {/* Spark */}
        <div className="flex-shrink-0 pt-0.5">
          <Image
            src="/branding/hedgespark/spark.png"
            alt="Spark"
            width={28}
            height={28}
            className="hs-float"
          />
        </div>

        <div className="min-w-0 flex-1">
          {/* Spark message */}
          <p className="text-[13px] text-slate-300">{message}</p>

          {/* Progress bar */}
          <div className="mt-3 flex items-center gap-3">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.04]">
              <div
                className="h-full rounded-full bg-[#d4893a] transition-all duration-1000"
                style={{ width: `${progress}%` }}
              />
            </div>
            <span className="flex-shrink-0 text-[10px] tabular-nums text-slate-600">
              {progress}%
            </span>
          </div>

          {/* Status line */}
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-slate-600">
            <span>{status.data_points.toLocaleString()} data points</span>
            {status.signals_active > 0 && (
              <span className="text-[#d4893a]">{status.signals_active} signal{status.signals_active !== 1 ? "s" : ""} active</span>
            )}
            {status.nudges_active > 0 && (
              <span className="text-emerald-400">{status.nudges_active} nudge{status.nudges_active !== 1 ? "s" : ""} measuring</span>
            )}
            <span>
              Confidence: {status.confidence}
            </span>
          </div>
        </div>

        {/* Live dot */}
        <div className="flex-shrink-0 pt-1">
          <div className="relative h-2 w-2">
            <div className="absolute inset-0 rounded-full bg-emerald-400" />
            <div className="absolute inset-0 animate-ping rounded-full bg-emerald-400/40" style={{ animationDuration: "2.5s" }} />
          </div>
        </div>
      </div>
    </div>
  );
}
