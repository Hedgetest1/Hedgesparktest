"use client";

/**
 * CustomerChurnTile — per-customer churn risk forecast for the Lite
 * floor's Retention section.
 *
 * Born 2026-04-27 from the brutal Lite vs $0-70 audit: customer-level
 * churn risk is one of 5 "open lanes" no competitor in the band ships.
 * Lifetimely does cohort-level retention, Datadrew adds RFM tags at
 * $99, BeProfit only at $149. We close this lane on the entry tier
 * with a deterministic + explainable + actionable per-customer model.
 *
 * Why deterministic: scoring formula is reproducible, audit-grade for
 * the merchant. Each customer's score = days_since_last / personal
 * median gap (NOT cohort average — beats Lifetimely's grain).
 *
 * Loss-aversion framing per CLAUDE.md §5: heading = "Customers slipping
 * away", revenue-at-risk total in shop currency, suggested next step.
 */

import { useCardFetch, CardSkeleton, CardError, CardEmpty } from "./_CardStates";
import { formatMoneyCompact } from "../app/_lib/formatters";

type ChurnRiskCustomer = {
  customer_email_hash: string;
  risk_score: number;
  risk_band: "slipping" | "at_risk" | "lapsed" | string;
  days_since_last_order: number;
  median_days_between_orders: number;
  overdue_factor: number;
  last_order_at: string | null;
  predicted_lapse_at: string | null;
  order_count: number;
  total_spent: number;
  suggested_action: string;
};

type Payload = {
  currency: string;
  has_data: boolean;
  customers_with_2plus: number;
  customers_at_risk_count: number;
  revenue_at_risk: number;
  customers: ChurnRiskCustomer[];
};

const COLD_START_MIN = 30;

// Risk band → visual color. Three tiers, color-coded per CLAUDE.md §4
// palette: rose=bad, amber=warning, emerald=growth (here used inversely
// for "lapsed" — the customer IS the bad outcome). Rose for the most
// severe risk, amber middle, slate for slipping (still recoverable).
const BAND_VISUAL: Record<string, { dot: string; label: string; pillBg: string; pillText: string }> = {
  slipping: {
    dot: "bg-amber-300",
    label: "Slipping",
    pillBg: "bg-amber-500/[0.12]",
    pillText: "text-amber-200",
  },
  at_risk: {
    dot: "bg-rose-400",
    label: "At risk",
    pillBg: "bg-rose-500/[0.12]",
    pillText: "text-rose-200",
  },
  lapsed: {
    dot: "bg-rose-500",
    label: "Lapsed",
    pillBg: "bg-rose-600/[0.18]",
    pillText: "text-rose-100",
  },
};

