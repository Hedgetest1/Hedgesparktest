"use client";

/**
 * OnboardingChecklist — guided activation progress for new installs.
 *
 * Steps:
 *   1. App installed (auto)
 *   2. Storefront tracker active (auto)
 *   3. Store connected / webhook (auto)
 *   4. Purchase tracking pixel (manual — with visual walkthrough)
 *   5. First visitor tracked (auto)
 *   6. First insight generated (auto)
 *   7. Pro plan (optional)
 */

import { useCallback, useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type StepState = "complete" | "in_progress" | "waiting" | "blocked";

type ChecklistStep = {
  key: string;
  label: string;
  state: StepState;
  detail: string;
  action?: { label: string; onClick: () => void };
};

export type OnboardingData = {
  setupChecks: {
    merchant_exists: boolean;
    install_active: boolean;
    token_ok: boolean;
    webhook_ok: boolean;
    tracker_ok: boolean;
    billing_active: boolean;
    billing_plan: string;
    billing_charge_pending: boolean;
  } | null;
  readiness: string | null;
  totalVisitors: number | null;
  signalCount: number | null;
  overviewLoading: boolean;
};

type PixelStatus = {
  pixel_active: boolean;
  orders_from_pixel: number;
  purchase_events: number;
  pixel_code: string;
  instructions: string[];
};

// ---------------------------------------------------------------------------
// Step derivation
// ---------------------------------------------------------------------------

function deriveSteps(
  d: OnboardingData,
  ps: PixelStatus | null,
  onShowPixel: () => void,
): ChecklistStep[] {
  const c = d.setupChecks;
  const steps: ChecklistStep[] = [];
  const installOk = c?.merchant_exists && c?.install_active && c?.token_ok;

  // 1. Install
  steps.push(
    !c ? { key: "install", label: "App installed", state: "waiting", detail: "Checking…" }
    : (!c.merchant_exists || !c.install_active)
      ? { key: "install", label: "App installed", state: "blocked", detail: "Install from the Shopify App Store." }
      : { key: "install", label: "App installed", state: "complete", detail: "Store connected." }
  );

  // 2. Tracker
  steps.push(
    !c ? { key: "tracker", label: "Visitor tracking", state: "waiting", detail: "Checking…" }
    : !installOk ? { key: "tracker", label: "Visitor tracking", state: "blocked", detail: "Requires app installation." }
    : !c.tracker_ok ? { key: "tracker", label: "Visitor tracking", state: "in_progress", detail: "Not detected — use repair above." }
    : { key: "tracker", label: "Visitor tracking", state: "complete", detail: "Tracking storefront behavior." }
  );

  // 3. Webhook
  steps.push(
    !c ? { key: "webhook", label: "Store connected", state: "waiting", detail: "Checking…" }
    : !installOk ? { key: "webhook", label: "Store connected", state: "blocked", detail: "Requires app installation." }
    : !c.webhook_ok ? { key: "webhook", label: "Store connected", state: "in_progress", detail: "Webhook missing — use repair above." }
    : { key: "webhook", label: "Store connected", state: "complete", detail: "Lifecycle webhook active." }
  );

  // 4. Purchase pixel — CRITICAL
  if (!ps) {
    steps.push({ key: "pixel", label: "Purchase tracking", state: "waiting", detail: "Checking…" });
  } else if (ps.pixel_active) {
    steps.push({ key: "pixel", label: "Purchase tracking", state: "complete",
      detail: `Active — ${ps.orders_from_pixel} order${ps.orders_from_pixel === 1 ? "" : "s"} captured.` });
  } else {
    steps.push({
      key: "pixel", label: "Purchase tracking", state: "in_progress",
      detail: "Required to track revenue and attribute purchases to traffic sources.",
      action: { label: "Open setup guide", onClick: onShowPixel },
    });
  }

  // 5. First visitor
  steps.push(
    (d.totalVisitors === null && d.overviewLoading) ? { key: "visitor", label: "First visitor", state: "waiting", detail: "Loading…" }
    : !c?.tracker_ok ? { key: "visitor", label: "First visitor", state: "blocked", detail: "Tracker needs to be active." }
    : (d.totalVisitors ?? 0) === 0 ? { key: "visitor", label: "First visitor", state: "in_progress", detail: "Waiting for first storefront visit." }
    : { key: "visitor", label: "First visitor", state: "complete", detail: `${d.totalVisitors!.toLocaleString()} visitor${d.totalVisitors === 1 ? "" : "s"} tracked.` }
  );

  // 6. First signal
  steps.push(
    d.signalCount === null ? { key: "signal", label: "First insight", state: "waiting", detail: "Loading…" }
    : (d.totalVisitors ?? 0) === 0 ? { key: "signal", label: "First insight", state: "blocked", detail: "Needs visitor data." }
    : d.signalCount === 0 ? { key: "signal", label: "First insight", state: "in_progress", detail: "Analyzing behavior…" }
    : { key: "signal", label: "First insight", state: "complete", detail: `${d.signalCount} signal${d.signalCount === 1 ? "" : "s"} detected.` }
  );

  // 7. Pro (optional)
  if (c) {
    steps.push(
      (c.billing_active && c.billing_plan === "pro") ? { key: "pro", label: "Pro plan", state: "complete", detail: "Full AI intelligence unlocked." }
      : c.billing_charge_pending ? { key: "pro", label: "Pro plan", state: "in_progress", detail: "Upgrade pending." }
      : { key: "pro", label: "Pro plan", state: "waiting", detail: "Optional — unlock AI actions." }
    );
  }

  return steps;
}

function shouldShow(steps: ChecklistStep[], readiness: string | null): boolean {
  if (readiness === "degraded") return false;
  const core = steps.filter((s) => s.key !== "pro");
  return core.some((s) => s.state !== "complete");
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const S: Record<StepState, { dot: string; text: string; detail: string }> = {
  complete:    { dot: "bg-emerald-400",               text: "text-slate-300",   detail: "text-slate-500" },
  in_progress: { dot: "bg-amber-400 animate-pulse",   text: "text-amber-200",   detail: "text-amber-200/70" },
  waiting:     { dot: "bg-slate-600",                  text: "text-slate-500",   detail: "text-slate-600" },
  blocked:     { dot: "bg-rose-400/60",                text: "text-rose-300/80", detail: "text-rose-300/60" },
};

function StepRow({ step, isLast }: { step: ChecklistStep; isLast: boolean }) {
  const s = S[step.state];
  return (
    <div className="flex items-start gap-3">
      <div className="flex flex-col items-center">
        <div className={`mt-1 h-2 w-2 flex-shrink-0 rounded-full ${s.dot}`} />
        {!isLast && <div className={`mt-1 w-px flex-1 min-h-[20px] ${step.state === "complete" ? "bg-emerald-400/20" : "bg-white/[0.06]"}`} />}
      </div>
      <div className="pb-3">
        <div className="flex items-center gap-2">
          <span className={`text-[12px] font-medium ${s.text}`}>
            {step.state === "complete" && <Check />}
            {step.label}
          </span>
          {step.key === "pro" && step.state !== "complete" && (
            <span className="rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.1em] bg-violet-500/15 text-violet-400/70">Optional</span>
          )}
        </div>
        <p className={`mt-0.5 text-[11px] leading-[1.5] ${s.detail}`}>{step.detail}</p>
        {step.action && (
          <button onClick={step.action.onClick}
            className="mt-1.5 rounded-lg bg-amber-500/20 px-3 py-1.5 text-[11px] font-semibold text-amber-200 transition hover:bg-amber-500/30">
            {step.action.label}
          </button>
        )}
      </div>
    </div>
  );
}

function Check() {
  return <svg className="mr-1 inline-block h-3 w-3 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}><path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" /></svg>;
}

// ---------------------------------------------------------------------------
// Visual Pixel Walkthrough
// ---------------------------------------------------------------------------

function PixelWalkthrough({ pixelStatus, onClose }: { pixelStatus: PixelStatus; onClose: () => void }) {
  const [activeStep, setActiveStep] = useState(0);
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    const code = pixelStatus.pixel_code;
    const onSuccess = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(code).then(onSuccess).catch(() => {
        try {
          const ta = document.createElement("textarea");
          ta.value = code;
          ta.style.position = "fixed";
          ta.style.opacity = "0";
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
          onSuccess();
        } catch { /* clipboard unavailable */ }
      });
    }
  }, [pixelStatus.pixel_code]);

  const STEPS = [
    {
      title: "Open Shopify Settings",
      desc: "Go to your Shopify Admin and click Settings (bottom-left gear icon).",
      visual: (
        <MockShopifyPanel>
          <div className="flex items-center gap-2 rounded-lg bg-white/10 px-3 py-2 ring-2 ring-amber-400/60">
            <svg className="h-4 w-4 text-amber-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 010 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 010-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28z" /><path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
            <span className="text-[11px] font-medium text-amber-200">Settings</span>
          </div>
        </MockShopifyPanel>
      ),
    },
    {
      title: "Open Customer Events",
      desc: "In the Settings sidebar, find and click Customer events.",
      visual: (
        <MockShopifyPanel>
          <div className="space-y-1">
            <SidebarItem label="General" />
            <SidebarItem label="Shipping" />
            <SidebarItem label="Checkout" />
            <SidebarItem label="Customer events" highlighted />
            <SidebarItem label="Languages" />
          </div>
        </MockShopifyPanel>
      ),
    },
    {
      title: "Add Custom Pixel",
      desc: 'Click the "Add custom pixel" button in the top-right corner.',
      visual: (
        <MockShopifyPanel>
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-slate-400">Customer events</span>
            <div className="rounded-lg bg-emerald-500/80 px-3 py-1.5 ring-2 ring-amber-400/60 text-[11px] font-semibold text-white">
              Add custom pixel
            </div>
          </div>
          <div className="mt-3 rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
            <div className="text-[10px] text-slate-500">Name your pixel:</div>
            <div className="mt-1 rounded bg-white/[0.06] px-2 py-1 text-[11px] text-amber-200">Hedge Spark</div>
          </div>
        </MockShopifyPanel>
      ),
    },
    {
      title: "Paste the Pixel Code",
      desc: "Copy the code below and paste it into the code editor. Then click Save.",
      visual: (
        <div className="relative rounded-xl border border-amber-400/15 bg-black/50 p-3">
          <pre className="max-h-24 overflow-auto text-[9px] leading-relaxed text-slate-500 font-mono whitespace-pre-wrap">
            {pixelStatus.pixel_code.slice(0, 300)}
            {pixelStatus.pixel_code.length > 300 ? "\n…" : ""}
          </pre>
          <button
            onClick={handleCopy}
            className={`absolute top-2 right-2 rounded-lg px-3 py-1.5 text-[11px] font-semibold transition ${
              copied ? "bg-emerald-500/30 text-emerald-300" : "bg-amber-500/25 text-amber-200 hover:bg-amber-500/40"
            }`}
          >
            {copied ? "\u2705 Copied!" : "Copy code"}
          </button>
        </div>
      ),
    },
    {
      title: "Connect and Test",
      desc: 'Click "Connect" in Shopify to activate the pixel. Then make one test purchase.',
      visual: (
        <MockShopifyPanel>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="h-2 w-2 rounded-full bg-emerald-400" />
              <span className="text-[11px] text-slate-300">Hedge Spark</span>
            </div>
            <div className="rounded-lg bg-emerald-500/80 px-3 py-1.5 ring-2 ring-amber-400/60 text-[11px] font-semibold text-white">
              Connect
            </div>
          </div>
          <div className="mt-3 rounded-lg border border-emerald-400/20 bg-emerald-500/[0.06] p-2 text-center">
            <span className="text-[10px] text-emerald-300">Make a test purchase to verify</span>
          </div>
        </MockShopifyPanel>
      ),
    },
  ];

  const step = STEPS[activeStep];

  return (
    <div className="mt-3 rounded-xl border border-amber-400/15 bg-gradient-to-b from-amber-500/[0.04] to-transparent p-4">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <span className="text-[12px] font-semibold text-amber-200">Setup Guide</span>
          <span className="rounded-full bg-amber-500/20 px-2 py-0.5 text-[9px] font-bold text-amber-300">
            {activeStep + 1} / {STEPS.length}
          </span>
        </div>
        <button onClick={onClose} className="rounded p-1 text-slate-600 hover:text-slate-400 transition">
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
        </button>
      </div>

      {/* Step indicator dots */}
      <div className="flex gap-1.5 mb-4">
        {STEPS.map((_, i) => (
          <button
            key={i}
            onClick={() => setActiveStep(i)}
            className={`h-1 flex-1 rounded-full transition ${
              i === activeStep ? "bg-amber-400" : i < activeStep ? "bg-emerald-400/40" : "bg-white/[0.08]"
            }`}
          />
        ))}
      </div>

      {/* Visual */}
      <div className="mb-3">{step.visual}</div>

      {/* Text */}
      <div className="text-[13px] font-medium text-white mb-1">{step.title}</div>
      <div className="text-[11px] text-slate-400 mb-4">{step.desc}</div>

      {/* Navigation */}
      <div className="flex items-center justify-between">
        <button
          onClick={() => setActiveStep(Math.max(0, activeStep - 1))}
          disabled={activeStep === 0}
          className="rounded-lg px-3 py-1.5 text-[11px] font-medium text-slate-500 transition hover:text-slate-300 disabled:opacity-30"
        >
          Back
        </button>
        {activeStep < STEPS.length - 1 ? (
          <button
            onClick={() => setActiveStep(activeStep + 1)}
            className="rounded-lg bg-amber-500/25 px-4 py-1.5 text-[11px] font-semibold text-amber-200 transition hover:bg-amber-500/40"
          >
            Next
          </button>
        ) : (
          <button
            onClick={onClose}
            className="rounded-lg bg-emerald-500/25 px-4 py-1.5 text-[11px] font-semibold text-emerald-300 transition hover:bg-emerald-500/40"
          >
            Done — waiting for first order
          </button>
        )}
      </div>
    </div>
  );
}

