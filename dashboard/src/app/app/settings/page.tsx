"use client";

/**
 * /app/settings — Settings hub.
 *
 * Top-right gear in the TopBar routes here. Lists every settings
 * category available to the merchant with a one-click path into the
 * dedicated sub-page for each.
 *
 * Pattern established alongside /app/settings/costs (2026-04-19 sprint):
 * every settings category gets its own sub-page rather than living in a
 * bottom-of-dashboard accordion. This hub is the directory.
 *
 * 2026-04-20 status — sub-pages available:
 *   /app/settings/costs    — per-product COGS + Shopify sync
 *
 * Pending migrations (bottom of /app still renders these inline via
 * SettingsSection; each will get its own /app/settings/<area> sub-page
 * in a follow-up sprint):
 *   • Display currency  (USD ⇄ EUR toggle)
 *   • Cost defaults     (shop-wide COGS + shipping + payment fees)
 *   • Klaviyo           (connect / disconnect merchant's key)
 *   • Privacy / Art. 22 (automated-targeting opt-out)
 *   • Team              (future — multi-seat management)
 */

import Link from "next/link";
import { FloorLayout } from "../../components/FloorLayout";

type SettingsCard = {
  href: string;
  title: string;
  blurb: string;
  status: "live" | "inline";
  icon: React.ReactNode;
};

const SETTINGS: SettingsCard[] = [
  {
    href: "/app/settings/costs",
    title: "Product costs",
    blurb:
      "Per-product COGS and shipping — what each SKU costs you. Powers the P&L and every margin-aware recommendation.",
    status: "live",
    icon: (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.8}
        className="h-5 w-5"
        aria-hidden="true"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 18L9 11.25l4.306 4.306a11.95 11.95 0 015.814-5.519l2.74-1.22m0 0l-5.94-2.28m5.94 2.28l-2.28 5.941" />
      </svg>
    ),
  },
  {
    href: "/app/settings/currency",
    title: "Display currency",
    blurb:
      "USD ⇄ EUR toggle for every amount on the dashboard. Purely a presentation setting — data stays in shop native currency.",
    status: "live",
    icon: (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.8}
        className="h-5 w-5"
        aria-hidden="true"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m-3-2.818l.879.659c1.171.879 3.07.879 4.242 0 1.172-.879 1.172-2.303 0-3.182C13.536 12.219 12.768 12 12 12c-2.25 0-4.5-1.5-4.5-3.75S9.75 4.5 12 4.5c1.157 0 2.2.464 2.955 1.214m-6.96 9.072c.755.75 1.798 1.214 2.955 1.214" />
      </svg>
    ),
  },
  {
    href: "/app/settings/slack",
    title: "Slack integration",
    blurb:
      "Pipe the daily merchant summary — Revenue-at-Risk, top leaks, recoveries — to your team's Slack channel. One-click OAuth.",
    status: "live",
    icon: (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.8}
        className="h-5 w-5"
        aria-hidden="true"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
      </svg>
    ),
  },
  {
    href: "/app/settings/klaviyo",
    title: "Klaviyo integration",
    blurb:
      "Forward HedgeSpark intelligence events into your Klaviyo flows. Private API key, encrypted at rest.",
    status: "live",
    icon: (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.8}
        className="h-5 w-5"
        aria-hidden="true"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
      </svg>
    ),
  },
];

const INLINE_FALLBACKS: { title: string; blurb: string }[] = [
  {
    title: "Cost defaults",
    blurb: "Shop-wide COGS %, shipping per order, payment fees, monthly ad spend.",
  },
  {
    title: "Privacy (Art. 22)",
    blurb: "Opt-out of automated decision-making for your storefront data.",
  },
];

export default function SettingsHubPage() {
  return (
    <FloorLayout floor="intelligence">
      {() => <SettingsHub />}
    </FloorLayout>
  );
}

function SettingsHub() {
  return (
    <>
      <div className="mb-8">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] font-semibold text-slate-500">
          <Link href="/app" className="text-slate-400 hover:text-[#e8a04e]">
            ← Dashboard
          </Link>
          <span>/</span>
          <span className="text-slate-300">Settings</span>
        </div>
        <div className="text-[11px] font-bold uppercase tracking-[0.2em] text-[#e8a04e]">
          Settings
        </div>
        <h1 className="mt-3 text-[2rem] font-extrabold leading-[1.1] text-[#e8a04e] sm:text-[2.5rem]">
          Configure your store
        </h1>
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-slate-400">
          Each category below has its own page. Changes you make on a
          settings page apply immediately across every floor of the
          dashboard.
        </p>
      </div>

      {/* Live settings sub-pages */}
      <section aria-labelledby="settings-live-heading" className="mb-8">
        <h2 id="settings-live-heading" className="sr-only">
          Available settings
        </h2>
        <div className="grid gap-4 sm:grid-cols-2">
          {SETTINGS.map((s) => (
            <Link
              key={s.href}
              href={s.href}
              className="group rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5 transition-colors hover:border-[#e8a04e]/30 hover:bg-[#e8a04e]/[0.04]"
            >
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-400 group-hover:bg-[#e8a04e]/10 group-hover:text-[#e8a04e]">
                  {s.icon}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-[14px] font-bold text-white">{s.title}</h3>
                    <span className="rounded-full border border-emerald-400/20 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.08em] text-emerald-300">
                      Live
                    </span>
                  </div>
                  <p className="mt-1 text-[12px] leading-relaxed text-slate-400">
                    {s.blurb}
                  </p>
                </div>
                <div className="flex-shrink-0 self-center text-slate-500 transition-colors group-hover:text-[#e8a04e]">
                  →
                </div>
              </div>
            </Link>
          ))}
        </div>
      </section>

      {/* Still-inline settings — rendered inside the main /app page
          (Pro/Scale floors) via SettingsSection, awaiting their own
          sub-page migration in the next sprint. Lite does NOT render
          these inline — merchant reaches them via this hub once the
          sub-pages land. */}
      <section
        aria-labelledby="settings-inline-heading"
        className="mb-8 rounded-2xl border border-white/[0.04] bg-white/[0.01] p-5"
      >
        <h2
          id="settings-inline-heading"
          className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-slate-500"
        >
          Migration in progress
        </h2>
        <p className="mb-4 text-[12px] leading-relaxed text-slate-400">
          These settings are still rendered inline at the bottom of the
          main dashboard (Pro/Scale floors) while their dedicated
          sub-pages are built. Functionality is unchanged; only the
          location is moving.
        </p>
        <ul className="space-y-2">
          {INLINE_FALLBACKS.map((f) => (
            <li
              key={f.title}
              className="rounded-lg border border-white/[0.04] bg-white/[0.015] p-3"
            >
              <div className="text-[12.5px] font-semibold text-slate-200">
                {f.title}
              </div>
              <div className="mt-0.5 text-[11px] text-slate-500">{f.blurb}</div>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
