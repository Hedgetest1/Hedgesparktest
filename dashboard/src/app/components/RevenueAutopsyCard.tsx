"use client";

/**
 * RevenueAutopsyCard — "Why Revenue Changed"
 *
 * Per-product decomposition of the week-over-week revenue delta into
 * three causes: traffic (how many visitors), conversion (how many
 * bought once they arrived) and value (how much each order was worth).
 * For each product we show the primary cause and the full breakdown.
 *
 * Data source: GET /pro/revenue-autopsy
 */

import { useState } from "react";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
  DrawerHowCalculated,
  DrawerNextAction,
} from "./DetailDrawer";

type AutopsyProduct = {
  product_name: string;
  revenue_delta_eur: number;
  direction: string;
  primary_cause: string;
  narrative: string;
  traffic: { change_pct: number; impact_eur: number };
  conversion: {
    cvr_recent_pct: number;
    cvr_prior_pct: number;
    delta_pp: number;
    impact_eur: number;
  };
  value: {
    aov_recent: number;
    aov_prior: number;
    change_pct: number;
    impact_eur: number;
  };
};

type AutopsyData = {
  products: AutopsyProduct[];
  summary: {
    declining_count: number;
    growing_count: number;
    total_loss_per_week: number;
    top_decline_cause: string;
  };
  headline: string;
  // Shop's native currency — every money field in products + summary
  // is in this currency.
  currency?: string;
};

const CAUSE_COLORS: Record<string, string> = {
  traffic: "#60a5fa",
  conversion: "#f59e0b",
  value: "#a78bfa",
};

const CAUSE_LABELS: Record<string, string> = {
  traffic: "Traffic",
  conversion: "Conversion",
  value: "Order value",
};

import { formatMoneyCompact } from "@/app/app/_lib/formatters";

// Currency-aware formatters for the Autopsy card. The merchant's
// native currency comes from the /pro/revenue-autopsy response.
function fmtEur(n: number, currency?: string): string {
  const base = formatMoneyCompact(Math.abs(n), currency || "USD");
  return n < 0 ? `-${base}` : `+${base}`;
}

function fmtEurAbs(n: number, currency?: string): string {
  return formatMoneyCompact(Math.abs(n), currency || "USD");
}

