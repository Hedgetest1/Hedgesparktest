"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

// ---------------------------------------------------------------------------
// Environment
// ---------------------------------------------------------------------------
const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "";
const UPGRADE_URL = process.env.NEXT_PUBLIC_UPGRADE_URL || null;

function apiHeaders(): HeadersInit {
  return { "Content-Type": "application/json" };
}

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
  "Full product insights",
  "Revenue loss per product",
  "Full AI suggestions per signal",
  "Full daily brief breakdown",
  "Complete product list",
  "Price intelligence",
  "Market intelligence",
];

type ComparisonRow = {
  label: string;
  lite: string | false;
  pro: string;
};

const COMPARISON: ComparisonRow[] = [
  { label: "Daily brief",         lite: "Headline only",  pro: "Full breakdown"  },
  { label: "Product performance", lite: "Top 3 products", pro: "Full list"        },
  { label: "Revenue loss",        lite: false,            pro: "Per product"     },
  { label: "AI actions",          lite: false,            pro: "Included"        },
  { label: "Full product list",   lite: false,            pro: "Included"        },
  { label: "Price intelligence",  lite: false,            pro: "Included"        },
  { label: "Market intelligence", lite: false,            pro: "Included"        },
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
      className={`h-3.5 w-3.5 flex-shrink-0 ${dim ? "text-slate-600" : "text-violet-400"}`}
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
  const [shop, setShop] = useState("");
  const [tier, setTier] = useState<"lite" | "pro" | null>(null);
  const [trialDays, setTrialDays] = useState(14);
  const [price, setPrice] = useState(49);
  const [upgradeLoading, setUpgradeLoading] = useState(false);

  useEffect(() => {
    if (!API_BASE) { setTier("lite"); return; }

    // Resolve shop from session cookie — same pattern as main page
    fetch(`${API_BASE}/merchant/me`, {
      headers: apiHeaders(),
      credentials: "include",
      cache: "no-store",
    })
      .then(async (res) => {
        if (res.ok) {
          const json = await res.json();
          if (json.shop_domain) setShop(json.shop_domain);
          setTier(json.plan === "pro" && json.billing_active === true ? "pro" : "lite");
          if (json.pro_trial_days != null) setTrialDays(json.pro_trial_days);
          if (json.pro_price != null) setPrice(json.pro_price);
          return;
        }
        // No session — try ?shop= from URL as fallback
        const s = new URLSearchParams(window.location.search).get("shop") || "";
        if (s) setShop(s);
        setTier("lite");
      })
      .catch(() => {
        const s = new URLSearchParams(window.location.search).get("shop") || "";
        if (s) setShop(s);
        setTier("lite");
      });
  }, []);

  const dashboardHref = "/app";
  const isProUser = tier === "pro";
  const hasTrial = trialDays > 0;
  const priceStr = price % 1 === 0 ? `$${price}` : `$${price.toFixed(2)}`;

  async function handleUpgrade() {
    // Use real Shopify billing flow when shop + API are available.
    if (shop && API_BASE) {
      setUpgradeLoading(true);
      try {
        const res = await fetch(
          `${API_BASE}/billing/subscribe?shop=${encodeURIComponent(shop)}`,
          { method: "POST", headers: apiHeaders(), credentials: "include" }
        );
        const json = await res.json();
        if (res.ok && json.confirmation_url) {
          window.location.href = json.confirmation_url;
          return;
        }
      } catch { /* fall through to UPGRADE_URL */ }
      setUpgradeLoading(false);
    }
    // Fallback: external upgrade URL
    if (UPGRADE_URL) {
      window.open(UPGRADE_URL, "_blank", "noopener,noreferrer");
    }
  }

  return (
    <div className="min-h-screen bg-[#080811] text-white">

      {/* ── Top nav ── */}
      <div className="sticky top-0 z-10 border-b border-white/[0.06] bg-[#080811]/90 backdrop-blur-sm">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <Link
            href={dashboardHref}
            className="flex items-center gap-2 text-[13px] text-slate-500 transition-colors hover:text-slate-300"
          >
            <ArrowLeftIcon />
            Back to dashboard
          </Link>
          <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/50">
            Hedge Spark
          </span>
        </div>
      </div>

      <div className="mx-auto max-w-4xl px-6 py-12">

        {/* ── A. Hero ── */}
        <div className="hs-fade-up mb-10 text-center">
          <div className="mb-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/70">
            Plans &amp; Pricing
          </div>
          <h1 className="mb-3 text-[28px] font-semibold leading-tight text-white">
            Unlock full store intelligence
          </h1>
          <p className="mx-auto max-w-md text-[15px] leading-relaxed text-slate-400">
            See exactly what to fix, what revenue is at risk, and where to act first.
          </p>
          <p className="mt-3 text-[13px] text-slate-600">
            🦔 Hedge Spark is already tracking your store — Pro helps you act on what it finds.
          </p>
        </div>

        {/* ── B. Pricing cards ── */}
        <div className="mb-10 grid gap-4 sm:grid-cols-2">

          {/* Lite */}
          <div className="relative rounded-2xl border border-white/[0.08] bg-white/[0.02] p-6">
            {tier === "lite" && (
              <span className="absolute -top-3 left-5 rounded-full border border-white/10 bg-[#080811] px-3 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                Current plan
              </span>
            )}

            <div className="mb-5">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                Lite
              </div>
              <div className="flex items-baseline gap-1">
                <span className="text-3xl font-semibold tabular-nums text-white">€12</span>
                <span className="text-[13px] text-slate-500">/mo</span>
              </div>
              <p className="mt-2 text-[12px] leading-5 text-slate-500">
                Best for tracking what&apos;s happening
              </p>
            </div>

            <ul className="space-y-2.5">
              {LITE_FEATURES.map((f) => (
                <li key={f} className="flex items-center gap-2.5">
                  <CheckIcon dim />
                  <span className="text-[13px] text-slate-500">{f}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* Pro */}
          <div className="relative rounded-2xl border border-violet-400/30 bg-gradient-to-br from-violet-500/[0.08] to-transparent p-6 shadow-[0_0_48px_rgba(124,58,237,0.10)]">
            <span
              className={`absolute -top-3 left-5 rounded-full border px-3 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] ${
                tier === "pro"
                  ? "border-white/10 bg-[#080811] text-slate-500"
                  : "border-violet-400/30 bg-violet-500/20 text-violet-300"
              }`}
            >
              {tier === "pro" ? "Current plan" : "Recommended"}
            </span>

            <div className="mb-5">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-violet-300/80">
                Pro
              </div>
              <div className="flex items-baseline gap-1">
                <span className="text-3xl font-semibold tabular-nums text-white">{priceStr}</span>
                <span className="text-[13px] text-slate-500">/mo</span>
              </div>
              {hasTrial && (
                <p className="mt-1.5 text-[12px] font-medium text-violet-300/80">
                  {trialDays}-day free trial included
                </p>
              )}
              <p className="mt-1 text-[12px] leading-5 text-slate-400">
                Best for knowing what to fix
              </p>
            </div>

            <ul className="space-y-2.5">
              {PRO_FEATURES.map((f) => (
                <li key={f} className="flex items-center gap-2.5">
                  <CheckIcon />
                  <span className={`text-[13px] ${f === "Everything in Lite" ? "text-slate-500" : "text-slate-200"}`}>
                    {f}
                  </span>
                </li>
              ))}
            </ul>

            {!isProUser && (
              <button
                onClick={handleUpgrade}
                disabled={upgradeLoading}
                className="mt-6 w-full rounded-xl bg-violet-600 py-2.5 text-sm font-semibold text-white shadow-[0_0_16px_rgba(124,58,237,0.35)] transition-colors hover:bg-violet-500 active:bg-violet-700 disabled:opacity-60"
              >
                {upgradeLoading
                  ? "Opening Shopify billing…"
                  : hasTrial
                  ? `Start ${trialDays}-day free trial`
                  : `Upgrade to Pro — ${priceStr}/mo`}
              </button>
            )}

            {isProUser && (
              <div className="mt-6 flex items-center justify-center gap-2 rounded-xl border border-emerald-400/20 bg-emerald-500/5 py-2.5">
                <CheckIcon />
                <span className="text-[13px] font-medium text-emerald-300">
                  Active
                </span>
              </div>
            )}
          </div>

        </div>

        {/* ── C. Comparison table ── */}
        <div className="mb-10">
          <div className="mb-4 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-600">
            Feature comparison
          </div>

          <div className="overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.02]">
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-[13px]">
                <thead>
                  <tr className="border-b border-white/[0.06] text-[11px] uppercase tracking-wide text-slate-600">
                    <th className="w-[46%] px-5 py-3 font-medium">Feature</th>
                    <th className="px-5 py-3 font-medium">Lite</th>
                    <th className="px-5 py-3 font-medium text-violet-300/80">Pro</th>
                  </tr>
                </thead>
                <tbody>
                  {COMPARISON.map((row, i) => (
                    <tr
                      key={row.label}
                      className={`border-t border-white/[0.04] ${i % 2 !== 0 ? "bg-white/[0.01]" : ""}`}
                    >
                      <td className="px-5 py-3 text-slate-400">{row.label}</td>

                      {/* Lite cell */}
                      <td className="px-5 py-3">
                        {row.lite === false ? (
                          <div className="flex items-center gap-2">
                            <LockIcon />
                            <span className="text-[12px] text-slate-700">—</span>
                          </div>
                        ) : (
                          <span className="text-[12px] text-slate-500">{row.lite}</span>
                        )}
                      </td>

                      {/* Pro cell */}
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

        {/* ── D. Urgency panel ── (only for Lite) */}
        {!isProUser && (
          <div className="mb-10 rounded-2xl border border-violet-400/15 bg-violet-500/[0.05] px-6 py-5">
            <p className="text-[14px] font-medium text-slate-300">
              You already have signals waiting in your dashboard.
            </p>
            <p className="mt-1.5 text-[13px] leading-relaxed text-slate-500">
              Upgrade to reveal the exact products and actions behind them.
            </p>
          </div>
        )}

        {/* ── E. CTA footer ── */}
        <div className="flex flex-col items-center gap-3 pb-16 text-center">
          {isProUser ? (
            <Link
              href={dashboardHref}
              className="rounded-xl border border-emerald-400/20 bg-emerald-500/5 px-8 py-3 text-sm font-semibold text-emerald-300 transition-colors hover:border-emerald-400/30 hover:bg-emerald-500/10"
            >
              Back to your dashboard →
            </Link>
          ) : (
            <>
              <button
                onClick={handleUpgrade}
                disabled={upgradeLoading}
                className="rounded-xl bg-violet-600 px-8 py-3 text-sm font-semibold text-white shadow-[0_0_20px_rgba(124,58,237,0.4)] transition-colors hover:bg-violet-500 active:bg-violet-700 disabled:opacity-60"
              >
                {upgradeLoading
                  ? "Opening Shopify billing…"
                  : hasTrial
                  ? `Start ${trialDays}-day free trial`
                  : `Upgrade to Pro — ${priceStr}/mo`}
              </button>
              {hasTrial && !upgradeLoading && (
                <p className="text-[11px] text-slate-600">
                  Then {priceStr}/mo. Cancel anytime from Shopify.
                </p>
              )}
              <Link
                href={dashboardHref}
                className="text-[12px] text-slate-600 transition-colors hover:text-slate-400"
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
