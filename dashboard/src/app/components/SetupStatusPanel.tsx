"use client";

/**
 * SetupStatusPanel — truthful merchant onboarding status and repair surface.
 *
 * Reads GET /setup/status and renders one of four states:
 *
 *   degraded     — critical failure (reinstall required). Shown until resolved.
 *   needs_repair — webhook or tracker missing. Shown with repair buttons.
 *   lite_ready   — everything working, Pro not active. Shows upgrade CTA.
 *                  Dismissible (persisted in sessionStorage). Resets on state change.
 *   pro_active   — everything including billing is active. Panel is hidden.
 *
 * Repair buttons:
 *   POST /setup/repair/webhook  — idempotent, updates DB on success
 *   POST /setup/repair/tracker  — idempotent, updates DB on success
 *   After either repair, the panel re-fetches status with ?deep=true.
 *
 * Deep verification:
 *   "Verify with Shopify" button on every non-degraded panel.
 *   Calls GET /setup/status?deep=true → live Shopify API check.
 *   Shows audit_mode so merchant knows whether status is from DB or live.
 *
 * Billing CTA:
 *   POST /billing/subscribe — returns { confirmation_url }
 *   Redirects to confirmation_url (Shopify billing page).
 *   Only shown when billing_active=false and setup_complete=true.
 *
 * Post-billing re-verification:
 *   When billingJustActivated=true, component fires a deep check on mount
 *   so the panel/tier transition happens from real backend truth.
 */

import { useCallback, useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SetupChecks = {
  merchant_exists:        boolean;
  install_active:         boolean;
  token_ok:               boolean;
  token_encrypted:        boolean;
  webhook_ok:             boolean;
  webhook_id:             string | null;
  tracker_ok:             boolean;
  tracker_id:             string | null;
  billing_active:         boolean;
  billing_plan:           string;
  billing_charge_pending: boolean;
};

type SetupStatus = {
  shop_domain:      string;
  computed_at:      string;
  audit_mode:       string;
  setup_complete:   boolean;
  readiness:        "degraded" | "needs_repair" | "lite_ready" | "pro_active";
  degraded_reasons: string[];
  checks:           SetupChecks;
};

type RepairState = "idle" | "loading" | "success" | "error";

// ---------------------------------------------------------------------------
// Dismissal persistence — sessionStorage so it survives page nav but not
// browser close.  Key includes shop domain for multi-store setups.
// ---------------------------------------------------------------------------

const DISMISS_KEY_PREFIX = "hs_setup_dismissed_";

function isDismissed(shop: string): boolean {
  try { return sessionStorage.getItem(`${DISMISS_KEY_PREFIX}${shop}`) === "1"; }
  catch { return false; }
}

function persistDismiss(shop: string) {
  try { sessionStorage.setItem(`${DISMISS_KEY_PREFIX}${shop}`, "1"); }
  catch { /* quota / SSR */ }
}

function clearDismiss(shop: string) {
  try { sessionStorage.removeItem(`${DISMISS_KEY_PREFIX}${shop}`); }
  catch { /* SSR */ }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function CheckDot({ ok, title }: { ok: boolean; title: string }) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${
          ok ? "bg-emerald-400" : "bg-rose-400"
        }`}
      />
      <span className={`text-[11px] ${ok ? "text-slate-400" : "text-rose-300"}`}>
        {title}
      </span>
    </div>
  );
}

function RepairButton({
  label,
  state,
  onClick,
}: {
  label: string;
  state: RepairState;
  onClick: () => void;
}) {
  const isDisabled = state === "loading" || state === "success";

  return (
    <button
      onClick={onClick}
      disabled={isDisabled}
      className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] font-semibold transition-all ${
        state === "success"
          ? "cursor-default bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-400/20"
          : state === "error"
          ? "bg-rose-500/20 text-rose-300 ring-1 ring-rose-400/20 hover:bg-rose-500/30"
          : state === "loading"
          ? "cursor-wait bg-amber-500/10 text-amber-300 ring-1 ring-amber-400/20"
          : "bg-white/[0.06] text-slate-200 ring-1 ring-white/10 hover:bg-white/[0.10] hover:text-white"
      }`}
    >
      {state === "loading" && (
        <svg className="h-3 w-3 animate-spin text-amber-300" fill="none" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
          <path fill="currentColor" className="opacity-75" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
        </svg>
      )}
      {state === "success" && (
        <svg className="h-3 w-3 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
        </svg>
      )}
      {state === "idle"  && label}
      {state === "loading" && "Fixing…"}
      {state === "success" && "Fixed"}
      {state === "error" && "Retry"}
    </button>
  );
}

