"use client";

import Image from "next/image";

const PRO_FEATURES = [
  "Price Intelligence — monitor competitor pricing signals in real time",
  "Market Intelligence — discover your competitive uniqueness score",
  "Revenue Leak Detector — find exactly where you're losing conversions",
  "Advanced opportunity signals across all product pages",
  "AI action recommendations for every detected signal",
];

export function UpgradeModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;

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
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-5 w-5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        {/* Mascot */}
        <div className="mb-6 flex justify-center">
          <Image
            src="/branding/hedgespark-mascot.png"
            alt="Hedge Spark"
            width={96}
            height={96}
            className="hs-bob"
            priority
          />
        </div>

        {/* Heading */}
        <div className="mb-6 text-center">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/80">
            Upgrade to Pro
          </div>
          <h2 className="text-2xl font-semibold text-white">
            Unlock decision intelligence
          </h2>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            Hedge Spark Pro gives you the full signal layer — price movements,
            market position, and conversion recovery in one place.
          </p>
        </div>

        {/* Feature list */}
        <ul className="mb-8 space-y-3">
          {PRO_FEATURES.map((feature) => (
            <li key={feature} className="flex items-start gap-3 text-sm text-slate-300">
              <span className="mt-0.5 flex-shrink-0 rounded-full bg-violet-500/20 p-0.5">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor" className="h-3 w-3 text-violet-400">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                </svg>
              </span>
              {feature}
            </li>
          ))}
        </ul>

        {/* CTA */}
        <button
          onClick={onClose}
          className="w-full rounded-xl bg-violet-600 py-3 text-sm font-semibold text-white transition-colors hover:bg-violet-500 active:bg-violet-700"
        >
          Upgrade to Pro
        </button>
        <p className="mt-3 text-center text-[11px] text-slate-600">
          Payment flow coming soon.
        </p>
      </div>
    </div>
  );
}
