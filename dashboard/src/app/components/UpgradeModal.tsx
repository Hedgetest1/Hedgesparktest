"use client";

import Image from "next/image";

// ---------------------------------------------------------------------------
// Set NEXT_PUBLIC_UPGRADE_URL in your environment to point to the Shopify
// billing flow or upgrade page when the payment integration is ready.
// ---------------------------------------------------------------------------
const UPGRADE_URL =
  process.env.NEXT_PUBLIC_UPGRADE_URL ||
  "mailto:hello@hedgespark.com?subject=Upgrade%20to%20Pro";

type ProFeature = {
  title: string;
  description: string;
};

const PRO_FEATURES: ProFeature[] = [
  {
    title: "Know what to do, not just what happened",
    description:
      "Every signal comes with a plain-English action. No guessing, no dashboards to interpret.",
  },
  {
    title: "Daily AI brief in your inbox",
    description:
      "A ranked summary of your top product opportunities, written in merchant language, generated every morning.",
  },
  {
    title: "Price Intelligence",
    description:
      "See how your pricing compares to the market and get specific reposition recommendations per product.",
  },
  {
    title: "Market position scoring",
    description:
      "Understand which of your products are unique, which face heavy competition, and where to focus.",
  },
];

export function UpgradeModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;

  function handleUpgrade() {
    window.open(UPGRADE_URL, "_blank", "noopener,noreferrer");
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
        className="hs-fade-up relative z-10 w-full max-w-md rounded-3xl border border-violet-400/20 bg-[#0d0d1e] p-8 shadow-[0_32px_80px_rgba(124,58,237,0.22)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute right-5 top-5 rounded-lg p-1 text-slate-500 transition-colors hover:text-slate-300"
          aria-label="Close"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
            stroke="currentColor"
            className="h-5 w-5"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        {/* Mascot */}
        <div className="mb-5 flex justify-center">
          <Image
            src="/branding/hedgespark-mascot.png"
            alt="Hedge Spark"
            width={88}
            height={88}
            className="hs-bob"
            priority
          />
        </div>

        {/* Heading */}
        <div className="mb-6 text-center">
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/80">
            Hedge Spark Pro
          </div>
          <h2 className="text-xl font-semibold leading-snug text-white">
            Stop reading data.<br />Start making decisions.
          </h2>
          <p className="mt-2.5 text-sm leading-6 text-slate-400">
            Lite shows you what your visitors are doing. Pro tells you exactly
            what to do about it — with AI-written actions, daily briefs, and
            competitor context per product.
          </p>
        </div>

        {/* Feature list */}
        <ul className="mb-7 space-y-4">
          {PRO_FEATURES.map((feature) => (
            <li key={feature.title} className="flex items-start gap-3">
              <span className="mt-0.5 flex-shrink-0 rounded-full bg-violet-500/20 p-0.5">
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={2.5}
                  stroke="currentColor"
                  className="h-3 w-3 text-violet-400"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                </svg>
              </span>
              <div>
                <div className="text-[13px] font-medium text-slate-200">
                  {feature.title}
                </div>
                <div className="mt-0.5 text-[12px] leading-5 text-slate-500">
                  {feature.description}
                </div>
              </div>
            </li>
          ))}
        </ul>

        {/* CTA */}
        <button
          onClick={handleUpgrade}
          className="w-full rounded-xl bg-violet-600 py-3 text-sm font-semibold text-white shadow-[0_0_20px_rgba(124,58,237,0.4)] transition-colors hover:bg-violet-500 active:bg-violet-700"
        >
          Get Pro access →
        </button>

        <p className="mt-3 text-center text-[11px] text-slate-600">
          You'll be contacted within one business day.
        </p>
      </div>
    </div>
  );
}
