"use client";

/**
 * SettingsSection — Preferences + integrations (all tiers).
 *
 * Extracted from app/page.tsx PageInner as part of the Phase Ω⁷ split.
 * Takes a single typed props bag so the parent owns all state.
 */

import { SectionHeading } from "../_components/SectionHeading";
import { ConnectToolsPanel } from "../../components/ConnectToolsPanel";
import { YourTeamPanel } from "../../components/YourTeamPanel";

type CostDefaults = {
  default_cogs_pct?: number | null;
  default_shipping_cost?: number | null;
  default_payment_pct?: number | null;
  default_payment_flat?: number | null;
  default_monthly_ad_spend?: number | null;
  updated_at?: string | null;
};

type PnlData = { precision: string } | null;

type KlaviyoStatus = {
  status: string;
  has_key: boolean;
  key_hint: string | null;
  last_verified_at: string | null;
  last_error: string | null;
  last_sync_at: string | null;
  last_sync_error: string | null;
} | null;

type Msg = { type: "ok" | "err"; text: string } | null;

export interface SettingsSectionProps {
  apiBase: string;
  shop: string;
  tier: "lite" | "pro";
  isProUser: boolean;

  // Display currency
  displayCurrency: "USD" | "EUR";
  setDisplayCurrency: (c: "USD" | "EUR") => void;

  // Cost config
  costDefaults: CostDefaults | null;
  costFormCogsPct: string;
  setCostFormCogsPct: (v: string) => void;
  costFormShipping: string;
  setCostFormShipping: (v: string) => void;
  costFormAdSpend: string;
  setCostFormAdSpend: (v: string) => void;
  costFormPayPct: string;
  setCostFormPayPct: (v: string) => void;
  costFormPayFlat: string;
  setCostFormPayFlat: (v: string) => void;
  costSaving: boolean;
  costSavedMsg: Msg;
  costSyncing: boolean;
  costSyncMsg: Msg;
  pnlData: PnlData;
  handleCostDefaultsSave: () => void;
  handleShopifyCogsSync: () => void;

  // Klaviyo
  klaviyoStatus: KlaviyoStatus;
  klaviyoIsConnected: boolean;
  klaviyoKeyInput: string;
  setKlaviyoKeyInput: (v: string) => void;
  klaviyoConnecting: boolean;
  klaviyoShowReplace: boolean;
  setKlaviyoShowReplace: (v: boolean) => void;
  klaviyoMessage: Msg;
  setKlaviyoMessage: (m: Msg) => void;
  handleKlaviyoConnect: () => void;
  handleKlaviyoDisconnect: () => void;

  // Privacy (Art. 22)
  privacyOptedOut: boolean;
  privacyLoading: boolean;
  handlePrivacyToggle: () => void;

  // Lite → Pro upgrade
  setUpgradeModalOpen: (v: boolean) => void;
}

