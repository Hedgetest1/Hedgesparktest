"use client";

import { useEffect, useState } from "react";
import { NotificationBell } from "./NotificationBell";
import type { SparkNotification } from "../lib/sparkNotifications";
import type { ReputationScore } from "../lib/sparkReputation";

type Tier = "lite" | "pro";

export type TrialInfo = {
  daysRemaining: number | null;
  isPaidPro: boolean;
};

export function TopBar({
  shop,
  tier,
  onTierToggle,
  trial,
  notifications,
  reputation,
}: {
  shop: string;
  tier: Tier;
  onTierToggle: () => void;
  trial?: TrialInfo;
  notifications?: SparkNotification[];
  reputation?: ReputationScore | null;
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

  const showTrialBadge =
    tier === "pro" &&
    trial &&
    trial.daysRemaining != null &&
    !trial.isPaidPro;

  const trialUrgent = showTrialBadge && trial!.daysRemaining! <= 3;
  const isProUser = tier === "pro";

  return (
    <header className="flex h-16 flex-shrink-0 items-center justify-between border-b border-white/[0.06] bg-[#07070f]/90 px-6 backdrop-blur-sm">
      {/* Left: shop pill + date */}
      <div className="flex items-center gap-3">
        {shop ? (
          <div className="flex items-center gap-2.5 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2">
            <span className="hs-pulse h-2 w-2 rounded-full bg-emerald-400" />
            <span className="text-[14px] font-medium text-slate-200">{shop}</span>
          </div>
        ) : (
          <div className="rounded-xl border border-amber-400/30 bg-amber-500/10 px-4 py-2">
            <span className="text-[14px] font-medium text-amber-300">No shop connected</span>
          </div>
        )}
        {dateStr && (
          <span className="hidden text-[14px] text-slate-500 sm:block">{dateStr}</span>
        )}
      </div>

      {/* Right: reputation + bell + trial + tier */}
      <div className="flex items-center gap-2.5">
        {/* Spark reputation badge — only when enough data */}
        {reputation?.ready && reputation.accuracy != null && (
          <div className="hidden items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.03] px-2.5 py-1 sm:flex">
            <span className={`h-1 w-1 rounded-full ${
              reputation.accuracy >= 70 ? "bg-emerald-400" : reputation.accuracy >= 50 ? "bg-amber-400" : "bg-slate-500"
            }`} />
            <span className="text-[10px] tabular-nums text-slate-500">
              Spark: {reputation.accuracy}% accurate
            </span>
          </div>
        )}

        {/* Notification bell */}
        <NotificationBell
          notifications={notifications ?? []}
          isProUser={isProUser}
        />

        {/* Trial countdown badge */}
        {showTrialBadge && (
          <div
            className={`hidden items-center gap-1.5 rounded-full border px-3 py-1 sm:flex ${
              trialUrgent
                ? "border-amber-400/30 bg-amber-500/10"
                : "border-white/[0.08] bg-white/[0.03]"
            }`}
          >
            <span
              className={`h-1 w-1 rounded-full ${
                trialUrgent ? "bg-amber-400 animate-pulse" : "bg-slate-500"
              }`}
            />
            <span
              className={`text-[11px] ${
                trialUrgent ? "font-medium text-amber-300" : "text-slate-500"
              }`}
            >
              {trial!.daysRemaining! <= 0
                ? "Trial ends today"
                : trial!.daysRemaining === 1
                ? "1 day left in trial"
                : `${trial!.daysRemaining} days left in trial`}
            </span>
          </div>
        )}

        {tier === "pro" ? (
          <div className="flex items-center gap-1.5 rounded-full border border-[#d4893a]/30 bg-[#d4893a]/15 px-3.5 py-1.5">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="currentColor"
              className="h-3 w-3 text-[#e8a04e]"
            >
              <path
                fillRule="evenodd"
                d="M9 4.5a.75.75 0 01.721.544l.813 2.846a3.75 3.75 0 002.576 2.576l2.846.813a.75.75 0 010 1.442l-2.846.813a3.75 3.75 0 00-2.576 2.576l-.813 2.846a.75.75 0 01-1.442 0l-.813-2.846a3.75 3.75 0 00-2.576-2.576l-2.846-.813a.75.75 0 010-1.442l2.846-.813A3.75 3.75 0 007.466 7.89l.813-2.846A.75.75 0 019 4.5z"
                clipRule="evenodd"
              />
            </svg>
            <span className="text-[14px] font-bold text-[#e8a04e]">
              {showTrialBadge ? "Pro trial" : "Pro"}
            </span>
          </div>
        ) : (
          <button
            onClick={onTierToggle}
            className="hs-cta-gradient flex items-center gap-2 rounded-xl px-5 py-2 text-[14px] font-bold text-white shadow-[0_0_20px_rgba(212,137,58,0.35)] transition-all hover:shadow-[0_0_28px_rgba(212,137,58,0.4)]"
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