// Mock Shopify admin panel
function MockShopifyPanel({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-[#1a1a2e]/80 p-3">
      {/* Title bar */}
      <div className="flex items-center gap-1.5 mb-3 pb-2 border-b border-white/[0.06]">
        <div className="h-1.5 w-1.5 rounded-full bg-rose-400/40" />
        <div className="h-1.5 w-1.5 rounded-full bg-amber-400/40" />
        <div className="h-1.5 w-1.5 rounded-full bg-emerald-400/40" />
        <span className="ml-2 text-[9px] text-slate-600">Shopify Admin</span>
      </div>
      {children}
    </div>
  );
}

function SidebarItem({ label, highlighted }: { label: string; highlighted?: boolean }) {
  return (
    <div className={`rounded-md px-2.5 py-1.5 text-[11px] ${
      highlighted
        ? "bg-white/10 text-amber-200 font-medium ring-2 ring-amber-400/60"
        : "text-slate-500"
    }`}>
      {label}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function OnboardingChecklist({ data }: { data: OnboardingData }) {
  const [collapsed, setCollapsed] = useState(false);
  const [showWalkthrough, setShowWalkthrough] = useState(false);
  const [pixelStatus, setPixelStatus] = useState<PixelStatus | null>(null);

  // Fetch pixel status with polling
  useEffect(() => {
    if (!API_BASE) return;
    let active = true;

    async function check() {
      try {
        const r = await fetch(`${API_BASE}/setup/pixel-status`, {
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          cache: "no-store",
        });
        if (r.ok && active) setPixelStatus(await r.json());
      } catch {}
    }

    check();
    const id = setInterval(() => { if (!pixelStatus?.pixel_active) check(); }, 8000);
    return () => { active = false; clearInterval(id); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-close walkthrough when pixel becomes active
  useEffect(() => {
    if (pixelStatus?.pixel_active) setShowWalkthrough(false);
  }, [pixelStatus?.pixel_active]);

  const steps = deriveSteps(data, pixelStatus, () => setShowWalkthrough(true));

  if (!shouldShow(steps, data.readiness)) return null;

  const core = steps.filter((s) => s.key !== "pro");
  const done = core.filter((s) => s.state === "complete").length;
  const total = core.length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

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
            <div className="mt-0.5 text-[11px] text-slate-600">{done}/{total} steps</div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="hidden sm:flex items-center gap-2">
            <div className="h-1.5 w-24 overflow-hidden rounded-full bg-white/[0.06]">
              <div className="h-full rounded-full bg-emerald-400/70 transition-all duration-500" style={{ width: `${pct}%` }} />
            </div>
            <span className="text-[10px] text-slate-600">{pct}%</span>
          </div>
          <button onClick={() => setCollapsed(!collapsed)} className="rounded p-1 text-slate-600 hover:text-slate-400">
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

          {/* Visual walkthrough (expandable) */}
          {showWalkthrough && pixelStatus && !pixelStatus.pixel_active && (
            <PixelWalkthrough pixelStatus={pixelStatus} onClose={() => setShowWalkthrough(false)} />
          )}
        </div>
      )}
    </div>
  );
}
