"use client";

import Image from "next/image";
import Link from "next/link";
import { type ReactNode, useEffect, useRef } from "react";

export type NavItem = {
  id: string;
  label: string;
  icon: ReactNode;
  pro?: boolean;
};

/* ── Three Floors = Three Tiers ───────────────────────────────────────
 * HedgeSpark dashboard is organized as three tier-named experiences:
 *
 *   Lite   — right-now signals (all merchants, entry-tier)
 *   Pro    — deeper analytics (Pro + Scale merchants)
 *   Scale  — agency, multi-store, API (Scale only)
 *
 * Each floor is a separate route so scrolling stays scoped. Floors
 * above the merchant's tier are visible but rendered as
 * preview-with-lock so the merchant sees the full product surface
 * and what they'd unlock by upgrading. Never hide a feature — gate
 * the drill-down per memo `feedback_no_silent_feature_removal.md`.
 *
 * Naming canonical as of 2026-04-20 (founder directive): the floor
 * labels MATCH the tier names the merchant sees on the landing and
 * in billing. Internal tier codes stay `lite`/`pro`/`scale` because
 * they're a database + JWT payload contract — changing them is a
 * TIER_2 billing-sprint job, tracked in
 * `project_tier_rename_dashboard_backlog.md`.
 * ──────────────────────────────────────────────────────────────────── */
// "settings" is a cross-tier meta-floor — used by /app/settings/*
// routes so the Sidebar does NOT highlight any of the 3 real floors
// (pulse/intelligence/operations) while the merchant is configuring
// their store. Introduced 2026-04-21 per founder directive: "Settings
// non dovrebbe evidenziare nessuna tra Lite, Pro e Scale".
export type Floor = "pulse" | "intelligence" | "operations" | "settings";

type FloorDef = {
  id: Floor;
  label: string;
  href: string;
  icon: ReactNode;
  /** Lowest tier that can fully access this floor. Lower tiers see a preview. */
  requires: "lite" | "pro" | "scale";
  /** Short description shown on hover tooltip + floor landing. */
  desc: string;
};

const FLOORS: FloorDef[] = [
  {
    id: "pulse",
    label: "Lite",
    href: "/app/lite",
    requires: "lite",
    desc: "Right-now signals across your store",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 12h3l2.25-6.75L12 18.75l2.25-9 2.25 4.5h2.25" />
      </svg>
    ),
  },
  {
    id: "intelligence",
    label: "Pro",
    href: "/app/pro",
    requires: "pro",
    desc: "Deep analytics: cohort, P&L, causal lift, Ask HS",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.847.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
      </svg>
    ),
  },
  {
    id: "operations",
    label: "Scale",
    href: "/app/scale",
    requires: "scale",
    desc: "Agency, multi-store, API, Shopify Admin actions",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
];

function isFloorAccessible(floor: FloorDef, tier?: "lite" | "pro" | "scale"): boolean {
  const rank: Record<string, number> = { lite: 1, pro: 2, scale: 3 };
  const t = tier ?? "lite";
  return rank[t] >= rank[floor.requires];
}

export { FLOORS };

const NAV_ITEMS: NavItem[] = [
  {
    id: "brief",
    label: "Daily Brief",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    id: "overview",
    label: "Store Pulse",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" />
      </svg>
    ),
  },
  {
    id: "revenue",
    label: "Revenue",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m-3-2.818l.879.659c1.171.879 3.07.879 4.242 0 1.172-.879 1.172-2.303 0-3.182C13.536 12.219 12.768 12 12 12c-.725 0-1.45-.22-2.003-.659-1.106-.879-1.106-2.303 0-3.182s2.9-.879 4.006 0l.415.33M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    id: "signals",
    label: "Signals & Products",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
      </svg>
    ),
  },
  {
    id: "funnel",
    label: "Funnel & Sessions",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 01-.659 1.591l-5.432 5.432a2.25 2.25 0 00-.659 1.591v2.927a2.25 2.25 0 01-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 00-.659-1.591L3.659 7.409A2.25 2.25 0 013 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0112 3z" />
      </svg>
    ),
    pro: true,
  },
  {
    id: "live",
    label: "Live Radar",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 12h3l2.25-6.75L12 18.75l2.25-9 2.25 4.5h2.25" />
      </svg>
    ),
  },
  {
    id: "audience",
    label: "Audience",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M18 18.72a9.094 9.094 0 003.741-.479 3 3 0 00-4.682-2.72m.94 3.198l.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0112 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 016 18.719m12 0a5.971 5.971 0 00-.941-3.197m0 0A5.995 5.995 0 0012 12.75a5.995 5.995 0 00-5.058 2.772m0 0a3 3 0 00-4.681 2.72 8.986 8.986 0 003.74.477m.94-3.197a5.971 5.971 0 00-.94 3.197M15 6.75a3 3 0 11-6 0 3 3 0 016 0zm6 3a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0zm-13.5 0a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0z" />
      </svg>
    ),
    pro: true,
  },
  {
    id: "nudges",
    label: "Nudges & Lift",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
      </svg>
    ),
    pro: true,
  },
  {
    id: "price-intelligence",
    label: "Price",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.568 3H5.25A2.25 2.25 0 003 5.25v4.318c0 .597.237 1.17.659 1.591l9.581 9.581c.699.699 1.78.872 2.607.33a18.095 18.095 0 005.223-5.223c.542-.827.369-1.908-.33-2.607L11.16 3.66A2.25 2.25 0 009.568 3z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 6h.008v.008H6V6z" />
      </svg>
    ),
    pro: true,
  },
  // NOTE: a "Settings" entry used to live here. Removed 2026-04-21
  // per founder directive: Settings migrated fully to the TopBar
  // gear (top-right) → /app/settings hub → dedicated sub-pages. The
  // Sidebar is for in-page floor navigation; Settings is cross-floor
  // and belongs to the top chrome, not the left nav.
];

