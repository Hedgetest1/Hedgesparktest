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
  bellPulse = false,
  reputation,
}: {
  shop: string;
  tier: Tier;
  onTierToggle: () => void;
  trial?: TrialInfo;
  notifications?: SparkNotification[];
  /** True when at least one HIGH/CRITICAL backend alert is live — bell shakes. */
  bellPulse?: boolean;
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
    <header className="relative z-[60] flex h-16 flex-shrink-0 items-center justify-between border-b border-white/[0.06] bg-[#07070f]/90 px-6 backdrop-blur-sm">
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
          <span className="hidden text-[14px] text-slate-400 sm:block">{dateStr}</span>
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
            <span className="text-[10px] tabular-nums text-slate-400">
              Spark: {reputation.accuracy}% accurate
            </span>
          </div>
        )}

        {/* Notification bell */}
        <NotificationBell
          notifications={notifications ?? []}
          isProUser={isProUser}
          pulse={bellPulse}
        />

        {/* Reports — Gap #1 hub. Tier-agnostic chrome (every tier
            sees the same icon; the page itself adapts to what the
            merchant has saved). */}
        <a
          href="/app/reports"
          title="Reports"
          aria-label="Open reports"
          className="flex h-9 w-9 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.03] text-slate-400 transition-colors hover:border-white/[0.14] hover:bg-white/[0.06] hover:text-[#e8a04e] focus-visible:ring-2 focus-visible:ring-[#e8a04e]/40"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.8}
            className="h-4 w-4"
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
          </svg>
        </a>

        {/* Settings gear — routes to the dedicated settings surface.
            Positioned between the notification bell and the tier pill
            per founder directive 2026-04-20: "metti settings in alto a
            destra vicino alla scritta del tipo di piano". */}
        <a
          href="/app/settings"
          title="Settings & integrations"
          aria-label="Open settings"
          className="flex h-9 w-9 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.03] text-slate-400 transition-colors hover:border-white/[0.14] hover:bg-white/[0.06] hover:text-[#e8a04e] focus-visible:ring-2 focus-visible:ring-[#e8a04e]/40"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.8}
            className="h-4 w-4"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z"
            />
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
        </a>

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
                trialUrgent ? "font-medium text-amber-300" : "text-slate-400"
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
