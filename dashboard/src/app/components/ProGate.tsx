"use client";

import type { ReactNode } from "react";

export function ProGate({
  tier,
  onUpgradeClick,
  children,
  label = "this Pro feature",
}: {
  tier: "lite" | "pro";
  onUpgradeClick: () => void;
  children: ReactNode;
  label?: string;
}) {
  if (tier === "pro") {
    return <>{children}</>;
  }

  return (
    <div className="relative overflow-hidden rounded-2xl">
      {/* Blurred content — not interactive */}
      <div
        className="pointer-events-none select-none"
        style={{ filter: "blur(5px)", opacity: 0.45 }}
        aria-hidden="true"
      >
        {children}
      </div>

      {/* Overlay */}
      <div
        className="absolute inset-0 flex cursor-pointer flex-col items-center justify-center gap-3 rounded-2xl bg-[#080811]/60 backdrop-blur-[2px] transition-colors hover:bg-[#080811]/70"
        onClick={onUpgradeClick}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && onUpgradeClick()}
        aria-label={`Unlock ${label}`}
      >
        <div className="rounded-full border border-violet-400/40 bg-violet-500/20 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-violet-200">
          Pro
        </div>
        <div className="text-center text-xs text-slate-400">
          Click to unlock {label}
        </div>
      </div>
    </div>
  );
}