// ─── Lite-floor NAV items ────────────────────────────────────────
// The Pulse NAV_ITEMS above (Daily Brief / Store Pulse / Revenue /
// Signals / Funnel / Live / Audience / Nudges / Price) are designed
// for the Pro layout — most of those sections are gated `!isLiteFloor`
// in /app/page.tsx so clicking them on Lite scrolls to nothing.
// Lite has a completely different vertical spine: RARS hero → Peers
// → P&L → Attribution → Retention → Features cassettoni → Radar.
// Founder directive 2026-04-21: "scorro le features di lite e non
// succede niente... solo radar funziona... non fa riferimento alle
// features Lite". NAV_ITEMS_LITE fixes that.
//
// Each id maps to a `section-lite-*` anchor rendered on /app (Lite
// tier) — see page.tsx for the anchors. Clicking scrolls via the
// shared handleNavigate → scrollIntoView pattern.
const NAV_ITEMS_LITE: NavItem[] = [
  {
    id: "lite-rars",
    label: "Revenue at risk",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
      </svg>
    ),
  },
  {
    id: "lite-peers",
    label: "You vs peers",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M15 6.75a3 3 0 11-6 0 3 3 0 016 0zm6 3a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0zm-13.5 0a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0z" />
      </svg>
    ),
  },
  {
    id: "lite-pnl",
    label: "Profit",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 18L9 11.25l4.306 4.306a11.95 11.95 0 015.814-5.519l2.74-1.22m0 0l-5.94-2.28m5.94 2.28l-2.28 5.941" />
      </svg>
    ),
  },
  {
    id: "lite-attribution",
    label: "Attribution",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244" />
      </svg>
    ),
  },
  {
    id: "lite-retention",
    label: "Retention",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
      </svg>
    ),
  },
  {
    id: "lite-signals",
    label: "Signals",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
      </svg>
    ),
  },
  {
    id: "live",
    label: "Live Radar",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 12h3l2.25-6.75L12 18.75l2.25-9 2.25 4.5h2.25" />
      </svg>
    ),
  },
];

const SECTION_TO_NAV: Record<string, string> = {
  brief: "brief",
  overview: "overview",
  revenue: "revenue",
  signals: "signals",
  "product-performance": "signals",
  "what-next": "signals",
  proof: "signals",
  funnel: "funnel",
  sessions: "funnel",
  live: "live",
  audience: "audience",
  nudges: "nudges",
  lift: "nudges",
  "scroll-cohorts": "nudges",
  "price-intelligence": "price-intelligence",
  "market-intelligence": "price-intelligence",
  // Lite-floor anchor ids → NAV_ITEMS_LITE nav ids. The observer
  // watches `section-lite-*` IDs; stripping the section- prefix
  // yields the nav id match.
  "lite-rars": "lite-rars",
  "lite-peers": "lite-peers",
  "lite-pnl": "lite-pnl",
  "lite-attribution": "lite-attribution",
  "lite-retention": "lite-retention",
  "lite-signals": "lite-signals",
};

export { NAV_ITEMS, NAV_ITEMS_LITE, SECTION_TO_NAV };

