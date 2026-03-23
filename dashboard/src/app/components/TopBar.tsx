"use client";

import { useEffect, useState } from "react";

type Tier = "lite" | "pro";

export function TopBar({
  shop,
  tier,
  onTierToggle,
}: {
  shop: string;
  tier: Tier;
  onTierToggle: () => void;
}) {
  const [dateStr, setDateStr] = useState("");

  useEffect(() => {
    setDateStr(
      new Date().toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    );
  }, []);

  return (
    <header className="flex h-14 flex-shrink-0 items-center justify-between border-b border-white/[0.08] bg-[#06060e]/90 px-5 backdrop-blur-sm">
      {/* Left: shop pill + date */}
      <div className="flex items-center gap-3">
        {shop ? (
          <div className="flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5">
            <span className="hs-pulse h-1.5 w-1.5 rounded-full bg-emerald-400" />
            <span className="text-[12px] text-slate-300">{shop}</span>
          </div>
        ) : (
          <div className="rounded-full border border-amber-400/30 bg-amber-500/10 px-3 py-1.5">
            <span className="text-[12px] text-amber-300">No shop connected</span>
          </div>
        )}
        {dateStr && (
          <span className="hidden text-[12px] text-slate-600 sm:block">{dateStr}</span>
        )}
      </div>

      {/* Right: tier indicator */}
      <div className="flex items-center">
        {tier === "pro" ? (
          <div className="flex items-center gap-1.5 rounded-full border border-violet-400/30 bg-violet-500/15 px-3.5 py-1.5">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="currentColor"
              className="h-3 w-3 text-violet-300"
            >
              <path
                fillRule="evenodd"
                d="M9 4.5a.75.75 0 01.721.544l.813 2.846a3.75 3.75 0 002.576 2.576l2.846.813a.75.75 0 010 1.442l-2.846.813a3.75 3.75 0 00-2.576 2.576l-.813 2.846a.75.75 0 01-1.442 0l-.813-2.846a3.75 3.75 0 00-2.576-2.576l-2.846-.813a.75.75 0 010-1.442l2.846-.813A3.75 3.75 0 007.466 7.89l.813-2.846A.75.75 0 019 4.5z"
                clipRule="evenodd"
              />
            </svg>
            <span className="text-[12px] font-semibold text-violet-200">Pro</span>
          </div>
        ) : (
          <button
            onClick={onTierToggle}
            className="flex items-center gap-1.5 rounded-full bg-violet-600 px-4 py-1.5 text-[12px] font-semibold text-white shadow-[0_0_16px_rgba(124,58,237,0.35)] transition-colors hover:bg-violet-500 active:bg-violet-700"
          >
            Upgrade
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2.5}
              className="h-3 w-3"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
            </svg>
          </button>
        )}
      </div>
    </header>
  );
}
