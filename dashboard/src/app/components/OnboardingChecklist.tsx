"use client";

/**
 * OnboardingChecklist — truthful activation progress for new installs.
 *
 * Steps:
 *   1. App installed (from SetupStatus.checks)
 *   2. Storefront tracker active (from SetupStatus.checks)
 *   3. Store connected / webhook (from SetupStatus.checks)
 *   4. Purchase tracking pixel (from pixel_status fetch — CRITICAL)
 *   5. First visitor tracked (from dashboard overview)
 *   6. First insight generated (from signals)
 *   7. First weekly digest (computed from step 6)
 *   8. Pro plan active (optional)
 *
 * Visibility: hidden when all core steps (1-6) are complete.
 */

import { useCallback, useEffect, useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type StepState = "complete" | "in_progress" | "waiting" | "blocked";

type ChecklistStep = {
  key:         string;
  label:       string;
  state:       StepState;
  detail:      string;
  action?:     { label: string; onClick: () => void };
};

export type OnboardingData = {
  setupChecks: {
    merchant_exists:  boolean;
    install_active:   boolean;
    token_ok:         boolean;
    webhook_ok:       boolean;
    tracker_ok:       boolean;
    billing_active:   boolean;
    billing_plan:     string;
    billing_charge_pending: boolean;
  } | null;
  readiness:      string | null;
  totalVisitors:  number | null;
  signalCount:    number | null;
  overviewLoading: boolean;
};

type PixelStatus = {
  pixel_active: boolean;
  orders_from_pixel: number;
  pixel_code: string;
  instructions: string[];
};

// ---------------------------------------------------------------------------
// Step derivation
// ---------------------------------------------------------------------------

function deriveSteps(
  d: OnboardingData,
  pixelStatus: PixelStatus | null,
  onShowPixel: () => void,
): ChecklistStep[] {
  const c = d.setupChecks;
  const steps: ChecklistStep[] = [];

  // 1. Install confirmed
  if (!c) {
    steps.push({ key: "install", label: "App installed", state: "waiting", detail: "Checking installation status…" });
  } else if (!c.merchant_exists || !c.install_active) {
    steps.push({ key: "install", label: "App installed", state: "blocked", detail: "Install Hedge Spark from the Shopify App Store to get started." });
  } else {
    steps.push({ key: "install", label: "App installed", state: "complete", detail: "Your store is connected." });
  }

  // 2. Tracker active
  const installOk = c?.merchant_exists && c?.install_active && c?.token_ok;
  if (!c) {
    steps.push({ key: "tracker", label: "Storefront tracker active", state: "waiting", detail: "Checking tracker…" });
  } else if (!installOk) {
    steps.push({ key: "tracker", label: "Storefront tracker active", state: "blocked", detail: "Requires a working app installation first." });
  } else if (!c.tracker_ok) {
    steps.push({ key: "tracker", label: "Storefront tracker active", state: "in_progress", detail: "Tracker not detected — use the repair button above to fix it." });
  } else {
    steps.push({ key: "tracker", label: "Storefront tracker active", state: "complete", detail: "Tracking visitor behavior on your storefront." });
  }

  // 3. Lifecycle webhook
  if (!c) {
    steps.push({ key: "webhook", label: "Store connected", state: "waiting", detail: "Checking connection…" });
  } else if (!installOk) {
    steps.push({ key: "webhook", label: "Store connected", state: "blocked", detail: "Requires a working app installation first." });
  } else if (!c.webhook_ok) {
    steps.push({ key: "webhook", label: "Store connected", state: "in_progress", detail: "Lifecycle webhook not registered — use the repair button above." });
  } else {
    steps.push({ key: "webhook", label: "Store connected", state: "complete", detail: "Store lifecycle connected." });
  }

  // 4. Purchase tracking pixel — CRITICAL for revenue + attribution
  if (!pixelStatus) {
    steps.push({ key: "pixel", label: "Purchase tracking", state: "waiting", detail: "Checking purchase pixel…" });
  } else if (pixelStatus.pixel_active) {
    steps.push({ key: "pixel", label: "Purchase tracking", state: "complete", detail: `Active — ${pixelStatus.orders_from_pixel} order${pixelStatus.orders_from_pixel === 1 ? "" : "s"} captured.` });
  } else {
    steps.push({
      key: "pixel",
      label: "Purchase tracking",
      state: "in_progress",
      detail: "Add the checkout pixel so Hedge Spark can track revenue and attribute purchases.",
      action: { label: "Show setup guide", onClick: onShowPixel },
    });
  }

  // 5. First visitor tracked
  const trackingReady = c?.tracker_ok;
  if (d.totalVisitors === null && d.overviewLoading) {
    steps.push({ key: "visitor", label: "First visitor tracked", state: "waiting", detail: "Loading visitor data…" });
  } else if (!trackingReady) {
    steps.push({ key: "visitor", label: "First visitor tracked", state: "blocked", detail: "The tracker needs to be active first." });
  } else if ((d.totalVisitors ?? 0) === 0) {
    steps.push({ key: "visitor", label: "First visitor tracked", state: "in_progress", detail: "Tracker is live — waiting for your first visitor." });
  } else {
    steps.push({ key: "visitor", label: "First visitor tracked", state: "complete", detail: `${d.totalVisitors!.toLocaleString()} visitor${d.totalVisitors === 1 ? "" : "s"} tracked.` });
  }

  // 6. First signal
  if (d.signalCount === null) {
    steps.push({ key: "signal", label: "First insight generated", state: "waiting", detail: "Loading signals…" });
  } else if ((d.totalVisitors ?? 0) === 0) {
    steps.push({ key: "signal", label: "First insight generated", state: "blocked", detail: "Insights require visitor data." });
  } else if (d.signalCount === 0) {
    steps.push({ key: "signal", label: "First insight generated", state: "in_progress", detail: "Analyzing behavior — signals appear once there's enough data." });
  } else {
    steps.push({ key: "signal", label: "First insight generated", state: "complete", detail: `${d.signalCount} active signal${d.signalCount === 1 ? "" : "s"}.` });
  }

  // 7. Pro (optional)
  if (c) {
    if (c.billing_active && c.billing_plan === "pro") {
      steps.push({ key: "pro", label: "Pro plan active", state: "complete", detail: "Full AI intelligence unlocked." });
    } else if (c.billing_charge_pending) {
      steps.push({ key: "pro", label: "Pro plan active", state: "in_progress", detail: "Upgrade pending — waiting for Shopify confirmation." });
    } else {
      steps.push({ key: "pro", label: "Pro plan active", state: "waiting", detail: "Optional — upgrade to unlock AI-driven actions." });
    }
  }

  return steps;
}

// ---------------------------------------------------------------------------
// Visibility
// ---------------------------------------------------------------------------

function shouldShow(steps: ChecklistStep[], readiness: string | null): boolean {
  if (readiness === "degraded") return false;
  const coreSteps = steps.filter((s) => s.key !== "pro");
  const coreComplete = coreSteps.filter((s) => s.state === "complete").length;
  if (coreComplete === coreSteps.length) return false;
  return true;
}

// ---------------------------------------------------------------------------
// Step row
// ---------------------------------------------------------------------------

const STATE_STYLES: Record<StepState, { dot: string; text: string; detail: string }> = {
  complete:    { dot: "bg-emerald-400",          text: "text-slate-300",     detail: "text-slate-500" },
  in_progress: { dot: "bg-amber-400 animate-pulse", text: "text-amber-200",   detail: "text-amber-200/70" },
  waiting:     { dot: "bg-slate-600",            text: "text-slate-500",     detail: "text-slate-600" },
  blocked:     { dot: "bg-rose-400/60",          text: "text-rose-300/80",   detail: "text-rose-300/60" },
};

function StepRow({ step, isLast }: { step: ChecklistStep; isLast: boolean }) {
  const style = STATE_STYLES[step.state];
  return (
    <div className="flex items-start gap-3">
      <div className="flex flex-col items-center">
        <div className={`mt-1 h-2 w-2 flex-shrink-0 rounded-full ${style.dot}`} />
        {!isLast && <div className={`mt-1 w-px flex-1 min-h-[20px] ${step.state === "complete" ? "bg-emerald-400/20" : "bg-white/[0.06]"}`} />}
      </div>
      <div className="pb-3">
        <div className="flex items-center gap-2">
          <span className={`text-[12px] font-medium ${style.text}`}>
            {step.state === "complete" && (
              <svg className="mr-1 inline-block h-3 w-3 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
              </svg>
            )}
            {step.label}
          </span>
          {step.key === "pro" && step.state !== "complete" && (
            <span className="rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.1em] bg-violet-500/15 text-violet-400/70">Optional</span>
          )}
        </div>
        <p className={`mt-0.5 text-[11px] leading-[1.5] ${style.detail}`}>{step.detail}</p>
        {step.action && (
          <button
            onClick={step.action.onClick}
            className="mt-1.5 rounded-lg bg-amber-500/20 px-3 py-1 text-[11px] font-semibold text-amber-200 transition hover:bg-amber-500/30"
          >
            {step.action.label}
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pixel setup modal
// ---------------------------------------------------------------------------

function PixelSetupGuide({ pixelStatus, onClose }: { pixelStatus: PixelStatus; onClose: () => void }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(pixelStatus.pixel_code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [pixelStatus.pixel_code]);

  return (
    <div className="mt-3 rounded-xl border border-amber-400/20 bg-amber-500/[0.05] p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-[12px] font-semibold text-amber-200">Setup: Purchase Tracking Pixel</div>
        <button onClick={onClose} className="text-slate-500 hover:text-slate-300 text-[11px]">Close</button>
      </div>

      <div className="space-y-2 mb-3">
        {pixelStatus.instructions.map((step, i) => (
          <div key={i} className="flex items-start gap-2 text-[11px] text-slate-300">
            <span className="flex-shrink-0 mt-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-amber-500/20 text-[9px] font-bold text-amber-300">{i + 1}</span>
            <span>{step}</span>
          </div>
        ))}
      </div>

      <div className="relative">
        <pre className="max-h-32 overflow-auto rounded-lg bg-black/40 p-3 text-[10px] leading-relaxed text-slate-400 font-mono">
          {pixelStatus.pixel_code}
        </pre>
        <button
          onClick={handleCopy}
          className={`absolute top-2 right-2 rounded px-2 py-1 text-[10px] font-semibold transition ${
            copied
              ? "bg-emerald-500/30 text-emerald-300"
              : "bg-white/10 text-slate-400 hover:bg-white/20 hover:text-slate-200"
          }`}
        >
          {copied ? "Copied!" : "Copy code"}
        </button>
      </div>

      <p className="mt-2 text-[10px] text-slate-600">
        After saving, make a test purchase to verify. The status above will update automatically.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

export function OnboardingChecklist({ data }: { data: OnboardingData }) {
  const [collapsed, setCollapsed] = useState(false);
  const [showPixelGuide, setShowPixelGuide] = useState(false);
  const [pixelStatus, setPixelStatus] = useState<PixelStatus | null>(null);

  // Fetch pixel status
  useEffect(() => {
    if (!API_BASE) return;
    let active = true;

    async function checkPixel() {
      try {
        const res = await fetch(`${API_BASE}/setup/pixel-status`, {
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          cache: "no-store",
        });
        if (res.ok && active) {
          setPixelStatus(await res.json());
        }
      } catch { /* silent */ }
    }

    checkPixel();
    // Poll every 10 seconds until pixel is active
    const id = setInterval(() => {
      if (!pixelStatus?.pixel_active) checkPixel();
    }, 10000);
    return () => { active = false; clearInterval(id); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const steps = deriveSteps(data, pixelStatus, () => setShowPixelGuide(true));

  if (!shouldShow(steps, data.readiness)) return null;

  const coreSteps = steps.filter((s) => s.key !== "pro");
  const completedCore = coreSteps.filter((s) => s.state === "complete").length;
  const totalCore = coreSteps.length;
  const pct = totalCore > 0 ? Math.round((completedCore / totalCore) * 100) : 0;

  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex-shrink-0 rounded-lg bg-violet-500/15 p-1.5">
            <svg className="h-4 w-4 text-violet-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <div>
            <div className="text-[12px] font-semibold text-slate-300">Getting started</div>
            <div className="mt-0.5 text-[11px] text-slate-600">{completedCore}/{totalCore} steps complete</div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="hidden sm:flex items-center gap-2">
            <div className="h-1.5 w-24 overflow-hidden rounded-full bg-white/[0.06]">
              <div className="h-full rounded-full bg-emerald-400/70 transition-all duration-500" style={{ width: `${pct}%` }} />
            </div>
            <span className="text-[10px] text-slate-600">{pct}%</span>
          </div>
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="rounded p-1 text-slate-600 transition hover:text-slate-400"
          >
            <svg className={`h-3.5 w-3.5 transition-transform ${collapsed ? "" : "rotate-180"}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
            </svg>
          </button>
        </div>
      </div>

      {/* Steps */}
      {!collapsed && (
        <div className="mt-4">
          {steps.map((step, i) => (
            <StepRow key={step.key} step={step} isLast={i === steps.length - 1} />
          ))}
          {/* Pixel setup guide (expandable) */}
          {showPixelGuide && pixelStatus && !pixelStatus.pixel_active && (
            <PixelSetupGuide pixelStatus={pixelStatus} onClose={() => setShowPixelGuide(false)} />
          )}
        </div>
      )}
    </div>
  );
}
