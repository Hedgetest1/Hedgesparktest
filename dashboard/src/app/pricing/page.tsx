"use client";

/**
 * /pricing — beta-phase pricing page.
 *
 * Per the beta launch master plan (§4.2 + Stage 4 Step 8), explicit
 * prices and "free trial" CTAs are HIDDEN during the beta. The page
 * still exists and still lists what Lite vs Pro includes, but:
 *
 *  - no € / $ numbers anywhere
 *  - CTAs are pricing-neutral ("Install on Shopify" / dashboard link)
 *  - the feature comparison table remains so prospects can evaluate the
 *    product, not haggle over a number before they've seen it work
 *  - a short honest note explains that pricing lands after the beta
 *
 * When pricing is finalized post-beta, swap the hidden-price blocks for
 * real numbers and reintroduce the upgrade CTA — the structure is
 * preserved on purpose to keep the swap small.
 */

import Link from "next/link";
import { useSession } from "@/app/lib/useSession";

// ---------------------------------------------------------------------------
// Feature data
// ---------------------------------------------------------------------------
const LITE_FEATURES: string[] = [
  "Live visitor pulse",
  "Daily brief headline",
  "Weekly trends",
  "Top 3 products",
  "Basic signals",
];

const PRO_FEATURES: string[] = [
  "Everything in Lite",
  "Revenue at Risk Score (5 dimensions)",
  "Revenue Autopsy per product",
  "Abandoned intent detection",
  "Automated nudges + holdout proof",
  "Causal lift measurement (real A/B)",
  "Cohort & LTV analysis",
  "P&L intelligence per product",
  "Scroll heatmaps per product",
  "Price sensitivity detection",
  "Peer benchmarks (anonymous)",
  "Revenue Genome (source/segment DNA)",
  "Goals, ROI tracking, risk forecast",
  "Team collaboration + webhooks",
  "Weekly email digest",
];

type ComparisonRow = {
  label: string;
  lite: string | false;
  pro: string;
};

const COMPARISON: ComparisonRow[] = [
  { label: "Daily brief",             lite: "Headline only",  pro: "Full breakdown"        },
  { label: "Product performance",     lite: "Top 3 products", pro: "Full catalog"          },
  { label: "Revenue at Risk Score",   lite: "Estimate only",  pro: "5-dimension breakdown" },
  { label: "Revenue Autopsy",         lite: false,            pro: "Per product"           },
  { label: "Abandoned intent",        lite: false,            pro: "Scored visitors"       },
  { label: "Automated nudges",        lite: false,            pro: "Deploy + measure"      },
  { label: "Holdout proof (A/B)",     lite: false,            pro: "Causal lift"           },
  { label: "Cohort & LTV analysis",   lite: false,            pro: "Multi-dimensional"     },
  { label: "Scroll heatmaps",         lite: false,            pro: "Per product"           },
  { label: "Price sensitivity",       lite: false,            pro: "Elasticity detection"  },
  { label: "Peer benchmarks",         lite: false,            pro: "Anonymous comparison"  },
  { label: "P&L intelligence",        lite: false,            pro: "Per product/channel"   },
  { label: "Revenue Genome",          lite: false,            pro: "Source/segment DNA"    },
  { label: "Goals & ROI tracking",    lite: false,            pro: "Set + measure"         },
  { label: "Risk forecast",           lite: false,            pro: "Predictive"            },
  { label: "Team + webhooks",         lite: false,            pro: "Collaborate + integrate"},
  { label: "Refund loss tracking",    lite: false,            pro: "Trend analysis"        },
  { label: "Weekly email digest",     lite: false,            pro: "Included"              },
];

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------
function CheckIcon({ dim }: { dim?: boolean }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={2.5}
      stroke="currentColor"
      aria-hidden="true"
      className={`h-3.5 w-3.5 flex-shrink-0 ${dim ? "text-slate-600" : "text-[#e8a04e]"}`}
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={1.8}
      stroke="currentColor"
      aria-hidden="true"
      className="h-3 w-3 flex-shrink-0 text-slate-700"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25-2.25v6.75a2.25 2.25 0 002.25 2.25z"
      />
    </svg>
  );
}

function ArrowLeftIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={1.5}
      stroke="currentColor"
      aria-hidden="true"
      className="h-4 w-4"
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function PricingPage() {
  // Session-identity fetch centralized in useSession (2026-04-23 retro
  // DA migration). Previously this page had an inline apiClient.GET
  // call that bypassed the shared fallback chain. useSession returns
  // `tier: "lite" | "pro"` derived from the same plan + billing_active
  // logic, plus `resolved` so we can distinguish "still loading" from
  // "resolved to lite".
  const session = useSession();
  const tier: "lite" | "pro" | "scale" | null = session.resolved ? session.tier : null;

  const dashboardHref = "/app";
  const isProUser = tier === "pro";
  const primaryCta = isProUser ? "Back to your dashboard" : "Install on Shopify";
  const primaryHref = isProUser ? dashboardHref : "/install";

  return (
    <div className="min-h-screen bg-[#080811] text-white">

      {/* ── Top nav ── */}
      <div className="sticky top-0 z-10 border-b border-white/[0.06] bg-[#080811]/90 backdrop-blur-sm">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <Link
            href={dashboardHref}
            className="flex items-center gap-2 text-[13px] text-slate-300 transition-colors hover:text-white"
          >
            <ArrowLeftIcon />
            Back to dashboard
          </Link>
          <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#c4b5fd]">
            HedgeSpark
          </span>
        </div>
      </div>

      <div className="mx-auto max-w-4xl px-6 py-12">

        {/* ── A. Hero ── */}
        <div className="hs-fade-up mb-10 text-center">
          <div className="mb-3 text-[11px] font-bold uppercase tracking-[0.18em] text-[#e8a04e]">
            Plans
          </div>
          <h1 className="mb-4 text-[36px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
            Two plans. One product that actually works.
          </h1>
          <p className="mx-auto max-w-xl text-[15px] leading-relaxed text-slate-400">
            Lite tracks what&apos;s happening on your store. Pro tells you exactly what to fix, proves
            every result against a control group, and stops leaks while you sleep.
          </p>
        </div>

        {/* ── B. Plan cards — pricing hidden during beta ── */}
        <div className="mb-10 grid gap-4 sm:grid-cols-2">

          {/* Lite */}
          <div className="relative rounded-2xl border border-white/[0.08] bg-white/[0.02] p-6">
            {tier === "lite" && (
              <span className="absolute -top-3 left-5 rounded-full border border-white/10 bg-[#080811] px-3 py-0.5 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-400">
                Current plan
              </span>
            )}

            <div className="mb-5">
              <div className="mb-1 text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">
                Lite
              </div>
              <div className="text-[20px] font-semibold text-slate-300">
                Operational clarity
              </div>
              <p className="mt-2 text-[12px] leading-relaxed text-slate-400">
                Best for tracking what&apos;s happening on your store.
              </p>
            </div>

            <ul className="space-y-2.5">
              {LITE_FEATURES.map((f) => (
                <li key={f} className="flex items-center gap-2.5">
                  <CheckIcon dim />
                  <span className="text-[13px] text-slate-400">{f}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* Pro */}
          <div className="relative rounded-2xl border border-[#e8a04e]/30 bg-gradient-to-br from-[#e8a04e]/[0.06] to-transparent p-6 shadow-[0_0_48px_rgba(232,160,78,0.08)]">
            <span
              className={`absolute -top-3 left-5 rounded-full border px-3 py-0.5 text-[10px] font-bold uppercase tracking-[0.12em] ${
                tier === "pro"
                  ? "border-white/10 bg-[#080811] text-slate-400"
                  : "border-[#e8a04e]/30 bg-[#e8a04e]/20 text-[#e8a04e]"
              }`}
            >
              {tier === "pro" ? "Current plan" : "Recommended"}
            </span>

            <div className="mb-5">
              <div className="mb-1 text-[11px] font-bold uppercase tracking-[0.14em] text-[#e8a04e]">
                Pro
              </div>
              <div className="text-[20px] font-semibold text-white">
                Structural intelligence
              </div>
              <p className="mt-2 text-[12px] leading-relaxed text-slate-400">
                Best for knowing exactly what to fix — and proving every fix worked.
              </p>
            </div>

            <ul className="space-y-2.5">
              {PRO_FEATURES.map((f) => (
                <li key={f} className="flex items-center gap-2.5">
                  <CheckIcon />
                  <span
                    className={`text-[13px] ${
                      f === "Everything in Lite" ? "text-slate-400" : "text-slate-200"
                    }`}
                  >
                    {f}
                  </span>
                </li>
              ))}
            </ul>

            {!isProUser && (
              <Link
                href="/install"
                className="mt-6 block w-full rounded-xl bg-gradient-to-br from-[#e8a04e] to-[#d4893a] py-3 text-center text-sm font-bold text-[#0b1220] shadow-[0_0_16px_rgba(232,160,78,0.35)] transition-colors hover:from-[#f2ab5a] hover:to-[#e8a04e] focus:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#080811]"
              >
                Install on Shopify
              </Link>
            )}

            {isProUser && (
              <div className="mt-6 flex items-center justify-center gap-2 rounded-xl border border-emerald-400/20 bg-emerald-500/5 py-2.5">
                <CheckIcon />
                <span className="text-[13px] font-semibold text-emerald-300">Active</span>
              </div>
            )}
          </div>

        </div>

        {/* ── Beta notice — honest framing of where we are right now ── */}
        {!isProUser && (
          <div className="mb-10 rounded-2xl border border-[#e8a04e]/20 bg-[#e8a04e]/[0.04] px-6 py-5 text-center">
            <div className="mb-1 text-[11px] font-bold uppercase tracking-[0.14em] text-[#e8a04e]">
              During the beta
            </div>
            <p className="text-[14px] leading-relaxed text-slate-300">
              HedgeSpark is in a closed beta. Install on Shopify to get access — we&apos;ll announce
              final pricing before general launch, and beta merchants get grandfathered in.
            </p>
          </div>
        )}

        {/* ── C. Comparison table ── */}
        <div className="mb-10">
          <div className="mb-4 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
            Feature comparison
          </div>

          <div className="overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.02]">
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-[13px]">
                <thead>
                  <tr className="border-b border-white/[0.06] text-[11px] uppercase tracking-wide text-slate-400">
                    <th className="w-[46%] px-5 py-3 font-semibold">Feature</th>
                    <th className="px-5 py-3 font-semibold">Lite</th>
                    <th className="px-5 py-3 font-semibold text-[#e8a04e]">Pro</th>
                  </tr>
                </thead>
                <tbody>
                  {COMPARISON.map((row, i) => (
                    <tr
                      key={row.label}
                      className={`border-t border-white/[0.04] ${i % 2 !== 0 ? "bg-white/[0.01]" : ""}`}
                    >
                      <td className="px-5 py-3 text-slate-400">{row.label}</td>

                      <td className="px-5 py-3">
                        {row.lite === false ? (
                          <div className="flex items-center gap-2">
                            <LockIcon />
                            <span className="text-[12px] text-slate-700">—</span>
                          </div>
                        ) : (
                          <span className="text-[12px] text-slate-400">{row.lite}</span>
                        )}
                      </td>

                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2">
                          <CheckIcon />
                          <span className="text-[12px] text-slate-200">{row.pro}</span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* ── D. CTA footer ── */}
        <div className="flex flex-col items-center gap-3 pb-16 text-center">
          {isProUser ? (
            <Link
              href={dashboardHref}
              className="rounded-xl border border-emerald-400/20 bg-emerald-500/5 px-8 py-3 text-sm font-semibold text-emerald-300 transition-colors hover:border-emerald-400/30 hover:bg-emerald-500/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300"
            >
              {primaryCta} →
            </Link>
          ) : (
            <>
              <Link
                href={primaryHref}
                className="rounded-xl bg-gradient-to-br from-[#e8a04e] to-[#d4893a] px-8 py-3 text-sm font-bold text-[#0b1220] shadow-[0_0_20px_rgba(232,160,78,0.4)] transition-colors hover:from-[#f2ab5a] hover:to-[#e8a04e] focus:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#080811]"
              >
                {primaryCta}
              </Link>
              <Link
                href={dashboardHref}
                className="text-[12px] text-slate-400 transition-colors hover:text-slate-300"
              >
                Keep exploring Lite
              </Link>
            </>
          )}
        </div>

      </div>
    </div>
  );
}