export function Sidebar({
  collapsed,
  onToggle,
  activeSection,
  onNavigate,
  tier,
  currentFloor = "pulse",
  isLiteView = false,
}: {
  collapsed: boolean;
  onToggle: () => void;
  activeSection: string;
  onNavigate: (id: string) => void;
  tier?: "lite" | "pro";
  /** Which of the three floors the merchant is currently viewing.
   *  Drives the active-state on the floor selector at the top of the
   *  sidebar. Section nav below only renders on the Pulse floor. */
  currentFloor?: Floor;
  /** True when the main /app route is rendering the Lite-floor
   *  vertical (RARS + Peers + P&L + Attribution + Retention +
   *  Cassettoni + Radar). Derived from isLiteFloor in page.tsx — NOT
   *  from the merchant's tier, because a Pro merchant navigating to
   *  /app/lite still sees the Lite layout and wants Lite section-nav.
   *  When true, NAV_ITEMS_LITE is used instead of NAV_ITEMS. */
  isLiteView?: boolean;
}) {
  const activeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeSection]);

  const activeNavId = SECTION_TO_NAV[activeSection] || activeSection;

  return (
    <aside
      className={`sticky top-0 flex h-screen flex-shrink-0 flex-col border-r border-white/[0.06] bg-[#07070f] transition-[width] duration-200 ease-in-out ${
        collapsed ? "w-16" : "w-56"
      }`}
    >
      {/* Brand */}
      <div className="flex h-16 flex-shrink-0 items-center border-b border-white/[0.06] px-3">
        {collapsed ? (
          <Image
            src="/branding/hedgespark/spark.png"
            alt="HedgeSpark"
            width={32}
            height={32}
            className="mx-auto flex-shrink-0"
            priority
          />
        ) : (
          <Image
            src="/logo-beta-v2.png"
            alt="HedgeSpark"
            width={140}
            height={58}
            className="flex-shrink-0"
            priority
          />
        )}
      </div>

      {/* Floor selector — top of sidebar, above section nav.
          Every floor stays visible for every tier; inaccessible
          floors render with a lock icon but are still clickable
          (they navigate to a preview page that shows WHAT Pro/Scale
          unlocks — no dead clicks, no silent hides).

          Hidden on /app/settings/* (currentFloor === "settings") —
          settings is cross-tier configuration, showing 3 floor tabs
          (two of them locked for Lite users) creates visual noise
          that has nothing to do with configuring the shop. Merchant
          returns to the dashboard via the breadcrumb "← Dashboard"
          present on every settings sub-page. */}
      {currentFloor !== "settings" && (
      <div className="flex flex-col gap-1 border-b border-white/[0.04] px-2 py-3">
        {FLOORS.map((floor) => {
          const isActive = currentFloor === floor.id;
          const accessible = isFloorAccessible(floor, tier);
          return (
            <Link
              key={floor.id}
              href={floor.href}
              title={collapsed ? `${floor.label} — ${floor.desc}` : floor.desc}
              className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-[13px] font-bold uppercase tracking-[0.08em] transition-all duration-150 ${
                isActive
                  ? "bg-[#e8a04e]/15 text-[#e8a04e] shadow-[inset_0_0_0_1px_rgba(232,160,78,0.22)]"
                  : accessible
                  ? "text-slate-300 hover:bg-white/[0.05] hover:text-white"
                  : "text-slate-600 hover:bg-white/[0.03] hover:text-slate-500"
              } ${collapsed ? "justify-center" : ""}`}
            >
              <span className="flex-shrink-0">{floor.icon}</span>
              {!collapsed && (
                <>
                  <span className="flex-1 truncate">{floor.label}</span>
                  {!accessible && (
                    <svg
                      className="h-3 w-3 flex-shrink-0 opacity-60"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z" />
                    </svg>
                  )}
                </>
              )}
            </Link>
          );
        })}
      </div>
      )}

      {/* Section nav — contextual to the current floor + tier.
          Lite uses NAV_ITEMS_LITE (7 entries that match the actual
          Lite vertical: RARS, Peers, P&L, Attribution, Retention,
          Features, Radar). Pro/Scale on Pulse floor use the original
          NAV_ITEMS. Other floors currently render no section nav. */}
      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto py-4">
        {currentFloor !== "pulse" ? null : (isLiteView ? NAV_ITEMS_LITE : NAV_ITEMS).map((item) => {
          const isActive = activeNavId === item.id;
          const isLocked = item.pro && tier === "lite";
          return (
            <button
              key={item.id}
              ref={isActive ? activeRef : undefined}
              onClick={() => onNavigate(item.id)}
              title={collapsed ? item.label : undefined}
              className={`mx-2 flex items-center gap-3 rounded-xl px-3 py-3 text-[15px] font-medium transition-all duration-150 ${
                isActive
                  ? "bg-[#d4893a]/15 text-[#e8a04e] shadow-[inset_0_0_0_1px_rgba(212,137,58,0.18)]"
                  : isLocked
                  ? "text-slate-600 hover:bg-white/[0.03] hover:text-slate-500"
                  : "text-slate-400 hover:bg-white/[0.05] hover:text-slate-200"
              } ${collapsed ? "justify-center" : ""}`}
            >
              <span className="flex-shrink-0">{item.icon}</span>
              {!collapsed && (
                <span className="flex min-w-0 flex-1 items-center gap-2 truncate">
                  {item.label}
                  {isLocked && (
                    <span className="rounded border border-[#d4893a]/20 bg-[#d4893a]/10 px-1.5 py-px text-[9px] font-bold uppercase tracking-[0.08em] text-[#d4893a]/60">
                      Pro
                    </span>
                  )}
                </span>
              )}
              {isActive && !collapsed && (
                <span className="ml-auto h-5 w-[3px] flex-shrink-0 rounded-full bg-[#d4893a]" />
              )}
            </button>
          );
        })}
      </nav>

      {/* Collapse toggle */}
      <div className="border-t border-white/[0.06] p-2">
        <button
          onClick={onToggle}
          className="flex w-full items-center justify-center rounded-lg p-2 text-slate-600 transition-colors hover:bg-white/[0.04] hover:text-slate-400"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
            </svg>
          ) : (
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
            </svg>
          )}
        </button>
      </div>
    </aside>
  );
}