export function RevenueAutopsyCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<AutopsyData>({
    url: `${apiBase}/pro/revenue-autopsy`,
    enabled: !!apiBase && !!shop && isProUser,
    isEmpty: (d) => !d.products || d.products.length === 0,
  });

  if (!isProUser) return null;

  if (state === "loading") {
    return <CardSkeleton label="Loading your revenue autopsy" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Revenue autopsy unavailable"
        message="We couldn't load this week's revenue autopsy. Your order history is safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="amber"
        title="Revenue autopsy is warming up"
        body="The autopsy compares this week to last week and attributes every euro gained or lost to traffic, conversion, or order value. Needs at least two weeks of orders to produce a reliable report."
        eta="Ready after 2 weeks of orders"
      />
    );
  }

  const decliningProducts = data.products.filter((p) => p.direction === "declining");
  const growingProducts = data.products.filter((p) => p.direction === "growing");
  const topProducts = data.products.slice(0, 6);
  const worstDecline = decliningProducts[0];
  const weeklyLoss = data.summary.total_loss_per_week;
  const topCause = data.summary.top_decline_cause;

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open revenue autopsy details — ${decliningProducts.length} products declining, ${growingProducts.length} growing`}
        onClick={() => setDrawerOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setDrawerOpen(true);
          }
        }}
        className="group cursor-pointer rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6 transition-shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-[#e8a04e] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220] hover:border-white/[0.12]"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-[#e8a04e]">
              Revenue autopsy
            </div>
            <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
              Why revenue changed
            </h3>
            <p className="mt-2 text-[14px] leading-relaxed text-slate-400">{data.headline}</p>
          </div>
          {weeklyLoss > 0 && (
            <div className="flex-shrink-0 rounded-xl border border-rose-400/25 bg-rose-500/[0.06] px-4 py-3 text-right">
              <div className="text-[10px] font-bold uppercase tracking-wider text-rose-400">
                Leaking per week
              </div>
              <div className="mt-1 text-[24px] font-extrabold tabular-nums text-rose-300">
                -{fmtEurAbs(weeklyLoss, data?.currency)}
              </div>
            </div>
          )}
        </div>

        <div className="mt-5 space-y-2">
          {topProducts.map((p) => {
            const causeColor = CAUSE_COLORS[p.primary_cause] || "#94a3b8";
            const isDeclining = p.direction === "declining";
            return (
              <div
                key={p.product_name}
                className="rounded-xl border border-white/[0.05] bg-white/[0.015] p-4"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[13px] font-semibold text-slate-200">
                        {p.product_name}
                      </span>
                      <span
                        className="flex-shrink-0 rounded-full border px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider"
                        style={{
                          color: causeColor,
                          background: causeColor + "18",
                          borderColor: causeColor + "40",
                        }}
                      >
                        {CAUSE_LABELS[p.primary_cause] || p.primary_cause}
                      </span>
                    </div>
                    <p className="mt-1 text-[11px] leading-relaxed text-slate-400">{p.narrative}</p>
                  </div>
                  <div
                    className={`flex-shrink-0 text-[16px] font-extrabold tabular-nums ${
                      isDeclining ? "text-rose-400" : "text-emerald-400"
                    }`}
                  >
                    {fmtEur(p.revenue_delta_eur, data?.currency)}
                    <span className="ml-1 text-[10px] font-semibold text-slate-400">/wk</span>
                  </div>
                </div>

                {/* Impact bars — traffic / conversion / value share of the delta */}
                <div className="mt-3 flex gap-1" aria-hidden="true">
                  {(["traffic", "conversion", "value"] as const).map((cause) => {
                    const impact = Math.abs(p[cause].impact_eur);
                    const total =
                      Math.abs(p.traffic.impact_eur) +
                      Math.abs(p.conversion.impact_eur) +
                      Math.abs(p.value.impact_eur);
                    const pct = total > 0 ? (impact / total) * 100 : 33;
                    return (
                      <div
                        key={cause}
                        className="h-1.5 rounded-full"
                        style={{
                          width: `${Math.max(5, pct)}%`,
                          background:
                            cause === p.primary_cause
                              ? CAUSE_COLORS[cause]
                              : CAUSE_COLORS[cause] + "40",
                        }}
                      />
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>

        <div className="mt-4 text-[11px] font-semibold text-slate-400">
          Click for per-product breakdown and next action →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="🔬"
        title="Why revenue changed"
        subtitle="This week versus last week, broken down by cause"
      >
        <DrawerExplainer
          body={
            "Every time revenue moves — up or down — there are only three possible causes: traffic " +
            "(how many people came), conversion (how many of them bought), and order value (how " +
            "much they spent). The autopsy splits the weekly change across these three causes for " +
            "every product so you can see exactly why the number moved."
          }
          why={
            "When revenue drops, the wrong fix wastes money. A traffic drop needs new visitors; a " +
            "conversion drop needs a better product page; an order-value drop needs merchandising. " +
            "The autopsy tells you which one it is before you spend a euro on the wrong lever."
          }
        />

        {weeklyLoss > 0 && (
          <DrawerBigStat
            label="Weekly revenue at risk"
            value={`-${fmtEurAbs(weeklyLoss, data?.currency)}`}
            sublabel={`Driven mostly by ${CAUSE_LABELS[topCause] || topCause} across ${
              decliningProducts.length
            } declining product${decliningProducts.length === 1 ? "" : "s"}`}
            color="#f43f5e"
          />
        )}

        <DrawerKeyValueList
          items={[
            {
              label: "Declining products",
              value: `${decliningProducts.length}`,
              color: decliningProducts.length > 0 ? "#f43f5e" : "#94a3b8",
            },
            {
              label: "Growing products",
              value: `${growingProducts.length}`,
              color: growingProducts.length > 0 ? "#10b981" : "#94a3b8",
            },
            {
              label: "Leading decline cause",
              value: CAUSE_LABELS[topCause] || topCause || "—",
              color: CAUSE_COLORS[topCause] || "#94a3b8",
            },
            {
              label: "Products analyzed",
              value: `${data.products.length}`,
            },
          ]}
        />

        <DrawerSectionHeading>Top movers, in order</DrawerSectionHeading>
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          {topProducts.map((p, i) => {
            const isDeclining = p.direction === "declining";
            const deltaColor = isDeclining ? "#f43f5e" : "#10b981";
            return (
              <div
                key={p.product_name}
                style={{
                  padding: "12px 14px",
                  borderRadius: "10px",
                  background: "rgba(15,23,42,0.55)",
                  border: "1px solid rgba(148,163,184,0.12)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    gap: "12px",
                    marginBottom: "8px",
                  }}
                >
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div
                      style={{
                        color: "#e2e8f0",
                        fontWeight: 600,
                        fontSize: "13px",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {i + 1}. {p.product_name}
                    </div>
                    <div
                      style={{
                        color: "#64748b",
                        fontSize: "11px",
                        marginTop: "2px",
                        lineHeight: 1.5,
                      }}
                    >
                      {p.narrative}
                    </div>
                  </div>
                  <div
                    style={{
                      color: deltaColor,
                      fontWeight: 800,
                      fontSize: "16px",
                      fontVariantNumeric: "tabular-nums",
                      flexShrink: 0,
                    }}
                  >
                    {fmtEur(p.revenue_delta_eur, data?.currency)}
                  </div>
                </div>

                {/* Per-cause breakdown strip */}
                <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                  {(["traffic", "conversion", "value"] as const).map((cause) => {
                    const impact = p[cause].impact_eur;
                    const isPrimary = cause === p.primary_cause;
                    return (
                      <div
                        key={cause}
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          fontSize: "11px",
                          padding: "4px 8px",
                          borderRadius: "6px",
                          background: isPrimary ? CAUSE_COLORS[cause] + "14" : "transparent",
                          border: isPrimary
                            ? `1px solid ${CAUSE_COLORS[cause]}40`
                            : "1px solid transparent",
                        }}
                      >
                        <span
                          style={{
                            color: CAUSE_COLORS[cause],
                            fontWeight: isPrimary ? 700 : 500,
                          }}
                        >
                          {CAUSE_LABELS[cause]}
                          {isPrimary && " · primary cause"}
                        </span>
                        <span
                          style={{
                            color: impact < 0 ? "#fb7185" : "#34d399",
                            fontWeight: 600,
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {fmtEur(impact, data?.currency)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>

        <DrawerHowCalculated
          formula="For each product we compute three counterfactuals: how much revenue would have moved if ONLY traffic changed, ONLY conversion changed, or ONLY order value changed. The sum of the three impacts equals the actual weekly revenue delta. The largest of the three is the primary cause."
          inputs={[
            {
              label: "Declining products",
              value: `${decliningProducts.length}`,
            },
            {
              label: "Growing products",
              value: `${growingProducts.length}`,
            },
            {
              label: "Leading decline cause",
              value: CAUSE_LABELS[topCause] || topCause || "—",
            },
          ]}
          note="Traffic is usually a marketing or SEO fix; conversion is usually a product-page or price fix; order value is usually a merchandising, bundle, or shipping-threshold fix."
        />

        {worstDecline && (
          <DrawerNextAction
            headline="Start here"
            primary={{
              label: `Fix ${worstDecline.product_name}`,
              description: `This product is losing ${fmtEurAbs(
                worstDecline.revenue_delta_eur,
                data?.currency,
              )}/week, driven primarily by ${
                (CAUSE_LABELS[worstDecline.primary_cause] || worstDecline.primary_cause).toLowerCase()
              }. ${worstDecline.narrative}`,
              onClick: () => setDrawerOpen(false),
            }}
          />
        )}
      </DetailDrawer>
    </>
  );
}
