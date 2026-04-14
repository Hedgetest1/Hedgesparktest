"use client";

/**
 * OrdersSummary — real revenue + top products from Shopify orders.
 *
 * Shows 7-day and 30-day revenue totals, order counts, average order
 * value, and the top products by revenue. This is the "truth" card —
 * every number comes from real Shopify orders, not estimates or
 * models. It's the one a merchant looks at first to see the outcome.
 *
 * Data source: GET /orders/summary
 */

import { useState } from "react";
import { createMoneyFormatter, type DisplayCurrency } from "../lib/currency";
import type { paths } from "../lib/api-client";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
} from "./DetailDrawer";

// Source of truth: GET /orders/summary → OrdersSummaryResponse.
type OrdersSummaryData =
  paths["/orders/summary"]["get"]["responses"]["200"]["content"]["application/json"];

export function OrdersSummary({
  apiBase,
  shop,
  displayCurrency = "USD",
}: {
  apiBase: string;
  shop: string;
  displayCurrency?: DisplayCurrency;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<OrdersSummaryData>({
    url: `${apiBase}/orders/summary`,
    enabled: !!shop && !!apiBase,
    isEmpty: (d) => !d.has_orders,
  });

  if (state === "loading") {
    return <CardSkeleton label="Loading your real revenue totals" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Revenue totals unavailable"
        message="We couldn't load your real revenue totals right now. Your order history is safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="slate"
        title="No orders received yet"
        body="Once Shopify sends your first order webhook, your real 7-day, 30-day, and top-product totals appear here. Nothing is estimated — every number is an actual order."
        eta="First reading on your first order"
      />
    );
  }

  const c = data.currency;
  const d7 = data.last_7d;
  const d30 = data.last_30d;
  const fmtCurrency = createMoneyFormatter(displayCurrency, c);
  const topProducts = data.top_products_by_revenue;

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open revenue details — ${fmtCurrency(d7.total_revenue)} in the last 7 days, ${fmtCurrency(d30.total_revenue)} in the last 30 days`}
        onClick={() => setDrawerOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setDrawerOpen(true);
          }
        }}
        className="group cursor-pointer rounded-2xl border border-white/[0.07] bg-white/[0.03] p-6 transition-shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-[#e8a04e] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220] hover:border-white/[0.12]"
      >
        <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-[#e8a04e]">
          Revenue · real orders
        </div>
        <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
          What you actually made
        </h3>
        <p className="mt-2 text-[14px] leading-relaxed text-slate-400">
          Straight from Shopify orders. Zero estimates, zero modelling — just the money that
          actually landed in your account.
        </p>

        {/* KPI row */}
        <div className="mt-5 grid gap-5 sm:grid-cols-3">
          <div>
            <div className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
              Last 7 days
            </div>
            <div className="mt-1.5 text-[32px] font-extrabold tabular-nums leading-none text-white">
              {fmtCurrency(d7.total_revenue)}
            </div>
            <div className="mt-2 text-[13px] text-slate-400">
              {d7.order_count} order{d7.order_count === 1 ? "" : "s"}
            </div>
          </div>
          <div>
            <div className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
              Last 30 days
            </div>
            <div className="mt-1.5 text-[32px] font-extrabold tabular-nums leading-none text-white">
              {fmtCurrency(d30.total_revenue)}
            </div>
            <div className="mt-2 text-[13px] text-slate-400">
              {d30.order_count} order{d30.order_count === 1 ? "" : "s"}
            </div>
          </div>
          <div>
            <div className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
              Average order
            </div>
            <div className="mt-1.5 text-[32px] font-extrabold tabular-nums leading-none text-white">
              {fmtCurrency(d30.avg_order_value)}
            </div>
            <div className="mt-2 text-[13px] text-slate-400">30-day average</div>
          </div>
        </div>

        {/* Top products */}
        {topProducts.length > 0 && (
          <div className="mt-6 border-t border-white/[0.06] pt-5">
            <div className="mb-3 text-[11px] font-bold uppercase tracking-[0.12em] text-slate-400">
              Top products by revenue · 30 days
            </div>
            <div className="space-y-2">
              {topProducts.slice(0, 5).map((p, i) => (
                <div
                  key={`${p.product_title}-${i}`}
                  className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.04] bg-white/[0.015] px-4 py-3 text-[14px]"
                >
                  <span className="min-w-0 truncate font-semibold text-slate-200">
                    {p.product_title}
                  </span>
                  <div className="flex flex-shrink-0 items-center gap-4">
                    <span className="text-[12px] tabular-nums text-slate-500">
                      {p.units_sold} sold
                    </span>
                    <span className="font-extrabold tabular-nums text-emerald-300">
                      {fmtCurrency(p.revenue)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-4 text-[11px] font-semibold text-slate-500">
          Click for the full product list and revenue breakdown →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="💳"
        title="What you actually made"
        subtitle="Straight from your Shopify orders · 30-day window"
      >
        <DrawerExplainer
          body={
            "Every other card on this dashboard works with signals, estimates or models. This one " +
            "reads the raw Shopify order stream and reports it. If there's a mismatch between what " +
            "you see here and what's in your Shopify admin, something is wrong with the pipeline — " +
            "we'd rather you catch it than us."
          }
          why={
            "A truth card anchors every other number on the dashboard. When the smart cards claim " +
            "'we recovered €X', this is where you confirm the base reality those claims sit on."
          }
        />

        <DrawerBigStat
          label="Revenue · last 30 days"
          value={fmtCurrency(d30.total_revenue)}
          sublabel={`${d30.order_count} order${d30.order_count === 1 ? "" : "s"} · average ${fmtCurrency(
            d30.avg_order_value,
          )}`}
          color="#10b981"
        />

        <DrawerKeyValueList
          items={[
            {
              label: "Last 7 days · revenue",
              value: fmtCurrency(d7.total_revenue),
              color: "#10b981",
            },
            {
              label: "Last 7 days · orders",
              value: `${d7.order_count}`,
            },
            {
              label: "Last 30 days · revenue",
              value: fmtCurrency(d30.total_revenue),
              color: "#10b981",
            },
            {
              label: "Last 30 days · orders",
              value: `${d30.order_count}`,
            },
            {
              label: "Average order value (30d)",
              value: fmtCurrency(d30.avg_order_value),
            },
            {
              label: "Currency",
              value: c,
            },
          ]}
        />

        {topProducts.length > 0 && (
          <>
            <DrawerSectionHeading>Every top product · 30-day revenue</DrawerSectionHeading>
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              {topProducts.map((p, i) => (
                <div
                  key={`drawer-${p.product_title}-${i}`}
                  style={{
                    padding: "11px 14px",
                    borderRadius: "10px",
                    background: "rgba(15,23,42,0.55)",
                    border: "1px solid rgba(148,163,184,0.12)",
                    display: "flex",
                    alignItems: "center",
                    gap: "12px",
                  }}
                >
                  <div
                    style={{
                      width: "22px",
                      height: "22px",
                      borderRadius: "50%",
                      background: "rgba(16,185,129,0.15)",
                      color: "#34d399",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: "11px",
                      fontWeight: 800,
                      flexShrink: 0,
                    }}
                  >
                    {i + 1}
                  </div>
                  <div
                    style={{
                      flex: 1,
                      minWidth: 0,
                      color: "#e2e8f0",
                      fontSize: "13px",
                      fontWeight: 600,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {p.product_title}
                  </div>
                  <div
                    style={{
                      color: "#94a3b8",
                      fontSize: "12px",
                      fontVariantNumeric: "tabular-nums",
                      flexShrink: 0,
                    }}
                  >
                    {p.units_sold} sold
                  </div>
                  <div
                    style={{
                      color: "#34d399",
                      fontSize: "14px",
                      fontWeight: 800,
                      fontVariantNumeric: "tabular-nums",
                      flexShrink: 0,
                      minWidth: "72px",
                      textAlign: "right",
                    }}
                  >
                    {fmtCurrency(p.revenue)}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </DetailDrawer>
    </>
  );
}
