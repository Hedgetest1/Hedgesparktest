"use client";

/**
 * OnboardingChecklist — truthful activation progress for new installs.
 *
 * Derives all step states from data the page already fetches:
 *   - Steps 1-3 (install, tracker, webhook) from SetupStatus.checks
 *   - Step 4 (first visitor) from dashboard overview summary.total_visitors
 *   - Step 5 (first signal) from opportunity signals array length
 *   - Step 6 (Pro active) from SetupStatus.checks.billing_active
 *
 * No new backend endpoints. No fake progress.
 *
 * Visibility rules:
 *   - Hidden when all core steps (1-5) are complete — merchant is activated
 *   - Hidden when setup is degraded — SetupStatusPanel handles that
 *   - Auto-collapses after 4+ steps done so it's not in the way
 */

import { useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type StepState = "complete" | "in_progress" | "waiting" | "blocked";

type ChecklistStep = {
  key:         string;
  label:       string;
  state:       StepState;
  detail:      string;
};

export type OnboardingData = {
  /** From SetupStatus.checks — null if setup status hasn't loaded yet */
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
  /** From SetupStatus.readiness */
  readiness:      string | null;
  /** From dashboard overview — null if overview hasn't loaded yet */
  totalVisitors:  number | null;
  /** From signals array — null if signals haven't loaded yet */
  signalCount:    number | null;
  /** True while dashboard overview is still loading */
  overviewLoading: boolean;
};

// ---------------------------------------------------------------------------
// Step derivation — pure function, no API calls
// ---------------------------------------------------------------------------

function deriveSteps(d: OnboardingData): ChecklistStep[] {
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

  // 3. Lifecycle webhook (app/uninstalled) + revenue tracking
  if (!c) {
    steps.push({ key: "webhook", label: "Store connected", state: "waiting", detail: "Checking connection…" });
  } else if (!installOk) {
    steps.push({ key: "webhook", label: "Store connected", state: "blocked", detail: "Requires a working app installation first." });
  } else if (!c.webhook_ok) {
    steps.push({ key: "webhook", label: "Store connected", state: "in_progress", detail: "Lifecycle webhook not registered — use the repair button above to fix it." });
  } else {
    steps.push({ key: "webhook", label: "Store connected", state: "complete", detail: "Lifecycle webhook active. Revenue tracked via checkout pixel." });
  }

  // 4. First visitor tracked
  const trackingReady = c?.tracker_ok;
  if (d.totalVisitors === null && d.overviewLoading) {
    steps.push({ key: "visitor", label: "First visitor tracked", state: "waiting", detail: "Loading visitor data…" });
  } else if (!trackingReady) {
    steps.push({ key: "visitor", label: "First visitor tracked", state: "blocked", detail: "The storefront tracker needs to be active before visitors can be tracked." });
  } else if ((d.totalVisitors ?? 0) === 0) {
    steps.push({ key: "visitor", label: "First visitor tracked", state: "in_progress", detail: "Tracker is live — waiting for your first storefront visitor. This usually happens within minutes." });
  } else {
    steps.push({ key: "visitor", label: "First visitor tracked", state: "complete", detail: `${d.totalVisitors!.toLocaleString()} visitor${d.totalVisitors === 1 ? "" : "s"} tracked so far.` });
  }

  // 5. First signal generated
  if (d.signalCount === null) {
    steps.push({ key: "signal", label: "First insight generated", state: "waiting", detail: "Loading signals…" });
  } else if ((d.totalVisitors ?? 0) === 0) {
    steps.push({ key: "signal", label: "First insight generated", state: "blocked", detail: "Insights require visitor data. Once visitors arrive, signals follow." });
  } else if (d.signalCount === 0) {
    steps.push({ key: "signal", label: "First insight generated", state: "in_progress", detail: "Analyzing visitor behavior — your first actionable signal will appear once there's enough data." });
  } else {
    steps.push({ key: "signal", label: "First insight generated", state: "complete", detail: `${d.signalCount} active signal${d.signalCount === 1 ? "" : "s"} detected.` });
  }

  // 6. First weekly digest
  const signalsDone = d.signalCount !== null && d.signalCount > 0;
  if (signalsDone) {
    // Compute next Monday at 8AM UTC
    const now = new Date();
    const dayOfWeek = now.getUTCDay(); // 0=Sun
    const daysUntilMonday = dayOfWeek === 0 ? 1 : dayOfWeek === 1 ? 7 : 8 - dayOfWeek;
    const nextMonday = new Date(now);
    nextMonday.setUTCDate(now.getUTCDate() + daysUntilMonday);
    nextMonday.setUTCHours(8, 0, 0, 0);
    // If it's Monday before 8AM UTC, use today
    if (dayOfWeek === 1 && now.getUTCHours() < 8) {
      nextMonday.setUTCDate(now.getUTCDate());
    }
    const dateStr = nextMonday.toLocaleDateString("en-US", {
      weekday: "long", month: "short", day: "numeric",
    });
    steps.push({
      key: "digest",
      label: "First weekly digest",
      state: "in_progress",
      detail: `Your first revenue intelligence report arrives ${dateStr} at 8:00 AM UTC.`,
    });
  } else if ((d.totalVisitors ?? 0) > 0) {
    steps.push({
      key: "digest",
      label: "First weekly digest",
      state: "waiting",
      detail: "After your first signals are generated, your weekly digest will be scheduled.",
    });
  }

  // 7. Pro active (optional milestone — never blocks anything)
  if (!c) {
    // skip if setup not loaded
  } else if (c.billing_active && c.billing_plan === "pro") {
    steps.push({ key: "pro", label: "Pro plan active", state: "complete", detail: "Full AI actions, daily briefs, and market intelligence unlocked." });
  } else if (c.billing_charge_pending) {
    steps.push({ key: "pro", label: "Pro plan active", state: "in_progress", detail: "Upgrade pending — waiting for Shopify billing confirmation." });
  } else {
    steps.push({ key: "pro", label: "Pro plan active", state: "waiting", detail: "Optional — upgrade anytime to unlock AI-driven actions per product." });
  }

  return steps;
}

// ---------------------------------------------------------------------------
// Visibility logic
// ---------------------------------------------------------------------------

function shouldShow(steps: ChecklistStep[], readiness: string | null): boolean {
  // Don't show if setup is degraded — SetupStatusPanel handles that
  if (readiness === "degraded") return false;
  // Count core steps (exclude "pro" which is optional)
  const coreSteps = steps.filter((s) => s.key !== "pro" && s.key !== "digest");
  const coreComplete = coreSteps.filter((s) => s.state === "complete").length;
  // Hide when all core steps are done — merchant is fully activated
  if (coreComplete === coreSteps.length) return false;
  return true;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const STATE_STYLES: Record<StepState, { dot: string; text: string; detail: string }> = {
  complete:    { dot: "bg-emerald-400",          text: "text-slate-300",     detail: "text-slate-500" },
  in_progress: { dot: "bg-amber-400 animate-pulse", text: "text-amber-200",   detail: "text-amber-200/70" },
  waiting:     { dot: "bg-slate-600",            text: "text-slate-500",     detail: "text-slate-600" },
  blocked:     { dot: "bg-rose-400/60",          text: "text-rose-300/80",   detail: "text-rose-300/60" },
};

function StepRow({ step, index, isLast }: { step: ChecklistStep; index: number; isLast: boolean }) {
  const style = STATE_STYLES[step.state];

  return (
    <div className="flex items-start gap-3">
      {/* Vertical timeline connector + dot */}
      <div className="flex flex-col items-center">
        <div className={`mt-1 h-2 w-2 flex-shrink-0 rounded-full ${style.dot}`} />
        {!isLast && (
          <div className={`mt-1 w-px flex-1 min-h-[20px] ${
            step.state === "complete" ? "bg-emerald-400/20" : "bg-white/[0.06]"
          }`} />
        )}
      </div>
      {/* Content */}
      <div className={`pb-3 ${isLast ? "" : ""}`}>
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
            <span className="rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.1em] bg-violet-500/15 text-violet-400/70">
              Optional
            </span>
          )}
        </div>
        <p className={`mt-0.5 text-[11px] leading-[1.5] ${style.detail}`}>
          {step.detail}
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function OnboardingChecklist({ data }: { data: OnboardingData }) {
  const steps = deriveSteps(data);
  const [collapsed, setCollapsed] = useState(false);

  if (!shouldShow(steps, data.readiness)) return null;

  const coreSteps = steps.filter((s) => s.key !== "pro" && s.key !== "digest");
  const completedCore = coreSteps.filter((s) => s.state === "complete").length;
  const totalCore = coreSteps.length;
  const pct = totalCore > 0 ? Math.round((completedCore / totalCore) * 100) : 0;

  // Auto-suggest collapse once most steps are done
  const mostlyDone = completedCore >= 4;

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
            <div className="text-[12px] font-semibold text-slate-300">
              Getting started
            </div>
            <div className="mt-0.5 text-[11px] text-slate-600">
              {completedCore}/{totalCore} steps complete
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {/* Progress bar */}
          <div className="hidden sm:flex items-center gap-2">
            <div className="h-1.5 w-24 overflow-hidden rounded-full bg-white/[0.06]">
              <div
                className="h-full rounded-full bg-emerald-400/70 transition-all duration-500"
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="text-[10px] text-slate-600">{pct}%</span>
          </div>
          {/* Collapse toggle */}
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="rounded p-1 text-slate-600 transition hover:text-slate-400"
            aria-label={collapsed ? "Expand checklist" : "Collapse checklist"}
          >
            <svg
              className={`h-3.5 w-3.5 transition-transform ${collapsed ? "" : "rotate-180"}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
            </svg>
          </button>
        </div>
      </div>

      {/* Step list */}
      {!collapsed && (
        <div className="mt-4">
          {steps.map((step, i) => (
            <StepRow key={step.key} step={step} index={i} isLast={i === steps.length - 1} />
          ))}
          {mostlyDone && (
            <p className="mt-2 text-[11px] text-slate-600">
              Almost there — your store is nearly fully activated.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
