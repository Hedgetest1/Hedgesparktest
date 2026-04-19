"use client";

/**
 * /app/intelligence — Intelligence floor (Phase 1.8.1 foundation).
 *
 * Pro-tier deep analytics surface. This commit ships the foundation
 * only: route + sidebar + top bar + placeholder content. Content
 * migration from /app/page.tsx happens in Phase 1.8.2 (move Pro
 * components here) and Phase 1.8.3 (remove them from /app).
 *
 * Lite merchants reaching this route see a preview list of what Pro
 * unlocks. Pro merchants will see the full Intelligence dashboard
 * once migration lands.
 *
 * NEVER hide features — every Pro capability is listed here visibly
 * for Lite merchants, with a lock icon + upgrade CTA. Per
 * `feedback_no_silent_feature_removal.md`.
 */

import { useState } from "react";
import { Sidebar } from "../../components/Sidebar";
import { TopBar } from "../../components/TopBar";
import { useSession } from "../../lib/useSession";

export default function IntelligencePage() {
  const { shop, tier, isProUser, isPreviewing, resolved } = useSession();
  const [collapsed, setCollapsed] = useState(false);

  // Intelligence floor highlights — shown as preview to Lite, as
  // content sections (in 1.8.2) for Pro. Each entry maps to a Pro
  // component that lives on /app today and will move here.
  const features = [
    { name: "Revenue Autopsy", desc: "Per-product diagnosis: where each product loses sales and why." },
    { name: "Causal Lift", desc: "Real A/B holdout measurement. Causation with statistical confidence, not correlation." },
    { name: "Cohort & LTV", desc: "Customer lifetime value by acquisition date, source, device, and behavior." },
    { name: "P&L Intelligence", desc: "Profitability per product and channel. Sync costs from Shopify or set them manually." },
    { name: "Peer Benchmarks", desc: "Anonymous comparison against shops in your revenue band." },
    { name: "Price Sensitivity", desc: "Detects which products have price friction; engage but bounce at the price point." },
    { name: "Session Replay + Heatmaps", desc: "Full behavioral replay per visitor + scroll depth per product page." },
    { name: "AI Nudge Composer + Holdout Proof", desc: "Auto-deploy targeted nudges; every result measured against a control group." },
    { name: "Ask HedgeSpark", desc: "Chat with your store data. Natural language over the Knowledge Graph." },
    { name: "Night Shift Agent", desc: "Autonomous overnight work log. Sees what happened, proposes what to do next." },
    { name: "Revenue Genome", desc: "DNA of your revenue — which sources, segments, and products drive profit." },
    { name: "Multi-touch Attribution", desc: "5-model MTA comparison. See first-click vs last-click vs data-driven side by side." },
    { name: "Risk Forecast", desc: "Predicted churn and revenue decline; flags products and segments heading for trouble." },
    { name: "Competitor Playbook", desc: "What the most successful shops in your vertical did to beat the leak." },
  ];

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
        currentFloor="intelligence"
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
              <div className="text-[11px] font-bold uppercase tracking-[0.2em] text-[#e8a04e]">
                Floor 2 · Intelligence
              </div>
              <h1 className="mt-3 text-[2rem] font-extrabold leading-[1.1] text-white sm:text-[2.5rem]">
                Deep analytics. Every number defended.
              </h1>
              <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-slate-400">
                Cohort LTV, causal lift, holdout-measured nudges, AI chatbot
                over your store data. This floor is where HedgeSpark moves
                from &ldquo;right now&rdquo; (Pulse) to &ldquo;why, how much,
                what next&rdquo;.
              </p>
            </div>

            {!isProUser && (
              <div className="mb-8 rounded-2xl border border-[#e8a04e]/25 bg-gradient-to-br from-[#e8a04e]/[0.06] to-transparent p-6">
                <div className="flex flex-wrap items-center gap-4">
                  <div className="flex-1 min-w-[200px]">
                    <h2 className="text-[18px] font-bold text-white">
                      {features.length} more capabilities on Pro
                    </h2>
                    <p className="mt-1 text-[13.5px] leading-relaxed text-slate-400">
                      Your Starter tier gives you the full Pulse floor.
                      Pro adds everything listed below — deep analytics
                      that Triple Whale and Peel charge $99-279/mo for.
                    </p>
                  </div>
                  <a
                    href="/#pricing"
                    className="rounded-lg bg-[#d4893a] px-5 py-2.5 text-[13px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-[#e8a04e]"
                  >
                    Upgrade to Pro
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
                  {!isProUser && (
                    <div className="absolute right-4 top-4">
                      <span className="inline-flex items-center gap-1 rounded-md border border-[#d4893a]/25 bg-[#d4893a]/[0.08] px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.1em] text-[#d4893a]">
                        <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z" />
                        </svg>
                        Pro
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

            {isProUser && (
              <div className="mt-10 rounded-2xl border border-violet-400/20 bg-violet-500/[0.04] p-5">
                <p className="text-[13px] leading-relaxed text-slate-300">
                  You&apos;re on Pro. The live interactive versions of
                  these cards are migrating to this floor in the next
                  deploy — Phase 1.8.2. For now, use the Pulse floor
                  (home) for your daily dashboard.
                </p>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
