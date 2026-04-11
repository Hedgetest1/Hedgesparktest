"use client";

import type { ReactNode } from "react";
import Image from "next/image";

export function ProGate({
  tier,
  onUpgradeClick,
  children,
  label = "this Pro feature",
  teaser,
}: {
  tier: "lite" | "pro";
  onUpgradeClick: () => void;
  children: ReactNode;
  label?: string;
  /** Short benefit line shown below the unlock button */
  teaser?: string;
}) {
  if (tier === "pro") {
    return <>{children}</>;
  }

  return (
    <div className="group relative overflow-hidden rounded-2xl">
      {/* Blurred content — not interactive */}
      <div
        className="pointer-events-none select-none"
        style={{ filter: "blur(6px)", opacity: 0.35 }}
        aria-hidden="true"
      >
        {children}
      </div>

      {/* Premium overlay — aspirational & motivating */}
      <div
        className="absolute inset-0 flex cursor-pointer flex-col items-center justify-center gap-4 rounded-2xl bg-gradient-to-b from-[#0a0a1a]/70 via-[#0d0b1e]/80 to-[#0a0a1a]/70 backdrop-blur-[3px] transition-all duration-200 group-hover:from-[#0a0a1a]/60 group-hover:via-[#120e24]/70 group-hover:to-[#0a0a1a]/60"
        onClick={onUpgradeClick}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && onUpgradeClick()}
        aria-label={`Unlock ${label}`}
      >
        {/* Shimmer border effect */}
        <div className="absolute inset-0 rounded-2xl border border-[#d4893a]/[0.12] hs-shimmer" />

        {/* Mascot + badge */}
        <div className="relative">
          <Image
            src="/branding/hedgespark/spark.png"
            alt=""
            width={48}
            height={48}
            className="opacity-90 transition-transform duration-200 group-hover:scale-105"
          />
          <span className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full border border-[#d4893a]/40 bg-[#d4893a]/30 text-[8px] font-bold text-[#e8a04e]">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="h-3 w-3">
              <path fillRule="evenodd" d="M12 1.5a5.25 5.25 0 00-5.25 5.25v3a3 3 0 00-3 3v6.75a3 3 0 003 3h10.5a3 3 0 003-3v-6.75a3 3 0 00-3-3v-3c0-2.9-2.35-5.25-5.25-5.25zm3.75 8.25v-3a3.75 3.75 0 10-7.5 0v3h7.5z" clipRule="evenodd" />
            </svg>
          </span>
        </div>

        {/* Text */}
        <div className="text-center">
          <div className="mb-1.5 inline-flex items-center gap-1.5 rounded-full border border-[#d4893a]/30 bg-[#d4893a]/20 px-3 py-1 shadow-[0_0_16px_rgba(212,137,58,0.2)]">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="h-3 w-3 text-[#e8a04e]">
              <path fillRule="evenodd" d="M9 4.5a.75.75 0 01.721.544l.813 2.846a3.75 3.75 0 002.576 2.576l2.846.813a.75.75 0 010 1.442l-2.846.813a3.75 3.75 0 00-2.576 2.576l-.813 2.846a.75.75 0 01-1.442 0l-.813-2.846a3.75 3.75 0 00-2.576-2.576l-2.846-.813a.75.75 0 010-1.442l2.846-.813A3.75 3.75 0 007.466 7.89l.813-2.846A.75.75 0 019 4.5z" clipRule="evenodd" />
            </svg>
            <span className="text-[13px] font-bold uppercase tracking-[0.14em] text-[#e8a04e]">
              Pro
            </span>
          </div>
          <div className="text-[15px] font-semibold text-slate-300 transition-colors group-hover:text-white">
            Unlock {label}
          </div>
          {teaser && (
            <div className="mt-1.5 max-w-[280px] text-[13px] leading-[1.5] text-slate-500">
              {teaser}
            </div>
          )}
        </div>

        {/* CTA */}
        <button className="hs-cta-gradient rounded-xl px-6 py-2.5 text-[14px] font-bold text-white shadow-[0_0_16px_rgba(212,137,58,0.3)] transition-all duration-200 group-hover:shadow-[0_0_24px_rgba(212,137,58,0.4)]">
          Start free trial &rarr;
        </button>
      </div>
    </div>
  );
}