function formatRelativeDays(iso: string | null): string {
  if (!iso) return "—";
  const last = new Date(iso);
  const days = Math.floor((Date.now() - last.getTime()) / 86400000);
  if (days < 1) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

function formatPredictedLapse(iso: string | null): string {
  if (!iso) return "—";
  const lapse = new Date(iso);
  const daysFromNow = Math.floor((lapse.getTime() - Date.now()) / 86400000);
  if (daysFromNow < 0) {
    return `lapsed ${Math.abs(daysFromNow)}d ago`;
  }
  if (daysFromNow < 7) return `next ${daysFromNow}d`;
  if (daysFromNow < 60) return `in ~${Math.floor(daysFromNow / 7)}wk`;
  return "long-term";
}

export function CustomerChurnTile({
  apiBase,
  shop,
  displayCurrency,
}: {
  apiBase: string;
  shop: string;
  displayCurrency?: string;
}) {
  const { data, state, retry } = useCardFetch<Payload>({
    url: `${apiBase}/analytics/customer-churn-forecast?top_n=10`,
    enabled: !!apiBase && !!shop,
    isEmpty: (d) => !d.has_data,
    component: "CustomerChurnTile",
  });

  if (state === "loading") return <CardSkeleton label="Loading churn forecast" />;
  if (state === "error") return <CardError onRetry={retry} label="Churn forecast failed to load" />;

  // Cold-start: not enough cohort. Be transparent about WHY (idiot-proof
  // per §5 filter 2 — "if it needs documentation to understand, rewrite it").
  if (!data || !data.has_data) {
    const have = data?.customers_with_2plus ?? 0;
    const need = COLD_START_MIN - have;
    const eta =
      have >= COLD_START_MIN
        ? "Once a few customers fall outside their personal cadence, the at-risk list shows here."
        : `Need ${Math.max(0, need)} more repeat customer${need === 1 ? "" : "s"} (you have ${have}/${COLD_START_MIN}). Keep going.`;
    return <CardEmpty title="Customers slipping away" body={eta} />;
  }

  const ccy = data.currency || displayCurrency || "USD";

  return (
    <div
      className="rounded-2xl border border-rose-400/[0.12] bg-gradient-to-br from-[#1a0d10] via-[#0e0a0e] to-[#0a0a14] p-5"
      role="region"
      aria-label="Customers slipping away"
    >
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <div>
          <h3 className="text-[1.05rem] font-bold text-rose-100">
            Customers slipping away
          </h3>
          <div className="mt-0.5 text-[12px] text-slate-300">
            {data.customers_at_risk_count}{" "}
            {data.customers_at_risk_count === 1 ? "customer" : "customers"} past their typical
            re-order window · {formatMoneyCompact(data.revenue_at_risk, ccy)} lifetime value at risk
          </div>
        </div>
        <div className="hidden sm:block text-right">
          <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-rose-300">
            Personal cadence model
          </div>
          <div className="mt-0.5 text-[10px] text-slate-300">
            Each customer measured against their own pace
          </div>
        </div>
      </div>

      <ul className="mt-3 divide-y divide-white/[0.04]">
        {data.customers.map((c) => {
          const visual = BAND_VISUAL[c.risk_band] ?? BAND_VISUAL.at_risk;
          return (
            <li key={c.customer_email_hash} className="flex items-center gap-3 py-2.5">
              {/* Risk dot + score */}
              <div className="flex flex-shrink-0 flex-col items-center w-12">
                <span
                  className={`h-2 w-2 rounded-full ${visual.dot}`}
                  aria-hidden
                />
                <div className="mt-1 text-[14px] font-bold tabular-nums text-slate-100">
                  {c.risk_score}
                </div>
              </div>

              {/* Customer + pacing */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-[12px] font-mono text-slate-300 truncate">
                    {c.customer_email_hash}
                  </span>
                  <span
                    className={`flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${visual.pillBg} ${visual.pillText}`}
                  >
                    {visual.label}
                  </span>
                </div>
                <div className="mt-0.5 text-[11px] text-slate-300">
                  Last order {formatRelativeDays(c.last_order_at)} · normally every{" "}
                  {Math.round(c.median_days_between_orders)}d ·{" "}
                  <span className="text-rose-300 font-medium">
                    {Math.round(c.overdue_factor * 10) / 10}× overdue
                  </span>
                </div>
                <div className="mt-1 text-[11px] text-slate-300 italic">
                  {c.suggested_action}
                </div>
              </div>

              {/* Lifetime value */}
              <div className="flex-shrink-0 text-right">
                <div className="text-[14px] font-bold tabular-nums text-emerald-300">
                  {formatMoneyCompact(c.total_spent, ccy)}
                </div>
                <div className="text-[10px] text-slate-300">
                  {c.order_count} orders
                </div>
                <div className="mt-0.5 text-[10px] text-rose-300">
                  Predicted lapse {formatPredictedLapse(c.predicted_lapse_at)}
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      {/* Methodology footnote — every number is derivable per CLAUDE.md §2 r14 */}
      <div className="mt-3 pt-3 border-t border-white/[0.04] text-[10px] text-slate-300">
        <span className="font-semibold text-slate-300">How:</span> Each customer scored against
        their own median time-between-orders. Score 30+ = past their usual gap.
        Lapsed = 2.5× overdue.
      </div>
    </div>
  );
}
