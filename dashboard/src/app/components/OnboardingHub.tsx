"use client";

/**
 * OnboardingHub — unified onboarding surface for merchant activation.
 *
 * Replaces the previous split of SetupStatusPanel + OnboardingChecklist
 * into one coherent component with one source of truth.
 *
 * States (in order of severity / lifecycle):
 *
 *   grace        — fresh install detected, auto-setup in progress
 *   degraded     — critical failure (reinstall required)
 *   repairing    — auto-repair in progress (webhook/tracker)
 *   setup_done   — auto-setup complete, shows pixel hero + progress
 *   active       — tracking active, optional Pro upsell (dismissible)
 *   pro_active   — everything active, panel hidden
 *
 * Design principles:
 *   - one panel, never two
 *   - auto-steps collapsed into "Store connected"
 *   - pixel setup is the hero manual action
 *   - plain merchant language, no jargon
 *   - explicit time expectations
 *   - grace period prevents "broken" impression on first load
 */

import { useCallback, useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SetupChecks = {
  merchant_exists: boolean;
  install_active: boolean;
  token_ok: boolean;
  token_encrypted: boolean;
  webhook_ok: boolean;
  webhook_id: string | null;
  tracker_ok: boolean;
  tracker_id: string | null;
  billing_active: boolean;
  billing_plan: string;
  billing_charge_pending: boolean;
};

type SetupStatus = {
  shop_domain: string;
  computed_at: string;
  audit_mode: string;
  setup_complete: boolean;
  readiness: "degraded" | "needs_repair" | "lite_ready" | "pro_active";
  degraded_reasons: string[];
  checks: SetupChecks;
};

type PixelStatus = {
  pixel_active: boolean;
  orders_from_pixel: number;
  purchase_events: number;
  pixel_code: string;
  instructions: string[];
};

// Re-export for page.tsx consumption
export type OnboardingHubChecks = SetupChecks;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const GRACE_DURATION_MS = 15_000; // show "Setting up..." for up to 15s
const GRACE_POLL_MS = 3_000;     // poll every 3s during grace
const PIXEL_POLL_MS = 8_000;     // poll pixel status every 8s

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

// Session counter — increments on each fresh page load for this shop
const SESSION_KEY_PREFIX = "hs_onboard_session_";
function getSessionNumber(shop: string): number {
  try {
    const key = `${SESSION_KEY_PREFIX}${shop}`;
    const current = parseInt(sessionStorage.getItem(key) || "0", 10);
    // Only increment once per page load — use a flag
    const flagKey = `${key}_counted`;
    if (!sessionStorage.getItem(flagKey)) {
      const next = current + 1;
      sessionStorage.setItem(key, String(next));
      sessionStorage.setItem(flagKey, "1");
      return next;
    }
    return current || 1;
  } catch { return 1; }
}

/**
 * Fire-and-forget onboarding event to POST /onboarding/event.
 * Non-blocking, silently swallows errors.
 */
function trackOnboardingEvent(
  eventType: string,
  sessionNumber: number,
  context?: Record<string, unknown>,
) {
  if (!API_BASE) return;
  fetch(`${API_BASE}/onboarding/event`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      event_type: eventType,
      session_number: sessionNumber,
      context: context || null,
    }),
  }).catch(() => { /* silent */ });
}

// Dismissal persistence — localStorage so it persists across sessions
const DISMISS_KEY_PREFIX = "hs_onboard_dismissed_";

function isDismissed(shop: string): boolean {
  try { return localStorage.getItem(`${DISMISS_KEY_PREFIX}${shop}`) === "1"; }
  catch { return false; }
}
function persistDismiss(shop: string) {
  try { localStorage.setItem(`${DISMISS_KEY_PREFIX}${shop}`, "1"); }
  catch { /* SSR / quota */ }
}
function clearDismiss(shop: string) {
  try { localStorage.removeItem(`${DISMISS_KEY_PREFIX}${shop}`); }
  catch { /* SSR */ }
}

// ---------------------------------------------------------------------------
// SVG icons (inlined, small)
// ---------------------------------------------------------------------------

function SpinnerIcon({ className = "h-3 w-3" }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} fill="none" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
      <path fill="currentColor" className="opacity-75" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
    </svg>
  );
}

