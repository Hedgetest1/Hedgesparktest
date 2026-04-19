"use client";

/**
 * /app/operations — Operations floor (Phase 1.8.1 foundation).
 *
 * Scale-tier infrastructure surface: multi-store groups, agency
 * white-label, Shopify Admin actions (inventory/discount/price/
 * products), Ads connector (Meta / Google / TikTok), API access.
 *
 * Phase 1.8.1 ships foundation + preview list for non-Scale tiers.
 * Phase 1.8.4 migrates live Scale components here (multi-store
 * groups, agency console).
 */

import { useState } from "react";
import { Sidebar } from "../../components/Sidebar";
import { TopBar } from "../../components/TopBar";
import { useSession } from "../../lib/useSession";

export default function OperationsPage() {
  const { shop, tier, isProUser, isPreviewing, resolved } = useSession();
  const [collapsed, setCollapsed] = useState(false);

  // Operations floor highlights — Scale tier only. Lower tiers see
  // this list as a preview with lock badges.
  const features = [
    { name: "Unified Ads Connector", desc: "Meta + Google + TikTok spend in one ROAS view. True blended CAC." },
    { name: "Multi-store Groups", desc: "Cross-shop revenue rollup. Consolidated dashboard across every store you own." },
    { name: "Agency White-label", desc: "Branded reports, sub-client management, per-client margin dashboards." },
    { name: "API Access", desc: "Pull every HedgeSpark metric into your stack. REST + webhooks + full OpenAPI spec." },
    { name: "Shopify Admin Actions", desc: "One-click inventory updates, discount creation, price changes — audit-logged and reversible." },
    { name: "Shopify Flow Integration", desc: "Your HedgeSpark signals become Flow triggers. Automate across your entire Shopify stack." },
  ];

  const isScaleUser = false; // TODO Phase 1.8.4 — wire to real plan check

  if (!resolved) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#07070f] text-slate-400">
        <div className="animate-pulse text-[14px]">Loading your plan…</div>
      </div>
    );
  }

  if (!shop) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 bg-[#07070f] text-slate-300">
        <p className="text-[14px]">Your session expired.</p>
        <a href="/install" className="rounded-lg bg-[#d4893a] px-4 py-2 text-[13px] font-bold text-white">
          Reconnect your store
        </a>
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[#07070f] text-white">
      {isPreviewing && (
        <div
          className="fixed inset-x-0 top-0 z-[9999] flex items-center justify-center gap-3 bg-[#e8a04e] px-4 py-2 text-[13px] font-bold text-[#0b1220] shadow-[0_4px_20px_-4px_rgba(232,160,78,0.5)]"
          role="status"
          aria-live="polite"
        >
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-[#0b1220]" />
          Previewing as Starter — you are seeing the Lite experience
          <button
            type="button"
            onClick={() => {
              const url = new URL(window.location.href);
              url.searchParams.delete("as");
              window.location.href = url.toString();
            }}
            className="ml-2 rounded-md border border-[#0b1220]/40 bg-[#0b1220]/10 px-3 py-0.5 text-[12px] font-bold uppercase tracking-[0.1em] transition-colors hover:bg-[#0b1220]/20"
          >
            Exit preview
          </button>
        </div>
      )}
      <Sidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed((c) => !c)}
        activeSection=""
        onNavigate={() => {}}
        tier={tier}
        currentFloor="operations"
      />

      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar
          shop={shop}
          tier={tier}
          onTierToggle={() => {}}
          trial={{ daysRemaining: null, isPaidPro: isProUser }}
        />

        <main className="flex-1 overflow-y-auto px-6 py-10 lg:px-10">
          <div className="mx-auto max-w-[72rem]">
            <div className="mb-10">
              <div className="text-[11px] font-bold uppercase tracking-[0.2em] text-[#3b82f6]">
                Floor 3 · Operations
              </div>
              <h1 className="mt-3 text-[2rem] font-extrabold leading-[1.1] text-white sm:text-[2.5rem]">
                Agency-grade tooling. Multi-store. API.
              </h1>
              <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-slate-400">
                For operators managing multiple stores, agencies
                white-labeling HedgeSpark for clients, and teams who
                need to integrate every metric into their own systems.
                Triple Whale Agency starts at $1,500/mo for comparable
                feature-set.
              </p>
            </div>

            {!isScaleUser && (
              <div className="mb-8 rounded-2xl border border-[#3b82f6]/25 bg-gradient-to-br from-[#3b82f6]/[0.06] to-transparent p-6">
                <div className="flex flex-wrap items-center gap-4">
                  <div className="flex-1 min-w-[200px]">
                    <h2 className="text-[18px] font-bold text-white">
                      Scale adds {features.length} operational tools
                    </h2>
                    <p className="mt-1 text-[13.5px] leading-relaxed text-slate-400">
                      Upgrade to Scale to unlock multi-store
                      consolidation, white-label reports, unified ads
                      connector, and full API access.
                    </p>
                  </div>
                  <a
                    href="/#pricing"
                    className="rounded-lg bg-[#3b82f6] px-5 py-2.5 text-[13px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-[#60a5fa]"
                  >
                    Upgrade to Scale
                  </a>
                </div>
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {features.map((f) => (
                <div
                  key={f.name}
                  className="group relative rounded-2xl border border-white/[0.06] bg-[#0e0e1a] p-5 transition-colors hover:border-white/[0.1]"
                >
                  {!isScaleUser && (
                    <div className="absolute right-4 top-4">
                      <span className="inline-flex items-center gap-1 rounded-md border border-[#3b82f6]/25 bg-[#3b82f6]/[0.08] px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.1em] text-[#3b82f6]">
                        <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z" />
                        </svg>
                        Scale
                      </span>
                    </div>
                  )}
                  <h3 className="text-[15px] font-bold text-white">{f.name}</h3>
                  <p className="mt-2 text-[13px] leading-[1.55] text-slate-400">
                    {f.desc}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