/** Inline verify button — reused in needs_repair and lite_ready panels. */
function VerifyButton({
  state,
  onClick,
  auditMode,
}: {
  state: RepairState;
  onClick: () => void;
  auditMode: string | null;
}) {
  return (
    <div className="flex items-center gap-2.5">
      <button
        onClick={onClick}
        disabled={state === "loading" || state === "success"}
        className={`inline-flex items-center gap-1.5 text-[11px] transition ${
          state === "loading"
            ? "cursor-wait text-amber-400/80"
            : state === "success"
            ? "cursor-default text-emerald-400/80"
            : state === "error"
            ? "text-rose-400/70 hover:text-rose-300"
            : "text-slate-500 hover:text-slate-300"
        }`}
      >
        {state === "loading" && (
          <svg className="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
            <path fill="currentColor" className="opacity-75" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
          </svg>
        )}
        {state === "success" && (
          <svg className="h-3 w-3 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
          </svg>
        )}
        {state === "idle"    && "Verify with Shopify"}
        {state === "loading" && "Checking Shopify…"}
        {state === "success" && "Verified live"}
        {state === "error"   && "Verify failed — retry"}
      </button>
      {/* Audit mode badge — lets merchant know if last check was DB or live */}
      {auditMode && (
        <span className={`rounded px-1.5 py-0.5 text-[10px] ${
          auditMode === "deep"
            ? "bg-emerald-500/10 text-emerald-400/70"
            : "bg-white/[0.04] text-slate-600"
        }`}>
          {auditMode === "deep" ? "live check" : "cached"}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SetupStatusPanel({
  shop,
  apiBase,
  apiHeaders,
  onReadinessChange,
  billingJustActivated,
  trialDays = 14,
  price = 49,
}: {
  shop:       string;
  apiBase:    string;
  apiHeaders: () => HeadersInit;
  onReadinessChange?: (readiness: SetupStatus["readiness"], checks: SetupChecks) => void;
  /** When true, fire an immediate deep check on mount (post-billing redirect). */
  billingJustActivated?: boolean;
  /** Pro billing config from /merchant/plan — used for trial-aware CTA copy. */
  trialDays?: number;
  price?: number;
}) {
  const [status,         setStatus]         = useState<SetupStatus | null>(null);
  const [fetchError,     setFetchError]      = useState(false);
  const [dismissed,      setDismissed]       = useState(() => isDismissed(shop));
  const [webhookRepair,  setWebhookRepair]   = useState<RepairState>("idle");
  const [trackerRepair,  setTrackerRepair]   = useState<RepairState>("idle");
  const [deepCheck,      setDeepCheck]        = useState<RepairState>("idle");
  const [billingLoading, setBillingLoading]  = useState(false);
  const [billingError,   setBillingError]    = useState("");
  const mountedRef = useRef(true);
  // Track the previous readiness so we can reset dismissal on real changes
  const prevReadinessRef = useRef<string | null>(null);

  // ---------------------------------------------------------------------------
  // Fetch status
  // ---------------------------------------------------------------------------
  const fetchStatus = useCallback(
    async (deep = false) => {
      if (!shop) return;
      try {
        const res = await fetch(
          `${apiBase}/setup/status?shop=${encodeURIComponent(shop)}&deep=${deep}`,
          { headers: apiHeaders(), credentials: "include", cache: "no-store" }
        );
        if (!res.ok) { setFetchError(true); return; }
        const json: SetupStatus = await res.json();
        if (!mountedRef.current) return;
        setFetchError(false);
        setStatus(json);
        // Notify parent of readiness changes (tier sync, billing confirmation)
        onReadinessChange?.(json.readiness, json.checks);
        // Reset dismissed when readiness degrades or changes away from lite_ready
        if (json.readiness === "needs_repair" || json.readiness === "degraded") {
          setDismissed(false);
          clearDismiss(shop);
        }
        // If readiness changed from what we had before, clear stale dismissal
        if (prevReadinessRef.current && prevReadinessRef.current !== json.readiness) {
          setDismissed(false);
          clearDismiss(shop);
        }
        prevReadinessRef.current = json.readiness;
      } catch {
        if (mountedRef.current) setFetchError(true);
      }
    },
    [shop, apiBase, apiHeaders]
  );

  // ---------------------------------------------------------------------------
  // Mount — initial fast check, or deep check if billing just activated
  // ---------------------------------------------------------------------------
  useEffect(() => {
    mountedRef.current = true;
    // Re-sync dismissal from sessionStorage when shop changes
    setDismissed(isDismissed(shop));
    if (billingJustActivated) {
      // Post-billing: skip fast check, go straight to deep verification
      // so the panel and tier update from real Shopify truth immediately.
      setDeepCheck("loading");
      fetchStatus(true).then(() => {
        if (mountedRef.current) setDeepCheck("success");
      }).catch(() => {
        if (mountedRef.current) setDeepCheck("error");
      });
    } else {
      fetchStatus(false);
    }
    return () => { mountedRef.current = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop]);

  // ---------------------------------------------------------------------------
  // Deep verify action — merchant-triggered live Shopify check
  // ---------------------------------------------------------------------------
  async function triggerDeepCheck() {
    setDeepCheck("loading");
    try {
      await fetchStatus(true);
      if (mountedRef.current) setDeepCheck("success");
    } catch {
      if (mountedRef.current) setDeepCheck("error");
    }
  }

  // ---------------------------------------------------------------------------
  // Repair actions
  // ---------------------------------------------------------------------------
  async function repairWebhook() {
    setWebhookRepair("loading");
    try {
      const res = await fetch(
        `${apiBase}/setup/repair/webhook?shop=${encodeURIComponent(shop)}`,
        { method: "POST", headers: apiHeaders(), credentials: "include" }
      );
      if (!mountedRef.current) return;
      if (res.ok) {
        setWebhookRepair("success");
        // Re-check live state after repair
        setTimeout(() => fetchStatus(true), 800);
      } else {
        setWebhookRepair("error");
      }
    } catch {
      if (mountedRef.current) setWebhookRepair("error");
    }
  }

  async function repairTracker() {
    setTrackerRepair("loading");
    try {
      const res = await fetch(
        `${apiBase}/setup/repair/tracker?shop=${encodeURIComponent(shop)}`,
        { method: "POST", headers: apiHeaders(), credentials: "include" }
      );
      if (!mountedRef.current) return;
      if (res.ok) {
        setTrackerRepair("success");
        setTimeout(() => fetchStatus(true), 800);
      } else {
        setTrackerRepair("error");
      }
    } catch {
      if (mountedRef.current) setTrackerRepair("error");
    }
  }

  // ---------------------------------------------------------------------------
  // Billing subscribe → redirect to Shopify billing page
  // ---------------------------------------------------------------------------
  async function startBillingUpgrade() {
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
      // Redirect merchant to Shopify billing confirmation page
      const confirmationUrl: string = json.confirmation_url;
      if (confirmationUrl) {
        window.location.href = confirmationUrl;
      } else {
        setBillingError("No confirmation URL returned. Please contact support.");
        setBillingLoading(false);
      }
    } catch {
      if (mountedRef.current) {
        setBillingError("Network error. Please check your connection and retry.");
        setBillingLoading(false);
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Dismiss handler — persists to sessionStorage
  // ---------------------------------------------------------------------------
  function handleDismiss() {
    setDismissed(true);
    persistDismiss(shop);
  }

  // ---------------------------------------------------------------------------
  // Render guards
  // ---------------------------------------------------------------------------

  // No shop yet — nothing to show
  if (!shop) return null;

  // Status not loaded yet — render skeleton placeholder so the space is reserved
  if (!status && !fetchError) {
    return (
      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4 animate-pulse">
        <div className="h-3 w-40 rounded bg-white/[0.06]" />
        <div className="mt-2 h-2.5 w-64 rounded bg-white/[0.04]" />
        {billingJustActivated && (
          <div className="mt-2 text-[11px] text-slate-600">
            Verifying billing with Shopify…
          </div>
        )}
      </div>
    );
  }

  // Fetch error — show minimal warning; rest of dashboard still renders
  if (fetchError || !status) {
    return (
      <div className="rounded-2xl border border-amber-400/20 bg-amber-500/[0.06] px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-amber-400" />
          <span className="text-[12px] font-semibold text-amber-300">
            Setup status unavailable
          </span>
          <button
            onClick={() => fetchStatus(false)}
            className="ml-auto text-[11px] text-amber-400/70 underline-offset-2 hover:text-amber-300 hover:underline"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  // Pro active and everything is fine — hide the panel entirely
  if (status.readiness === "pro_active") return null;

  // Merchant dismissed the lite_ready banner — hide it
  if (dismissed && status.readiness === "lite_ready") return null;

  const { checks } = status;

  // ---------------------------------------------------------------------------
  // DEGRADED — critical failure, reinstall required
  // ---------------------------------------------------------------------------
  if (status.readiness === "degraded") {
    return (
      <div className="rounded-2xl border border-rose-400/25 bg-rose-500/[0.07] p-4">
        <div className="flex items-start gap-3">
          {/* Icon */}
          <div className="mt-0.5 flex-shrink-0 rounded-lg bg-rose-500/20 p-1.5">
            <svg className="h-4 w-4 text-rose-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
            </svg>
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-[12px] font-semibold uppercase tracking-[0.12em] text-rose-400">
                Setup Incomplete
              </span>
            </div>
            <p className="mt-1 text-[13px] text-rose-200">
              {!checks.merchant_exists
                ? "Your store has no installation record. Please reinstall the app from the Shopify App Store."
                : !checks.install_active
                ? "The app was uninstalled from this store. Reinstall to resume tracking."
                : "Your access credentials are no longer valid. Please reinstall the app to restore them."}
            </p>

            {/* Check grid */}
            <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
              <CheckDot ok={checks.merchant_exists}  title="Store registered" />
              <CheckDot ok={checks.install_active}   title="App installed" />
              <CheckDot ok={checks.token_ok}         title="Credentials valid" />
            </div>

            {/* Degraded reasons — useful for support */}
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

  // ---------------------------------------------------------------------------
  // NEEDS REPAIR — token ok, but webhook / tracker missing
  // ---------------------------------------------------------------------------
  if (status.readiness === "needs_repair") {
    const webhookFixed = webhookRepair === "success" || checks.webhook_ok;
    const trackerFixed = trackerRepair === "success" || checks.tracker_ok;

    return (
      <div className="rounded-2xl border border-amber-400/25 bg-amber-500/[0.06] p-4">
        <div className="flex items-start gap-3">
          {/* Icon */}
          <div className="mt-0.5 flex-shrink-0 rounded-lg bg-amber-500/20 p-1.5">
            <svg className="h-4 w-4 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17L17.25 21A2.652 2.652 0 0021 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 11-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 004.486-6.336l-3.276 3.277a3.004 3.004 0 01-2.25-2.25l3.276-3.276a4.5 4.5 0 00-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437l1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008z" />
            </svg>
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between">
              <div className="text-[12px] font-semibold uppercase tracking-[0.12em] text-amber-400">
                Needs Repair
              </div>
              <VerifyButton state={deepCheck} onClick={triggerDeepCheck} auditMode={status.audit_mode} />
            </div>
            <p className="mt-1 text-[13px] text-amber-200/90">
              {!webhookFixed && !trackerFixed
                ? "Your lifecycle webhook and storefront tracker both need to be reconnected."
                : !webhookFixed
                ? "The lifecycle webhook needs to be reconnected to your store."
                : "The storefront tracker is not installed on your store."}
            </p>

            {/* Check grid */}
            <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
              <CheckDot ok={checks.token_ok}          title="Credentials valid" />
              <CheckDot ok={webhookFixed}              title="Lifecycle webhook" />
              <CheckDot ok={trackerFixed}              title="Storefront tracker" />
              <CheckDot ok={checks.billing_active}     title="Billing active" />
            </div>

            {/* Repair actions */}
            <div className="mt-3.5 flex flex-wrap items-center gap-2">
              {!webhookFixed && (
                <RepairButton
                  label="Reconnect webhook"
                  state={webhookRepair}
                  onClick={repairWebhook}
                />
              )}
              {!trackerFixed && (
                <RepairButton
                  label="Fix storefront tracker"
                  state={trackerRepair}
                  onClick={repairTracker}
                />
              )}
              {/* Refresh once both are marked fixed */}
              {webhookFixed && trackerFixed && (
                <button
                  onClick={() => fetchStatus(true)}
                  className="text-[11px] text-emerald-400/70 hover:text-emerald-400"
                >
                  Verify setup →
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // LITE READY — fully functional on Lite, Pro not yet active
  // ---------------------------------------------------------------------------
  if (status.readiness === "lite_ready") {
    return (
      <div className="rounded-2xl border border-violet-400/15 bg-violet-500/[0.04] p-4">
        <div className="flex items-start gap-3">
          {/* Status dots — compact health overview */}
          <div className="mt-0.5 hidden flex-shrink-0 flex-col gap-1 sm:flex">
            <div className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              <span className="text-[10px] text-slate-500">Tracker</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              <span className="text-[10px] text-slate-500">Connected</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className={`h-1.5 w-1.5 rounded-full ${checks.billing_active ? "bg-emerald-400" : "bg-slate-600"}`} />
              <span className="text-[10px] text-slate-500">Billing</span>
            </div>
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2">
              <div>
                <span className="text-[12px] font-semibold text-slate-300">
                  Tracking active —{" "}
                </span>
                <span className="text-[12px] text-slate-500">
                  your store is live on Hedge Spark Lite
                </span>
              </div>
              <div className="flex items-center gap-2">
                <VerifyButton state={deepCheck} onClick={triggerDeepCheck} auditMode={status.audit_mode} />
                <button
                  onClick={handleDismiss}
                  className="flex-shrink-0 rounded p-1 text-slate-600 transition hover:text-slate-400"
                  aria-label="Dismiss"
                >
                  <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            </div>

            {/* Pro upsell — trial-aware */}
            {!checks.billing_active && (() => {
              const hasTrial = trialDays > 0;
              const priceStr = price % 1 === 0 ? `$${price}` : `$${price.toFixed(2)}`;
              return (
              <div className="mt-3 rounded-xl border border-violet-400/15 bg-violet-500/[0.06] px-4 py-3">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <div className="text-[13px] font-semibold text-white">
                      {hasTrial
                        ? `Try Pro free for ${trialDays} days`
                        : "Upgrade to Pro — see what to do, not just what happened"}
                    </div>
                    <div className="mt-0.5 text-[12px] text-slate-500">
                      {hasTrial
                        ? `AI actions, daily briefs, market intelligence. Then ${priceStr}/mo.`
                        : "AI actions per product, daily briefs, market intelligence."}
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
                      {billingLoading
                        ? "Opening Shopify billing…"
                        : hasTrial
                        ? `Start ${trialDays}-day free trial`
                        : `Get Pro — ${priceStr}/mo`}
                    </button>
                  )}
                </div>
                {billingError && (
                  <p className="mt-2 text-[12px] text-rose-400">{billingError}</p>
                )}
              </div>
              );
            })()}
          </div>
        </div>
      </div>
    );
  }

  return null;
}
