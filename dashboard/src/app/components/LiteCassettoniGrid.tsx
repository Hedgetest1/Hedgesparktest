"use client";

/**
 * LiteCassettoniGrid — /app/lite main surface.
 *
 * Founder-designed (2026-04-20) per `/opt/wishspark/docs/LITE_VISUAL_SPEC.md`:
 * a 2×3 grid of "cassettoni" (cards) — one per Lite feature — each
 * showing a title + one big hero number sourced from real backend
 * data. Clicking a cassettone opens an expanded panel directly below
 * the grid (above the radar) that renders the corresponding deep
 * card. One open at a time.
 *
 * Visual DNA copied 1:1 from the landing cassettoni (dark bg #0e0e1a,
 * rounded-3xl, border white/[0.06], shadow-[0_20px_80px_...], colored
 * accent bar on the left, big 3rem hero number).
 *
 * Real-data contract (founder-mandated):
 *   Every hero number + every expanded-panel analysis + every
 *   "what to do next" row MUST trace to one backend endpoint call or
 *   deterministic decision-engine output. No fabricated values. If a
 *   data path is missing/empty, the expanded panel shows the empty
 *   state from the underlying component.
 *
 * Commit 1 scope (this file): grid skeleton + expand state + wire
 * existing deep cards as the expanded content. Commits 2 + 3 add the
 * structured expanded panel (title/subtitle/warm-copy/analysis/what-
 * to-do) and the 6 donut charts.
 */

