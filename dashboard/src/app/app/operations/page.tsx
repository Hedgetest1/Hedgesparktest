"use client";

/**
 * /app/operations — Operations floor.
 *
 * Scale-tier infrastructure surface: multi-store groups, agency
 * white-label, Shopify Admin actions, Unified ads connector, API
 * access. Phase 1.8.1 shipped the route; Phase 1.8.2 migrated it
 * to FloorLayout; Phase 1.8.4 will wire live Scale components
 * (groups + agency + ads + API management).
 *
 * `isScaleUser` is hardcoded false today. Phase 1.8.4 will derive
 * it from the real plan (plan === "scale" && billing_active).
 */

import { FloorLayout } from "../../components/FloorLayout";

const FEATURES = [
  { name: "Unified Ads Connector", desc: "Meta + Google + TikTok spend in one ROAS view. True blended CAC." },
  { name: "Multi-store Groups", desc: "Cross-shop revenue rollup. Consolidated dashboard across every store you own." },
  { name: "Agency White-label", desc: "Branded reports, sub-client management, per-client margin dashboards." },
  { name: "API Access", desc: "Pull every HedgeSpark metric into your stack. REST + webhooks + full OpenAPI spec." },
  { name: "Shopify Admin Actions", desc: "One-click inventory updates, discount creation, price changes — audit-logged and reversible." },
  { name: "Shopify Flow Integration", desc: "Your HedgeSpark signals become Flow triggers. Automate across your entire Shopify stack." },
];

export default function OperationsPage() {
  // TODO Phase 1.8.4 — derive from real plan check.
  const isScaleUser = false;

  return (
    <FloorLayout floor="operations">
      {() => (
        <>
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
                <div className="min-w-[200px] flex-1">
                  <h2 className="text-[18px] font-bold text-white">
                    Scale adds {FEATURES.length} operational tools
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
            {FEATURES.map((f) => (
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
        </>
      )}
    </FloorLayout>
  );
}
