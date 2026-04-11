"use client";


import { RevenueTrendChart } from "./RevenueTrendChart";
import { SparkInline } from "./SparkCompanion";
import { createMoneyFormatter, type DisplayCurrency } from "../lib/currency";

type Props = {
  revenue: number;
  orders: number;
  currency: string;
  signalCount: number;
  topSignalMessage?: string;
  isProUser: boolean;
  onViewSignals: () => void;
  onUpgrade: () => void;
  coldStartPhase: number;
  apiBase: string;
  shop: string;
  displayCurrency?: DisplayCurrency;
};

export function RevenueHero({
  revenue,
  orders,
  currency,
  signalCount,
  topSignalMessage,
  isProUser,
  onViewSignals,
  onUpgrade,
  coldStartPhase,
  apiBase,
  shop,
  displayCurrency = "USD",
}: Props) {
  // Cold start: show a welcoming hero instead of revenue
  if (coldStartPhase < 3) {
    const messages = [
      "Connecting your store...",
      "Tracker live — revenue appears with your first order.",
      "Visitors arriving. First report building.",
    ];
    return (
      <div className="relative overflow-hidden rounded-3xl border border-[#d4893a]/12 bg-gradient-to-br from-[#d4893a]/[0.04] via-transparent to-transparent p-7">
        <div className="text-[16px] font-medium leading-relaxed text-slate-300">
          {messages[coldStartPhase] ?? messages[0]}
        </div>
      </div>
    );
  }

  // Determine hedgehog message
  let hedgehogMessage = "Monitoring your store.";
  if (signalCount > 0 && topSignalMessage) {
    hedgehogMessage = topSignalMessage;
  } else if (orders > 0 && signalCount === 0) {
    hedgehogMessage = `${orders} order${orders !== 1 ? "s" : ""} — store running clean.`;
  } else if (revenue === 0) {
    hedgehogMessage = "No revenue yet this week. Orders appear as they come in.";
  }

  // Money formatter respecting both native shop currency AND display preference.
  const fmtRevenue = createMoneyFormatter(displayCurrency, currency);

  return (
    <div className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-gradient-to-br from-white/[0.04] via-transparent to-[#d4893a]/[0.02] p-7 shadow-[0_0_60px_rgba(212,137,58,0.04)]">
      {/* Top row: revenue */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="mb-2 text-[13px] font-bold uppercase tracking-[0.15em] hs-brand-gradient">Revenue this week</div>
          {/* Revenue — the dominant element */}
          <div className="text-[3.5rem] font-extrabold leading-none tracking-tight text-white">
            {fmtRevenue(revenue)}
          </div>
          <div className="mt-3 flex items-center gap-3">
            <span className="text-[15px] font-medium text-slate-400">
              {orders} order{orders !== 1 ? "s" : ""}
            </span>
            {signalCount > 0 && (
              <span className="flex items-center gap-2 rounded-lg bg-[#d4893a]/15 px-3 py-1.5 text-[14px] font-bold text-[#e8a04e] ring-1 ring-[#d4893a]/25">
                <span className="hs-pulse inline-block h-2 w-2 rounded-full bg-[#d4893a]" />
                {signalCount} finding{signalCount !== 1 ? "s" : ""}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Revenue trend chart */}
      <RevenueTrendChart apiBase={apiBase} shop={shop} currency={currency} displayCurrency={displayCurrency} />

      {/* Spark message */}
      <div className="mt-5 rounded-xl border border-white/[0.06] bg-white/[0.025] px-5 py-4">
        <SparkInline message={hedgehogMessage} size={24} />
      </div>

      {/* CTA */}
      <div className="mt-5 flex gap-3">
        {signalCount > 0 && (
          <button
            onClick={onViewSignals}
            className="rounded-xl bg-[#d4893a]/20 px-5 py-2.5 text-[14px] font-bold text-[#e8a04e] transition hover:bg-[#d4893a]/30"
          >
            View findings &rarr;
          </button>
        )}
        {!isProUser && signalCount > 0 && (
          <button
            onClick={onUpgrade}
            className="rounded-xl border border-[#d4893a]/20 px-5 py-2.5 text-[14px] font-medium text-[#d4893a]/70 transition hover:border-[#d4893a]/40 hover:text-[#e8a04e]"
          >
            Unlock actions
          </button>
        )}
      </div>
    </div>
  );
}
