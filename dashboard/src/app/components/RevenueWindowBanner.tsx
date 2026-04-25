"use client";

import { createMoneyFormatter, type DisplayCurrency } from "../lib/currency";

// RevenueWindowBanner — Revenue at risk summary for the Pro dashboard.
//
// This is the loudest number in the entire Pro dashboard.
// "You have $X in estimated revenue at risk right now."
//
// For Lite users: shows the dollar amount but locks the breakdown.
// For Pro users: shows the full per-product breakdown and actions.
//
// Revenue window = visitor_count × CVR × AOV per hot segment.
// Computed by segment_monitor_worker.py and stored in active_nudges.
// Sourced from: GET /dashboard/overview (revenue_window_tease)
//               GET /dashboard/overview/pro (revenue_windows)

type RevenueWindowTeaseData = {
  estimated_revenue_at_risk?: number;
  active_opportunity_count?: number;
  note?: string;
};

type RevenueWindowOpportunity = {
  product_url?: string;
  action_type?: string;
  visitor_count?: number;
  revenue_window?: number;
  calibration_state?: string;
};

type RevenueWindowData = {
  total_revenue_at_risk?: number;
  opportunities?: RevenueWindowOpportunity[];
  currency?: string;
};

// formatDollars replaced by createMoneyFormatter at each callsite (bound per
// component to the user's displayCurrency preference).

function shortProductLabel(url: string | undefined): string {
  if (!url) return "Product";
  const slug = url.split("/").filter(Boolean).pop() || url;
  return slug.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()).slice(0, 30);
}

function actionTypeLabel(t: string | undefined): string {
  if (!t) return "Opportunity";
  return t.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(" ");
}

// ---------------------------------------------------------------------------
// Pro variant — full breakdown
// ---------------------------------------------------------------------------
export function RevenueWindowPro({
  data,
  displayCurrency = "USD",
}: {
  data: RevenueWindowData | null;
  displayCurrency?: DisplayCurrency;
}) {
  const total = data?.total_revenue_at_risk ?? 0;
  const opps = data?.opportunities ?? [];
  const nativeCurrency = data?.currency ?? "USD";
  const formatDollars = createMoneyFormatter(displayCurrency, nativeCurrency);

  return (
    <div className="rounded-2xl border border-emerald-400/[0.16] bg-gradient-to-br from-emerald-950/40 to-[#09091a] p-5">
      {/* Header */}
      <div className="mb-4 flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-emerald-400/60">
            Revenue at Risk
          </div>
          <div className="text-[28px] font-bold tabular-nums text-emerald-300">
            {formatDollars(total)}
          </div>
          <p className="mt-0.5 text-[11px] text-slate-400">
            Estimated conversion opportunity across live hot segments
          </p>
        </div>
        <span className="flex-shrink-0 rounded-full border border-emerald-400/30 bg-emerald-500/10 px-2.5 py-1 text-[10px] font-semibold text-emerald-300">
          Live
        </span>
      </div>

      {/* Per-product breakdown */}
      {opps.length === 0 ? (
        <p className="text-[12px] text-slate-400">
          No active revenue windows right now. Segments update every 5 minutes.
        </p>
      ) : (
        <div className="space-y-2">
          {opps.map((opp, i) => (
            <div
              key={`opp-${i}`}
              className="flex items-center justify-between rounded-xl border border-white/[0.05] bg-white/[0.02] px-3.5 py-2.5"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-[12px] font-medium text-white">
                  {shortProductLabel(opp.product_url)}
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[10px] text-slate-400">
                  <span>{actionTypeLabel(opp.action_type)}</span>
                  {opp.visitor_count != null && opp.visitor_count > 0 && (
                    <>
                      <span className="text-slate-700">·</span>
                      <span>{opp.visitor_count} hot visitors</span>
                    </>
                  )}
                  {opp.calibration_state === "empirical" && (
                    <>
                      <span className="text-slate-700">·</span>
                      <span className="text-emerald-400/60">empirical CVR</span>
                    </>
                  )}
                </div>
              </div>
              <div className="ml-3 flex-shrink-0 text-right">
                <div className="text-[14px] font-semibold tabular-nums text-emerald-300">
                  {formatDollars(opp.revenue_window)}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="mt-3 text-[10px] text-slate-400">
        Revenue window = hot visitors × empirical CVR × store AOV. Probabilistic estimate.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lite variant — tease with upgrade prompt
// ---------------------------------------------------------------------------
export function RevenueWindowLite({
  data,
  onUpgradeClick,
  displayCurrency = "USD",
}: {
  data: RevenueWindowTeaseData | null;
  onUpgradeClick: () => void;
  displayCurrency?: DisplayCurrency;
}) {
  const amount = data?.estimated_revenue_at_risk ?? 0;
  const oppCount = data?.active_opportunity_count ?? 0;
  // Lite variant's number is blurred out anyway — still respect the merchant's
  // chosen display currency so the shape is correct.
  const formatDollars = createMoneyFormatter(displayCurrency, "USD");

  if (amount <= 0 && oppCount === 0) return null;

  return (
    <div
      className="relative overflow-hidden rounded-2xl border border-violet-400/[0.14] bg-gradient-to-br from-violet-950/30 to-[#09091a] p-5 cursor-pointer"
      onClick={onUpgradeClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onUpgradeClick()}
    >
      {/* Blur overlay over the number */}
      <div className="flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-violet-400/50">
            Revenue at Risk — Pro
          </div>
          <div className="relative inline-block">
            <div
              className="text-[28px] font-bold tabular-nums text-violet-300"
              style={{ filter: "blur(8px)", userSelect: "none" }}
            >
              {formatDollars(amount)}
            </div>
          </div>
          <p className="mt-1 text-[12px] text-slate-400">
            {oppCount > 0
              ? `${oppCount} active opportunity${oppCount !== 1 ? "s" : ""} detected in your store`
              : "Revenue windows detected — see which products are at risk"}
          </p>
        </div>
        <button
          onClick={(e) => { e.stopPropagation(); onUpgradeClick(); }}
          className="ml-4 flex-shrink-0 rounded-xl bg-violet-600 px-3.5 py-2 text-[11px] font-semibold text-white shadow-lg shadow-violet-900/40 hover:bg-violet-500 transition-colors"
        >
          Unlock
        </button>
      </div>
      <p className="mt-3 text-[10px] text-slate-400">
        Upgrade to Pro to see which products, how many visitors, and which actions to take.
      </p>
    </div>
  );
}
