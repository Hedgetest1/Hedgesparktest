"use client";

/**
 * LiteLast7DaysSection — operational depth on the Lite floor.
 *
 * Closes the second half of the 2026-04-25 audit Cat-A follow-up: the
 * Today section answers "where do I stand now?", but every cheap
 * Shopify analytics tool also surfaces (a) a 7-day revenue arc and
 * (b) a visitor funnel V→ATC→CO→Buy. Lifetimely Free, OrderMetrics,
 * Better Reports all show this depth at €0–9. Without it, a merchant
 * on the Lite floor has no operational trend context — they see today
 * vs yesterday but no week-shape arc and no drop-off insight.
 *
 * One section, two sub-blocks, cyan accent (distinct from Today's
 * emerald → "right now" vs cyan → "how you got here"):
 *
 *   1. 7-day revenue trend  — reuses RevenueTrendChart (self-fetches
 *      /orders/daily-revenue, momentum chip, hover tooltip)
 *   2. Visitor funnel       — reuses FunnelVisualization with the
 *      page-level funnelSteps (parent already fetches /analytics/funnel
 *      on every dashboard paint — no duplicate request)
 *
 * Component reuse rules followed (audit_pro_scale_before_build): both
 * underlying components existed and were rendered only on Pro floors;
 * this wrapper exposes them on Lite without forking visuals.
 */

import { RevenueTrendChart } from "./RevenueTrendChart";
import { SectionErrorBoundary } from "./SectionErrorBoundary";
import { FunnelVisualization, type FunnelStepShape } from "../app/_components/FunnelVisualization";
import type { DisplayCurrency } from "../lib/currency";

export function LiteLast7DaysSection({
  apiBase,
  shop,
  displayCurrency,
  funnelSteps,
}: {
  apiBase: string;
  shop: string;
  displayCurrency: DisplayCurrency;
  funnelSteps: FunnelStepShape[];
}) {
  const hasFunnel = funnelSteps.length > 0 && funnelSteps[0]?.count > 0;

  return (
    <section
      id="section-lite-last7"
      aria-labelledby="lite-last7-heading"
      className="relative mb-8 overflow-hidden rounded-3xl border border-cyan-400/[0.15] bg-gradient-to-br from-[#0a121a] via-[#0a0a14] to-[#0b0c18] p-7 sm:p-9"
    >
      <SectionErrorBoundary name="Last 7 days">
      <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-transparent via-[#22d3ee] to-transparent opacity-50" />
      <div className="pointer-events-none absolute -right-32 -top-32 h-[340px] w-[340px] rounded-full bg-[#22d3ee]/[0.05] blur-[150px]" />

      <div className="relative">
        <div className="mb-5">
          <h2
            id="lite-last7-heading"
            className="text-[2rem] font-extrabold leading-[1.05] tracking-tight text-[#67e8f9] sm:text-[2.5rem]"
          >
            Last 7 days
          </h2>
          <div className="mt-1 text-[16px] font-medium leading-snug text-slate-200 sm:text-[17px]">
            How you got here — revenue arc and where visitors drop off
          </div>
          <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-slate-400">
            The week-shape behind today&apos;s number, plus the
            visitor funnel from product view through purchase. Spot
            the day that broke the trend and the step that loses the
            most carts before they reach checkout.
          </p>
        </div>

        {/* Sub-block 1 — 7-day revenue arc */}
        <div className="mb-7">
          <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-slate-400">
            Revenue trend · day-by-day
          </div>
          <RevenueTrendChart
            apiBase={apiBase}
            shop={shop}
            displayCurrency={displayCurrency}
          />
        </div>

        {/* Sub-block 2 — Visitor funnel */}
        <div className="border-t border-white/[0.06] pt-6">
          <div className="mb-3 text-[11px] font-bold uppercase tracking-[0.16em] text-slate-400">
            Visitor funnel · view → cart → checkout → purchase
          </div>
          {hasFunnel ? (
            <FunnelVisualization steps={funnelSteps} />
          ) : (
            <div className="rounded-xl border border-dashed border-white/[0.10] bg-white/[0.015] px-5 py-6 text-center">
              <div className="text-[12.5px] font-semibold text-slate-300">
                No visitor signal yet
              </div>
              <p className="mt-1 text-[11.5px] leading-relaxed text-slate-400">
                Once your storefront tracker fires its first
                product_view event, the four funnel steps and per-step
                drop-off rates appear here automatically.
              </p>
            </div>
          )}
        </div>

        <p className="mt-5 text-[11px] leading-relaxed text-slate-400">
          <span className="font-semibold text-slate-400">How this is measured.</span>{" "}
          Revenue trend reads <code className="rounded bg-white/[0.04] px-1 py-0.5 text-slate-400">shop_orders</code> in your store&apos;s
          primary currency, day-bucketed in your shop timezone.
          Funnel counts <em>distinct</em> visitor IDs at each event
          stage — so one visitor browsing five products counts as one
          product-view, not five.
        </p>
      </div>
      </SectionErrorBoundary>
    </section>
  );
}
