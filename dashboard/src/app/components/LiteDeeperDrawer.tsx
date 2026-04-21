"use client";

/**
 * LiteDeeperDrawer — /app/lite v5 side drawer.
 *
 * The v5 primary slot (`LiteSparkDaily`) carries the 6-zone morning
 * brief; everything v4 had as primary sections (Peers, P&L,
 * Attribution, Retention + cassettoni grid) lives here, one click
 * away. Slide-over on desktop (560px), full-screen on mobile.
 *
 * Contract per /docs/LITE_VISUAL_SPEC_v5.md §7:
 *  • 4 tabs: Peers · P&L · Attribution · Retention
 *  • localStorage.lite_deeper_last_tab persists last opened tab
 *  • localStorage.lite_deeper_{tab}_scroll persists scroll per tab
 *  • Default on very first open: Retention (familiar cassettoni)
 *  • Per-tab footer: amber CTA "Ask Spark about {tab} →" — closes
 *    drawer + scrolls to Zone 6 (AnalyticsAssistant).
 *  • Backdrop bg-black/60 + blur; slide-in 400ms.
 *
 * Nothing in here is fabricated — every child card pulls from the
 * same endpoints the v4 primary sections did.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Image from "next/image";

import type { DisplayCurrency } from "../lib/currency";
import { ChannelAttributionCard } from "./ChannelAttributionCard";
import { CohortSummaryCard } from "./CohortSummaryCard";
import { GatewayProductsCard } from "./GatewayProductsCard";
import { LiteCassettoniGrid, type CassettoneId } from "./LiteCassettoniGrid";
import { MarginDragCard } from "./MarginDragCard";
import { MonthlyCohortsCard } from "./MonthlyCohortsCard";
import { PeerBenchmarksCard } from "./PeerBenchmarksCard";
import { PnlReport } from "./PnlReport";
import type { ComponentProps } from "react";
import { VerticalBenchmarksCard } from "./VerticalBenchmarksCard";

// ─────────────────────────────────────────────────────────────────────
// Tab model
// ─────────────────────────────────────────────────────────────────────

type TabId = "peers" | "pnl" | "attribution" | "retention";

const TABS: Array<{ id: TabId; label: string; accent: string }> = [
  { id: "peers", label: "Peers", accent: "#a78bfa" },
  { id: "pnl", label: "P&L", accent: "#e8a04e" },
  { id: "attribution", label: "Attribution", accent: "#60a5fa" },
  { id: "retention", label: "Retention", accent: "#34d399" },
];

// Per spec §7: "restore last tab" is silent on the very first open.
// Founder decision 2026-04-21: default = Retention — cassettoni grid
// is where v4 regulars lived, so the drawer's first landing feels
// familiar instead of foreign.
const DEFAULT_FIRST_OPEN_TAB: TabId = "retention";

const LS_KEY_LAST_TAB = "lite_deeper_last_tab";
const lsKeyScroll = (tab: TabId) => `lite_deeper_${tab}_scroll`;

function isTabId(v: unknown): v is TabId {
  return v === "peers" || v === "pnl" || v === "attribution" || v === "retention";
}

// ─────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────

type LiteDeeperDrawerProps = {
  open: boolean;
  onClose: () => void;
  apiBase: string;
  shop: string;
  isProUser: boolean;
  displayCurrency: DisplayCurrency;
  pnlData: ComponentProps<typeof PnlReport>["data"];
  topProducts: React.ComponentProps<typeof LiteCassettoniGrid>["topProducts"];
  effectiveBrief: React.ComponentProps<typeof LiteCassettoniGrid>["effectiveBrief"];
  briefLoading: boolean;
  coldStartPhase: React.ComponentProps<typeof LiteCassettoniGrid>["coldStartPhase"];
  loading: boolean;
  cassettoneExpandedId: CassettoneId | null;
  onCassettoneExpandedChange: (id: CassettoneId | null) => void;
};

// ─────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────

export function LiteDeeperDrawer(props: LiteDeeperDrawerProps) {
  const {
    open,
    onClose,
    apiBase,
    shop,
    isProUser,
    displayCurrency,
    pnlData,
    topProducts,
    effectiveBrief,
    briefLoading,
    coldStartPhase,
    loading,
    cassettoneExpandedId,
    onCassettoneExpandedChange,
  } = props;

  // Tab selection — hydrated from localStorage on first open.
  const [activeTab, setActiveTab] = useState<TabId>(DEFAULT_FIRST_OPEN_TAB);
  const [hydrated, setHydrated] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Hydrate last-tab once the drawer opens for the first time.
  useEffect(() => {
    if (!open || hydrated) return;
    try {
      const saved = window.localStorage.getItem(LS_KEY_LAST_TAB);
      if (isTabId(saved)) {
        setActiveTab(saved);
      }
    } catch {
      /* localStorage may be unavailable (private mode); fall through */
    }
    setHydrated(true);
  }, [open, hydrated]);

  // Persist tab + restore scroll when tab changes.
  useEffect(() => {
    if (!open || !hydrated) return;
    try {
      window.localStorage.setItem(LS_KEY_LAST_TAB, activeTab);
      const raw = window.localStorage.getItem(lsKeyScroll(activeTab));
      const saved = raw ? Number.parseInt(raw, 10) : 0;
      // Defer to after paint so the new tab's DOM exists.
      requestAnimationFrame(() => {
        if (scrollRef.current) {
          scrollRef.current.scrollTop = Number.isFinite(saved) ? saved : 0;
        }
      });
    } catch {
      /* noop */
    }
  }, [activeTab, open, hydrated]);

  // Save scroll position as the merchant scrolls (debounced via rAF).
  useEffect(() => {
    if (!open) return;
    const el = scrollRef.current;
    if (!el) return;
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        try {
          window.localStorage.setItem(
            lsKeyScroll(activeTab),
            String(el.scrollTop),
          );
        } catch {
          /* noop */
        }
      });
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      cancelAnimationFrame(raf);
      el.removeEventListener("scroll", onScroll);
    };
  }, [activeTab, open]);

  // Keyboard: Esc closes. Body scroll lock while open.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  // Footer CTA: close + scroll to Zone 6 (AnalyticsAssistant) inside
  // LiteSparkDaily. The spec calls for a pre-fill; shipping the
  // scroll-to affordance now, pre-fill is a follow-up commit on
  // AnalyticsAssistant (it has no prop surface today).
  const askSparkAbout = useCallback(() => {
    onClose();
    setTimeout(() => {
      const el = document.querySelector("[data-ask-spark-zone]");
      if (el instanceof HTMLElement) {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }, 220);
  }, [onClose]);

  if (!open) return null;

  const activeTabMeta = TABS.find((t) => t.id === activeTab) ?? TABS[0];

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="lite-deeper-title"
      className="fixed inset-0 z-[60]"
    >
      {/* Backdrop */}
      <button
        type="button"
        aria-label="Close drawer"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-black/60 backdrop-blur-sm"
      />

      {/* Slide-over panel */}
      <div
        className="absolute right-0 top-0 flex h-full w-full flex-col border-l border-white/[0.06] bg-[#07070f] shadow-[-30px_0_60px_-15px_rgba(0,0,0,0.6)] sm:w-[560px]"
        style={{
          animation: "lite-deeper-slide-in 400ms cubic-bezier(0.16, 1, 0.3, 1)",
        }}
      >
        {/* Top stripe matching the active tab accent (subtle signature) */}
        <div
          className="absolute inset-x-0 top-0 h-[2px] opacity-60 transition-colors duration-300"
          style={{
            background: `linear-gradient(to right, transparent, ${activeTabMeta.accent}, transparent)`,
          }}
        />

        {/* Header */}
        <div className="flex items-start justify-between border-b border-white/[0.04] px-6 pt-7 pb-5 sm:px-8">
          <div className="flex items-start gap-4">
            <Image
              src="/branding/hedgespark/hedgespark-logo.png"
              alt=""
              width={120}
              height={24}
              className="h-6 w-auto opacity-90"
              priority
            />
            <div>
              <h2
                id="lite-deeper-title"
                className="text-[1.5rem] font-extrabold leading-[1.04] tracking-tight text-[#e8a04e]"
              >
                Deeper
              </h2>
              <p className="mt-1 max-w-[360px] text-[12.5px] leading-relaxed text-slate-400">
                All your waterfalls, cohort grids, and attribution — one
                click away. We just don&apos;t lead with them because you
                don&apos;t need them to know what&apos;s broken right now.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.02] text-slate-400 transition hover:border-white/[0.18] hover:bg-white/[0.05] hover:text-slate-200"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
              aria-hidden="true"
            >
              <path
                d="M1 1L13 13M13 1L1 13"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        {/* Sticky tab bar */}
        <div className="sticky top-0 z-10 flex gap-1 border-b border-white/[0.04] bg-[#07070f]/95 px-6 backdrop-blur-md sm:px-8">
          {TABS.map((tab) => {
            const isActive = tab.id === activeTab;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className="relative px-3 py-3.5 text-[13px] font-semibold tracking-tight transition-colors"
                style={{
                  color: isActive ? "#e8a04e" : undefined,
                }}
              >
                <span className={isActive ? "" : "text-slate-400 hover:text-slate-200"}>
                  {tab.label}
                </span>
                {isActive && (
                  <span
                    aria-hidden="true"
                    className="absolute inset-x-2 bottom-0 h-[2px] rounded-full"
                    style={{ backgroundColor: "#e8a04e" }}
                  />
                )}
              </button>
            );
          })}
        </div>

        {/* Scrollable body */}
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto overscroll-contain"
        >
          <div className="px-5 pt-6 pb-10 sm:px-7">
            {activeTab === "peers" && (
              <div className="space-y-5">
                <TabIntro
                  eyebrow="You vs peers"
                  title="How you rank against similar Shopify stores"
                  accent="#a78bfa"
                  body="Four metrics compared against anonymous peers in your revenue band. Minimum 10 peers for privacy — no fake numbers below threshold."
                />
                <PeerBenchmarksCard apiBase={apiBase} shop={shop} isProUser={isProUser} />
                <VerticalBenchmarksCard apiBase={apiBase} shop={shop} isProUser={isProUser} />
              </div>
            )}

            {activeTab === "pnl" && (
              <div className="space-y-5">
                <TabIntro
                  eyebrow="Profit intelligence"
                  title="What you actually keep after costs"
                  accent="#e8a04e"
                  body="Revenue minus COGS, payment fees, and shipping — the real money your store keeps. Waterfall + per-product margin drag below."
                />
                <PnlReport data={pnlData} displayCurrency={displayCurrency} />
                <MarginDragCard
                  apiBase={apiBase}
                  shop={shop}
                  displayCurrency={displayCurrency}
                />
              </div>
            )}

            {activeTab === "attribution" && (
              <div className="space-y-5">
                <TabIntro
                  eyebrow="Channel attribution"
                  title="Where your converting traffic comes from"
                  accent="#60a5fa"
                  body="UTM-deterministic attribution — every converting visitor's first-touch and last-touch source tracked at purchase time. Not modeled, not probabilistic."
                />
                <ChannelAttributionCard
                  apiBase={apiBase}
                  shop={shop}
                  displayCurrency={displayCurrency}
                />
              </div>
            )}

            {activeTab === "retention" && (
              <div className="space-y-5">
                <TabIntro
                  eyebrow="Customer retention"
                  title="How well your customers come back"
                  accent="#34d399"
                  body="Weekly cohort repeat-rates, monthly acquisition economics, gateway products. Plus the 6-feature drill-down grid from v4."
                />
                <CohortSummaryCard apiBase={apiBase} shop={shop} isProUser={isProUser} />
                <MonthlyCohortsCard
                  apiBase={apiBase}
                  shop={shop}
                  displayCurrency={displayCurrency}
                />
                <GatewayProductsCard
                  apiBase={apiBase}
                  shop={shop}
                  displayCurrency={displayCurrency}
                />

                {/* Cassettoni subsection — visual separator so merchants
                    returning from v4 recognise their grid. */}
                <div className="pt-3">
                  <div className="mb-4 flex items-center gap-3">
                    <div className="h-[1px] flex-1 bg-white/[0.06]" />
                    <div className="text-[10.5px] font-bold uppercase tracking-[0.24em] text-slate-500">
                      Feature drill-downs
                    </div>
                    <div className="h-[1px] flex-1 bg-white/[0.06]" />
                  </div>
                  <LiteCassettoniGrid
                    apiBase={apiBase}
                    shop={shop}
                    displayCurrency={displayCurrency}
                    topProducts={topProducts}
                    effectiveBrief={effectiveBrief}
                    briefLoading={briefLoading}
                    coldStartPhase={coldStartPhase}
                    loading={loading}
                    expandedId={cassettoneExpandedId}
                    onExpandedChange={onCassettoneExpandedChange}
                  />
                </div>
              </div>
            )}
          </div>

          {/* Per-tab footer CTA */}
          <div className="sticky bottom-0 border-t border-white/[0.04] bg-[#07070f]/95 px-6 py-4 backdrop-blur-md sm:px-8">
            <button
              type="button"
              onClick={askSparkAbout}
              className="flex w-full items-center justify-center gap-2 rounded-2xl border border-[#e8a04e]/30 bg-[#e8a04e]/[0.06] px-5 py-3 text-[14px] font-semibold text-[#e8a04e] transition hover:border-[#e8a04e]/60 hover:bg-[#e8a04e]/[0.10]"
            >
              Ask Spark about {activeTabMeta.label}
              <span aria-hidden="true">→</span>
            </button>
          </div>
        </div>
      </div>

      <style jsx>{`
        @keyframes lite-deeper-slide-in {
          from {
            transform: translateX(100%);
          }
          to {
            transform: translateX(0);
          }
        }
      `}</style>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Tab intro — shared eyebrow/title/body for each tab
// ─────────────────────────────────────────────────────────────────────

function TabIntro({
  eyebrow,
  title,
  body,
  accent,
}: {
  eyebrow: string;
  title: string;
  body: string;
  accent: string;
}) {
  return (
    <div className="mb-2">
      <div
        className="text-[10.5px] font-bold uppercase tracking-[0.22em]"
        style={{ color: accent }}
      >
        {eyebrow}
      </div>
      <h3 className="mt-1.5 text-[1.35rem] font-extrabold leading-[1.1] tracking-tight text-[#e8a04e] sm:text-[1.55rem]">
        {title}
      </h3>
      <p className="mt-2 max-w-[440px] text-[13.5px] leading-relaxed text-slate-400">
        {body}
      </p>
    </div>
  );
}