export function SettingsSection(p: SettingsSectionProps) {
  return (
    <section id="section-settings">
      <SectionHeading
        eyebrow="Settings"
        title="Preferences & integrations"
      />

      {/* Display currency card */}
      <div className="mb-4 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#e8a04e]/10 text-[#e8a04e]">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor" className="h-5 w-5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m-3-2.818.879.659c1.171.879 3.07.879 4.242 0 1.172-.879 1.172-2.303 0-3.182C13.536 12.219 12.768 12 12 12c-2.25 0-3-1.125-3-2.25s.75-2.25 3-2.25c.768 0 1.536.219 2.121.659l.879.659" />
                </svg>
              </div>
              <div>
                <span className="block text-[13px] font-semibold text-white">Display currency</span>
                <span className="block text-[11px] text-slate-500">
                  How amounts are shown across the dashboard.
                  {p.displayCurrency === "EUR" && " Values are converted from USD at a static rate of 0.92."}
                </span>
              </div>
            </div>
          </div>

          <div className="inline-flex flex-shrink-0 rounded-xl border border-white/[0.08] bg-white/[0.02] p-1" role="radiogroup" aria-label="Display currency">
            {(["USD", "EUR"] as const).map((c) => {
              const isActive = p.displayCurrency === c;
              return (
                <button
                  key={c}
                  type="button"
                  role="radio"
                  aria-checked={isActive}
                  onClick={() => p.setDisplayCurrency(c)}
                  className={`relative rounded-lg px-5 py-2 text-[13px] font-bold transition-all duration-200 ${
                    isActive
                      ? "bg-[#e8a04e]/15 text-[#e8a04e] shadow-[0_0_12px_-2px_rgba(232,160,78,0.35)]"
                      : "text-slate-500 hover:text-slate-300"
                  }`}
                >
                  <span className="mr-1.5 tabular-nums">{c === "USD" ? "$" : "€"}</span>
                  {c}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Cost Configuration card */}
      <div className="mb-4 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-400">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor" className="h-5 w-5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 18L9 11.25l4.306 4.306a11.95 11.95 0 015.814-5.519l2.74-1.22m0 0l-5.94-2.28m5.94 2.28l-2.28 5.941" />
              </svg>
            </div>
            <div>
              <span className="block text-[13px] font-semibold text-white">Cost Configuration</span>
              <span className="block text-[11px] text-slate-500">
                Real costs per sale — powers Profit Intelligence precision.
              </span>
            </div>
          </div>
          {p.pnlData && (
            <span
              className={`rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] ring-1 ${
                p.pnlData.precision === "exact"
                  ? "bg-emerald-500/15 text-emerald-400 ring-emerald-400/30"
                  : p.pnlData.precision === "refined"
                  ? "bg-amber-500/15 text-amber-400 ring-amber-400/30"
                  : "bg-white/5 text-slate-500 ring-white/10"
              }`}
            >
              {p.pnlData.precision}
            </span>
          )}
        </div>

        <p className="mb-4 text-[11px] leading-relaxed text-slate-500">
          Override the default cost assumptions with your real numbers. Leave any
          field empty to keep the current default. Every saved field lifts your
          Profit Intelligence precision from <em>rough</em> toward <em>exact</em>.
        </p>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <label className="block">
            <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">COGS %</span>
            <div className="relative">
              <input
                type="number" inputMode="decimal" step="0.1" min="0" max="100"
                value={p.costFormCogsPct}
                onChange={(e) => p.setCostFormCogsPct(e.target.value)}
                placeholder="40"
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 pr-8 text-[13px] text-white tabular-nums outline-none transition-colors focus:border-emerald-400/40 focus:bg-white/[0.05]"
              />
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[12px] text-slate-500">%</span>
            </div>
            <span className="mt-1 block text-[10px] text-slate-600">Cost of goods as % of revenue</span>
          </label>

          <label className="block">
            <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">Shipping per order</span>
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[12px] text-slate-500">
                {p.displayCurrency === "EUR" ? "€" : "$"}
              </span>
              <input
                type="number" inputMode="decimal" step="0.01" min="0"
                value={p.costFormShipping}
                onChange={(e) => p.setCostFormShipping(e.target.value)}
                placeholder="5.00"
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 pl-7 text-[13px] text-white tabular-nums outline-none transition-colors focus:border-emerald-400/40 focus:bg-white/[0.05]"
              />
            </div>
            <span className="mt-1 block text-[10px] text-slate-600">Fulfillment + carrier cost per order</span>
          </label>

          <label className="block">
            <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">Ad spend / month</span>
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[12px] text-slate-500">
                {p.displayCurrency === "EUR" ? "€" : "$"}
              </span>
              <input
                type="number" inputMode="decimal" step="1" min="0"
                value={p.costFormAdSpend}
                onChange={(e) => p.setCostFormAdSpend(e.target.value)}
                placeholder="0"
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 pl-7 text-[13px] text-white tabular-nums outline-none transition-colors focus:border-emerald-400/40 focus:bg-white/[0.05]"
              />
            </div>
            <span className="mt-1 block text-[10px] text-slate-600">Bridge until Meta + Google Ads connect</span>
          </label>

          <label className="block">
            <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">Payment %</span>
            <div className="relative">
              <input
                type="number" inputMode="decimal" step="0.01" min="0" max="100"
                value={p.costFormPayPct}
                onChange={(e) => p.setCostFormPayPct(e.target.value)}
                placeholder="2.9"
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 pr-8 text-[13px] text-white tabular-nums outline-none transition-colors focus:border-emerald-400/40 focus:bg-white/[0.05]"
              />
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[12px] text-slate-500">%</span>
            </div>
            <span className="mt-1 block text-[10px] text-slate-600">Payment processor rate (Shopify default 2.9%)</span>
          </label>

          <label className="block">
            <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">Payment flat</span>
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[12px] text-slate-500">
                {p.displayCurrency === "EUR" ? "€" : "$"}
              </span>
              <input
                type="number" inputMode="decimal" step="0.01" min="0"
                value={p.costFormPayFlat}
                onChange={(e) => p.setCostFormPayFlat(e.target.value)}
                placeholder="0.30"
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 pl-7 text-[13px] text-white tabular-nums outline-none transition-colors focus:border-emerald-400/40 focus:bg-white/[0.05]"
              />
            </div>
            <span className="mt-1 block text-[10px] text-slate-600">Flat fee per order (Shopify default 0.30)</span>
          </label>
        </div>

        <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-h-[1.25rem] text-[11px]">
            {p.costSavedMsg && (
              <span className={p.costSavedMsg.type === "ok" ? "text-emerald-400" : "text-rose-400"}>
                {p.costSavedMsg.text}
              </span>
            )}
            {!p.costSavedMsg && p.costSyncMsg && (
              <span className={p.costSyncMsg.type === "ok" ? "text-emerald-400" : "text-rose-400"}>
                {p.costSyncMsg.text}
              </span>
            )}
            {!p.costSavedMsg && !p.costSyncMsg && p.costDefaults?.updated_at && (
              <span className="text-slate-600">
                Last updated {new Date(p.costDefaults.updated_at).toLocaleString()}
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={p.handleShopifyCogsSync}
              disabled={p.costSyncing}
              title="Import real COGS from Shopify — reads inventory_items.cost for every product variant"
              className="inline-flex items-center gap-2 rounded-lg bg-white/[0.04] px-4 py-2 text-[12px] font-semibold text-slate-300 ring-1 ring-white/10 transition-colors hover:bg-white/[0.07] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {p.costSyncing ? (
                <>
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-400/40 border-t-slate-300" />
                  Importing from Shopify…
                </>
              ) : (
                <>
                  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="h-3.5 w-3.5">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 13.5L12 21m0 0l-7.5-7.5M12 21V3" />
                  </svg>
                  Auto-import from Shopify
                </>
              )}
            </button>
            <button
              type="button"
              onClick={p.handleCostDefaultsSave}
              disabled={p.costSaving}
              className="inline-flex items-center gap-2 rounded-lg bg-emerald-500/20 px-4 py-2 text-[12px] font-semibold text-emerald-300 ring-1 ring-emerald-400/30 transition-colors hover:bg-emerald-500/25 hover:text-emerald-200 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {p.costSaving ? (
                <>
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-emerald-400/40 border-t-emerald-400" />
                  Saving…
                </>
              ) : (
                "Save cost config"
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Klaviyo card */}
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className={`flex h-9 w-9 items-center justify-center rounded-lg ${
              p.klaviyoIsConnected ? "bg-emerald-500/10 text-emerald-400" : "bg-white/5 text-slate-500"
            }`}>
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-5 w-5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
              </svg>
            </div>
            <div>
              <span className="block text-[13px] font-semibold text-white">Klaviyo</span>
              <span className="block text-[11px] text-slate-500">Email & SMS marketing automation</span>
            </div>
          </div>
          {p.klaviyoStatus && (
            <span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] ${
              p.klaviyoStatus.status === "connected"
                ? "bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-400/30"
                : p.klaviyoStatus.status === "invalid_key"
                ? "bg-red-500/15 text-red-400 ring-1 ring-red-400/30"
                : p.klaviyoStatus.has_key
                ? "bg-amber-500/15 text-amber-400 ring-1 ring-amber-400/30"
                : "bg-white/5 text-slate-500 ring-1 ring-white/10"
            }`}>
              {p.klaviyoStatus.status === "connected" ? "Connected" :
               p.klaviyoStatus.status === "invalid_key" ? "Invalid key" :
               p.klaviyoStatus.status === "unverified" ? "Unverified" :
               p.klaviyoStatus.status === "error" ? "Error" : "Not connected"}
            </span>
          )}
        </div>

        <div className="space-y-3">
          {p.klaviyoIsConnected && !p.klaviyoShowReplace && (
            <div className="rounded-xl border border-emerald-400/10 bg-emerald-500/[0.04] p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex h-7 w-7 items-center justify-center rounded-full bg-emerald-500/15">
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="h-3.5 w-3.5 text-emerald-400">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                    </svg>
                  </div>
                  <div>
                    <span className="block text-[12px] font-medium text-white">Klaviyo is connected</span>
                    <span className="block text-[11px] text-slate-500">
                      Key: <code className="font-mono text-slate-400">{p.klaviyoStatus?.key_hint}</code>
                      {p.klaviyoStatus?.last_verified_at && (
                        <span className="ml-1.5 text-slate-600">
                          · verified {new Date(p.klaviyoStatus.last_verified_at).toLocaleDateString()}
                        </span>
                      )}
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => p.setKlaviyoShowReplace(true)}
                    className="rounded-lg px-2.5 py-1.5 text-[10px] font-medium text-slate-500 transition-colors hover:bg-white/[0.05] hover:text-slate-300"
                  >
                    Replace key
                  </button>
                  <button
                    onClick={p.handleKlaviyoDisconnect}
                    className="rounded-lg px-2.5 py-1.5 text-[10px] font-medium text-red-400/50 transition-colors hover:bg-red-500/10 hover:text-red-400"
                  >
                    Disconnect
                  </button>
                </div>
              </div>

              {p.klaviyoStatus?.last_sync_at && (
                <div className="mt-3 border-t border-emerald-400/10 pt-3">
                  <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
                    <span className="font-semibold uppercase tracking-[0.08em]">Last sync</span>
                    <span className="text-slate-400">{new Date(p.klaviyoStatus.last_sync_at).toLocaleString()}</span>
                    {p.klaviyoStatus.last_sync_error && (
                      <span className="text-red-400/70">· {p.klaviyoStatus.last_sync_error}</span>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {(!p.klaviyoIsConnected || p.klaviyoShowReplace) && (
            <>
              {p.klaviyoStatus?.has_key && !p.klaviyoIsConnected && (
                <div className="flex items-center gap-2 rounded-lg bg-red-500/[0.06] px-3 py-2 text-[11px] text-red-400/80">
                  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-3.5 w-3.5 flex-shrink-0">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                  </svg>
                  <span>{p.klaviyoStatus.last_error || "Key needs verification"}</span>
                </div>
              )}

              <div className="flex gap-2">
                <input
                  type="password"
                  value={p.klaviyoKeyInput}
                  onChange={(e) => p.setKlaviyoKeyInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && p.klaviyoKeyInput.trim()) p.handleKlaviyoConnect(); }}
                  placeholder="Paste your Klaviyo Private API Key"
                  autoComplete="off"
                  className="flex-1 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2.5 text-[12px] text-white placeholder-slate-600 outline-none transition-colors focus:border-violet-400/40 focus:ring-1 focus:ring-violet-400/20"
                />
                <button
                  onClick={p.handleKlaviyoConnect}
                  disabled={p.klaviyoConnecting || !p.klaviyoKeyInput.trim()}
                  className="rounded-lg bg-violet-500/20 px-5 py-2.5 text-[12px] font-semibold text-violet-300 transition-all hover:bg-violet-500/30 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {p.klaviyoConnecting ? "Connecting..." : "Connect"}
                </button>
                {p.klaviyoShowReplace && (
                  <button
                    onClick={() => {
                      p.setKlaviyoShowReplace(false);
                      p.setKlaviyoKeyInput("");
                      p.setKlaviyoMessage(null);
                    }}
                    className="rounded-lg border border-white/[0.06] px-3 py-2.5 text-[11px] text-slate-500 transition-colors hover:bg-white/[0.03]"
                  >
                    Cancel
                  </button>
                )}
              </div>

              <p className="text-[10px] leading-relaxed text-slate-600">
                Find your Private API Key in Klaviyo: Account → Settings → API Keys.
                Your key is encrypted at rest and never displayed after saving.
              </p>
            </>
          )}

          {p.klaviyoMessage && (
            <div className={`rounded-lg px-3 py-2 text-[11px] ${
              p.klaviyoMessage.type === "ok"
                ? "bg-emerald-500/10 text-emerald-400"
                : "bg-red-500/10 text-red-400"
            }`}>
              {p.klaviyoMessage.text}
            </div>
          )}
        </div>
      </div>

      {/* Lite tier upgrade hint */}
      {p.tier === "lite" && (
        <div className="mt-4 flex items-center gap-2.5 rounded-xl border border-violet-400/10 bg-violet-500/[0.04] px-4 py-3">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4 flex-shrink-0 text-violet-400/60">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" />
          </svg>
          <span className="text-[11px] text-violet-300/70">
            Connect now — upgrade to{" "}
            <button
              onClick={() => p.setUpgradeModalOpen(true)}
              className="font-semibold text-violet-300 underline decoration-violet-400/30 underline-offset-2 transition-colors hover:text-violet-200"
            >
              Pro
            </button>{" "}
            to unlock automated flows and AI-driven actions.
          </span>
        </div>
      )}

      {/* Privacy — Art. 22 */}
      <div className="mb-4 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-violet-500/10 text-violet-400">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor" className="h-5 w-5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
                </svg>
              </div>
              <div>
                <span className="block text-[13px] font-semibold text-white">Automated targeting</span>
                <span className="block text-[11px] text-slate-500">
                  {p.privacyOptedOut
                    ? "Opted out — AI scoring, nudge composition, and automated targeting are disabled for your store."
                    : "Enabled — HedgeSpark uses AI to score visitors, compose nudges, and target recommendations."}
                </span>
              </div>
            </div>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={!p.privacyOptedOut}
            disabled={p.privacyLoading}
            onClick={p.handlePrivacyToggle}
            className={`relative inline-flex h-7 w-12 flex-shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
              p.privacyOptedOut ? "bg-white/[0.08]" : "bg-violet-500/60"
            } ${p.privacyLoading ? "opacity-50 cursor-wait" : ""}`}
          >
            <span
              className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                p.privacyOptedOut ? "translate-x-0.5" : "translate-x-[22px]"
              }`}
            />
          </button>
        </div>
        <p className="mt-3 text-[10px] leading-relaxed text-slate-600">
          GDPR Art. 22 & CCPA §1798.120 — you can opt out of automated decision-making
          and profiling at any time. This disables AI-powered features but does not affect
          basic analytics. You can re-enable it whenever you want.
        </p>
      </div>

      {/* Pro-only: outbound webhooks + team collab */}
      {p.isProUser && (
        <div className="mt-4 space-y-4">
          <ConnectToolsPanel apiBase={p.apiBase} shop={p.shop} isProUser={p.isProUser} />
          <YourTeamPanel apiBase={p.apiBase} shop={p.shop} isProUser={p.isProUser} />
        </div>
      )}
    </section>
  );
}
