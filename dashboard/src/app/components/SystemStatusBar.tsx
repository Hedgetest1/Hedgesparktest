"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { apiClient, type paths } from "../lib/api-client";
import { reportFrontendError } from "../lib/error-reporter";

// Source of truth: GET /merchant/sip-status → MerchantSipStatusResponse.
type StatusData =
  paths["/merchant/sip-status"]["get"]["responses"]["200"]["content"]["application/json"];

// /pro/store-profile typed shape (Sprint 4 #7).
type StoreProfileView =
  paths["/pro/store-profile"]["get"]["responses"]["200"]["content"]["application/json"];

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
  const [profile, setProfile] = useState<StoreProfileView | null>(null);

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

    // Sprint 4 #7 — /pro/store-profile extension. Pro-gated; for Lite
    // users the call returns 401/403 and we silently skip the extra
    // line. No noise: tier mismatch on a Pro endpoint is expected
    // for Lite, not an error worth reporting.
    apiClient
      .GET("/pro/store-profile")
      .then((res) => {
        if (res.data != null) setProfile(res.data);
      })
      .catch(() => {
        // No-op: store-profile is optional warming-bar enrichment;
        // any failure (network, 401 for Lite) is silent by design.
      });
  }, [shop]);

  // Cold-start / loading → render a simple "watching" placeholder so
  // the merchant sees the system is alive even before /merchant/sip-status
  // returns a payload. Pre-fix this returned null silently.
  if (!status) {
    return (
      <div className="relative mt-8 overflow-hidden rounded-3xl border border-dashed border-white/[0.10] px-6 py-5 sm:px-10">
        <div className="flex items-center gap-3">
          <span className="relative inline-flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
          </span>
          <div className="text-[12px] font-semibold text-slate-300">
            Watching your store · building behavioral baselines
          </div>
          <div className="ml-auto text-[10px] font-mono uppercase tracking-[0.14em] text-slate-400">
            warming up
          </div>
        </div>
      </div>
    );
  }

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
          {/* Unified heading: ONE big amber H2, no separate eyebrow. */}
          <h2 className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight text-[#e8a04e] sm:text-[2rem]">
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
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-400">
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

          {/* Sprint 4 #7 — Pro learning-engine line. Vertical + trust +
              autonomy + blended cart_rate (when prior is applied).
              Only renders for Pro merchants (profile loaded → fetch
              succeeded → tier check passed at the API boundary). */}
          {profile != null && (
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-400">
              {profile.vertical_prior != null && (
                <span className="text-violet-300">
                  <b>{profile.vertical_prior.vertical_display}</b> vertical
                </span>
              )}
              <span>
                Trust{" "}
                <b className="text-slate-300 tabular-nums">{profile.trust_score.toFixed(2)}</b>
              </span>
              <span>
                Autonomy{" "}
                <b className="text-slate-300 tabular-nums">
                  lvl {profile.autonomy_level} / 5
                </b>
              </span>
              {profile.vertical_prior?.applied &&
                profile.vertical_prior.blended_cart_rate != null && (
                  <span className="text-amber-300">
                    Vertical prior applied{" "}
                    <span className="tabular-nums">
                      (shrinkage to {(profile.vertical_prior.blended_cart_rate * 100).toFixed(1)}%)
                    </span>
                  </span>
                )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
