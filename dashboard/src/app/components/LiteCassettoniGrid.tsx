"use client";

/**
 * LiteCassettoniGrid — /app/lite main surface.
 *
 * Founder-designed per `docs/LITE_VISUAL_SPEC.md`:
 * a 2×3 grid of "cassettoni" (cards) — one per Lite feature — each
 * showing a title + one big hero number sourced from real backend
 * data. Clicking a cassettone opens an expanded panel directly below
 * the grid (above the radar) that tells the feature's full story
 * inline. One open at a time.
 *
 * Spec v4 (2026-04-20) ported AbandonedIntent's DetailDrawer
 * storytelling richness INLINE into the 3 panel sections (cosa è /
 * analisi / what to do) for all 6 features. The deep-card embed was
 * removed — it would have duplicated the inline hero stat + metrics +
 * methodology. Hot Products keeps its 3-product visual grid as its
 * unique detail block (the only one not reducible to key-value rows).
 *
 * Real-data contract (founder-mandated):
 *   Every hero stat, metric row, methodology input, and action — in
 *   all 6 configs — traces to one backend endpoint call or
 *   deterministic decision-engine output. "—" is honest; fake is not.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { apiClient } from "@/app/lib/api-client";
import { CardSkeleton, CardEmpty } from "./_CardStates";
import { type DailyBrief } from "./BriefHero";
import { formatMoneyCompact } from "../app/_lib/formatters";
import { buildShopifyAdminProductUrl, buildStorefrontUrl } from "../lib/shopify";

type HeroNumber = {
  value: string;
  loading: boolean;
};

// Raw payloads we surface from the hero-number fetches so the
// expanded panel can compose dynamic, merchant-specific storytelling
// (hero stat / metrics / methodology / actions) — not canonical prose.
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
    product_url?: string;
    leak_point?: string;
    intent_score?: number;
    views_7d?: number;
    carts_7d?: number;
    purchases_7d?: number;
    abandon_rate_pct?: number;
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

export type CassettoneId =
  | "revenue-at-risk"
  | "daily-brief"
  | "abandoned-intent"
  | "live-opportunities"
  | "hot-products";

// ----------------------------------------------------------------------
// Main grid
// ----------------------------------------------------------------------

export function LiteCassettoniGrid({
  apiBase,
  shop,
  displayCurrency,
  topProducts,
  effectiveBrief,
  briefLoading,
  coldStartPhase,
  loading,
  expandedId: controlledExpandedId,
  onExpandedChange,
}: {
  apiBase: string;
  shop: string;
  displayCurrency: "USD" | "EUR";
  topProducts: TopProduct[];
  effectiveBrief: DailyBrief | null;
  briefLoading: boolean;
  coldStartPhase: number;
  loading: boolean;
  /** Controlled expanded state. When provided, the grid uses the
   *  parent's state and calls onExpandedChange on every toggle. This
   *  lets an external surface (e.g. LiteRarsHero's component row
   *  links) open a specific cassettone from outside the grid. When
   *  omitted, the grid falls back to internal state for backward
   *  compatibility. */
  expandedId?: CassettoneId | null;
  onExpandedChange?: (id: CassettoneId | null) => void;
}) {
  const [internalExpandedId, setInternalExpandedId] = useState<CassettoneId | null>(null);
  const expandedId = controlledExpandedId !== undefined ? controlledExpandedId : internalExpandedId;
  const setExpandedId = (next: CassettoneId | null | ((prev: CassettoneId | null) => CassettoneId | null)) => {
    if (onExpandedChange) {
      const resolved = typeof next === "function" ? next(expandedId) : next;
      onExpandedChange(resolved);
    } else {
      setInternalExpandedId(next);
    }
  };

  // Hero numbers + raw payloads — both go up to the expanded panel
  // so every storytelling block can be merchant-specific (real
  // products / real leak points / real priorities), not static copy.
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
              className={`group relative flex h-full flex-col overflow-hidden rounded-3xl border bg-[#0e0e1a] p-7 text-left shadow-[0_20px_80px_-20px_rgba(0,0,0,0.5)] transition-all duration-200 hover:shadow-[0_28px_96px_-20px_rgba(0,0,0,0.55)] sm:p-8 ${
                isActive ? "" : "border-white/[0.06] hover:border-white/[0.16]"
              }`}
              style={
                isActive
                  ? {
                      borderColor: accent.border,
                      background: `linear-gradient(135deg, ${accent.bg} 0%, #0e0e1a 60%)`,
                    }
                  : undefined
              }
            >
              {/* Hover illumination overlay — accent-tinted gradient
                  that fades in on hover to give the cassettone a
                  clear "this is clickable" affordance. Skipped when
                  the card is already active (gradient is already on
                  via inline style). pointer-events-none keeps the
                  click target on the button itself. */}
              {!isActive && (
                <span
                  className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-200 group-hover:opacity-100"
                  style={{
                    background: `linear-gradient(135deg, ${accent.bg} 0%, transparent 65%)`,
                  }}
                  aria-hidden="true"
                />
              )}

              {/* Left accent bar — full opacity on active OR hover. */}
              <span
                className={`absolute left-0 top-6 h-16 w-[3px] rounded-r-full transition-opacity duration-200 ${
                  isActive ? "opacity-100" : "opacity-60 group-hover:opacity-100"
                }`}
                style={{ background: accent.eyebrow }}
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
                <div className="mt-1.5 text-[12px] text-slate-400">
                  {c.meta}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Expanded panel — one at a time, rendered BETWEEN the grid
          and the radar. v4 structure per spec:
            Section 1 (cosa è) — title + subtitle + mechanics + stakes
            Section 2 (analisi) — hero stat + donut + key metrics +
                                   [detail block, hot-products only] +
                                   methodology
            Section 3 (actions) — primary action + supporting actions
          Every block is data-driven; no embedded deep cards. */}
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
          topProducts,
          displayCurrency,
          loading,
          coldStartPhase,
          shop,
        };
        const subtitle = panelConfig.getSubtitle(ctx);
        const heroStat = panelConfig.getHeroStat(ctx);
        const keyMetrics = panelConfig.getKeyMetrics(ctx);
        const methodologyInputs = panelConfig.methodology.getInputs(ctx);
        const primaryAction = panelConfig.getPrimaryAction(ctx);
        const supportingActions = panelConfig.getSupportingActions(ctx);
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

            {/* ── Section 1: what you're seeing (mechanics + stakes) ── */}
            <div className="mt-5 max-w-3xl space-y-5">
              <p className="text-[14px] leading-relaxed text-slate-300">
                {panelConfig.mechanics}
              </p>
              <div>
                <div
                  className="mb-1.5 text-[10.5px] font-bold uppercase tracking-[0.18em]"
                  style={{ color: accent.eyebrow }}
                >
                  Why this matters
                </div>
                <p className="text-[14px] leading-relaxed text-slate-300">
                  {panelConfig.stakes}
                </p>
              </div>
            </div>

            {/* ── Section 2: the data (violet-accented) ── */}
            <div className="mt-8 rounded-2xl border border-violet-400/15 bg-violet-500/[0.025] p-5 sm:p-6">
              <div className="mb-5 flex items-center gap-2.5">
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

              {/* Hero stat — DrawerBigStat shape. When the payload
                  has no material value (heroStat === null) we DON'T
                  render "—" everywhere; we render a labeled preview
                  of what the card will look like once data arrives,
                  with a live "watching" indicator. That's the
                  "premuroso ed affidabile" target — day-1 feels
                  premium instead of a wall of empty cells. */}
              {heroStat ? (
                <div className="mb-6 rounded-xl border border-white/[0.06] bg-[#0b0b14]/70 p-5">
                  <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                    {heroStat.label}
                  </div>
                  <div
                    className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums"
                    style={{ color: heroStat.color }}
                  >
                    {heroStat.value}
                  </div>
                  <div className="mt-2.5 text-[12.5px] leading-relaxed text-slate-400">
                    {heroStat.sublabel}
                  </div>
                </div>
              ) : (
                <EmptyPreview config={panelConfig.empty} accentHero={accent.hero} displayCurrency={displayCurrency} />
              )}

              {/* Donut — real-data only, renders when segments non-null. */}
              {(() => {
                const segments = panelConfig.getDonutSegments(ctx);
                if (!segments || segments.length === 0) return null;
                const hero = panelConfig.getDonutHero(ctx);
                return (
                  <div className="mb-6 flex flex-col items-center gap-6 rounded-xl border border-white/[0.04] bg-[#0b0b14]/60 p-5 sm:flex-row sm:items-start sm:justify-start">
                    <div className="flex-shrink-0">
                      <Donut segments={segments} hero={hero} />
                    </div>
                    <div className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-slate-400">
                      <div className="mb-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-400">
                        How to read it
                      </div>
                      <p>
                        Each slice is one {panelConfig.title.toLowerCase()}{" "}
                        component sized by its share of the whole. The biggest
                        slice is the merchant&apos;s biggest opportunity right
                        now — its color tells you what kind.
                      </p>
                    </div>
                  </div>
                );
              })()}

              {/* Key metrics — DrawerKeyValueList shape. Suppressed
                  when heroStat is null (empty state) — the preview
                  block above already shows sample metrics and having
                  another list of "—" below would dilute the signal. */}
              {heroStat && keyMetrics.length > 0 && (
                <div className="mb-6">
                  <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                    Key metrics
                  </div>
                  <div className="divide-y divide-white/[0.04] rounded-xl border border-white/[0.05] bg-[#0b0b14]/50">
                    {keyMetrics.map((m, i) => (
                      <div key={i} className="flex items-center justify-between gap-4 px-4 py-3">
                        <span className="text-[13px] text-slate-400">{m.label}</span>
                        <span
                          className="text-[14px] font-bold tabular-nums"
                          style={{ color: m.color ?? "#e2e8f0" }}
                        >
                          {m.value}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Optional detail block — only Hot Products renders the
                  3-product visual grid here (not reducible to rows). */}
              {panelConfig.renderDetailBlock?.(ctx)}

              {/* Methodology — DrawerHowCalculated shape. Always shown,
                  so a cold-start merchant still sees HOW the number is
                  computed — "premuroso" even when there's no data yet. */}
              <div>
                <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                  How this is calculated
                </div>
                <div className="rounded-xl border border-white/[0.05] bg-[#0b0b14]/50 p-5">
                  <p className="text-[13px] leading-relaxed text-slate-300">
                    {panelConfig.methodology.formula}
                  </p>
                  {methodologyInputs.length > 0 && (
                    <ul className="mt-4 space-y-1.5 text-[12.5px]">
                      {methodologyInputs.map((inp, i) => (
                        <li
                          key={i}
                          className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5 last:border-0 last:pb-0"
                        >
                          <span className="text-slate-500">{inp.label}</span>
                          <span className="tabular-nums text-slate-300">{inp.value}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {panelConfig.methodology.note && (
                    <p className="mt-4 text-[12.5px] leading-relaxed italic text-slate-400">
                      {panelConfig.methodology.note}
                    </p>
                  )}
                </div>
              </div>
            </div>

            {/* ── Section 3: your next moves (accent-colored) ── */}
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

              {/* Primary action — elevated. Renders as <a> when href
                  is present, giving the merchant a one-click jump to
                  the relevant Shopify admin or storefront page. */}
              {primaryAction && (() => {
                const innerContent = (
                  <>
                    <div
                      className="text-[10px] font-bold uppercase tracking-[0.2em]"
                      style={{ color: accent.hero }}
                    >
                      {primaryAction.headline}
                    </div>
                    <div className="mt-2 text-[16px] font-bold leading-snug text-white">
                      {primaryAction.label}
                    </div>
                    <p className="mt-2.5 max-w-3xl text-[13.5px] leading-relaxed text-slate-300">
                      {primaryAction.description}
                    </p>
                    {primaryAction.href && (
                      <div
                        className="mt-3 inline-flex items-center gap-1.5 text-[12px] font-bold uppercase tracking-wider"
                        style={{ color: accent.hero }}
                      >
                        {primaryAction.hrefLabel ?? "Open in Shopify admin"}
                        <svg
                          xmlns="http://www.w3.org/2000/svg"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth={2}
                          className="h-3.5 w-3.5"
                          aria-hidden="true"
                        >
                          <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                        </svg>
                      </div>
                    )}
                  </>
                );
                return primaryAction.href ? (
                  <a
                    href={primaryAction.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block rounded-xl border border-white/[0.08] bg-[#0e0e1a]/80 p-5 transition-colors hover:border-white/[0.16] hover:bg-[#0e0e1a]"
                  >
                    {innerContent}
                  </a>
                ) : (
                  <div className="rounded-xl border border-white/[0.08] bg-[#0e0e1a]/80 p-5">
                    {innerContent}
                  </div>
                );
              })()}

              {/* Supporting actions — secondary, numbered. Anchor when
                  href is set, div otherwise. */}
              {supportingActions.length > 0 && (
                <ul className="mt-3 space-y-2">
                  {supportingActions.map((s, i) => {
                    const supportInner = (
                      <>
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
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-[13.5px] font-semibold text-slate-200">
                              {s.label}
                            </span>
                            {s.href && (
                              <svg
                                xmlns="http://www.w3.org/2000/svg"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke={accent.hero}
                                strokeWidth={2}
                                className="h-3 w-3 flex-shrink-0"
                                aria-hidden="true"
                              >
                                <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                              </svg>
                            )}
                          </div>
                          <p className="mt-1 text-[12.5px] leading-relaxed text-slate-400">
                            {s.description}
                          </p>
                          {s.href && (
                            <div
                              className="mt-1.5 text-[11px] font-bold uppercase tracking-wider"
                              style={{ color: accent.hero }}
                            >
                              {s.hrefLabel ?? "Open in Shopify admin"}
                            </div>
                          )}
                        </div>
                      </>
                    );
                    return s.href ? (
                      <li key={i}>
                        <a
                          href={s.href}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-start gap-3 rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 px-4 py-3 transition-colors hover:border-white/[0.12] hover:bg-[#0e0e1a]"
                        >
                          {supportInner}
                        </a>
                      </li>
                    ) : (
                      <li
                        key={i}
                        className="flex items-start gap-3 rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 px-4 py-3"
                      >
                        {supportInner}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        );
      })()}
    </section>
  );
}

// ----------------------------------------------------------------------
// PanelConfig v4 — structured storytelling per spec.
// ----------------------------------------------------------------------

type PanelCtx = {
  heroValue: string;
  heroLoading: boolean;
  rarsData: RarsPayload;
  briefData: DailyBrief | null;
  abandonedData: AbandonedPayload;
  liveOppsData: LiveOppsPayload;
  topProducts: TopProduct[];
  displayCurrency: "USD" | "EUR";
  loading: boolean;
  coldStartPhase: number;
  shop: string;
};

type DonutSegment = { label: string; value: number; color: string };

type HeroStat = {
  label: string;
  /** Either a pre-formatted display string (e.g. "42%", "3 sources")
   *  or a raw number — when number, the renderer formats it as
   *  currency using the merchant's `displayCurrency`. Empty-state
   *  sample values use the number form so a USD merchant doesn't
   *  see a hardcoded €-symbol that mismatches their store currency. */
  value: string | number;
  sublabel: string;
  color: string;
};

type KeyMetric = {
  label: string;
  value: string | number;
  color?: string;
};

type PrimaryAction = {
  headline: string;
  label: string;
  description: string;
  /** Optional deep link — if present, the card renders as an anchor
   *  that opens the URL in a new tab. Used to jump the merchant
   *  directly to the relevant Shopify admin page or storefront URL
   *  instead of leaving them to hunt for it manually. */
  href?: string;
  /** Short label shown on the CTA button when href is set. Defaults
   *  to "Open in Shopify admin" when undefined. */
  hrefLabel?: string;
};

type SupportingAction = {
  label: string;
  description: string;
  href?: string;
  hrefLabel?: string;
};

type PanelConfig = {
  title: string;
  getSubtitle: (ctx: PanelCtx) => string | null;
  // Section 1 — cosa è
  mechanics: string;
  stakes: string;
  // Section 2 — analisi
  getHeroStat: (ctx: PanelCtx) => HeroStat | null;
  getDonutSegments: (ctx: PanelCtx) => DonutSegment[] | null;
  getDonutHero: (ctx: PanelCtx) => { value: string; label: string };
  getKeyMetrics: (ctx: PanelCtx) => KeyMetric[];
  methodology: {
    formula: string;
    getInputs: (ctx: PanelCtx) => Array<{ label: string; value: string }>;
    note: string;
  };
  renderDetailBlock?: (ctx: PanelCtx) => ReactNode;
  /**
   * Cold-start preview. When the merchant has no data yet (getHeroStat
   * returns null), we replace the empty hero+metrics block with a
   * faded "here's what this card will look like" sample so day-1 feels
   * premium instead of a wall of "—". Every sample value is labeled
   * as an example, never mistakable for real data.
   */
  empty: {
    description: string;
    sampleHeroStat: HeroStat;
    sampleKeyMetrics: KeyMetric[];
  };
  // Section 3 — what to do
  getPrimaryAction: (ctx: PanelCtx) => PrimaryAction | null;
  getSupportingActions: (ctx: PanelCtx) => SupportingAction[];
};

// ----------------------------------------------------------------------
// EmptyPreview — day-1 cold-start affordance.
//
// When a feature has no real data (getHeroStat returns null), we render
// THIS instead of an empty hero + "—" metrics list: a clearly-labeled
// sample showing EXACTLY what the card will look like once data flows,
// plus a live "watching your storefront" pulse that reassures the
// merchant Spark is actively listening. This is the difference between
// a dashboard that feels premium on day-1 and one that feels like it
// hasn't loaded.
// ----------------------------------------------------------------------

function EmptyPreview({
  config,
  accentHero,
  displayCurrency,
}: {
  config: PanelConfig["empty"];
  accentHero: string;
  displayCurrency: "USD" | "EUR";
}) {
  // Number values get formatted with the merchant's currency so a
  // USD merchant never sees a hardcoded €-symbol on the empty-state
  // preview. String values pass through (e.g. "42%", "3 sources").
  const fmt = (v: string | number) =>
    typeof v === "number" ? formatMoneyCompact(v, displayCurrency) : v;
  return (
    <div className="mb-6 rounded-xl border border-dashed border-white/[0.12] bg-[#0b0b14]/40 p-5 sm:p-6">
      <div className="mb-3 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
        <span
          className="inline-block h-1.5 w-1.5 animate-pulse rounded-full"
          style={{ background: accentHero }}
          aria-hidden="true"
        />
        Preview — what this card will show
      </div>
      <p className="mb-5 text-[13px] leading-relaxed text-slate-400">
        {config.description}
      </p>
      {/* Sample hero stat — half opacity so it's visually marked as
          example data, never mistakable for real. */}
      <div className="pointer-events-none mb-4 rounded-xl border border-white/[0.05] bg-[#0b0b14]/60 p-5 opacity-50">
        <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
          {config.sampleHeroStat.label}
        </div>
        <div
          className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums"
          style={{ color: config.sampleHeroStat.color }}
        >
          {fmt(config.sampleHeroStat.value)}
        </div>
        <div className="mt-2.5 text-[12.5px] leading-relaxed text-slate-400">
          {config.sampleHeroStat.sublabel}
        </div>
      </div>
      <div className="pointer-events-none mb-5 divide-y divide-white/[0.04] rounded-xl border border-white/[0.05] bg-[#0b0b14]/40 opacity-50">
        {config.sampleKeyMetrics.map((m, i) => (
          <div key={i} className="flex items-center justify-between gap-4 px-4 py-3">
            <span className="text-[13px] text-slate-400">{m.label}</span>
            <span
              className="text-[14px] font-bold tabular-nums"
              style={{ color: m.color ?? "#e2e8f0" }}
            >
              {fmt(m.value)}
            </span>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 rounded-lg bg-emerald-500/[0.05] border border-emerald-400/15 px-3 py-2 text-[11.5px] font-semibold text-emerald-300">
        <span
          className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400"
          aria-hidden="true"
        />
        Watching your storefront — real data will replace this preview within minutes.
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Donut — inline SVG, no library. Accepts 2–6 colored segments,
// renders a center label, legend below.
// ----------------------------------------------------------------------

function Donut({
  segments,
  hero,
  size = 180,
}: {
  segments: DonutSegment[];
  hero: { value: string; label: string };
  size?: number;
}) {
  const strokeWidth = 18;
  const radius = (size - strokeWidth) / 2;
  const center = size / 2;
  const circumference = 2 * Math.PI * radius;
  const total = segments.reduce((s, seg) => s + Math.max(0, seg.value), 0);
  const hasData = total > 0 && segments.length > 0;

  let cumulative = 0;

  return (
    <div className="flex flex-col items-center gap-3">
      <div className="relative" style={{ width: size, height: size }}>
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          role="img"
          aria-label={`Distribution donut chart: ${segments.map((s) => `${s.label} ${s.value}`).join(", ")}`}
        >
          <circle
            cx={center}
            cy={center}
            r={radius}
            fill="none"
            stroke="rgba(148, 163, 184, 0.08)"
            strokeWidth={strokeWidth}
          />
          {hasData &&
            segments.map((seg, i) => {
              const v = Math.max(0, seg.value);
              if (v <= 0) return null;
              const dashLength = (v / total) * circumference;
              const offset = -(cumulative / total) * circumference;
              cumulative += v;
              return (
                <circle
                  key={`${seg.label}-${i}`}
                  cx={center}
                  cy={center}
                  r={radius}
                  fill="none"
                  stroke={seg.color}
                  strokeWidth={strokeWidth}
                  strokeDasharray={`${dashLength} ${circumference - dashLength + 0.001}`}
                  strokeDashoffset={offset}
                  transform={`rotate(-90 ${center} ${center})`}
                  strokeLinecap="butt"
                />
              );
            })}
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <div className="text-[22px] font-extrabold leading-none text-white tabular-nums">
            {hero.value}
          </div>
          {hero.label && (
            <div className="mt-1.5 max-w-[60%] text-center text-[9.5px] font-medium uppercase tracking-[0.12em] leading-tight text-slate-400">
              {hero.label}
            </div>
          )}
        </div>
      </div>

      <ul className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5 max-w-xs">
        {segments.map((seg, i) => (
          <li
            key={`leg-${seg.label}-${i}`}
            className="flex items-center gap-1.5 text-[11px]"
          >
            <span
              className="h-2 w-2 flex-shrink-0 rounded-full"
              style={{ background: seg.color }}
              aria-hidden="true"
            />
            <span className="font-medium text-slate-300">{seg.label}</span>
            <span className="tabular-nums text-slate-500">{seg.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// Humanize leak_point slugs from the abandoned-intent payload.
function humanizeLeak(leak?: string): string {
  if (!leak) return "an unknown signal";
  return (
    {
      scroll_no_click: "scrolled but didn't click",
      view_no_cart: "viewed but didn't add to cart",
      cart_no_checkout: "carted but abandoned",
      high_intent_no_buy: "high-intent but didn't buy",
      bounce: "bounced quickly",
      browse_to_cart: "stopped at the product page",
      cart_to_purchase: "stopped at checkout",
    } as Record<string, string>
  )[leak] ?? leak.replace(/_/g, " ");
}

// Humanize RARS component sources.
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

// Humanize signal_type from brief metrics_snapshot.
function humanizeSignalType(signal?: string): string {
  if (!signal) return "signal";
  return (
    {
      TRAFFIC_SPIKE: "Traffic spike",
      HIGH_TRAFFIC_NO_CART: "High traffic · no cart",
      LOW_CONVERSION_ATTENTION: "Low conversion · high attention",
      HIGH_INTENT_NO_BUY: "High intent · no buy",
      SCROLL_NO_CLICK: "Scroll · no click",
    } as Record<string, string>
  )[signal] ?? signal.replace(/_/g, " ").toLowerCase();
}

const PANEL_CONFIG: Record<CassettoneId, PanelConfig> = {
  // ------------------------------------------------------------------
  // 1. Revenue at risk (amber)
  // ------------------------------------------------------------------
  "revenue-at-risk": {
    title: "Revenue at risk",
    mechanics:
      "I add up every signal on your store that points to lost revenue this month — abandoned high-intent carts, refund trends, nudges underperforming peers, and targets you're missing. One number, five sources, updated every minute.",
    stakes:
      "This is the money HedgeSpark exists to earn back for you. Leaving it on the floor is the most expensive thing you can do this month — cheaper than acquiring new traffic to replace it.",
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
    getHeroStat: (ctx) => {
      const comps = (ctx.rarsData?.components ?? [])
        .filter((c) => c.loss_eur > 0)
        .sort((a, b) => b.loss_eur - a.loss_eur);
      if (comps.length === 0) return null;
      const top = comps[0];
      const total = comps.reduce((s, c) => s + c.loss_eur, 0);
      const ccy = ctx.rarsData?.currency ?? ctx.displayCurrency;
      const share = total > 0 ? Math.round((top.loss_eur / total) * 100) : 0;
      return {
        label: "Biggest leak right now",
        value: formatMoneyCompact(top.loss_eur, ccy),
        sublabel: `From ${humanizeRarsSource(top.source)} — ${share}% of your total at-risk amount this month.`,
        color: "#fbbf24",
      };
    },
    getDonutSegments: (ctx) => {
      const comps = (ctx.rarsData?.components ?? []).filter((c) => c.loss_eur > 0);
      if (comps.length === 0) return null;
      const colorMap: Record<string, string> = {
        abandoned_high_intent: "#f87171",
        refund_decline: "#fbbf24",
        nudge_gap: "#a78bfa",
        below_benchmark: "#60a5fa",
        goal_gap: "#e8a04e",
      };
      return comps
        .sort((a, b) => b.loss_eur - a.loss_eur)
        .map((c) => ({
          label: humanizeRarsSource(c.source),
          value: Math.round(c.loss_eur),
          color: colorMap[c.source] ?? "#94a3b8",
        }));
    },
    getDonutHero: (ctx) => ({
      value: ctx.heroValue,
      label: "at risk",
    }),
    getKeyMetrics: (ctx) => {
      const comps = (ctx.rarsData?.components ?? []).filter((c) => c.loss_eur > 0);
      const total = comps.reduce((s, c) => s + c.loss_eur, 0);
      const prevented = ctx.rarsData?.prevented_eur_this_month ?? 0;
      const ccy = ctx.rarsData?.currency ?? ctx.displayCurrency;
      const top = [...comps].sort((a, b) => b.loss_eur - a.loss_eur)[0];
      return [
        {
          label: "Total at risk this month",
          value: total > 0 ? formatMoneyCompact(total, ccy) : "—",
          color: total > 0 ? "#fbbf24" : undefined,
        },
        {
          label: "Already prevented this month",
          value: prevented > 0 ? formatMoneyCompact(prevented, ccy) : "—",
          color: prevented > 0 ? "#34d399" : undefined,
        },
        {
          label: "Active leak sources",
          value: `${comps.length}`,
        },
        {
          label: "Top leak share",
          value: top && total > 0 ? `${Math.round((top.loss_eur / total) * 100)}%` : "—",
        },
      ];
    },
    methodology: {
      formula:
        "Sum of five independent signal losses (abandoned high-intent, refund decline, nudge gap, below-benchmark, goal gap), reduced by already-prevented amounts and priced in your store's currency.",
      getInputs: (ctx) => {
        const comps = (ctx.rarsData?.components ?? [])
          .filter((c) => c.loss_eur > 0)
          .sort((a, b) => b.loss_eur - a.loss_eur);
        const ccy = ctx.rarsData?.currency ?? ctx.displayCurrency;
        return comps.map((c) => ({
          label: humanizeRarsSource(c.source),
          value: formatMoneyCompact(c.loss_eur, ccy),
        }));
      },
      note:
        "Only components with material loss are included. The component list mirrors the full breakdown you see on Pro — nothing is hidden from Lite.",
    },
    empty: {
      description:
        "The moment any of the five loss signals crosses threshold, the biggest leak + prioritized fixes land here. Until then, a clean slate means your store isn't bleeding money this month.",
      sampleHeroStat: {
        label: "Biggest leak right now",
        value: 420,
        sublabel: "From abandoned high-intent carts — 42% of your total at-risk amount.",
        color: "#fbbf24",
      },
      sampleKeyMetrics: [
        { label: "Total at risk this month", value: 1020, color: "#fbbf24" },
        { label: "Already prevented this month", value: 280, color: "#34d399" },
        { label: "Active leak sources", value: "3" },
        { label: "Top leak share", value: "42%" },
      ],
    },
    getPrimaryAction: (ctx) => {
      const comps = (ctx.rarsData?.components ?? [])
        .filter((c) => c.loss_eur > 0)
        .sort((a, b) => b.loss_eur - a.loss_eur);
      if (comps.length === 0) {
        return {
          headline: "All clear",
          label: "No active leak source right now",
          description:
            "Use the quiet period to look at Hot Products below — doubling down on what's working beats chasing small leaks. When a new signal crosses threshold, you'll see it here first.",
        };
      }
      const top = comps[0];
      const ccy = ctx.rarsData?.currency ?? ctx.displayCurrency;
      const total = comps.reduce((s, c) => s + c.loss_eur, 0);
      const share = total > 0 ? Math.round((top.loss_eur / total) * 100) : 0;
      return {
        headline: "Fix first",
        label: `Tackle ${humanizeRarsSource(top.source)}`,
        description: `${formatMoneyCompact(top.loss_eur, ccy)} is leaking from this single source — roughly ${share}% of your total at-risk amount this month. Addressing it alone moves the needle more than anything else on your list.`,
      };
    },
    getSupportingActions: (ctx) => {
      const out: SupportingAction[] = [];
      const comps = (ctx.rarsData?.components ?? [])
        .filter((c) => c.loss_eur > 0)
        .sort((a, b) => b.loss_eur - a.loss_eur);
      const ccy = ctx.rarsData?.currency ?? ctx.displayCurrency;
      if (comps.length >= 2) {
        out.push({
          label: `Then tackle ${humanizeRarsSource(comps[1].source)}`,
          description: `${formatMoneyCompact(comps[1].loss_eur, ccy)} from your second-largest source. Address once the primary leak is under control — not in parallel, or you won't know which fix moved which number.`,
        });
      }
      const prevented = ctx.rarsData?.prevented_eur_this_month ?? 0;
      if (prevented > 0) {
        out.push({
          label: "Keep the active nudges running",
          description: `${formatMoneyCompact(prevented, ccy)} prevented so far this month — your active nudges are earning their rent. Don't pause them while you experiment with the leak above.`,
        });
      } else if (comps.length > 0) {
        out.push({
          label: "Drill into the specifics below",
          description:
            "Open Abandoned Intent and Live Opportunities — those two panels show the concrete pages and products behind the risk number above, with the exact action each one needs.",
        });
      }
      return out;
    },
  },

  // ------------------------------------------------------------------
  // 2. Daily brief (violet)
  // ------------------------------------------------------------------
  "daily-brief": {
    title: "Daily brief",
    mechanics:
      "Every morning I scan the previous 24 hours of events on your store, rank every finding by economic impact, and surface the top story. If a signal is trending toward money lost, you hear about it here first.",
    stakes:
      "Missing today's brief means missing the day's biggest opportunity. Merchants who act on the brief within 4 hours convert roughly 2× better on the flagged signal than those who wait until tomorrow.",
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
    getHeroStat: (ctx) => {
      const count = ctx.briefData?.signals_count ?? 0;
      if (count === 0) return null;
      const topProd = ctx.briefData?.top_product_label;
      const topAction = ctx.briefData?.top_action;
      return {
        label: "Lead story today",
        value: topProd ?? "Today's top signal",
        sublabel: topAction
          ? `Action suggested: ${topAction}`
          : "Open the analysis below to see why this lead ranked first.",
        color: "#a78bfa",
      };
    },
    getDonutSegments: (ctx) => {
      const snap = ctx.briefData?.metrics_snapshot;
      if (!snap || snap.length === 0) return null;
      const colorMap: Record<string, string> = {
        TRAFFIC_SPIKE: "#f87171",
        HIGH_TRAFFIC_NO_CART: "#fbbf24",
        LOW_CONVERSION_ATTENTION: "#60a5fa",
        HIGH_INTENT_NO_BUY: "#e8a04e",
        SCROLL_NO_CLICK: "#a78bfa",
      };
      const counts: Record<string, number> = {};
      for (const s of snap) {
        const key = s.signal_type || "OTHER";
        counts[key] = (counts[key] ?? 0) + 1;
      }
      return Object.entries(counts)
        .sort((a, b) => b[1] - a[1])
        .map(([key, count]) => ({
          label: humanizeSignalType(key),
          value: count,
          color: colorMap[key] ?? "#94a3b8",
        }));
    },
    getDonutHero: (ctx) => ({
      value: ctx.heroValue,
      label: "findings today",
    }),
    getKeyMetrics: (ctx) => {
      const count = ctx.briefData?.signals_count ?? 0;
      const snap = ctx.briefData?.metrics_snapshot ?? [];
      const counts: Record<string, number> = {};
      for (const s of snap) {
        const key = s.signal_type || "OTHER";
        counts[key] = (counts[key] ?? 0) + 1;
      }
      const topSignal = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
      return [
        { label: "Findings today", value: `${count}`, color: count > 0 ? "#a78bfa" : undefined },
        { label: "Lead product", value: ctx.briefData?.top_product_label ?? "—" },
        { label: "Signal types today", value: `${Object.keys(counts).length}` },
        {
          label: "Most frequent signal",
          value: topSignal ? humanizeSignalType(topSignal[0]) : "—",
        },
      ];
    },
    methodology: {
      formula:
        "Events in the last 24 hours are grouped by signal type. Each signal is scored by recoverable revenue × urgency × confidence, then ranked. The top-scoring signal becomes the lead story.",
      getInputs: (ctx) => {
        const snap = ctx.briefData?.metrics_snapshot ?? [];
        const counts: Record<string, number> = {};
        for (const s of snap) {
          const key = s.signal_type || "OTHER";
          counts[key] = (counts[key] ?? 0) + 1;
        }
        return Object.entries(counts)
          .sort((a, b) => b[1] - a[1])
          .map(([key, count]) => ({
            label: humanizeSignalType(key),
            value: `${count} event${count !== 1 ? "s" : ""}`,
          }));
      },
      note:
        "Rankings refresh every 10 minutes during trading hours. A signal that's old but has a new event layered on top can still re-surface as today's lead.",
    },
    empty: {
      description:
        "Every morning I rank yesterday's events by economic impact. The top story leads the brief; the rest follow. Once traffic arrives overnight, the first brief lands here.",
      sampleHeroStat: {
        label: "Lead story today",
        value: "Silk Pillowcase",
        sublabel: "Action suggested: price-test a small drop to match the engaged-visitor willingness-to-pay band.",
        color: "#a78bfa",
      },
      sampleKeyMetrics: [
        { label: "Findings today", value: "4", color: "#a78bfa" },
        { label: "Lead product", value: "Silk Pillowcase" },
        { label: "Signal types today", value: "3" },
        { label: "Most frequent signal", value: "High intent · no buy" },
      ],
    },
    getPrimaryAction: (ctx) => {
      const count = ctx.briefData?.signals_count ?? 0;
      if (!ctx.briefData || count === 0) {
        return {
          headline: "Quiet morning",
          label: "Nothing urgent yet — good time to explore",
          description:
            "No finding has crossed the economic-impact threshold today. Use the quiet window to check Hot Products and see what's winning — it's the best kind of day to invest in what's already working.",
        };
      }
      const topProd = ctx.briefData.top_product_label;
      const topAction = ctx.briefData.top_action;
      if (topProd && topAction) {
        return {
          headline: "Act on today's lead",
          label: `${topProd}: ${topAction}`,
          description:
            "The lead story has the highest economic impact of anything I've seen today. Acting on it in the next few hours is meaningfully more effective than waiting until tomorrow.",
        };
      }
      if (topProd) {
        return {
          headline: "Follow the signal",
          label: `Today's top signal is on ${topProd}`,
          description:
            "Open the Hot Products panel below to see what's happening on this product, then decide whether to double down, fix a leak, or adjust traffic.",
        };
      }
      return {
        headline: "Read the brief",
        label: `${count} findings ranked below`,
        description:
          "Each finding is scored by economic impact. Work them in the ranked order — the first one always moves the needle most.",
      };
    },
    getSupportingActions: (ctx) => {
      const out: SupportingAction[] = [];
      const count = ctx.briefData?.signals_count ?? 0;
      if (count > 1) {
        out.push({
          label: `Work through the remaining ${count - 1} finding${count - 1 !== 1 ? "s" : ""}`,
          description:
            "After the lead story, the rest of the brief is ranked by impact too. Tackle them in order; skipping ahead almost always underperforms.",
        });
      }
      out.push({
        label: "Come back tomorrow morning",
        description:
          "The brief refreshes overnight as new signals mature. A finding that's marginal today can become actionable by tomorrow if the trend strengthens.",
      });
      return out;
    },
  },

  // ------------------------------------------------------------------
  // 3. Abandoned intent (rose)
  // ------------------------------------------------------------------
  "abandoned-intent": {
    title: "Abandoned intent",
    mechanics:
      "These are your warmest leads that didn't close: visitors who scrolled your product pages, dwelled, sometimes added to cart — and still didn't buy. I compare buyer depth vs non-buyer depth and surface the products with the widest gap between interest and conversion.",
    stakes:
      "Traffic you already paid for is walking away with their wallet still in their pocket. Fixing one bottleneck on this list is almost always cheaper than buying more ad traffic to replace the lost intent.",
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
    getHeroStat: (ctx) => {
      const list = ctx.abandonedData?.products ?? [];
      if (list.length === 0) return null;
      const worst = [...list].sort(
        (a, b) => (b.abandon_rate_pct ?? 0) - (a.abandon_rate_pct ?? 0),
      )[0];
      if (!worst) return null;
      const rate = worst.abandon_rate_pct ?? 0;
      const views = worst.views_7d ?? 0;
      const carts = worst.carts_7d ?? 0;
      const purchases = worst.purchases_7d ?? 0;
      return {
        label: "Biggest leak this week",
        value: `${rate.toFixed(0)}%`,
        sublabel: `${worst.product_name ?? "—"} · ${views} views · ${carts} carts · ${purchases} sales.`,
        color: "#f87171",
      };
    },
    getDonutSegments: (ctx) => {
      const products = ctx.abandonedData?.products;
      if (!products || products.length === 0) return null;
      const colorMap: Record<string, string> = {
        scroll_no_click: "#a78bfa",
        view_no_cart: "#60a5fa",
        cart_no_checkout: "#fbbf24",
        high_intent_no_buy: "#f87171",
        bounce: "#94a3b8",
        browse_to_cart: "#fbbf24",
        cart_to_purchase: "#ef4444",
      };
      const counts: Record<string, number> = {};
      for (const p of products) {
        const key = p.leak_point || "unknown";
        counts[key] = (counts[key] ?? 0) + 1;
      }
      return Object.entries(counts)
        .sort((a, b) => b[1] - a[1])
        .map(([key, count]) => ({
          label: humanizeLeak(key),
          value: count,
          color: colorMap[key] ?? "#94a3b8",
        }));
    },
    getDonutHero: (ctx) => ({
      value: ctx.heroValue,
      label: "products leaking",
    }),
    getKeyMetrics: (ctx) => {
      const list = ctx.abandonedData?.products ?? [];
      const browseStage = list.filter(
        (p) => p.leak_point === "view_no_cart" || p.leak_point === "browse_to_cart",
      ).length;
      const cartStage = list.filter(
        (p) => p.leak_point === "cart_no_checkout" || p.leak_point === "cart_to_purchase",
      ).length;
      const highIntent = list.filter((p) => p.leak_point === "high_intent_no_buy").length;
      return [
        {
          label: "Products leaking intent",
          value: `${list.length}`,
          color: list.length > 0 ? "#f87171" : undefined,
        },
        {
          label: "Browse-stage leaks",
          value: `${browseStage}`,
          color: browseStage > 0 ? "#fbbf24" : undefined,
        },
        {
          label: "Cart-stage leaks",
          value: `${cartStage}`,
          color: cartStage > 0 ? "#ef4444" : undefined,
        },
        {
          label: "High-intent no-buys",
          value: `${highIntent}`,
          color: highIntent > 0 ? "#f87171" : undefined,
        },
      ];
    },
    methodology: {
      formula:
        "For each product I compute 1 − (purchases_7d / views_7d), ignoring products with too few views to be statistically real. Each product is then tagged with the leak stage — browse, cart, or checkout — where the drop happened.",
      getInputs: (ctx) => {
        const list = ctx.abandonedData?.products ?? [];
        return list.slice(0, 5).map((p) => ({
          label: p.product_name ?? "—",
          value: `${(p.abandon_rate_pct ?? 0).toFixed(0)}% abandon`,
        }));
      },
      note:
        "Browse-stage leaks usually mean the product page itself isn't convincing. Cart-stage leaks usually mean shipping, price, or checkout friction. The tag on each product tells you where to look.",
    },
    empty: {
      description:
        "Once visitors start bouncing, the products with the widest buyer-vs-non-buyer depth gap will appear here — colored by where they dropped off. Each product becomes a one-click jump to its Shopify admin edit page.",
      sampleHeroStat: {
        label: "Biggest leak this week",
        value: "64%",
        sublabel: "Silk Pillowcase · 220 views · 14 carts · 5 sales.",
        color: "#f87171",
      },
      sampleKeyMetrics: [
        { label: "Products leaking intent", value: "7", color: "#f87171" },
        { label: "Browse-stage leaks", value: "4", color: "#fbbf24" },
        { label: "Cart-stage leaks", value: "2", color: "#ef4444" },
        { label: "High-intent no-buys", value: "1", color: "#f87171" },
      ],
    },
    getPrimaryAction: (ctx) => {
      const list = ctx.abandonedData?.products ?? [];
      if (list.length === 0) {
        return {
          headline: "All clear",
          label: "Nothing to recover here right now",
          description:
            "No product on your store is leaking warm intent this week. Channel the effort into Hot Products — pushing more traffic to what's already converting beats fixing quiet products.",
        };
      }
      const top = list[0];
      const name = top?.product_name ?? "your top leak";
      const leak = humanizeLeak(top?.leak_point);
      const hint = top?.leak_point === "browse_to_cart" || top?.leak_point === "view_no_cart"
        ? "The product page itself isn't convincing. Check the photos, price, description, and stock availability first."
        : top?.leak_point === "cart_to_purchase" || top?.leak_point === "cart_no_checkout"
        ? "Cart-stage leaks are almost always shipping cost, unexpected fees, or checkout friction. Those are the three things to audit first."
        : "Review the product page end-to-end and compare it to a product that's converting well for you right now.";
      const adminUrl = buildShopifyAdminProductUrl(ctx.shop, top?.product_url);
      return {
        headline: "Fix first",
        label: `Start with ${name}`,
        description: `Visitors ${leak}. ${hint}`,
        href: adminUrl ?? undefined,
      };
    },
    getSupportingActions: (ctx) => {
      const out: SupportingAction[] = [];
      const list = ctx.abandonedData?.products ?? [];
      if (list.length >= 2 && list[1]?.product_name) {
        const second = list[1];
        const adminUrl = buildShopifyAdminProductUrl(ctx.shop, second.product_url);
        out.push({
          label: `Then: ${second.product_name}`,
          description: `Visitors ${humanizeLeak(second.leak_point)}. Apply the same class of fix if the leak pattern matches — it usually does.`,
          href: adminUrl ?? undefined,
        });
      }
      if (list.length > 0) {
        out.push({
          label: "Email the abandoning segment",
          description:
            "If you have these visitors' emails from past orders or newsletter signups, a 10% off targeted to the abandoning segment typically recovers 15–25% of the lost revenue within a week.",
        });
      }
      return out;
    },
  },

  // ------------------------------------------------------------------
  // 4. Live opportunities (amber-opp)
  // ------------------------------------------------------------------
  "live-opportunities": {
    title: "Live opportunities",
    mechanics:
      "These are the pages on your store leaking intent as I speak. Visitors are reading them, scrolling, clicking around — and not converting. Each row is one page + one reason it's leaking + one concrete fix you can ship in minutes.",
    stakes:
      "This is the fastest money on your store. High-intent pages with the right fix typically recover 10–30% of their lost revenue within a day. Cold acquisition funnels take weeks to recover the same amount.",
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
    getHeroStat: (ctx) => {
      const opps = (ctx.liveOppsData?.opportunities ?? [])
        .filter((o) => o.signal_type !== "LOW_SIGNAL")
        .sort((a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0));
      if (opps.length === 0) return null;
      const top = opps[0];
      return {
        label: "Top opportunity right now",
        value: `Priority ${top.priority_score ?? 0}`,
        sublabel: `${top.url ?? "—"} — ${top.recommended_action ?? "Open the page and audit it."}`,
        color: "#e8a04e",
      };
    },
    getDonutSegments: (ctx) => {
      const opps = (ctx.liveOppsData?.opportunities ?? []).filter(
        (o) => o.signal_type !== "LOW_SIGNAL",
      );
      if (opps.length === 0) return null;
      const colorMap: Record<string, string> = {
        HIGH_INTENT_PAGE: "#fbbf24",
        ENGAGED_PAGE: "#a78bfa",
      };
      const counts: Record<string, number> = {};
      for (const o of opps) {
        const key = o.signal_type || "UNKNOWN";
        counts[key] = (counts[key] ?? 0) + 1;
      }
      return Object.entries(counts)
        .sort((a, b) => b[1] - a[1])
        .map(([key, count]) => ({
          label:
            key === "HIGH_INTENT_PAGE" ? "High intent" :
            key === "ENGAGED_PAGE" ? "Engaged" :
            key.replace(/_/g, " ").toLowerCase(),
          value: count,
          color: colorMap[key] ?? "#94a3b8",
        }));
    },
    getDonutHero: (ctx) => ({
      value: ctx.heroValue,
      label: "pages leaking",
    }),
    getKeyMetrics: (ctx) => {
      const opps = (ctx.liveOppsData?.opportunities ?? []).filter(
        (o) => o.signal_type !== "LOW_SIGNAL",
      );
      const highIntent = opps.filter((o) => o.signal_type === "HIGH_INTENT_PAGE").length;
      const engaged = opps.filter((o) => o.signal_type === "ENGAGED_PAGE").length;
      const top = [...opps].sort(
        (a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0),
      )[0];
      return [
        {
          label: "Pages leaking intent",
          value: `${opps.length}`,
          color: opps.length > 0 ? "#e8a04e" : undefined,
        },
        {
          label: "High-intent pages",
          value: `${highIntent}`,
          color: highIntent > 0 ? "#fbbf24" : undefined,
        },
        {
          label: "Engaged pages",
          value: `${engaged}`,
          color: engaged > 0 ? "#a78bfa" : undefined,
        },
        {
          label: "Top priority score",
          value: top ? `${top.priority_score ?? 0}` : "—",
        },
      ];
    },
    methodology: {
      formula:
        "Pages are scored by engagement (scroll depth + dwell time + click count) minus realized conversion. Top scorers are pages where visitors signal interest but walk away without buying.",
      getInputs: (ctx) => {
        const opps = (ctx.liveOppsData?.opportunities ?? [])
          .filter((o) => o.signal_type !== "LOW_SIGNAL")
          .slice(0, 5);
        return opps.map((o) => ({
          label: o.url ?? "—",
          value: `Priority ${o.priority_score ?? 0}`,
        }));
      },
      note:
        "Rankings re-sort every 5 minutes as visitors flow through. A page that's #3 at 9am can become #1 by noon if a traffic surge hits it.",
    },
    empty: {
      description:
        "As visitors flow through your store, pages that earn engagement but not conversion will rank here by recoverable revenue. One click from each row opens the page your visitors see.",
      sampleHeroStat: {
        label: "Top opportunity right now",
        value: "Priority 82",
        sublabel: "/products/silk-pillowcase — Add urgency copy above the fold.",
        color: "#e8a04e",
      },
      sampleKeyMetrics: [
        { label: "Pages leaking intent", value: "5", color: "#e8a04e" },
        { label: "High-intent pages", value: "3", color: "#fbbf24" },
        { label: "Engaged pages", value: "2", color: "#a78bfa" },
        { label: "Top priority score", value: "82" },
      ],
    },
    getPrimaryAction: (ctx) => {
      const opps = (ctx.liveOppsData?.opportunities ?? [])
        .filter((o) => o.signal_type !== "LOW_SIGNAL")
        .sort((a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0));
      if (opps.length === 0) {
        return {
          headline: "All clear",
          label: "No leaking pages right now",
          description:
            "Your high-engagement pages are converting cleanly. Lean into Hot Products below — those are the pages to invest in while the funnel is healthy.",
        };
      }
      const top = opps[0];
      const urlHint = top.url ? ` on ${top.url}` : "";
      const storefrontUrl = buildStorefrontUrl(ctx.shop, top.url);
      return {
        headline: "Ship this first",
        label: top.recommended_action ?? `Audit${urlHint}`,
        description: `This fix is on the page with the highest priority score right now. High-intent page fixes typically recover 10–30% of lost revenue within a day when shipped fast.`,
        href: storefrontUrl ?? undefined,
        hrefLabel: "Open the page",
      };
    },
    getSupportingActions: (ctx) => {
      const out: SupportingAction[] = [];
      const opps = (ctx.liveOppsData?.opportunities ?? [])
        .filter((o) => o.signal_type !== "LOW_SIGNAL")
        .sort((a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0));
      if (opps.length >= 2 && opps[1]?.recommended_action) {
        const second = opps[1];
        const urlHint2 = second.url ? ` on ${second.url}` : "";
        const storefrontUrl = buildStorefrontUrl(ctx.shop, second.url);
        out.push({
          label: `Then${urlHint2}`,
          description: `${second.recommended_action}. Priority ${second.priority_score ?? 0} — work it after the primary ships.`,
          href: storefrontUrl ?? undefined,
          hrefLabel: "Open the page",
        });
      }
      out.push({
        label: "Come back in a few hours",
        description:
          "As visitors flow through, I'll re-rank these based on fresh priority scores. A second pass in the afternoon typically surfaces 1–2 new opportunities.",
      });
      return out;
    },
  },

  // ------------------------------------------------------------------
  // 5. Hot products (emerald) — Visitor Intent removed 2026-04-29 per
  // strict $0-70 parity (closest competitor Glew $79).
  // ------------------------------------------------------------------
  "hot-products": {
    title: "Hot products",
    mechanics:
      "These are the products pulling the most attention right now — ranked by views, unique visitors, and the intent score I assign each product based on visitor depth. If you want to double down on what's working, start here; if you want to know what to fix, look at Abandoned Intent instead.",
    stakes:
      "Quiet products die quiet deaths. A hot product today that you don't push harder becomes a cold product next week — attention cycles are shorter in ecommerce than merchants instinctively believe.",
    getSubtitle: (ctx) => {
      if (ctx.topProducts.length === 0) {
        return "No hot products yet — your first visitors will populate the list.";
      }
      const top = ctx.topProducts[0];
      return `Your #1 this week: ${top.product_name ?? top.product_id ?? "—"} · ${(top.total_views ?? 0).toLocaleString()} views.`;
    },
    getHeroStat: (ctx) => {
      if (ctx.topProducts.length === 0) return null;
      const top = ctx.topProducts[0];
      const views = top.total_views ?? 0;
      const visitors = top.unique_visitors ?? 0;
      const intent = Math.round(top.avg_intent_score ?? 0);
      return {
        label: "Leading product this week",
        value: top.product_name ?? top.product_id ?? "—",
        sublabel: `${views.toLocaleString()} views · ${visitors.toLocaleString()} visitors · intent ${intent}.`,
        color: "#34d399",
      };
    },
    getDonutSegments: (ctx) => {
      const top3 = ctx.topProducts.slice(0, 3);
      if (top3.length === 0) return null;
      const palette = ["#34d399", "#e8a04e", "#a78bfa"];
      return top3.map((p, i) => {
        const name = p.product_name ?? p.product_id ?? `#${i + 1}`;
        const trimmed = name.length > 22 ? name.slice(0, 20) + "…" : name;
        return {
          label: trimmed,
          value: p.total_views ?? 0,
          color: palette[i] ?? "#94a3b8",
        };
      });
    },
    getDonutHero: (ctx) => {
      const totalViews = ctx.topProducts.slice(0, 3).reduce(
        (s, p) => s + (p.total_views ?? 0),
        0,
      );
      return {
        value: totalViews > 0 ? totalViews.toLocaleString() : "—",
        label: "total views",
      };
    },
    getKeyMetrics: (ctx) => {
      const top = ctx.topProducts[0];
      if (!top) return [];
      const views = top.total_views ?? 0;
      const visitors = top.unique_visitors ?? 0;
      const ratio = visitors > 0 ? (views / visitors) : 0;
      const intent = Math.round(top.avg_intent_score ?? 0);
      return [
        {
          label: "#1 product views",
          value: views.toLocaleString(),
          color: views > 0 ? "#34d399" : undefined,
        },
        {
          label: "#1 product visitors",
          value: visitors.toLocaleString(),
        },
        {
          label: "Views per visitor",
          value: ratio > 0 ? ratio.toFixed(1) : "—",
          color: ratio > 2 ? "#a78bfa" : undefined,
        },
        {
          label: "Intent score",
          value: `${intent}`,
          color: intent >= 70 ? "#34d399" : intent < 40 && intent > 0 ? "#f87171" : undefined,
        },
      ];
    },
    methodology: {
      formula:
        "Products ranked by total_views × average intent_score over the last 7 days. Only products with enough qualifying visitors are surfaced; one-off traffic spikes don't pollute the ranking.",
      getInputs: (ctx) => {
        return ctx.topProducts.slice(0, 3).map((p, i) => ({
          label: `#${i + 1}: ${p.product_name ?? p.product_id ?? "—"}`,
          value: `${(p.total_views ?? 0).toLocaleString()} views`,
        }));
      },
      note:
        "Intent level (HOT/WARM/COLD) uses the same thresholds as Visitor Intent — so the signals are directly comparable between the two panels.",
    },
    empty: {
      description:
        "Products pulling the most attention this week will rank here by views × intent score. Each card becomes a one-click jump to its Shopify admin edit page so you can double down on the winners in minutes.",
      sampleHeroStat: {
        label: "Leading product this week",
        value: "Silk Pillowcase",
        sublabel: "340 views · 180 visitors · intent 72.",
        color: "#34d399",
      },
      sampleKeyMetrics: [
        { label: "#1 product views", value: "340", color: "#34d399" },
        { label: "#1 product visitors", value: "180" },
        { label: "Views per visitor", value: "1.9" },
        { label: "Intent score", value: "72", color: "#34d399" },
      ],
    },
    renderDetailBlock: (ctx) => {
      if (ctx.loading) {
        return (
          <div className="mb-6">
            <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
              Top 3 products
            </div>
            <CardSkeleton label="Loading hot products" />
          </div>
        );
      }
      if (ctx.topProducts.length === 0) {
        return (
          <div className="mb-6">
            <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
              Top 3 products
            </div>
            <CardEmpty
              accent="amber"
              title={ctx.coldStartPhase <= 1 ? "Warming up" : "No hot products yet this week"}
              body={
                ctx.coldStartPhase <= 1
                  ? "Your first visitors will populate this list."
                  : "No products have crossed the intent threshold in the last 7 days."
              }
              eta={ctx.coldStartPhase <= 1 ? "Populates within ~5 minutes of your first visitor" : undefined}
            />
          </div>
        );
      }
      return (
        <div className="mb-6">
          <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
            Top 3 products
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {ctx.topProducts.slice(0, 3).map((product, i) => {
              const adminUrl = buildShopifyAdminProductUrl(ctx.shop, product.product_id);
              return (
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
                  <div className="grid grid-cols-3 gap-2 border-t border-white/[0.05] pt-3">
                    <div>
                      <div className="text-[11px] font-medium uppercase text-slate-400">Views</div>
                      <div className="mt-1 text-[18px] font-bold tabular-nums text-white">
                        {(product.total_views ?? 0).toLocaleString()}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] font-medium uppercase text-slate-400">Visitors</div>
                      <div className="mt-1 text-[18px] font-bold tabular-nums text-white">
                        {(product.unique_visitors ?? 0).toLocaleString()}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] font-medium uppercase text-slate-400">Intent</div>
                      <div className="mt-1 text-[18px] font-bold tabular-nums text-white">
                        {Math.round(product.avg_intent_score ?? 0)}
                      </div>
                    </div>
                  </div>
                  {adminUrl && (
                    <a
                      href={adminUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-4 inline-flex items-center justify-center gap-1.5 rounded-lg border border-emerald-400/25 bg-emerald-500/[0.06] px-3 py-2 text-[11.5px] font-bold uppercase tracking-wider text-emerald-300 transition-colors hover:border-emerald-400/50 hover:bg-emerald-500/[0.12] hover:text-emerald-200"
                    >
                      Open in Shopify admin
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth={2}
                        className="h-3 w-3"
                        aria-hidden="true"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                      </svg>
                    </a>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      );
    },
    getPrimaryAction: (ctx) => {
      if (ctx.topProducts.length === 0) {
        return {
          headline: "Warming up",
          label: "No hot products yet",
          description:
            "Once your first visitors engage with your store, I'll rank products by attention and intent here. Nothing to act on yet — but keep the tracker running.",
        };
      }
      const top = ctx.topProducts[0];
      const name = top?.product_name ?? top?.product_id ?? "your top product";
      const views = top?.total_views ?? 0;
      const visitors = top?.unique_visitors ?? 0;
      const adminUrl = buildShopifyAdminProductUrl(ctx.shop, top?.product_id);
      return {
        headline: "Double down",
        label: `Invest in ${name}`,
        description: `${views.toLocaleString()} view${views !== 1 ? "s" : ""} from ${visitors.toLocaleString()} visitor${visitors !== 1 ? "s" : ""} this week. Add a bundle, refine the description, or push more ad traffic to this product — it's already converting the hardest-earned part of your funnel.`,
        href: adminUrl ?? undefined,
      };
    },
    getSupportingActions: (ctx) => {
      const out: SupportingAction[] = [];
      const top = ctx.topProducts[0];
      if (!top) return out;
      const views = top.total_views ?? 0;
      const visitors = top.unique_visitors ?? 0;
      const intent = Math.round(top.avg_intent_score ?? 0);
      if (views > 0 && visitors > 0 && views / Math.max(1, visitors) > 2) {
        out.push({
          label: "Re-engage the returning visitors",
          description: `${(views / Math.max(1, visitors)).toFixed(1)} views per visitor on this product — they're coming back, which is a strong re-engagement signal. An email or retargeting push typically converts a meaningful share of these visitors.`,
        });
      }
      if (intent > 0 && intent < 40) {
        out.push({
          label: "Refine the traffic quality",
          description: `Intent score is ${intent} — the visitors are looking but not signaling purchase. Consider tightening ad targeting so you acquire closer-to-buy traffic on this product.`,
        });
      } else if (intent >= 70) {
        out.push({
          label: "Push a time-boxed offer",
          description: `Intent is ${intent} — these visitors are warm. A price drop or limited-time offer right now could convert a chunk of them within hours, not weeks.`,
        });
      }
      return out;
    },
  },
};

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
      .GET("/analytics/revenue-at-risk")
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

