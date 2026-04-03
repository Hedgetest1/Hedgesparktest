"use client";


import { RevenueTrendChart } from "./RevenueTrendChart";
import { SparkInline } from "./SparkCompanion";

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
};

function fmtCurrency(value: number, currency: string): string {
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    return `${currency} ${Math.round(value)}`;
  }
}

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
}: Props) {
  // Cold start: show a welcoming hero instead of revenue
  if (coldStartPhase < 3) {
    const messages = [
      "Let's get your store connected. Your revenue command center is loading.",
      "Your tracker is live — revenue data will appear once orders start flowing.",
      "Visitors are arriving. Your first intelligence report is building.",
    ];
    return (
      <div className="relative overflow-hidden rounded-2xl border border-violet-400/[0.12] bg-gradient-to-br from-violet-500/[0.06] via-transparent to-transparent p-6">
        <div className="text-[13px] font-medium text-slate-300">
          {messages[coldStartPhase] ?? messages[0]}
        </div>
      </div>
    );
  }

  // Determine hedgehog message
  let hedgehogMessage = "Your store is being monitored. Check back for insights.";
  if (signalCount > 0 && topSignalMessage) {
    hedgehogMessage = topSignalMessage;
  } else if (orders > 0 && signalCount === 0) {
    hedgehogMessage = `${orders} order${orders !== 1 ? "s" : ""} this week. No issues detected — your store is running clean.`;
  } else if (revenue === 0) {
    hedgehogMessage = "No revenue tracked yet this week. Orders will appear here as they come in.";
  }

  return (
    <div className="relative overflow-hidden rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.04] via-transparent to-violet-500/[0.03] p-6 shadow-[0_0_48px_rgba(124,58,237,0.04)]">
      {/* Top row: revenue + mascot */}
      <div className="flex items-start justify-between gap-4">
        <div>
          {/* Revenue — the dominant element */}
          <div className="text-[48px] font-bold leading-none tracking-tight text-white">
            {fmtCurrency(revenue, currency)}
          </div>
          <div className="mt-1.5 flex items-center gap-3 text-[13px]">
            <span className="text-slate-400">
              {orders} order{orders !== 1 ? "s" : ""} this week
            </span>
            {signalCount > 0 && (
              <span className="flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-amber-300 ring-1 ring-amber-400/25">
                <span className="hs-pulse inline-block h-1.5 w-1.5 rounded-full bg-amber-400" />
                {signalCount} signal{signalCount !== 1 ? "s" : ""}
              </span>
            )}
          </div>
        </div>

        {/* Signal count badge already present above — mascot removed for cleaner data display */}
      </div>

      {/* Revenue trend chart — the visual heart of the hero */}
      <RevenueTrendChart apiBase={apiBase} shop={shop} currency={currency} />

      {/* Spark message */}
      <div className="mt-4 rounded-xl border border-white/[0.06] bg-white/[0.025] px-4 py-3">
        <SparkInline message={hedgehogMessage} size={22} />
      </div>

      {/* CTA */}
      <div className="mt-4 flex gap-3">
        {signalCount > 0 && (
          <button
            onClick={onViewSignals}
            className="rounded-lg bg-violet-500/20 px-4 py-2 text-[12px] font-semibold text-violet-200 transition hover:bg-violet-500/30"
          >
            View signals →
          </button>
        )}
        {!isProUser && signalCount > 0 && (
          <button
            onClick={onUpgrade}
            className="rounded-lg border border-violet-400/20 px-4 py-2 text-[12px] font-medium text-violet-300/70 transition hover:border-violet-400/40 hover:text-violet-200"
          >
            Unlock actions
          </button>
        )}
      </div>
    </div>
  );
}