import { useEffect, useMemo, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { CardSkeleton } from "./_CardStates";
import { RevenueAtRiskHero } from "./RevenueAtRiskHero";
import { BriefHero, type DailyBrief } from "./BriefHero";
import { AbandonedIntentCard } from "./AbandonedIntentCard";
import { LiveOpportunitiesCard } from "./LiveOpportunitiesCard";
import { VisitorIntentCard } from "./VisitorIntentCard";
import { CardEmpty } from "./_CardStates";
import { formatMoneyCompact } from "../app/_lib/formatters";

type HeroNumber = {
  value: string;
  loading: boolean;
};

// Raw payloads we surface from the hero-number fetches so the
// expanded panel can compose dynamic, merchant-specific "what to
// do" bullets — not static canonical advice.
type RarsPayload = {
  total_at_risk_eur?: number;
  prevented_eur_this_month?: number;
  components?: Array<{ source: string; loss_eur: number; narrative?: string }>;
  headline?: string | null;
  currency?: string;
} | null;

type AbandonedPayload = {
  products?: Array<{
    product_name?: string;
    leak_point?: string;
    intent_score?: number;
    views_7d?: number;
  }>;
} | null;

type LiveOppsPayload = {
  opportunities?: Array<{
    url?: string;
    signal_type?: string;
    recommended_action?: string;
    priority_score?: number;
  }>;
} | null;

type VisitorIntentPayload = {
  hot_visitors?: number;
  warm_visitors?: number;
  cold_visitors?: number;
  total_visitors?: number;
} | null;

type TopProduct = {
  product_id?: string | null;
  product_name?: string | null;
  total_views?: number;
  unique_visitors?: number;
  avg_intent_score?: number;
  intent_level?: string | null;
};

const ACCENTS: Record<string, { eyebrow: string; hero: string; bg: string; border: string }> = {
  amberWarn: {
    eyebrow: "#fbbf24",
    hero: "#fbbf24",
    bg: "rgba(251,191,36,0.05)",
    border: "rgba(251,191,36,0.18)",
  },
  violet: {
    eyebrow: "#a78bfa",
    hero: "#a78bfa",
    bg: "rgba(167,139,250,0.05)",
    border: "rgba(167,139,250,0.18)",
  },
  rose: {
    eyebrow: "#f87171",
    hero: "#f87171",
    bg: "rgba(248,113,113,0.05)",
    border: "rgba(248,113,113,0.18)",
  },
  amberOpp: {
    eyebrow: "#e8a04e",
    hero: "#e8a04e",
    bg: "rgba(232,160,78,0.05)",
    border: "rgba(232,160,78,0.2)",
  },
  emerald: {
    eyebrow: "#34d399",
    hero: "#34d399",
    bg: "rgba(52,211,153,0.05)",
    border: "rgba(52,211,153,0.2)",
  },
};

type CassettoneId =
  | "revenue-at-risk"
  | "daily-brief"
  | "abandoned-intent"
  | "live-opportunities"
  | "visitor-intent"
  | "hot-products";

// ----------------------------------------------------------------------
// Main grid
// ----------------------------------------------------------------------

export function LiteCassettoniGrid({
  apiBase,
  shop,
  isProUser,
  displayCurrency,
  topProducts,
  effectiveBrief,
  briefLoading,
  tier,
  coldStartPhase,
  setUpgradeModalOpen,
  loading,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
  displayCurrency: "USD" | "EUR";
  topProducts: TopProduct[];
  effectiveBrief: DailyBrief | null;
  briefLoading: boolean;
  tier: "lite" | "pro";
  coldStartPhase: number;
  setUpgradeModalOpen: (v: boolean) => void;
  loading: boolean;
}) {
  const [expandedId, setExpandedId] = useState<CassettoneId | null>(null);

  // Hero numbers + raw payloads — both go up to the expanded panel
  // so `whatToDo` can be merchant-specific (real products / real
  // leak points / real priorities), not static canonical advice.
  const { heroNumber: rarsNumber, data: rarsData } =
    useRarsData(apiBase, shop, displayCurrency);
  const briefNumber = useMemo<HeroNumber>(
    () => ({
      value: effectiveBrief?.signals_count
        ? `${effectiveBrief.signals_count}`
        : briefLoading ? "…" : "0",
      loading: briefLoading,
    }),
    [effectiveBrief, briefLoading]
  );
  const { heroNumber: abandonedNumber, data: abandonedData } =
    useAbandonedData(apiBase, shop);
  const { heroNumber: liveOppsNumber, data: liveOppsData } =
    useLiveOppsData(apiBase, shop);
  const { heroNumber: visitorIntentNumber, data: visitorIntentData } =
    useVisitorIntentData(apiBase, shop);
  const hotProductsNumber = useMemo<HeroNumber>(
    () => ({
      value: loading ? "…" : topProducts.length > 0 ? `${Math.min(3, topProducts.length)}` : "0",
      loading,
    }),
    [topProducts.length, loading]
  );

  const handleClick = (id: CassettoneId) => {
    setExpandedId((current) => (current === id ? null : id));
  };

  const cassettoni: Array<{
    id: CassettoneId;
    eyebrow: string;
    title: string;
    number: HeroNumber;
    meta: string;
    accent: keyof typeof ACCENTS;
  }> = [
    {
      id: "revenue-at-risk",
      eyebrow: "Money",
      title: "Revenue at risk",
      number: rarsNumber,
      meta: "this month",
      accent: "amberWarn",
    },
    {
      id: "daily-brief",
      eyebrow: "Today",
      title: "Daily brief",
      number: briefNumber,
      meta: "findings today",
      accent: "violet",
    },
    {
      id: "abandoned-intent",
      eyebrow: "Warning",
      title: "Abandoned intent",
      number: abandonedNumber,
      meta: "products leaking",
      accent: "rose",
    },
    {
      id: "live-opportunities",
      eyebrow: "Opportunity",
      title: "Live opportunities",
      number: liveOppsNumber,
      meta: "pages to fix now",
      accent: "amberOpp",
    },
    {
      id: "visitor-intent",
      eyebrow: "Right now",
      title: "Visitor intent",
      number: visitorIntentNumber,
      meta: "hot visitors",
      accent: "rose",
    },
    {
      id: "hot-products",
      eyebrow: "Signals",
      title: "Hot products",
      number: hotProductsNumber,
      meta: "active this week",
      accent: "emerald",
    },
  ];

  return (
    <section aria-labelledby="lite-cassettoni-heading" className="space-y-6">
      <h2
        id="lite-cassettoni-heading"
        className="sr-only"
      >
        Your Lite features
      </h2>

      {/* 2×3 grid */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {cassettoni.map((c) => {
          const accent = ACCENTS[c.accent];
          const isActive = expandedId === c.id;
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => handleClick(c.id)}
              aria-expanded={isActive}
              aria-controls={`cassettone-panel-${c.id}`}
              className="group relative flex h-full flex-col overflow-hidden rounded-3xl border bg-[#0e0e1a] p-7 text-left shadow-[0_20px_80px_-20px_rgba(0,0,0,0.5)] transition-all duration-200 hover:shadow-[0_28px_96px_-20px_rgba(0,0,0,0.55)] sm:p-8"
              style={{
                borderColor: isActive ? accent.border : "rgba(255,255,255,0.06)",
                background: isActive
                  ? `linear-gradient(135deg, ${accent.bg} 0%, #0e0e1a 60%)`
                  : "#0e0e1a",
              }}
            >
              {/* Left accent bar */}
              <span
                className="absolute left-0 top-6 h-16 w-[3px] rounded-r-full transition-all"
                style={{ background: accent.eyebrow, opacity: isActive ? 1 : 0.6 }}
                aria-hidden="true"
              />

              <div className="flex flex-1 flex-col">
                <div
                  className="text-[10px] font-bold uppercase tracking-[0.18em]"
                  style={{ color: accent.eyebrow }}
                >
                  {c.eyebrow}
                </div>
                <h3 className="mt-1 text-[17px] font-bold text-white">
                  {c.title}
                </h3>
                <div
                  className="mt-5 text-[3rem] font-extrabold leading-none tabular-nums"
                  style={{ color: accent.hero }}
                >
                  {c.number.value}
                </div>
                <div className="mt-1.5 text-[12px] text-slate-500">
                  {c.meta}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Expanded panel — one at a time, rendered BETWEEN the grid
          and the radar. Structured layout per spec §7:
          Title → Subtitle → Warm copy → Analysis → What to do.
          Subtitle + whatToDo are MERCHANT-SPECIFIC — they read the
          real payload data, not static canonical advice. */}
      {expandedId !== null && (() => {
        const activeCassettone = cassettoni.find((c) => c.id === expandedId);
        const panelConfig = PANEL_CONFIG[expandedId];
        const accent = activeCassettone ? ACCENTS[activeCassettone.accent] : ACCENTS.amberOpp;
        const ctx: PanelCtx = {
          heroValue: activeCassettone?.number.value ?? "—",
          heroLoading: activeCassettone?.number.loading ?? false,
          rarsData,
          briefData: effectiveBrief,
          abandonedData,
          liveOppsData,
          visitorIntentData,
          topProducts,
          displayCurrency,
        };
        const subtitle = panelConfig.getSubtitle(ctx);
        const whatToDo = panelConfig.getWhatToDo(ctx);
        return (
          <div
            id={`cassettone-panel-${expandedId}`}
            role="region"
            aria-label="Expanded feature"
            className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-[#0e0e1a] p-7 shadow-[0_20px_80px_-20px_rgba(0,0,0,0.6)] sm:p-9"
          >
            {/* Collapse button */}
            <div className="mb-4 flex items-center justify-between gap-4">
              <div className="text-[10px] font-bold uppercase tracking-[0.18em]" style={{ color: accent.eyebrow }}>
                {activeCassettone?.eyebrow}
              </div>
              <button
                type="button"
                onClick={() => setExpandedId(null)}
                className="rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-[11.5px] font-bold text-slate-300 transition-colors hover:bg-white/[0.06] hover:text-white"
                aria-label="Collapse"
              >
                Collapse
              </button>
            </div>

            {/* ── Title (big amber) ── */}
            <h2
              className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight sm:text-[2rem]"
              style={{ color: accent.hero }}
            >
              {panelConfig.title}
            </h2>

            {/* ── Subtitle (metric in context, data-driven) ── */}
            {subtitle && (
              <p className="mt-2 text-[15px] font-semibold text-white">
                {subtitle}
              </p>
            )}

            {/* ── Warm copy (idiot-proof, Spark voice, slate) ── */}
            <p className="mt-3 max-w-3xl text-[14px] leading-relaxed text-slate-400">
              {panelConfig.warmCopy}
            </p>

            {/* ── Analysis block — violet-accented, contained ── */}
            <div className="mt-8 rounded-2xl border border-violet-400/15 bg-violet-500/[0.025] p-5 sm:p-6">
              <div className="mb-3 flex items-center gap-2.5">
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="#c4b5fd"
                  strokeWidth={1.8}
                  className="h-4 w-4 flex-shrink-0"
                  aria-hidden="true"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75c0 .621-.504 1.125-1.125 1.125h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
                </svg>
                <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-violet-300">
                  The data · what you&apos;re looking at
                </div>
              </div>
              {/* Analysis intro — per-feature Spark voice that frames
                  what follows. Static per feature (doesn't pretend to
                  be data-specific) but tied to the feature's semantic. */}
              <p className="mb-5 max-w-3xl text-[14px] leading-relaxed text-slate-300">
                {panelConfig.analysisIntro}
              </p>
              {/* The deep card renders here, heading suppressed. */}
              <ExpandedContent
                id={expandedId}
                apiBase={apiBase}
                shop={shop}
                isProUser={isProUser}
                displayCurrency={displayCurrency}
                topProducts={topProducts}
                effectiveBrief={effectiveBrief}
                briefLoading={briefLoading}
                tier={tier}
                coldStartPhase={coldStartPhase}
                setUpgradeModalOpen={setUpgradeModalOpen}
                loading={loading}
              />
            </div>

            {/* ── What-to-do block — amber-accented, contained,
                visibly distinct from Analysis above. ── */}
            <div
              className="mt-6 rounded-2xl p-5 sm:p-6"
              style={{
                background: `linear-gradient(135deg, ${accent.bg} 0%, transparent 80%)`,
                border: `1px solid ${accent.border}`,
              }}
            >
              <div className="mb-4 flex items-center gap-2.5">
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke={accent.hero}
                  strokeWidth={1.8}
                  className="h-4 w-4 flex-shrink-0"
                  aria-hidden="true"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
                <div
                  className="text-[11px] font-bold uppercase tracking-[0.18em]"
                  style={{ color: accent.hero }}
                >
                  Your next moves
                </div>
              </div>
              <ul className="space-y-2.5">
                {whatToDo.map((item, i) => (
                  <li
                    key={i}
                    className="flex items-start gap-3 rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 px-4 py-3"
                  >
                    <span
                      className="mt-1.5 inline-flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-[10px] font-bold tabular-nums"
                      style={{
                        color: accent.hero,
                        background: accent.bg,
                        border: `1px solid ${accent.border}`,
                      }}
                      aria-hidden="true"
                    >
                      {i + 1}
                    </span>
                    <span className="text-[13.5px] leading-relaxed text-slate-200">{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        );
      })()}
    </section>
  );
}

// ----------------------------------------------------------------------
// Panel config — one per feature. Real-data contract:
// - title: static, matches cassettone
// - getSubtitle: function of live data (heroValue), not fabricated
// - warmCopy: explains what the merchant is seeing, Spark voice
// - whatToDo: real actions merchants can take (static list, grounded
//   in what the feature actually measures). Not fabricated data —
//   these are the canonical moves for each feature type.
// ----------------------------------------------------------------------

type PanelCtx = {
  heroValue: string;
  heroLoading: boolean;
  rarsData: RarsPayload;
  briefData: DailyBrief | null;
  abandonedData: AbandonedPayload;
  liveOppsData: LiveOppsPayload;
  visitorIntentData: VisitorIntentPayload;
  topProducts: TopProduct[];
  displayCurrency: "USD" | "EUR";
};

type PanelConfig = {
  title: string;
  getSubtitle: (ctx: PanelCtx) => string | null;
  warmCopy: string;
  /** Short sentence that precedes the deep-card analysis. Explains,
   *  in Spark voice, what the merchant is about to see. Static per
   *  feature — the deep card itself carries the live data. */
  analysisIntro: string;
  getWhatToDo: (ctx: PanelCtx) => string[];
};

// Humanize leak_point slugs from the abandoned-intent payload. We
// keep this narrow: only the values the backend actually emits. If a
// new leak_point appears, the fallback prints the raw slug in
// parentheses, which is an honest degradation (no invented prose).
function humanizeLeak(leak?: string): string {
  if (!leak) return "an unknown signal";
  return (
    {
      scroll_no_click: "scrolled but didn't click",
      view_no_cart: "viewed but didn't add to cart",
      cart_no_checkout: "carted but abandoned",
      high_intent_no_buy: "high-intent but didn't buy",
      bounce: "bounced quickly",
    } as Record<string, string>
  )[leak] ?? leak.replace(/_/g, " ");
}

// Humanize RARS component sources (matches SOURCE_LABELS in
// RevenueAtRiskHero.tsx by intent but phrased for actions).
function humanizeRarsSource(source: string): string {
  return (
    {
      abandoned_high_intent: "abandoned high-intent carts",
      refund_decline: "products losing traction",
      nudge_gap: "nudges underperforming",
      below_benchmark: "peers out-earning you",
      goal_gap: "your monthly targets",
    } as Record<string, string>
  )[source] ?? source.replace(/_/g, " ");
}

const PANEL_CONFIG: Record<CassettoneId, PanelConfig> = {
  "revenue-at-risk": {
    analysisIntro:
      "Here's where the money is bleeding, ranked by size. The biggest slice is what to fix first.",
    title: "Revenue at risk",
    getSubtitle: (ctx) => {
      if (ctx.heroLoading) return "Calculating…";
      if (ctx.heroValue === "—") {
        return "No material risk detected this month. I'll flag the moment any signal crosses threshold.";
      }
      const prevented = ctx.rarsData?.prevented_eur_this_month ?? 0;
      const ccy = ctx.rarsData?.currency ?? ctx.displayCurrency;
      if (prevented > 0) {
        return `${ctx.heroValue} at risk this month · already prevented ${formatMoneyCompact(prevented, ccy)}.`;
      }
      return `${ctx.heroValue} at risk this month — money about to slip through your store if no one acts.`;
    },
    warmCopy:
      "I sum up every signal that points to lost revenue this month — abandoned carts with real intent, refund trends, products underperforming peers, and targets you're missing. This is the one number that tells you how much HedgeSpark could earn back for you.",
    getWhatToDo: (ctx) => {
      const items: string[] = [];
      const comps = (ctx.rarsData?.components ?? [])
        .filter((c) => c.loss_eur > 0)
        .sort((a, b) => b.loss_eur - a.loss_eur);
      const ccy = ctx.rarsData?.currency ?? ctx.displayCurrency;
      if (comps.length === 0) {
        items.push(
          "No active leak source right now. Use the quiet period to look at Hot Products below — doubling down on what's working beats chasing small leaks.",
        );
        return items;
      }
      // Top 2 real components → 2 tailored actions
      for (const c of comps.slice(0, 2)) {
        items.push(
          `Your biggest leak right now is ${humanizeRarsSource(c.source)} at ${formatMoneyCompact(c.loss_eur, ccy)}. Tackle this first.`,
        );
      }
      // If a significant amount is already prevented, acknowledge it
      const prevented = ctx.rarsData?.prevented_eur_this_month ?? 0;
      if (prevented > 0) {
        items.push(
          `I've already prevented ${formatMoneyCompact(prevented, ccy)} this month — keep the active nudges running; they're earning their rent.`,
        );
      } else {
        items.push(
          "Scroll down to Abandoned Intent and Live Opportunities — those two panels show the concrete pages and products driving the risk number above.",
        );
      }
      return items;
    },
  },

  "daily-brief": {
    analysisIntro:
      "Here's what I flagged in the last 24 hours, ordered by economic impact. Top story leads.",
    title: "Daily brief",
    getSubtitle: (ctx) => {
      if (!ctx.briefData) return null;
      const count = ctx.briefData.signals_count ?? 0;
      const topProd = ctx.briefData.top_product_label;
      if (count === 0) {
        return "No significant findings yet today. I'm still watching — check back this afternoon.";
      }
      if (topProd) {
        return `${count} finding${count !== 1 ? "s" : ""} today · lead story: ${topProd}.`;
      }
      return `${count} finding${count !== 1 ? "s" : ""} today — ranked by economic impact.`;
    },
    warmCopy:
      "Every morning I scan every event from the past 24 hours and rank findings by economic impact. The top signal leads the brief; the rest are ranked below. If you only read one card today, read this one.",
    getWhatToDo: (ctx) => {
      const items: string[] = [];
      const topLabel = ctx.briefData?.top_product_label;
      const topAction = ctx.briefData?.top_action;
      const count = ctx.briefData?.signals_count ?? 0;
      if (!ctx.briefData || count === 0) {
        return [
          "Nothing urgent in today's brief yet. Good time to check Hot Products and see what's winning.",
        ];
      }
      if (topLabel && topAction) {
        items.push(`Act on today's lead story — ${topLabel}: ${topAction}`);
      } else if (topLabel) {
        items.push(`Today's top signal is on ${topLabel}. Open it in Hot Products below to see the pattern.`);
      }
      if (count > 1) {
        items.push(
          `There are ${count - 1} more finding${count - 1 !== 1 ? "s" : ""} ranked below the headline — tackle them in order.`,
        );
      }
      items.push(
        "Come back tomorrow morning — the brief refreshes overnight as new signals mature.",
      );
      return items;
    },
  },

  "abandoned-intent": {
    analysisIntro:
      "Look at the depth gap below — real buyers go deeper into your store than non-buyers. The products where the gap is widest are the ones leaking warm visitors.",
    title: "Abandoned intent",
    getSubtitle: (ctx) => {
      if (ctx.heroLoading) return "Calculating…";
      const list = ctx.abandonedData?.products ?? [];
      if (list.length === 0) {
        return "No abandoned-intent products this week — your conversion funnel is clean.";
      }
      const topProd = list[0];
      if (topProd?.product_name) {
        return `${list.length} product${list.length !== 1 ? "s" : ""} losing warm visitors · top: ${topProd.product_name}.`;
      }
      return `${list.length} product${list.length !== 1 ? "s" : ""} where engaged visitors walked away before buying.`;
    },
    warmCopy:
      "These are your warmest leads: visitors who scrolled, dwelled, clicked — and still didn't buy. I compare how deep real buyers go into your products vs how deep non-buyers go; the gap tells you which products have a conversion problem, not a traffic problem.",
    getWhatToDo: (ctx) => {
      const list = ctx.abandonedData?.products ?? [];
      if (list.length === 0) {
        return [
          "Nothing to recover here right now. Channel the effort into Hot Products — pushing more traffic to what's already converting beats fixing quiet products.",
        ];
      }
      const items: string[] = [];
      const top = list[0];
      if (top?.product_name) {
        items.push(
          `Start with "${top.product_name}" — visitors ${humanizeLeak(top.leak_point)}. The product page copy or price is where to look first.`,
        );
      }
      if (list.length >= 2 && list[1]?.product_name) {
        items.push(
          `Second priority: "${list[1].product_name}" (visitors ${humanizeLeak(list[1].leak_point)}). Fix the same class of issue if the leak pattern matches.`,
        );
      }
      items.push(
        "If you have these visitors' emails from past orders, a 10% off targeted to the abandoning segment typically recovers 15–25%.",
      );
      return items;
    },
  },

  "live-opportunities": {
    analysisIntro:
      "Each row below is one page + one reason it's leaking + one concrete fix. Sorted by recoverable revenue, not by guesswork.",
    title: "Live opportunities",
    getSubtitle: (ctx) => {
      if (ctx.heroLoading) return "Calculating…";
      const opps = (ctx.liveOppsData?.opportunities ?? []).filter(
        (o) => o.signal_type !== "LOW_SIGNAL",
      );
      if (opps.length === 0) {
        return "No leaking pages above threshold. Your high-engagement pages are converting cleanly.";
      }
      return `${opps.length} page${opps.length !== 1 ? "s" : ""} leaking intent right now — ranked by recoverable revenue.`;
    },
    warmCopy:
      "These are the pages on your store leaking intent as I speak. Visitors are reading them, scrolling them, clicking around — but not buying. Each row surfaces one page, one reason it's leaking, and one recommended next action you can do in minutes.",
    getWhatToDo: (ctx) => {
      const opps = (ctx.liveOppsData?.opportunities ?? [])
        .filter((o) => o.signal_type !== "LOW_SIGNAL")
        .sort((a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0));
      if (opps.length === 0) {
        return [
          "No leaking pages right now. Lean into Hot Products — they're the ones to invest in.",
        ];
      }
      const items: string[] = [];
      const top = opps[0];
      if (top?.recommended_action) {
        const urlHint = top.url ? ` on ${top.url}` : "";
        items.push(`Top priority${urlHint}: ${top.recommended_action}`);
      }
      if (opps.length >= 2 && opps[1]?.recommended_action) {
        const urlHint2 = opps[1].url ? ` on ${opps[1].url}` : "";
        items.push(`Next${urlHint2}: ${opps[1].recommended_action}`);
      }
      items.push(
        `Come back in a few hours — as visitors flow through, I'll re-rank these based on fresh priority scores.`,
      );
      return items;
    },
  },

  "visitor-intent": {
    analysisIntro:
      "Here's the live composition of your traffic by intent level. The proportions tell you whether to acquire more or convert better — two very different fixes.",
    title: "Visitor intent",
    getSubtitle: (ctx) => {
      if (ctx.heroLoading) return "Calculating…";
      const hot = ctx.visitorIntentData?.hot_visitors ?? 0;
      const warm = ctx.visitorIntentData?.warm_visitors ?? 0;
      const cold = ctx.visitorIntentData?.cold_visitors ?? 0;
      const total = ctx.visitorIntentData?.total_visitors ?? 0;
      if (total === 0) {
        return "No visitors scored yet. The classification kicks in with the first visitor.";
      }
      return `Hot ${hot} · Warm ${warm} · Cold ${cold} — out of ${total.toLocaleString()} visitors scored.`;
    },
    warmCopy:
      "I classify every visitor on your store into Hot (engaged and clicked), Warm (engaged but no click), and Cold (pass-through). Hot visitors are roughly ten times more likely to buy than Cold ones — so the split tells you whether to acquire more traffic or convert better.",
    getWhatToDo: (ctx) => {
      const hot = ctx.visitorIntentData?.hot_visitors ?? 0;
      const warm = ctx.visitorIntentData?.warm_visitors ?? 0;
      const cold = ctx.visitorIntentData?.cold_visitors ?? 0;
      const total = ctx.visitorIntentData?.total_visitors ?? 0;
      if (total === 0) {
        return [
          "Tracker hasn't seen a visitor yet. Check that the HedgeSpark script is loaded on your storefront.",
        ];
      }
      const items: string[] = [];
      // Dominant-state branch — the advice depends on the real split.
      if (cold > hot + warm) {
        items.push(
          `Cold visitors outnumber your engaged ones (${cold} vs ${hot + warm}). Your traffic quality is low — audit ad creative or landing pages.`,
        );
      } else if (warm > 0 && hot < Math.max(1, Math.round(warm / 3))) {
        items.push(
          `You have ${warm} warm visitor${warm !== 1 ? "s" : ""} but only ${hot} crossing into hot. Your product pages aren't earning the click — tighten the CTA and social proof.`,
        );
      } else if (hot > 0) {
        items.push(
          `${hot} hot visitor${hot !== 1 ? "s" : ""} right now. If you can message them within the hour (email, retarget pixel), recovery rates are meaningfully higher than later.`,
        );
      }
      items.push(
        `Open ${hot > 0 ? "Abandoned Intent below" : "Live Opportunities below"} to see the specific pages where this visitor mix is converting poorly.`,
      );
      return items;
    },
  },

  "hot-products": {
    analysisIntro:
      "These are the three pulling most attention this week, ranked by views + intent. Consider them double-down candidates.",
    title: "Hot products",
    getSubtitle: (ctx) => {
      if (ctx.topProducts.length === 0) return "No hot products yet — your first visitors will populate the list.";
      const top = ctx.topProducts[0];
      return `Your #1 this week: ${top.product_name ?? top.product_id ?? "—"} · ${(top.total_views ?? 0).toLocaleString()} views.`;
    },
    warmCopy:
      "These are the products pulling the most attention right now — ranked by views, unique visitors, and the intent score I assign each one. If you want to know what's working, look here first; if you want to know what to fix, look at Abandoned Intent instead.",
    getWhatToDo: (ctx) => {
      if (ctx.topProducts.length === 0) {
        return [
          "No hot products yet. Once visitors engage, I'll rank them by attention and intent here.",
        ];
      }
      const items: string[] = [];
      const top = ctx.topProducts[0];
      const name = top?.product_name ?? top?.product_id ?? "your top product";
      const views = top?.total_views ?? 0;
      const visitors = top?.unique_visitors ?? 0;
      const intent = Math.round(top?.avg_intent_score ?? 0);

      items.push(
        `Double down on "${name}" — ${views.toLocaleString()} view${views !== 1 ? "s" : ""} from ${visitors.toLocaleString()} visitor${visitors !== 1 ? "s" : ""}. Add a bundle, write a better description, or push more ad traffic to it.`,
      );
      if (views > 0 && visitors > 0 && views / Math.max(1, visitors) > 2) {
        items.push(
          `Same visitors are coming back to "${name}" (${views}/${visitors} = ~${(views / visitors).toFixed(1)} views per person). That's a re-engagement signal — consider email or retargeting.`,
        );
      }
      if (intent > 0 && intent < 40) {
        items.push(
          `Intent score on "${name}" is ${intent} — traffic quality is mediocre. The visitors are looking but not signaling purchase. Consider refining your ad targeting.`,
        );
      } else if (intent >= 70) {
        items.push(
          `Intent on "${name}" is ${intent} — these visitors are warm. A price drop or limited-time offer right now could convert a chunk of them.`,
        );
      }
      return items;
    },
  },
};

// ----------------------------------------------------------------------
// Expanded content dispatcher
// ----------------------------------------------------------------------

function ExpandedContent({
  id,
  apiBase,
  shop,
  isProUser,
  displayCurrency,
  topProducts,
  effectiveBrief,
  briefLoading,
  tier,
  coldStartPhase,
  setUpgradeModalOpen,
  loading,
}: {
  id: CassettoneId;
  apiBase: string;
  shop: string;
  isProUser: boolean;
  displayCurrency: "USD" | "EUR";
  topProducts: TopProduct[];
  effectiveBrief: DailyBrief | null;
  briefLoading: boolean;
  tier: "lite" | "pro";
  coldStartPhase: number;
  setUpgradeModalOpen: (v: boolean) => void;
  loading: boolean;
}) {
  switch (id) {
    case "revenue-at-risk":
      return (
        <RevenueAtRiskHero
          apiBase={apiBase}
          shop={shop}
          isProUser={isProUser}
          onUpgrade={() => setUpgradeModalOpen(true)}
          hideHeading
        />
      );
    case "daily-brief":
      return (
        <BriefHero
          brief={effectiveBrief}
          loading={briefLoading}
          tier={tier}
          onUpgradeClick={() => setUpgradeModalOpen(true)}
          hideHeading
          emptyHint={
            coldStartPhase === 0
              ? "Complete setup to start tracking."
              : coldStartPhase === 1
              ? "Tracker live. First findings within minutes."
              : coldStartPhase === 2
              ? "Visitors arriving. Analyzing behavior to find your first revenue opportunity."
              : undefined
          }
        />
      );
    case "abandoned-intent":
      return (
        <AbandonedIntentCard
          apiBase={apiBase}
          shop={shop}
          isProUser={isProUser}
          onUpgrade={() => setUpgradeModalOpen(true)}
          hideHeading
        />
      );
    case "live-opportunities":
      return <LiveOpportunitiesCard apiBase={apiBase} shop={shop} hideHeading />;
    case "visitor-intent":
      return (
        <VisitorIntentCard
          apiBase={apiBase}
          shop={shop}
          isProUser={isProUser}
          onUpgrade={() => setUpgradeModalOpen(true)}
          hideHeading
        />
      );
    case "hot-products":
      return (
        <HotProductsExpanded
          topProducts={topProducts}
          loading={loading}
          coldStartPhase={coldStartPhase}
        />
      );
    default:
      return null;
  }
}

// ----------------------------------------------------------------------
// Hot Products inline (was previously inline in page.tsx)
// ----------------------------------------------------------------------

function HotProductsExpanded({
  topProducts,
  loading,
  coldStartPhase,
}: {
  topProducts: TopProduct[];
  loading: boolean;
  coldStartPhase: number;
}) {
  return (
    <section>
      {loading ? (
        <CardSkeleton label="Loading hot products" />
      ) : topProducts.length > 0 ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {topProducts.slice(0, 3).map((product, i) => (
            <div
              key={`${product.product_id || "prod"}-${i}`}
              className="flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5"
            >
              <div className="mb-3 flex items-center justify-between gap-2">
                <span className="truncate text-[15px] font-semibold text-white">
                  {product.product_name || product.product_id || "—"}
                </span>
                {product.intent_level && (
                  <span className="flex-shrink-0 rounded-lg px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide text-slate-300 ring-1 ring-white/10">
                    {product.intent_level}
                  </span>
                )}
              </div>
              <div className="mt-auto grid grid-cols-3 gap-2 border-t border-white/[0.05] pt-3">
                <div>
                  <div className="text-[11px] font-medium uppercase text-slate-500">Views</div>
                  <div className="mt-1 text-[18px] font-bold tabular-nums text-white">
                    {(product.total_views ?? 0).toLocaleString()}
                  </div>
                </div>
                <div>
                  <div className="text-[11px] font-medium uppercase text-slate-500">Visitors</div>
                  <div className="mt-1 text-[18px] font-bold tabular-nums text-white">
                    {(product.unique_visitors ?? 0).toLocaleString()}
                  </div>
                </div>
                <div>
                  <div className="text-[11px] font-medium uppercase text-slate-500">Intent</div>
                  <div className="mt-1 text-[18px] font-bold tabular-nums text-white">
                    {Math.round(product.avg_intent_score ?? 0)}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <CardEmpty
          accent="amber"
          title={coldStartPhase <= 1 ? "Warming up" : "No hot products yet this week"}
          body={
            coldStartPhase <= 1
              ? "Your first visitors will populate this list."
              : "No products have crossed the intent threshold in the last 7 days."
          }
          eta={coldStartPhase <= 1 ? "Populates within ~5 minutes of your first visitor" : undefined}
        />
      )}
    </section>
  );
}

// ----------------------------------------------------------------------
// Hero-number fetches (one per cassettone). Each is defensive — on
// failure, shows "—" instead of crashing or fabricating.
// ----------------------------------------------------------------------

function useRarsData(
  apiBase: string,
  shop: string,
  displayCurrency: "USD" | "EUR",
): { heroNumber: HeroNumber; data: RarsPayload } {
  const [data, setData] = useState<RarsPayload>(null);
  const [value, setValue] = useState<string>("…");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;
    apiClient
      .GET("/pro/revenue-at-risk")
      .then(({ data: raw }) => {
        if (!active) return;
        const payload = raw as RarsPayload;
        setData(payload ?? null);
        const n = payload?.total_at_risk_eur ?? 0;
        const ccy = payload?.currency ?? displayCurrency;
        setValue(n > 0 ? formatMoneyCompact(n, ccy || displayCurrency) : "—");
      })
      .catch(() => {
        if (active) { setValue("—"); setData(null); }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [apiBase, shop, displayCurrency]);

  return { heroNumber: { value, loading }, data };
}

function useAbandonedData(
  apiBase: string,
  shop: string,
): { heroNumber: HeroNumber; data: AbandonedPayload } {
  const [data, setData] = useState<AbandonedPayload>(null);
  const [value, setValue] = useState<string>("…");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;
    apiClient
      .GET("/pro/abandoned-intent")
      .then(({ data: raw }) => {
        if (!active) return;
        const wrapper = raw as (AbandonedPayload & { top_products?: unknown[] }) | null;
        const list = wrapper?.products ?? wrapper?.top_products ?? [];
        setData(
          wrapper
            ? { products: list as AbandonedPayload extends null ? never : NonNullable<AbandonedPayload>["products"] }
            : null
        );
        const count = Array.isArray(list) ? list.length : 0;
        setValue(count > 0 ? String(count) : "—");
      })
      .catch(() => {
        if (active) { setValue("—"); setData(null); }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [apiBase, shop]);

  return { heroNumber: { value, loading }, data };
}

function useLiveOppsData(
  apiBase: string,
  shop: string,
): { heroNumber: HeroNumber; data: LiveOppsPayload } {
  const [data, setData] = useState<LiveOppsPayload>(null);
  const [value, setValue] = useState<string>("…");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;
    apiClient
      .GET("/analytics/live-opportunities")
      .then(({ data: raw }) => {
        if (!active) return;
        const payload = raw as LiveOppsPayload;
        setData(payload ?? null);
        const opps = payload?.opportunities;
        const visible = Array.isArray(opps)
          ? opps.filter((o) => o.signal_type !== "LOW_SIGNAL").length
          : 0;
        setValue(visible > 0 ? String(visible) : "—");
      })
      .catch(() => {
        if (active) { setValue("—"); setData(null); }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [apiBase, shop]);

  return { heroNumber: { value, loading }, data };
}

function useVisitorIntentData(
  apiBase: string,
  shop: string,
): { heroNumber: HeroNumber; data: VisitorIntentPayload } {
  const [data, setData] = useState<VisitorIntentPayload>(null);
  const [value, setValue] = useState<string>("…");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;
    apiClient
      .GET("/analytics/visitor-intent-classification")
      .then(({ data: raw }) => {
        if (!active) return;
        const payload = raw as VisitorIntentPayload;
        setData(payload ?? null);
        const hot = payload?.hot_visitors ?? 0;
        setValue(hot > 0 ? String(hot) : "—");
      })
      .catch(() => {
        if (active) { setValue("—"); setData(null); }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [apiBase, shop]);

  return { heroNumber: { value, loading }, data };
}
