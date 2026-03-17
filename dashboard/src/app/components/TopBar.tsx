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

      {/* Right: Lite / Pro toggle */}
      <div className="flex items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.03] p-0.5">
        <button
          onClick={tier === "pro" ? onTierToggle : undefined}
          className={`rounded-full px-4 py-1.5 text-[12px] font-medium transition-colors ${
            tier === "lite"
              ? "bg-white/[0.1] text-white shadow-sm"
              : "text-slate-500 hover:text-slate-300"
          }`}
        >
          Lite
        </button>
        <button
          onClick={tier === "lite" ? onTierToggle : undefined}
          className={`rounded-full px-4 py-1.5 text-[12px] font-semibold transition-colors ${
            tier === "pro"
              ? "bg-violet-600 text-white shadow-sm"
              : "text-slate-500 hover:text-slate-300"
          }`}
        >
          Pro
        </button>
      </div>
    </header>
  );
}