function CheckIcon({ className = "h-3 w-3" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Animated setup step with dot + label + status text */
function SetupStep({
  label,
  detail,
  state,
}: {
  label: string;
  detail: string;
  state: "done" | "active" | "waiting" | "failed";
}) {
  const dotClass =
    state === "done" ? "bg-emerald-400"
    : state === "active" ? "bg-amber-400 animate-pulse"
    : state === "failed" ? "bg-rose-400"
    : "bg-slate-600";
  const textClass =
    state === "done" ? "text-slate-400"
    : state === "active" ? "text-amber-200"
    : state === "failed" ? "text-rose-300"
    : "text-slate-600";

  return (
    <div className="flex items-center gap-2.5">
      <span className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${dotClass}`} />
      <span className={`text-[11px] ${textClass}`}>
        {state === "done" && <CheckIcon className="mr-1 inline-block h-2.5 w-2.5 text-emerald-400" />}
        {state === "active" && <SpinnerIcon className="mr-1 inline-block h-2.5 w-2.5 text-amber-300" />}
        {label}
        <span className="ml-1.5 text-[10px] opacity-70">{detail}</span>
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pixel setup — single-view, flattened (not a wizard)
// ---------------------------------------------------------------------------

function PixelSetupHero({
  pixelStatus,
  onDismiss,
  onTrack,
}: {
  pixelStatus: PixelStatus;
  onDismiss: () => void;
  onTrack: (eventType: string, context?: Record<string, unknown>) => void;
}) {
  const [copied, setCopied] = useState(false);

  // Track pixel_viewed once when this component mounts
  const viewedRef = useRef(false);
  useEffect(() => {
    if (!viewedRef.current) {
      viewedRef.current = true;
      onTrack("pixel_viewed");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleCopy = useCallback(() => {
    const code = pixelStatus.pixel_code;
    const onSuccess = () => {
      setCopied(true);
      onTrack("pixel_copy_clicked");
      setTimeout(() => setCopied(false), 2500);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(code).then(onSuccess).catch(() => {
        // Fallback for HTTP or permission-denied contexts
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
  }, [pixelStatus.pixel_code, onTrack]);

  if (pixelStatus.pixel_active) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-emerald-400/15 bg-emerald-500/[0.06] px-4 py-3">
        <span className="h-2 w-2 rounded-full bg-emerald-400" />
        <span className="text-[12px] font-medium text-emerald-300">
          Purchase tracking active
        </span>
        <span className="text-[11px] text-emerald-300/60">
          — {pixelStatus.orders_from_pixel} order{pixelStatus.orders_from_pixel === 1 ? "" : "s"} captured
        </span>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-amber-400/15 bg-gradient-to-b from-amber-500/[0.06] to-transparent p-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[13px] font-semibold text-white">
            See which visitors actually buy
          </div>
          <p className="mt-1 text-[11px] text-slate-400 leading-relaxed">
            Connect the purchase pixel to see which products convert browsers into buyers — and which ones lose them.
          </p>
        </div>
        <span className="flex-shrink-0 rounded bg-amber-500/20 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider text-amber-300">
          1 min
        </span>
      </div>

      {/* Instructions — all visible at once, no wizard */}
      <div className="mt-4 space-y-3">
        {/* Step 1 */}
        <div className="flex items-start gap-3">
          <span className="mt-px flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-white/[0.08] text-[10px] font-bold text-slate-400">1</span>
          <div className="text-[12px] text-slate-300">
            In Shopify Admin, go to <span className="font-medium text-white">Settings</span> &rarr; <span className="font-medium text-white">Customer events</span>
          </div>
        </div>

        {/* Step 2 */}
        <div className="flex items-start gap-3">
          <span className="mt-px flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-white/[0.08] text-[10px] font-bold text-slate-400">2</span>
          <div className="text-[12px] text-slate-300">
            Click <span className="font-medium text-white">&quot;Add custom pixel&quot;</span>, name it <span className="font-medium text-amber-200">HedgeSpark</span>
          </div>
        </div>

        {/* Step 3 — code box */}
        <div className="flex items-start gap-3">
          <span className="mt-px flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-white/[0.08] text-[10px] font-bold text-slate-400">3</span>
          <div className="flex-1 min-w-0">
            <div className="text-[12px] text-slate-300 mb-2">
              Paste this code, click <span className="font-medium text-white">Save</span>, then click <span className="font-medium text-white">Connect</span>
            </div>
            <div className="relative rounded-lg border border-white/[0.08] bg-black/50 p-3">
              <pre className="max-h-20 overflow-auto text-[9px] leading-relaxed text-slate-500 font-mono whitespace-pre-wrap">
                {pixelStatus.pixel_code.slice(0, 400)}
                {pixelStatus.pixel_code.length > 400 ? "\n..." : ""}
              </pre>
              <button
                onClick={handleCopy}
                className={`absolute top-2 right-2 rounded-lg px-3 py-1.5 text-[11px] font-semibold transition ${
                  copied
                    ? "bg-emerald-500/30 text-emerald-300"
                    : "bg-amber-500/25 text-amber-200 hover:bg-amber-500/40"
                }`}
              >
                {copied ? "Copied!" : "Copy code"}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className="mt-4 flex items-center justify-between">
        <button
          onClick={() => { onTrack("pixel_skipped"); onDismiss(); }}
          className="text-[11px] text-slate-600 hover:text-slate-400 transition"
        >
          I&apos;ll do this later
        </button>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1.5 text-[10px] text-slate-600">
            <span className="h-1 w-1 animate-pulse rounded-full bg-amber-400" />
            Listening for your first order...
          </span>
        </div>
      </div>

      {/* Skip warning */}
      <p className="mt-2 text-[10px] text-slate-600">
        You&apos;ll miss purchase attribution until the pixel is connected.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Welcome banner — shown once on fresh install
// ---------------------------------------------------------------------------

function WelcomeBanner({ onDismiss, shopDomain }: { onDismiss: () => void; shopDomain?: string }) {
  const [selfDetected, setSelfDetected] = useState(false);
  const storeUrl = shopDomain
    ? `https://${shopDomain}`
    : null;

  // Poll for the merchant's own visit appearing
  useEffect(() => {
    if (selfDetected || !shopDomain) return;
    const interval = setInterval(async () => {
      try {
        const r = await fetch(`/api/summary?shop=${shopDomain}`);
        if (r.ok) {
          const d = await r.json();
          if ((d.total_visitors ?? 0) > 0) {
            setSelfDetected(true);
            clearInterval(interval);
          }
        }
      } catch { /* ignore */ }
    }, 3000);
    return () => clearInterval(interval);
  }, [shopDomain, selfDetected]);

  return (
    <div className="rounded-2xl border border-violet-400/15 bg-gradient-to-r from-violet-500/[0.06] to-transparent p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          {selfDetected ? (
            <>
              <div className="flex items-center gap-2 text-[14px] font-semibold text-emerald-300">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                That&apos;s you — HedgeSpark is tracking your visitors
              </div>
              <p className="mt-1.5 text-[12px] text-slate-400 leading-relaxed max-w-lg">
                Every visitor to your store is now tracked like this. Browse any product for 10 seconds
                and watch the numbers update in real time above.
              </p>
            </>
          ) : (
            <>
              <div className="text-[14px] font-semibold text-white">
                See it working — right now
              </div>
              <p className="mt-1.5 text-[12px] text-slate-400 leading-relaxed max-w-lg">
                Open your store in a new tab and browse any product for 10 seconds.
                Come back here — you&apos;ll see your visit appear in real time.
              </p>

              {storeUrl && (
                <a
                  href={storeUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-4 py-2 text-[13px] font-semibold text-white shadow-[0_0_16px_rgba(124,58,237,0.25)] transition hover:bg-violet-500"
                >
                  Open my store
                  <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                  </svg>
                </a>
              )}

              <div className="mt-4 flex items-center gap-2 text-[10px] text-slate-600">
                <span className="h-1 w-1 animate-pulse rounded-full bg-violet-400" />
                Watching for your visit...
              </div>
            </>
          )}
        </div>

        <button
          onClick={onDismiss}
          className="flex-shrink-0 rounded p-1 text-slate-600 hover:text-slate-400 transition"
          aria-label="Dismiss"
        >
          <CloseIcon />
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Timeline expectations — shown after setup completes
// ---------------------------------------------------------------------------

function TimelineHint() {
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-1.5 mt-3">
      <span className="flex items-center gap-1.5 text-[10px] text-slate-500">
        <span className="h-1 w-1 rounded-full bg-emerald-400" />
        Visitors: within minutes
      </span>
      <span className="flex items-center gap-1.5 text-[10px] text-slate-500">
        <span className="h-1 w-1 rounded-full bg-amber-400" />
        First findings: typically under 10 min
      </span>
      <span className="flex items-center gap-1.5 text-[10px] text-slate-500">
        <span className="h-1 w-1 rounded-full bg-violet-400" />
        Full analysis: ~24 hours
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function OnboardingHub({
  shop,
  apiBase,
  apiHeaders,
  onReadinessChange,
  billingJustActivated,
  freshInstall,
  totalVisitors,
  signalCount,
}: {
  shop: string;
  apiBase: string;
  apiHeaders: () => HeadersInit;
  onReadinessChange?: (readiness: string, checks: SetupChecks) => void;
  billingJustActivated?: boolean;
  /** True when URL had ?installed=1 — triggers grace period */
  freshInstall?: boolean;
  /** From overview data, null = still loading */
  totalVisitors: number | null;
  /** Strong signal count, null = still loading */
  signalCount: number | null;
}) {
  // ── Status state ──
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [fetchError, setFetchError] = useState(false);
  const mountedRef = useRef(true);
  const prevReadinessRef = useRef<string | null>(null);

  // ── Grace period (fresh install) ──
  const [inGrace, setInGrace] = useState(!!freshInstall);
  const graceStartRef = useRef(Date.now());

  // ── Repair state ──
  const [autoRepairing, setAutoRepairing] = useState(false);

  // ── Pixel status ──
  const [pixelStatus, setPixelStatus] = useState<PixelStatus | null>(null);
  const pixelActiveRef = useRef(false);
  const [pixelDismissed, setPixelDismissed] = useState(false);

  // ── Welcome banner ──
  const welcomeKey = shop ? `hs_welcome_seen_${shop}` : "";
  const [showWelcome, setShowWelcome] = useState(() => {
    if (!freshInstall) return false;
    try { return !sessionStorage.getItem(welcomeKey); }
    catch { return true; }
  });

  // ── Dismissal (lite_ready banner) ──
  const [dismissed, setDismissed] = useState(() => isDismissed(shop));

  // ── Billing ──
  const [billingLoading, setBillingLoading] = useState(false);
  const [billingError, setBillingError] = useState("");

  // ── Deep check ──
  // deepCheckState existed as a per-phase indicator but was never rendered;
  // the async check still runs via fetchStatus(true) below, just without
  // visible state tracking. TODO: surface loading/error as a subtle toast
  // once we have a shared toast primitive.

  // ── Funnel tracking ──
  const sessionNum = useRef(shop ? getSessionNumber(shop) : 1);
  const track = useCallback((eventType: string, context?: Record<string, unknown>) => {
    trackOnboardingEvent(eventType, sessionNum.current, context);
  }, []);
  // Track session_start once on mount
  const sessionTrackedRef = useRef(false);
  useEffect(() => {
    if (shop && !sessionTrackedRef.current) {
      sessionTrackedRef.current = true;
      track("session_start");
      if (freshInstall) {
        track("install_completed");
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop]);

  // =========================================================================
  // Fetch status
  // =========================================================================
  const fetchStatus = useCallback(
    async (deep = false) => {
      if (!shop || !apiBase) return null;
      try {
        const res = await fetch(
          `${apiBase}/setup/status?shop=${encodeURIComponent(shop)}&deep=${deep}`,
          { headers: apiHeaders(), credentials: "include", cache: "no-store" }
        );
        if (!res.ok) { setFetchError(true); return null; }
        const json: SetupStatus = await res.json();
        if (!mountedRef.current) return null;
        setFetchError(false);
        setStatus(json);
        // Notify parent
        onReadinessChange?.(json.readiness, json.checks);
        // Clear dismissal on state changes
        if (json.readiness === "needs_repair" || json.readiness === "degraded") {
          setDismissed(false);
          clearDismiss(shop);
        }
        if (prevReadinessRef.current && prevReadinessRef.current !== json.readiness) {
          setDismissed(false);
          clearDismiss(shop);
        }
        prevReadinessRef.current = json.readiness;
        return json;
      } catch {
        if (mountedRef.current) setFetchError(true);
        return null;
      }
    },
    [shop, apiBase, apiHeaders]
  );

  // =========================================================================
  // Mount — initial check or billing-activated deep check
  // =========================================================================
  useEffect(() => {
    mountedRef.current = true;
    setDismissed(isDismissed(shop));

    if (billingJustActivated) {
      fetchStatus(true).catch(() => { /* surfaced via existing error state */ });
    } else {
      fetchStatus(false);
    }
    return () => { mountedRef.current = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop]);

  // =========================================================================
  // Grace period polling — poll every 3s during grace, exit when ready
  // =========================================================================
  useEffect(() => {
    if (!inGrace || !shop) return;
    let active = true;

    const poll = async () => {
      const result = await fetchStatus(false);
      if (!active) return;
      if (result && (result.readiness === "lite_ready" || result.readiness === "pro_active")) {
        setInGrace(false);
        track("setup_completed");
        return;
      }
      // If needs_repair during grace, try auto-repair silently
      if (result && result.readiness === "needs_repair") {
        setAutoRepairing(true);
        track("repair_triggered", { source: "auto_grace" });
        const repairs: Promise<Response>[] = [];
        if (!result.checks.webhook_ok) {
          repairs.push(fetch(`${apiBase}/setup/repair/webhook?shop=${encodeURIComponent(shop)}`, {
            method: "POST", headers: apiHeaders(), credentials: "include",
          }));
        }
        if (!result.checks.tracker_ok) {
          repairs.push(fetch(`${apiBase}/setup/repair/tracker?shop=${encodeURIComponent(shop)}`, {
            method: "POST", headers: apiHeaders(), credentials: "include",
          }));
        }
        await Promise.allSettled(repairs);
        if (!active) return;
        // Re-check after repair
        const updated = await fetchStatus(true);
        if (!active) return;
        if (updated && (updated.readiness === "lite_ready" || updated.readiness === "pro_active")) {
          setInGrace(false);
          setAutoRepairing(false);
          track("setup_completed");
          return;
        }
        setAutoRepairing(false);
      }
      // Check grace timeout
      if (Date.now() - graceStartRef.current > GRACE_DURATION_MS) {
        setInGrace(false);
      }
    };

    const id = setInterval(poll, GRACE_POLL_MS);
    // First poll immediately (mount already fetched, but this handles repair)
    const timeout = setTimeout(poll, 1000);

    return () => { active = false; clearInterval(id); clearTimeout(timeout); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inGrace, shop]);

  // =========================================================================
  // Auto-repair on needs_repair (non-grace) — fire once, silently
  // =========================================================================
  const autoRepairFiredRef = useRef(false);
  useEffect(() => {
    if (!status || status.readiness !== "needs_repair" || inGrace || autoRepairFiredRef.current) return;
    autoRepairFiredRef.current = true;
    setAutoRepairing(true);

    (async () => {
      const repairs: Promise<Response>[] = [];
      if (!status.checks.webhook_ok) {
        repairs.push(fetch(`${apiBase}/setup/repair/webhook?shop=${encodeURIComponent(shop)}`, {
          method: "POST", headers: apiHeaders(), credentials: "include",
        }));
      }
      if (!status.checks.tracker_ok) {
        repairs.push(fetch(`${apiBase}/setup/repair/tracker?shop=${encodeURIComponent(shop)}`, {
          method: "POST", headers: apiHeaders(), credentials: "include",
        }));
      }
      await Promise.allSettled(repairs);
      if (!mountedRef.current) return;
      // Deep check after repair
      await fetchStatus(true);
      if (mountedRef.current) setAutoRepairing(false);
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.readiness]);

  // =========================================================================
  // Pixel status polling — uses ref to avoid stale closure in interval
  // =========================================================================
  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;

    async function check() {
      try {
        const r = await fetch(`${apiBase}/setup/pixel-status`, {
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          cache: "no-store",
        });
        if (r.ok && active) {
          const data: PixelStatus = await r.json();
          setPixelStatus(data);
          pixelActiveRef.current = !!data.pixel_active;
        }
      } catch { /* silent */ }
    }

    check();
    const id = setInterval(() => { if (!pixelActiveRef.current) check(); }, PIXEL_POLL_MS);
    return () => { active = false; clearInterval(id); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, shop]);

  // =========================================================================
  // Billing upgrade
  // =========================================================================
  async function startBillingUpgrade() {
    track("upgrade_clicked");
    setBillingLoading(true);
    setBillingError("");
    try {
      const res = await fetch(
        `${apiBase}/billing/subscribe?shop=${encodeURIComponent(shop)}`,
        { method: "POST", headers: apiHeaders(), credentials: "include" }
      );
      const json = await res.json();
      if (!res.ok) {
        setBillingError(json?.detail || "Could not start upgrade. Please try again.");
        setBillingLoading(false);
        return;
      }
      if (json.confirmation_url) {
        window.location.href = json.confirmation_url;
      } else {
        setBillingError("Something went wrong starting your trial. Try again, or contact support.");
        setBillingLoading(false);
      }
    } catch {
      if (mountedRef.current) {
        setBillingError("Couldn't reach our server. Try refreshing the page.");
        setBillingLoading(false);
      }
    }
  }

  // =========================================================================
  // Welcome dismiss
  // =========================================================================
  function dismissWelcome() {
    setShowWelcome(false);
    track("welcome_dismissed");
    try { if (welcomeKey) sessionStorage.setItem(welcomeKey, "1"); }
    catch { /* SSR */ }
  }

  // =========================================================================
  // Lite-ready dismiss
  // =========================================================================
  function handleDismiss() {
    setDismissed(true);
    track("onboarding_dismissed");
    persistDismiss(shop);
  }

  // =========================================================================
  // Manual repair (shown only when auto-repair failed)
  // =========================================================================
  const [manualRepairLoading, setManualRepairLoading] = useState(false);
  async function manualRepair() {
    track("repair_triggered", { source: "manual" });
    setManualRepairLoading(true);
    const repairs: Promise<Response>[] = [];
    if (status && !status.checks.webhook_ok) {
      repairs.push(fetch(`${apiBase}/setup/repair/webhook?shop=${encodeURIComponent(shop)}`, {
        method: "POST", headers: apiHeaders(), credentials: "include",
      }));
    }
    if (status && !status.checks.tracker_ok) {
      repairs.push(fetch(`${apiBase}/setup/repair/tracker?shop=${encodeURIComponent(shop)}`, {
        method: "POST", headers: apiHeaders(), credentials: "include",
      }));
    }
    await Promise.allSettled(repairs);
    await fetchStatus(true);
    if (mountedRef.current) setManualRepairLoading(false);
  }

  // =========================================================================
  // Derived state
  // =========================================================================
  const checks = status?.checks;
  const storeConnected = !!(checks?.merchant_exists && checks?.install_active && checks?.token_ok && checks?.webhook_ok && checks?.tracker_ok);
  const pixelActive = !!(pixelStatus?.pixel_active);
  const hasVisitors = (totalVisitors ?? 0) > 0;
  const hasSignals = (signalCount ?? 0) > 0;

  // Progress: how many of the 4 merchant-facing milestones are done
  const milestones = [storeConnected, pixelActive, hasVisitors, hasSignals];
  const milestoneDone = milestones.filter(Boolean).length;
  const milestoneTotal = milestones.length;
  const allDone = milestoneDone === milestoneTotal;

  // ── Milestone tracking (fire once per transition) ──
  // Initialize ref with current values so we don't re-fire on page refresh
  // when milestones are already complete.  The first render sets the baseline;
  // only subsequent TRUE transitions fire events.
  const milestoneInitRef = useRef(false);
  const prevMilestonesRef = useRef({ storeConnected: false, pixelActive: false, hasVisitors: false, hasSignals: false, allDone: false });
  useEffect(() => {
    // Skip the very first run — just record current state as baseline
    if (!milestoneInitRef.current) {
      milestoneInitRef.current = true;
      prevMilestonesRef.current = { storeConnected, pixelActive, hasVisitors, hasSignals, allDone };
      return;
    }
    const prev = prevMilestonesRef.current;
    if (storeConnected && !prev.storeConnected) track("setup_completed");
    if (pixelActive && !prev.pixelActive) track("pixel_detected");
    if (hasVisitors && !prev.hasVisitors) track("first_visitor_detected");
    if (hasSignals && !prev.hasSignals) track("first_insight_generated");
    if (allDone && !prev.allDone) track("onboarding_complete");
    prevMilestonesRef.current = { storeConnected, pixelActive, hasVisitors, hasSignals, allDone };
  }, [storeConnected, pixelActive, hasVisitors, hasSignals, allDone, track]);

  // =========================================================================
  // Render
  // =========================================================================

  if (!shop) return null;

  // ── GRACE PERIOD — "Setting up..." ──
  if (inGrace) {
    return (
      <div className="space-y-3">
        {showWelcome && <WelcomeBanner onDismiss={dismissWelcome} />}
        <div className="rounded-2xl border border-violet-400/15 bg-violet-500/[0.04] p-4">
          <div className="flex items-center gap-3">
            <div className="flex-shrink-0 rounded-lg bg-violet-500/15 p-1.5">
              <SpinnerIcon className="h-4 w-4 text-violet-400" />
            </div>
            <div>
              <div className="text-[12px] font-semibold text-slate-300">
                Setting up HedgeSpark...
              </div>
              <div className="mt-1 space-y-1">
                <SetupStep
                  label="Connecting to your store"
                  detail=""
                  state={checks?.token_ok ? "done" : "active"}
                />
                <SetupStep
                  label="Installing visitor tracking"
                  detail=""
                  state={checks?.tracker_ok ? "done" : checks?.token_ok ? "active" : "waiting"}
                />
                <SetupStep
                  label="Activating intelligence"
                  detail=""
                  state={checks?.webhook_ok && checks?.tracker_ok ? "done" : "waiting"}
                />
              </div>
              <p className="mt-2 text-[10px] text-slate-600">This takes about 10 seconds.</p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ── LOADING — status not yet fetched ──
  if (!status && !fetchError) {
    return (
      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4 animate-pulse">
        <div className="h-3 w-40 rounded bg-white/[0.06]" />
        <div className="mt-2 h-2.5 w-64 rounded bg-white/[0.04]" />
        {billingJustActivated && (
          <div className="mt-2 text-[11px] text-slate-600">Verifying billing with Shopify...</div>
        )}
      </div>
    );
  }

  // ── FETCH ERROR ──
  if (fetchError || !status) {
    return (
      <div className="rounded-2xl border border-amber-400/20 bg-amber-500/[0.06] px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-amber-400" />
          <span className="text-[12px] font-semibold text-amber-300">
            Couldn&apos;t check setup status
          </span>
          <button
            onClick={() => { track("setup_retry"); fetchStatus(false); }}
            className="ml-auto text-[11px] text-amber-400/70 underline-offset-2 hover:text-amber-300 hover:underline"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  // ── PRO ACTIVE — hide everything ──
  if (status.readiness === "pro_active") return null;

  // ── DEGRADED — critical failure, reinstall required ──
  if (status.readiness === "degraded") {
    return (
      <div className="rounded-2xl border border-rose-400/25 bg-rose-500/[0.07] p-4">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex-shrink-0 rounded-lg bg-rose-500/20 p-1.5">
            <svg className="h-4 w-4 text-rose-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
            </svg>
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[12px] font-semibold uppercase tracking-[0.12em] text-rose-400">
              Connection Issue
            </div>
            <p className="mt-1 text-[13px] text-rose-200">
              {!checks?.merchant_exists
                ? "We can't find an installation record for your store. Please reinstall from the Shopify App Store."
                : !checks?.install_active
                ? "The app was removed from this store. Reinstall to resume tracking."
                : "We lost the connection to your store. This can happen after Shopify updates."}
            </p>
            <a
              href="https://apps.shopify.com/"
              target="_blank"
              rel="noopener noreferrer"
              className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-rose-500/20 px-3 py-1.5 text-[12px] font-semibold text-rose-200 ring-1 ring-rose-400/20 transition hover:bg-rose-500/30"
            >
              Reinstall HedgeSpark
            </a>
            {status.degraded_reasons.length > 0 && (
              <details className="mt-3">
                <summary className="cursor-pointer text-[11px] text-rose-400/60 hover:text-rose-400">
                  Technical details
                </summary>
                <ul className="mt-1.5 space-y-1">
                  {status.degraded_reasons.map((r, i) => (
                    <li key={i} className="text-[11px] text-rose-300/60">{r}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ── NEEDS REPAIR (auto-repair in progress or failed) ──
  if (status.readiness === "needs_repair") {
    return (
      <div className="rounded-2xl border border-amber-400/25 bg-amber-500/[0.06] p-4">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex-shrink-0 rounded-lg bg-amber-500/20 p-1.5">
            {autoRepairing ? (
              <SpinnerIcon className="h-4 w-4 text-amber-400" />
            ) : (
              <svg className="h-4 w-4 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17L17.25 21A2.652 2.652 0 0021 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 11-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 004.486-6.336l-3.276 3.277a3.004 3.004 0 01-2.25-2.25l3.276-3.276a4.5 4.5 0 00-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437l1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008z" />
              </svg>
            )}
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[12px] font-semibold text-amber-300">
              {autoRepairing ? "Reconnecting to your store..." : "Store connection needs attention"}
            </div>
            <p className="mt-1 text-[12px] text-amber-200/70">
              {autoRepairing
                ? "This usually takes a few seconds."
                : "We couldn't automatically restore the connection. Click below to try again."}
            </p>
            {!autoRepairing && (
              <button
                onClick={manualRepair}
                disabled={manualRepairLoading}
                className="mt-2.5 inline-flex items-center gap-1.5 rounded-lg bg-amber-500/20 px-3 py-1.5 text-[12px] font-semibold text-amber-200 ring-1 ring-amber-400/20 transition hover:bg-amber-500/30 disabled:opacity-60"
              >
                {manualRepairLoading ? <><SpinnerIcon className="h-3 w-3" /> Reconnecting...</> : "Reconnect"}
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ── LITE READY — everything works ──
  // If all milestones done and dismissed, hide entirely
  if (dismissed && allDone) return null;

  // If all milestones done, show only the lite-ready banner with Pro upsell
  if (allDone) {
    return (
      <div className="rounded-2xl border border-violet-400/15 bg-violet-500/[0.04] p-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-emerald-400" />
            <span className="text-[12px] font-semibold text-slate-300">
              Tracking active
            </span>
            <span className="text-[12px] text-slate-500">
              — your store is live on HedgeSpark
            </span>
          </div>
          <button
            onClick={handleDismiss}
            className="flex-shrink-0 rounded p-1 text-slate-600 transition hover:text-slate-400"
            aria-label="Dismiss"
          >
            <CloseIcon />
          </button>
        </div>

        {/* Pro upsell — only after first finding appears (not during onboarding).
            Beta-phase copy: no explicit price/trial language per master plan §4.2. */}
        {checks && !checks.billing_active && hasSignals && (
          <div className="mt-3 rounded-xl border border-violet-400/15 bg-violet-500/[0.06] px-4 py-3">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-[13px] font-semibold text-white">
                  We found {signalCount} revenue opportunity{signalCount === 1 ? "" : "ies"}. Want
                  us to fix {signalCount === 1 ? "it" : "them"}?
                </div>
                <div className="mt-0.5 text-[12px] text-slate-500">
                  Pro automatically turns findings into revenue and proves every result against a
                  control group. Closed beta — pricing announced before general launch.
                  {checks.billing_charge_pending && (
                    <span className="ml-2 text-amber-400">
                      Upgrade pending — check your Shopify billing page.
                    </span>
                  )}
                </div>
              </div>
              {!checks.billing_charge_pending && (
                <button
                  onClick={startBillingUpgrade}
                  disabled={billingLoading}
                  className="flex-shrink-0 rounded-lg bg-violet-600 px-4 py-2 text-[13px] font-semibold text-white shadow-[0_0_16px_rgba(124,58,237,0.3)] transition hover:bg-violet-500 active:bg-violet-700 disabled:opacity-60"
                >
                  {billingLoading ? "Opening Shopify billing..." : "Upgrade to Pro"}
                </button>
              )}
            </div>
            {billingError && (
              <p className="mt-2 text-[12px] text-rose-400">{billingError}</p>
            )}
          </div>
        )}
      </div>
    );
  }

  // ── ONBOARDING IN PROGRESS — show unified progress + pixel hero ──
  return (
    <div className="space-y-3">
      {/* Welcome banner — fresh install only, once */}
      {showWelcome && <WelcomeBanner onDismiss={dismissWelcome} />}

      {/* Unified onboarding card */}
      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
        {/* Header with progress */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex-shrink-0 rounded-lg bg-violet-500/15 p-1.5">
              <svg className="h-4 w-4 text-violet-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <div>
              <div className="text-[12px] font-semibold text-slate-300">Getting started</div>
              <div className="mt-0.5 text-[11px] text-slate-600">{milestoneDone}/{milestoneTotal} complete</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="hidden sm:flex items-center gap-2">
              <div className="h-1.5 w-24 overflow-hidden rounded-full bg-white/[0.06]">
                <div
                  className="h-full rounded-full bg-emerald-400/70 transition-all duration-500"
                  style={{ width: `${Math.round((milestoneDone / milestoneTotal) * 100)}%` }}
                />
              </div>
              <span className="text-[10px] text-slate-600">
                {Math.round((milestoneDone / milestoneTotal) * 100)}%
              </span>
            </div>
          </div>
        </div>

        {/* Milestone steps */}
        <div className="mt-4 space-y-2">
          {/* 1. Store connected (collapsed auto-steps) */}
          <SetupStep
            label="Store connected"
            detail={storeConnected ? "" : autoRepairing ? "" : ""}
            state={storeConnected ? "done" : autoRepairing ? "active" : "waiting"}
          />

          {/* 2. Purchase pixel */}
          <SetupStep
            label="Purchase tracking"
            detail={
              pixelActive
                ? `${pixelStatus?.orders_from_pixel ?? 0} order${(pixelStatus?.orders_from_pixel ?? 0) === 1 ? "" : "s"} captured`
                : storeConnected
                ? "Connect the pixel to see which visitors buy"
                : ""
            }
            state={
              pixelActive ? "done"
              : storeConnected ? "active"
              : "waiting"
            }
          />

          {/* 3. First visitor */}
          <SetupStep
            label="First visitor"
            detail={
              hasVisitors
                ? `${(totalVisitors ?? 0).toLocaleString()} tracked`
                : storeConnected
                ? "Visitors appear within minutes of your next store pageview"
                : ""
            }
            state={
              hasVisitors ? "done"
              : storeConnected ? "active"
              : "waiting"
            }
          />

          {/* 4. First finding */}
          <SetupStep
            label="First finding"
            detail={
              hasSignals
                ? `${signalCount} finding${signalCount === 1 ? "" : "s"} on your store`
                : hasVisitors
                ? "Analyzing your visitor behavior — typically under 10 minutes"
                : ""
            }
            state={
              hasSignals ? "done"
              : hasVisitors ? "active"
              : "waiting"
            }
          />
        </div>

        {/* Timeline hint */}
        {!hasSignals && storeConnected && <TimelineHint />}
      </div>

      {/* Pixel setup hero — shown prominently when store is connected but pixel not done */}
      {storeConnected && !pixelActive && !pixelDismissed && pixelStatus && (
        <PixelSetupHero
          pixelStatus={pixelStatus}
          onDismiss={() => setPixelDismissed(true)}
          onTrack={track}
        />
      )}
    </div>
  );
}
