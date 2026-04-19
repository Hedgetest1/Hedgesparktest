"use client";

/**
 * /app/intelligence — Intelligence floor.
 *
 * Pro-tier deep analytics surface. Phase 1.8.1 shipped the route;
 * Phase 1.8.2 extracted the auth + layout scaffolding into FloorLayout.
 * Phase 1.8.3 starts migrating live Pro components here, one card at a
 * time per `feedback_commit_per_card.md`. Cards listed in `MIGRATED_CARDS`
 * render live for Pro users; everything else still renders as a locked-
 * preview tile in the grid below. Starter users see the full locked-
 * preview grid regardless of migration status — the upgrade story stays
 * intact until every card is live.
 */

import { FloorLayout } from "../../components/FloorLayout";
import { RecommendationImpactCard } from "../../components/RecommendationImpactCard";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

const FEATURES: { name: string; desc: string }[] = [
  { name: "Revenue Autopsy", desc: "Per-product diagnosis: where each product loses sales and why." },
  { name: "Causal Lift", desc: "Real A/B holdout measurement. Causation with statistical confidence, not correlation." },
  { name: "Recommendation Impact", desc: "Every action you deployed, measured pre/post against its own revenue history." },
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

// Cards that have a live migrated component rendered above the preview
// grid. Keep this list in sync with the <... /> renders in the Pro
// section below. When every feature is migrated we remove the preview
// grid entirely (Phase 1.8.4 close-out).
const MIGRATED_CARDS = new Set<string>([
  "Recommendation Impact",
]);

export default function IntelligencePage() {
  return (
    <FloorLayout floor="intelligence">
      {({ isProUser, shop }) => {
        const previewFeatures = isProUser
          ? FEATURES.filter((f) => !MIGRATED_CARDS.has(f.name))
          : FEATURES;

        return (
          <>
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
                  <div className="min-w-[200px] flex-1">
                    <h2 className="text-[18px] font-bold text-white">
                      {FEATURES.length} more capabilities on Pro
                    </h2>
                    <p className="mt-1 text-[13.5px] leading-relaxed text-slate-400">
                      Your Starter tier gives you the full Pulse floor.
                      Pro adds everything listed below — deep analytics
                      that Triple Whale and Peel charge $99-279/mo for.
                    </p>
                  </div>
                  <a
                    href="/app?upgrade=1"
                    className="rounded-lg bg-[#d4893a] px-5 py-2.5 text-[13px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-[#e8a04e]"
                  >
                    Upgrade to Pro
                  </a>
                </div>
              </div>
            )}

            {isProUser && shop && (
              <div className="mb-8 space-y-4">
                <RecommendationImpactCard
                  apiBase={API_BASE}
                  shop={shop}
                  isProUser={isProUser}
                />
              </div>
            )}

            {previewFeatures.length > 0 && (
              <>
                {isProUser && (
                  <div className="mb-4 text-[11px] font-bold uppercase tracking-[0.16em] text-slate-500">
                    Still migrating · live versions arrive one by one
                  </div>
                )}
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {previewFeatures.map((f) => (
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
              </>
            )}
          </>
        );
      }}
    </FloorLayout>
  );
}
