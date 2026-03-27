"use client";

import Image from "next/image";
import { useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

function apiHeaders(): HeadersInit {
  return { "Content-Type": "application/json" };
}

type ProFeature = { title: string; description: string; icon: string };

const PRO_FEATURES: ProFeature[] = [
  {
    title: "AI actions per signal",
    description:
      "Every signal comes with a plain-English action — no guessing, no dashboards to interpret.",
    icon: "⚡",
  },
  {
    title: "Daily AI brief in your inbox",
    description:
      "A ranked summary of your top product opportunities, written in merchant language, every morning.",
    icon: "📬",
  },
  {
    title: "Price & Market Intelligence",
    description:
      "See how your pricing compares to the market. Know which products are unique and where you face competition.",
    icon: "🎯",
  },
  {
    title: "Revenue loss per product",
    description:
      "See exactly how much revenue is at risk per product — with the specific action to recover it.",
    icon: "💰",
  },
  {
    title: "Conversion funnel & sessions",
    description:
      "Watch where buyers drop off and replay individual visitor journeys to understand behavior.",
    icon: "🔍",
  },
];

export function UpgradeModal({
  open,
  onClose,
  shop,
  trialDays = 14,
  price = 49,
}: {
  open:       boolean;
  onClose:    () => void;
  shop?:      string;
  trialDays?: number;
  price?:     number;
}) {
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState("");

  if (!open) return null;

  const hasTrial = trialDays > 0;
  const priceStr = price % 1 === 0 ? `$${price}` : `$${price.toFixed(2)}`;

  async function handleUpgrade() {
    if (shop && API_BASE) {
      setLoading(true);
      setError("");
      try {
        const res = await fetch(
          `${API_BASE}/billing/subscribe?shop=${encodeURIComponent(shop)}`,
          { method: "POST", headers: apiHeaders(), credentials: "include" }
        );
        const json = await res.json();
        if (!res.ok) {
          setError(json?.detail || "Could not start upgrade. Please try again.");
          setLoading(false);
          return;
        }
        const confirmationUrl: string = json.confirmation_url;
        if (confirmationUrl) {
          window.location.href = confirmationUrl;
          return;
        }
        setError("No billing URL returned. Please contact support.");
        setLoading(false);
      } catch {
        setError("Network error — please check your connection and retry.");
        setLoading(false);
      }
      return;
    }

    const q = shop ? `?shop=${encodeURIComponent(shop)}` : "";
    window.location.href = `/pricing${q}`;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      onClick={onClose}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" />

      {/* Modal card */}
      <div
        className="hs-fade-up relative z-10 w-full max-w-md overflow-hidden rounded-3xl border border-violet-400/20 bg-[#0d0d1e] shadow-[0_32px_80px_rgba(124,58,237,0.22)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Gradient header */}
        <div className="relative overflow-hidden bg-gradient-to-br from-violet-600/20 via-violet-500/10 to-transparent px-8 pb-2 pt-8">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_30%_20%,rgba(124,58,237,0.15),transparent_50%)]" />

          {/* Close button */}
          <button
            onClick={onClose}
            className="absolute right-5 top-5 rounded-lg p-1 text-slate-500 transition-colors hover:text-slate-300"
            aria-label="Close"
          >
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-5 w-5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>

          {/* Mascot */}
          <div className="mb-4 flex justify-center">
            <div className="relative">
              <Image
                src="/branding/hedgespark-mascot.png"
                alt="Hedge Spark"
                width={80}
                height={80}
                className="hs-bob"
                priority
              />
              <span className="hs-sparkle absolute -right-2 -top-2 text-lg leading-none text-amber-300">
                ✦
              </span>
            </div>
          </div>

          {/* Heading */}
          <div className="relative text-center">
            <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/80">
              Hedge Spark Pro
            </div>
            <h2 className="text-xl font-semibold leading-snug text-white">
              Stop reading data.<br />Start making decisions.
            </h2>
          </div>
        </div>

        <div className="px-8 pb-8 pt-4">
          <p className="mb-5 text-center text-[13px] leading-6 text-slate-400">
            Lite shows you what your visitors are doing. Pro tells you exactly
            what to do about it — with AI actions, daily briefs, and competitive
            intelligence per product.
          </p>

          {/* Feature list */}
          <ul className="mb-6 space-y-3">
            {PRO_FEATURES.map((feature) => (
              <li key={feature.title} className="flex items-start gap-3 rounded-lg px-2 py-1.5 transition-colors hover:bg-white/[0.02]">
                <span className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md bg-violet-500/15 text-[12px]">
                  {feature.icon}
                </span>
                <div>
                  <div className="text-[13px] font-medium text-slate-200">
                    {feature.title}
                  </div>
                  <div className="mt-0.5 text-[11px] leading-[1.5] text-slate-500">
                    {feature.description}
                  </div>
                </div>
              </li>
            ))}
          </ul>

          {/* Price anchor */}
          <div className="mb-4 flex items-baseline justify-center gap-1.5">
            <span className="text-2xl font-bold tabular-nums text-white">{priceStr}</span>
            <span className="text-[13px] text-slate-500">/mo</span>
            {hasTrial && (
              <span className="ml-2 rounded-full border border-emerald-400/20 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold text-emerald-300">
                {trialDays} days free
              </span>
            )}
          </div>

          {/* CTA */}
          <button
            onClick={handleUpgrade}
            disabled={loading}
            className="w-full rounded-xl bg-violet-600 py-3 text-sm font-semibold text-white shadow-[0_0_20px_rgba(124,58,237,0.4)] transition-all hover:bg-violet-500 hover:shadow-[0_0_28px_rgba(124,58,237,0.5)] active:bg-violet-700 disabled:opacity-60"
          >
            {loading
              ? "Opening Shopify billing…"
              : hasTrial
              ? `Start ${trialDays}-day free trial`
              : `Get Pro — ${priceStr}/mo`}
          </button>

          {/* Trial clarification */}
          {!loading && hasTrial && (
            <p className="mt-2 text-center text-[11px] text-slate-500">
              Then {priceStr}/mo after trial. Cancel anytime from Shopify.
            </p>
          )}

          {error && (
            <p className="mt-2 text-center text-[12px] text-rose-400">{error}</p>
          )}

          <button
            onClick={onClose}
            className="mt-3 w-full text-center text-[12px] text-slate-600 transition hover:text-slate-400"
          >
            Continue with Lite
          </button>
        </div>
      </div>
    </div>
  );
}
