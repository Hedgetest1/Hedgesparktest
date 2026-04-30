"use client";

import Image from "next/image";
import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useRef } from "react";

export type NavItem = {
  id: string;
  label: string;
  icon: ReactNode;
  pro?: boolean;
  /** When set, clicking this nav item navigates to the URL instead of
   *  scrolling within the current floor. */
  href?: string;
};

// `scaleOnly` was a 2026-04-29 anti-pattern that put features the
// merchant sees in their tier's sidebar but rendered on a different
// floor (with a "Scale" badge cross-link). Founder rule (2026-04-30):
// every NAV_ITEMS_PRO entry that competitors $60-130 ship MUST live
// fully on Pro (no badge, no cross-floor click). Items that ONLY
// $140+ competitors ship migrate fully to Scale and are removed from
// the Pro nav entirely. Enforced by audit_pro_nav_section_parity.py.

/* ── Three Floors = Three Tiers ───────────────────────────────────────
 * HedgeSpark dashboard is organized as three tier-named experiences:
 *
 *   Lite   — right-now signals + multi-store brand view (entry-tier)
 *   Pro    — deeper analytics (Pro + Scale merchants)
 *   Scale  — agency white-label, API, Shopify Admin actions (Scale only)
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
export type Floor = "pulse" | "intelligence" | "operations" | "settings" | "reports";

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
    desc: "Agency white-label, API, Shopify Admin actions",
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
    id: "lite-today",
    label: "Today",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    id: "lite-last7",
    label: "Last 7 days",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5" />
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
    id: "lite-refunds",
    label: "Refunds",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 15L3 9m0 0l6-6M3 9h12a6 6 0 010 12h-3" />
      </svg>
    ),
  },
  {
    id: "lite-audience",
    label: "Audience",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15.182 16.318A4.486 4.486 0 0012.016 15a4.486 4.486 0 00-3.198 1.318M21 12a9 9 0 11-18 0 9 9 0 0118 0zM9.75 9.75c0 .414-.168.75-.375.75S9 10.164 9 9.75 9.168 9 9.375 9s.375.336.375.75zm-.375 0h.008v.015h-.008V9.75zm5.625 0c0 .414-.168.75-.375.75s-.375-.336-.375-.75.168-.75.375-.75.375.336.375.75zm-.375 0h.008v.015h-.008V9.75z" />
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
    id: "lite-multistore",
    label: "Multi-store",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 21h19.5m-18-18v18m10.5-18v18m6-13.5V21M6.75 6.75h.75m-.75 3h.75m-.75 3h.75m3-6h.75m-.75 3h.75m-.75 3h.75M6.75 21v-3.375c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21" />
      </svg>
    ),
  },
  {
    id: "lite-funnel",
    label: "Funnel & sessions",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 01-.659 1.591l-5.432 5.432a2.25 2.25 0 00-.659 1.591v2.927a2.25 2.25 0 01-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 00-.659-1.591L3.659 7.409A2.25 2.25 0 013 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0112 3z" />
      </svg>
    ),
  },
  {
    id: "lite-heatmaps",
    label: "Heatmaps",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
      </svg>
    ),
  },
  {
    id: "lite-nudges",
    label: "Nudges & Lift",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
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

// Per-floor section→nav resolution. The same anchor id (e.g. "signals",
// "overview") can appear on Pulse and Pro with DIFFERENT nav targets
// because the two floors have different sidebars. Keyed by floor
// because a flat dict can't fork. Resolution chain in `resolveNavId`
// below: floor map → fall back to bare section id.
const SECTION_TO_NAV_PULSE: Record<string, string> = {
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
  "lite-attribution": "lite-attribution",
  "lite-audience": "lite-audience",
  "lite-last7": "lite-last7",
  "lite-multistore": "lite-multistore",
  "lite-peers": "lite-peers",
  "lite-pnl": "lite-pnl",
  "lite-rars": "lite-rars",
  "lite-refunds": "lite-refunds",
  "lite-retention": "lite-retention",
  "lite-signals": "lite-signals",
  "lite-today": "lite-today",
};

const SECTION_TO_NAV_PRO: Record<string, string> = {
  // Pro tier ships only $60-130 features that Lite ($0-60) does not
  // have. 5 distinct nav slots — see NAV_ITEMS_PRO above. All other
  // sections (overview / revenue / signals / funnel / nudges / scroll
  // / pro-intelligence sub-sections / abandoned / targets / autopsy /
  // visitor-intent / what-next / kpi-goals / bi-sql) are doppione of
  // Lite tier and DISABLED on Pro per founder no-doppione doctrine
  // (`feedback_no_doppione_strict_tier_match.md`).
  "pro-daily-intel": "pro-daily-intel",
  "pro-mta": "pro-mta",
  "pro-night-shift": "pro-night-shift",
  "pro-price": "pro-price",
  "pro-goals": "pro-goals",
  "pro-bi-sql": "pro-bi-sql",
  "pro-subscriptions": "pro-subscriptions",
  "pro-trust": "pro-trust",
  "pro-ask": "pro-ask",
  "pro-changes": "pro-changes",
  "pro-proof": "pro-proof",
  "pro-intelligence": "pro-intelligence",
  // Sub-anchors of ProIntelligenceSection roll up to "Forecast &
  // intelligence" so scroll-spy stays lit through the inner blocks.
  "price-intelligence": "pro-intelligence",
  "market-intelligence": "pro-intelligence",
  "behavioral-intelligence": "pro-intelligence",
};

const SECTION_TO_NAV_LITE: Record<string, string> = {
  // Lite-floor anchor ids → NAV_ITEMS_LITE nav ids. The observer
  // watches `section-lite-*` IDs; stripping the section- prefix
  // yields the nav id match. Every section rendered on /app/lite
  // MUST appear here (and in NAV_ITEMS_LITE) — otherwise scrolling
  // into that section produces no active highlight and the sidebar
  // visually "loses" the merchant's position. Enforced by
  // `audit_lite_nav_section_parity.py` at preflight.
  "lite-rars": "lite-rars",
  "lite-today": "lite-today",
  "lite-last7": "lite-last7",
  "lite-peers": "lite-peers",
  "lite-pnl": "lite-pnl",
  "lite-attribution": "lite-attribution",
  "lite-retention": "lite-retention",
  "lite-refunds": "lite-refunds",
  "lite-audience": "lite-audience",
  "lite-signals": "lite-signals",
  "lite-multistore": "lite-multistore",
  "lite-funnel": "lite-funnel",
  "lite-heatmaps": "lite-heatmaps",
  "lite-nudges": "lite-nudges",
  // sessions is a sub-anchor of the funnel section.
  sessions: "lite-funnel",
  live: "live",
};

export function resolveNavId(
  activeSection: string,
  currentFloor: Floor,
  isLiteView: boolean,
): string {
  if (currentFloor === "intelligence") {
    return SECTION_TO_NAV_PRO[activeSection] || activeSection;
  }
  if (currentFloor === "pulse" && isLiteView) {
    return SECTION_TO_NAV_LITE[activeSection] || activeSection;
  }
  return SECTION_TO_NAV_PULSE[activeSection] || activeSection;
}

// Back-compat re-export for any caller that still imports the legacy
// flat map (e.g. preflight audits). Mirrors the Pulse-floor mapping
// since that's what the legacy callers consumed.
const SECTION_TO_NAV = SECTION_TO_NAV_PULSE;

// ─── Pro-floor NAV items ────────────────────────────────────────
//
// Founder directive 2026-04-29: when the merchant clicks the Pro
// floor in the FloorSelector, the section nav must show titles
// (mirroring NAV_ITEMS_LITE on Lite floor). Pre-this-commit the
// Pro floor rendered no section nav (currentFloor !== "pulse"
// short-circuited the render).
//
// Each id maps to a `section-*` anchor on the Pro vertical of
// /app/page.tsx. Placeholder entries (pro-goals, pro-bi-sql,
// pro-subscriptions) point to `section-pro-coming-soon` until the
// $60-130 parity-gap features ship.
// Order = real DOM scroll order on /app/pro. Audit script
// (audit_pro_nav_section_parity.py) enforces nav-id ↔ section-anchor
// parity at preflight — every entry without href maps to a
// `<section id="section-{id}">` anchor on the Pro vertical.
//
// 2026-04-30: 7 prior `scaleOnly` cross-floor entries (Anomaly Replay,
// Causal Why, Counterfactual, Competitor Playbook, Night Shift, Revenue
// Autopsy, MTA) reclassified per founder $60-130 competitor-parity
// rule:
//   KEEP-in-Pro  (4): competitors $60-130 ship the feature, so it
//                     lives fully on Pro (no badge, real anchor):
//                     Anomaly Replay (Triple Whale Lighthouse $129),
//                     Night Shift (Glew/Lifetimely $79+ alerts),
//                     Revenue Autopsy (Lifetimely/Glew/Polar $79+),
//                     MTA (Glew $79, Triple Whale $129+).
//   MIGRATE-Scale (3): only $1k+ competitors ship → removed from
//                     NAV_ITEMS_PRO entirely, lives only on Scale:
//                     Causal Why, Counterfactual, Competitor Playbook
//                     (Northbeam $1k+ territory).
const NAV_ITEMS_PRO: NavItem[] = [
  {
    id: "pro-daily-intel",
    label: "Daily intelligence",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
      </svg>
    ),
  },
  {
    id: "pro-mta",
    label: "Multi-touch attribution",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
  {
    id: "pro-night-shift",
    label: "Night Shift",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z" />
      </svg>
    ),
  },
  {
    id: "pro-price",
    label: "Price sensitivity",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.568 3H5.25A2.25 2.25 0 003 5.25v4.318c0 .597.237 1.17.659 1.591l9.581 9.581c.699.699 1.78.872 2.607.33a18.095 18.095 0 005.223-5.223c.542-.827.369-1.908-.33-2.607L11.16 3.66A2.25 2.25 0 009.568 3z" />
      </svg>
    ),
  },
  {
    id: "pro-goals",
    label: "KPI goals",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15.59 14.37a6 6 0 01-5.84 7.38v-4.8m5.84-2.58a14.98 14.98 0 006.16-12.12A14.98 14.98 0 009.631 8.41m5.96 5.96a14.926 14.926 0 01-5.841 2.58m-.119-8.54a6 6 0 00-7.381 5.84h4.8m2.581-5.84a14.927 14.927 0 00-2.58 5.84m2.699 2.7c-.103.021-.207.041-.311.06a15.09 15.09 0 01-2.448-2.448 14.9 14.9 0 01.06-.312m-2.24 2.39a4.493 4.493 0 00-1.757 4.306 4.493 4.493 0 004.306-1.758M16.5 9a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0z" />
      </svg>
    ),
    pro: true,
  },
  {
    id: "pro-bi-sql",
    label: "BI / SQL access",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 0v3.75m-16.5-3.75v3.75m16.5 0v3.75C20.25 16.153 16.556 18 12 18s-8.25-1.847-8.25-4.125v-3.75m16.5 0c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125" />
      </svg>
    ),
    pro: true,
  },
  {
    id: "pro-subscriptions",
    label: "Subscription analytics",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
      </svg>
    ),
    pro: true,
  },
  {
    id: "pro-trust",
    label: "Trust controls",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    id: "pro-ask",
    label: "Ask HedgeSpark",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H8.25m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H12m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0h-.375m-13.5 3.01c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.184-4.183a1.14 1.14 0 01.778-.332 48.294 48.294 0 005.83-.498c1.585-.233 2.708-1.626 2.708-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
      </svg>
    ),
  },
  {
    id: "pro-changes",
    label: "Change tracking",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0115.75 21H5.25A2.25 2.25 0 013 18.75V8.25A2.25 2.25 0 015.25 6H10" />
      </svg>
    ),
  },
  {
    id: "pro-proof",
    label: "Proof of value",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    id: "pro-intelligence",
    label: "Forecast & intelligence",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3v11.25A2.25 2.25 0 006 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0118 16.5h-2.25m-7.5 0h7.5m-7.5 0l-1 3m8.5-3l1 3m0 0l.5 1.5m-.5-1.5h-9.5m0 0l-.5 1.5M9 11.25v1.5M12 9v3.75m3-6v6" />
      </svg>
    ),
  },
];

export {
  NAV_ITEMS,
  NAV_ITEMS_LITE,
  NAV_ITEMS_PRO,
  SECTION_TO_NAV,
  SECTION_TO_NAV_PULSE,
  SECTION_TO_NAV_PRO,
  SECTION_TO_NAV_LITE,
};

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
  tier?: "lite" | "pro" | "scale";
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
  // Callback ref so the same identifier works for both <button> and
  // <Link> (rendered as <a>) — Link's typed ref is HTMLAnchorElement
  // and won't accept a useRef<HTMLButtonElement>. The ref only needs
  // to call scrollIntoView, which is defined on Element.
  const activeNodeRef = useRef<Element | null>(null);
  const activeRef = useCallback((node: Element | null) => {
    activeNodeRef.current = node;
  }, []);

  useEffect(() => {
    activeNodeRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeSection]);

  const activeNavId = resolveNavId(activeSection, currentFloor, isLiteView);

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
      {currentFloor !== "settings" && currentFloor !== "reports" && (
      <div className="flex flex-col gap-1 border-b border-white/[0.04] px-2 py-3">
        {FLOORS.map((floor) => {
          const isActive = currentFloor === floor.id;
          const accessible = isFloorAccessible(floor, tier);
          return (
            <Link
              key={floor.id}
              href={floor.href}
              title={collapsed ? `${floor.label} — ${floor.desc}` : floor.desc}
              aria-current={isActive ? "page" : undefined}
              className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-[13px] font-bold uppercase tracking-[0.08em] transition-all duration-150 ${
                isActive
                  ? "bg-[#e8a04e]/15 text-[#e8a04e] shadow-[inset_0_0_0_1px_rgba(232,160,78,0.22)]"
                  : accessible
                  ? "text-slate-300 hover:bg-white/[0.05] hover:text-white"
                  : "text-slate-400 hover:bg-white/[0.03] hover:text-slate-200"
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
          Lite uses NAV_ITEMS_LITE (RARS, Peers, P&L, Attribution,
          Retention, Features, Radar). Pulse floor (= isLiteView
          true) uses NAV_ITEMS_LITE; Pulse floor (default /app) uses
          legacy NAV_ITEMS. Pro floor (currentFloor === "pro") uses
          NAV_ITEMS_PRO — added 2026-04-29 per founder directive
          ("quando clicco PRO mancano i titoli"). Settings/reports/
          scale floors render no section nav (different patterns). */}
      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto py-4">
        {(() => {
          // Floor → nav-list mapping. Floor IDs are content-driven
          // (pulse/intelligence/operations) but the FloorSelector
          // labels them by tier (Lite/Pro/Scale). The Pro floor
          // (id="intelligence") gets NAV_ITEMS_PRO (added 2026-04-29
          // per founder directive: section titles missing on /app/pro).
          const navList: NavItem[] | null =
            currentFloor === "pulse"
              ? (isLiteView ? NAV_ITEMS_LITE : NAV_ITEMS)
              : currentFloor === "intelligence"
                ? NAV_ITEMS_PRO
                : null;
          if (navList === null) return null;
          return navList.map((item) => {
          const isActive = activeNavId === item.id;
          const isLocked = item.pro && tier === "lite";
          const className = `mx-2 flex items-center gap-3 rounded-xl px-3 py-3 text-[15px] font-medium transition-all duration-150 ${
            isActive
              ? "bg-[#d4893a]/15 text-[#e8a04e] shadow-[inset_0_0_0_1px_rgba(212,137,58,0.18)]"
              : isLocked
              ? "text-slate-400 hover:bg-white/[0.03] hover:text-slate-200"
              : "text-slate-400 hover:bg-white/[0.05] hover:text-slate-200"
          } ${collapsed ? "justify-center" : ""}`;
          const inner = (
            <>
              <span className="flex-shrink-0">{item.icon}</span>
              {!collapsed && (
                <span className="flex min-w-0 flex-1 items-center gap-2 truncate">
                  {item.label}
                  {isLocked && (
                    <span className="rounded border border-[#d4893a]/30 bg-[#d4893a]/10 px-1.5 py-px text-[9px] font-bold uppercase tracking-[0.08em] text-[#e8a04e]">
                      Pro
                    </span>
                  )}
                </span>
              )}
              {isActive && !collapsed && (
                <span className="ml-auto h-5 w-[3px] flex-shrink-0 rounded-full bg-[#d4893a]" />
              )}
            </>
          );
          if (item.href) {
            // Cross-floor nav — Link to the URL. Keeps the merchant
            // grounded ("Scale" badge says the click leaves Pro);
            // the destination floor renders the Scale-floor preview
            // for non-Scale tiers (locked moats with copy + lock).
            return (
              <Link
                key={item.id}
                ref={isActive ? activeRef : undefined}
                href={item.href}
                title={collapsed ? item.label : undefined}
                className={className}
              >
                {inner}
              </Link>
            );
          }
          return (
            <button
              key={item.id}
              ref={isActive ? activeRef : undefined}
              onClick={() => onNavigate(item.id)}
              title={collapsed ? item.label : undefined}
              className={className}
            >
              {inner}
            </button>
          );
          });
        })()}
      </nav>

      {/* Collapse toggle */}
      <div className="border-t border-white/[0.06] p-2">
        <button
          onClick={onToggle}
          className="flex w-full items-center justify-center rounded-lg p-2 text-slate-400 transition-colors hover:bg-white/[0.04] hover:text-slate-200"
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
