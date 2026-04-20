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

  // Hero numbers — independent fetches per cassettone. Each one is a
  // cheap call (usually already cached server-side or ~30ms). Data
  // duplicates when the cassettone expands (deep card also fetches),
  // acceptable for Commit 1; Commit 2 will hoist to a shared source.
  const rarsNumber = useRarsHeroNumber(apiBase, shop, displayCurrency);
  const briefNumber = useMemo<HeroNumber>(
    () => ({
      value: effectiveBrief?.signals_count
        ? `${effectiveBrief.signals_count}`
        : briefLoading ? "…" : "0",
      loading: briefLoading,
    }),
    [effectiveBrief, briefLoading]
  );
  const abandonedNumber = useAbandonedHeroNumber(apiBase, shop);
  const liveOppsNumber = useLiveOppsHeroNumber(apiBase, shop);
  const visitorIntentNumber = useVisitorIntentHeroNumber(apiBase, shop);
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
          Title → Subtitle → Warm copy → Analysis → What to do. */}
      {expandedId !== null && (() => {
        const activeCassettone = cassettoni.find((c) => c.id === expandedId);
        const panelConfig = PANEL_CONFIG[expandedId];
        const accent = activeCassettone ? ACCENTS[activeCassettone.accent] : ACCENTS.amberOpp;
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

            {/* ── Subtitle (metric in context, white small bold) ── */}
            <p className="mt-2 text-[15px] font-semibold text-white">
              {panelConfig.getSubtitle({
                heroValue: activeCassettone?.number.value ?? "—",
                meta: activeCassettone?.meta ?? "",
              })}
            </p>

            {/* ── Warm copy (idiot-proof, Spark voice, slate) ── */}
            <p className="mt-3 max-w-3xl text-[14px] leading-relaxed text-slate-400">
              {panelConfig.warmCopy}
            </p>

            {/* ── Analysis (the deep card, heading suppressed) ── */}
            <div className="mt-6 border-t border-white/[0.05] pt-6">
              <div className="mb-3 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-500">
                Analysis
              </div>
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

            {/* ── What to do next ── */}
            <div className="mt-6 border-t border-white/[0.05] pt-6">
              <div className="mb-3 text-[11px] font-bold uppercase tracking-[0.18em] text-[#e8a04e]">
                What to do next
              </div>
              <ul className="space-y-2">
                {panelConfig.whatToDo.map((item, i) => (
                  <li
                    key={i}
                    className="flex items-start gap-3 rounded-xl border border-white/[0.04] bg-white/[0.015] px-4 py-3"
                  >
                    <span
                      className="mt-1 h-1.5 w-1.5 flex-shrink-0 rounded-full"
                      style={{ background: accent.eyebrow }}
                      aria-hidden="true"
                    />
                    <span className="text-[13px] leading-relaxed text-slate-300">{item}</span>
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

type PanelConfig = {
  title: string;
  getSubtitle: (ctx: { heroValue: string; meta: string }) => string;
  warmCopy: string;
  whatToDo: string[];
};

const PANEL_CONFIG: Record<CassettoneId, PanelConfig> = {
  "revenue-at-risk": {
    title: "Revenue at risk",
    getSubtitle: ({ heroValue, meta }) =>
      `${heroValue} ${meta} — money about to slip through your store if no one acts.`,
    warmCopy:
      "I sum up every signal that points to lost revenue this month — abandoned carts with real intent, refund trends, products underperforming peers, and targets you're missing. This is the one number that tells you how much HedgeSpark could earn back for you.",
    whatToDo: [
      "Open Abandoned Intent below to see which products lost the most high-intent visitors.",
      "Check Live Opportunities to find the specific pages where money is leaking today.",
      "Review Visitor Intent — if hot visitors aren't converting, your product pages need attention.",
    ],
  },
  "daily-brief": {
    title: "Daily brief",
    getSubtitle: ({ heroValue, meta }) =>
      `${heroValue} ${meta} — today's top stories from your store, in one paragraph.`,
    warmCopy:
      "Every morning I scan every event from the past 24 hours and rank findings by economic impact. The top signal leads the brief; the rest are ranked below. If you only read one card today, read this one.",
    whatToDo: [
      "Act on the top finding first — it's ranked by real revenue impact, not vanity metrics.",
      "Glance at the product snapshot to see which three SKUs drove today's story.",
      "Come back tomorrow morning — the brief refreshes overnight with fresh data.",
    ],
  },
  "abandoned-intent": {
    title: "Abandoned intent",
    getSubtitle: ({ heroValue, meta }) =>
      `${heroValue} ${meta} — products where engaged visitors walked away before buying.`,
    warmCopy:
      "These are your warmest leads: visitors who scrolled, dwelled, clicked — and still didn't buy. I compare how deep real buyers go into your products vs how deep non-buyers go; the gap tells you which products have a conversion problem, not a traffic problem.",
    whatToDo: [
      "Review the top product: it has high intent but low conversion — fix the product page copy or price.",
      "Check the buyer-vs-non-buyer depth: if non-buyers look at more products, you have a choice paralysis issue.",
      "Email the abandoning visitors if you have their contact — a targeted 10% off often recovers 20%.",
    ],
  },
  "live-opportunities": {
    title: "Live opportunities",
    getSubtitle: ({ heroValue, meta }) =>
      `${heroValue} ${meta} — high-engagement pages under-converting right now.`,
    warmCopy:
      "These are the pages on your store leaking intent as I speak. Visitors are reading them, scrolling them, clicking around — but not buying. Each row surfaces one page, one reason it's leaking, and one recommended next action you can do in minutes.",
    whatToDo: [
      "Tackle the top-priority page first — it has the highest recoverable revenue.",
      "Read each row's recommended_action: it's the specific fix pulled from your real traffic pattern.",
      "Come back in a few hours — the list refreshes as behavior changes.",
    ],
  },
  "visitor-intent": {
    title: "Visitor intent",
    getSubtitle: ({ heroValue }) =>
      `${heroValue} hot visitors right now — the ones most likely to buy if you act fast.`,
    warmCopy:
      "I classify every visitor on your store into Hot (engaged and clicked), Warm (engaged but no click), and Cold (pass-through). Hot visitors are roughly ten times more likely to buy than Cold ones — so the split tells you whether to acquire more traffic or convert better.",
    whatToDo: [
      "If you have more Cold than Warm+Hot combined, your traffic quality is low — audit your ad creative.",
      "If you have Warm visitors but few Hot, your product pages aren't earning the click — fix the CTA.",
      "Upgrade to Pro to see the ranked list of each hot visitor with their behavior trail.",
    ],
  },
  "hot-products": {
    title: "Hot products",
    getSubtitle: ({ heroValue }) =>
      `${heroValue} products leading your store this week by attention and intent.`,
    warmCopy:
      "These are the products pulling the most attention right now — ranked by views, unique visitors, and the intent score I assign each one. If you want to know what's working, look here first; if you want to know what to fix, look at Abandoned Intent instead.",
    whatToDo: [
      "Double down on the #1 product: add a bundle, push more traffic to it, or raise prices if demand is strong.",
      "Compare views vs visitors: high views / low visitors means the same visitors return repeatedly (re-engagement signal).",
      "Check intent: a product with high views but low intent may be attracting wrong-fit traffic.",
    ],
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

function useRarsHeroNumber(
  apiBase: string,
  shop: string,
  displayCurrency: "USD" | "EUR",
): HeroNumber {
  const [value, setValue] = useState<string>("…");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;
    apiClient
      .GET("/pro/revenue-at-risk")
      .then(({ data }) => {
        if (!active) return;
        const n = typeof data === "object" && data !== null && "total_at_risk_eur" in data
          ? (data as { total_at_risk_eur?: number }).total_at_risk_eur ?? 0
          : 0;
        const ccy = typeof data === "object" && data !== null && "currency" in data
          ? (data as { currency?: string }).currency ?? displayCurrency
          : displayCurrency;
        setValue(n > 0 ? formatMoneyCompact(n, ccy || displayCurrency) : "—");
      })
      .catch(() => {
        if (active) setValue("—");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [apiBase, shop, displayCurrency]);

  return { value, loading };
}

function useAbandonedHeroNumber(apiBase: string, shop: string): HeroNumber {
  const [value, setValue] = useState<string>("…");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;
    // AbandonedIntent endpoint returns top products + leak points.
    // The hero number = count of top products flagged with leak signal.
    apiClient
      .GET("/pro/abandoned-intent")
      .then(({ data }) => {
        if (!active) return;
        const products = (data as { products?: unknown[]; top_products?: unknown[] } | null);
        const list = products?.products ?? products?.top_products ?? [];
        const count = Array.isArray(list) ? list.length : 0;
        setValue(count > 0 ? String(count) : "—");
      })
      .catch(() => {
        if (active) setValue("—");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [apiBase, shop]);

  return { value, loading };
}

function useLiveOppsHeroNumber(apiBase: string, shop: string): HeroNumber {
  const [value, setValue] = useState<string>("…");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;
    apiClient
      .GET("/analytics/live-opportunities")
      .then(({ data }) => {
        if (!active) return;
        const opps = (data as { opportunities?: Array<{ signal_type?: string }> } | null)
          ?.opportunities;
        const visible = Array.isArray(opps)
          ? opps.filter((o) => o.signal_type !== "LOW_SIGNAL").length
          : 0;
        setValue(visible > 0 ? String(visible) : "—");
      })
      .catch(() => {
        if (active) setValue("—");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [apiBase, shop]);

  return { value, loading };
}

function useVisitorIntentHeroNumber(apiBase: string, shop: string): HeroNumber {
  const [value, setValue] = useState<string>("…");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;
    apiClient
      .GET("/analytics/visitor-intent-classification")
      .then(({ data }) => {
        if (!active) return;
        const hot = (data as { hot_visitors?: number } | null)?.hot_visitors ?? 0;
        setValue(hot > 0 ? String(hot) : "—");
      })
      .catch(() => {
        if (active) setValue("—");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [apiBase, shop]);

  return { value, loading };
}
