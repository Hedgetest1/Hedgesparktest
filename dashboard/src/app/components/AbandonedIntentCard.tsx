"use client";

/**
 * AbandonedIntentCard — "Where Intent Dies"
 *
 * Shows products where merchants lose visitors that already showed intent:
 * they viewed, maybe added to cart, then left. Classifies the leak point
 * (browse → cart, cart → purchase) and compares buyer vs non-buyer session
 * depth so the merchant can see "they barely looked" vs "they really tried
 * and the funnel failed them".
 *
 * Data source: GET /pro/abandoned-intent
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

type IntentProduct = {
  product_name: string;
  views_7d: number;
  carts_7d: number;
  purchases_7d: number;
  view_to_cart_pct: number;
  abandon_rate_pct: number;
  leak_point: string;
  leak_label: string;
  exit_sessions: number;
};

type SessionInsights = {
  buyer_avg_events: number;
  nonbuyer_avg_events: number;
  buyer_avg_products_viewed: number;
  nonbuyer_avg_products_viewed: number;
  top_exit_products: { product_name: string; exit_count: number }[];
};

type IntentData = {
  products: IntentProduct[];
  session_insights: SessionInsights;
  headline: string;
};

const LEAK_COLORS: Record<string, string> = {
  browse_to_cart: "#f59e0b",
  cart_to_purchase: "#ef4444",
  none: "#34d399",
};

const LEAK_LABELS: Record<string, string> = {
  browse_to_cart: "Browse leak",
  cart_to_purchase: "Cart leak",
  none: "Healthy",
};

export function AbandonedIntentCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<IntentData>({
    url: `${apiBase}/pro/abandoned-intent`,
    enabled: !!apiBase && !!shop && isProUser,
    isEmpty: (d) => !d.products || d.products.length === 0,
  });

  if (!isProUser) return null;

  if (state === "loading") {
    return <CardSkeleton label="Loading your abandoned-intent report" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Abandoned-intent report unavailable"
        message="We couldn't load this week's abandoned-intent report. The underlying visitor data is safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="amber"
        title="No abandoned intent yet"
        body="Once enough visitors view a product and drop off before buying, we'll show you exactly which products are leaking the most money and where in the funnel it happens."
        eta="Needs ~10 visits per product"
      />
    );
  }

  const si = data.session_insights;
  const topProducts = data.products.slice(0, 5);
  const worst = topProducts[0];
  const browseLeaks = data.products.filter((p) => p.leak_point === "browse_to_cart").length;
  const cartLeaks = data.products.filter((p) => p.leak_point === "cart_to_purchase").length;
  const buyerDepth = si?.buyer_avg_products_viewed ?? 0;
  const nonbuyerDepth = si?.nonbuyer_avg_products_viewed ?? 0;

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open abandoned-intent details — ${topProducts.length} products leaking, worst is ${worst?.product_name ?? "unknown"}`}
        onClick={() => setDrawerOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setDrawerOpen(true);
          }
        }}
        className="group cursor-pointer rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6 transition-shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-[#e8a04e] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220] hover:border-white/[0.12]"
      >
        <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-[#e8a04e]">
          Abandoned intent
        </div>
        <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
          Where intent dies
        </h3>
        <p className="mt-2 text-[14px] leading-relaxed text-slate-400">{data.headline}</p>

        {/* Buyer vs non-buyer comparison — kept on the card because it's the
            single most powerful diagnostic at a glance: how much do real
            buyers look at compared to the ones who leave? */}
        {si && buyerDepth > 0 && (
          <div className="mt-5 grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-emerald-400/15 bg-emerald-500/[0.05] px-4 py-3">
              <div className="text-[10px] font-bold uppercase tracking-wider text-emerald-400">Buyers look at</div>
              <div className="mt-1 text-[22px] font-extrabold tabular-nums text-emerald-300">
                {buyerDepth.toFixed(1)}{" "}
                <span className="text-[12px] font-semibold text-emerald-400/80">products</span>
              </div>
              <div className="mt-1 text-[11px] text-emerald-400/70">before they buy</div>
            </div>
            <div className="rounded-xl border border-slate-400/15 bg-slate-500/[0.05] px-4 py-3">
              <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Non-buyers look at</div>
              <div className="mt-1 text-[22px] font-extrabold tabular-nums text-slate-300">
                {nonbuyerDepth.toFixed(1)}{" "}
                <span className="text-[12px] font-semibold text-slate-400/80">products</span>
              </div>
              <div className="mt-1 text-[11px] text-slate-400/70">before they leave</div>
            </div>
          </div>
        )}

        {/* Products with highest abandoned intent */}
        <div className="mt-5 space-y-2">
          {topProducts.map((p) => {
            const leakColor = LEAK_COLORS[p.leak_point] || "#94a3b8";
            const leakLabel = LEAK_LABELS[p.leak_point] || "Signal";
            return (
              <div
                key={p.product_name}
                className="flex items-center gap-3 rounded-xl border border-white/[0.05] bg-white/[0.015] px-4 py-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-semibold text-slate-200">{p.product_name}</div>
                  <div className="mt-0.5 text-[11px] tabular-nums text-slate-500">
                    {p.views_7d} views · {p.carts_7d} carts · {p.purchases_7d} sales
                  </div>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <span className="text-[15px] font-extrabold tabular-nums text-amber-300">
                    {p.abandon_rate_pct.toFixed(0)}%
                  </span>
                  <span
                    className="rounded-full px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider"
                    style={{ color: leakColor, background: leakColor + "18" }}
                  >
                    {leakLabel}
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        <div className="mt-4 text-[11px] font-semibold text-slate-500">
          Click for full breakdown and next action →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="💔"
        title="Where intent dies"
        subtitle="Products your visitors wanted but didn't buy"
      >
        <DrawerExplainer
          body={
            "These are the products that got real attention from your visitors but failed to convert. " +
            "Every product on this list has people viewing it — they're not invisible — yet the funnel " +
            "breaks before the purchase. The list is sorted by how much money you're leaving on the table."
          }
          why={
            "Traffic you already paid for is walking away with their wallet still in their pocket. " +
            "Fixing one bottleneck on this list is almost always cheaper than buying more traffic to " +
            "replace the lost intent."
          }
        />

        {worst && (
          <DrawerBigStat
            label="Biggest leak this week"
            value={`${worst.abandon_rate_pct.toFixed(0)}%`}
            sublabel={`${worst.product_name} · ${worst.views_7d} views · ${worst.carts_7d} carts · ${worst.purchases_7d} sales`}
            color="#f59e0b"
          />
        )}

        <DrawerKeyValueList
          items={[
            {
              label: "Products leaking intent",
              value: `${data.products.length}`,
              color: data.products.length > 0 ? "#f59e0b" : "#94a3b8",
            },
            {
              label: "Browse-stage leaks",
              value: `${browseLeaks}`,
              color: browseLeaks > 0 ? "#f59e0b" : "#94a3b8",
            },
            {
              label: "Cart-stage leaks",
              value: `${cartLeaks}`,
              color: cartLeaks > 0 ? "#ef4444" : "#94a3b8",
            },
            {
              label: "Buyer depth",
              value: `${buyerDepth.toFixed(1)} products`,
            },
            {
              label: "Non-buyer depth",
              value: `${nonbuyerDepth.toFixed(1)} products`,
            },
          ]}
        />

        <DrawerSectionHeading>Top 5 leaks, in order</DrawerSectionHeading>
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {topProducts.map((p, i) => {
            const leakColor = LEAK_COLORS[p.leak_point] || "#94a3b8";
            const leakLabel = LEAK_LABELS[p.leak_point] || "Signal";
            return (
              <div
                key={p.product_name}
                style={{
                  padding: "12px 14px",
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
                    background: "rgba(232,160,78,0.12)",
                    color: "#e8a04e",
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
                <div style={{ flex: 1, minWidth: 0 }}>
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
                    {p.product_name}
                  </div>
                  <div style={{ color: "#64748b", fontSize: "11px", marginTop: "2px" }}>
                    {p.views_7d} views · {p.carts_7d} carts · {p.purchases_7d} sales
                  </div>
                </div>
                <div style={{ textAlign: "right", flexShrink: 0 }}>
                  <div
                    style={{
                      color: "#fbbf24",
                      fontWeight: 800,
                      fontSize: "16px",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {p.abandon_rate_pct.toFixed(0)}%
                  </div>
                  <div
                    style={{
                      color: leakColor,
                      fontSize: "9px",
                      fontWeight: 700,
                      textTransform: "uppercase",
                      letterSpacing: "0.05em",
                      marginTop: "2px",
                    }}
                  >
                    {leakLabel}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <DrawerHowCalculated
          formula="For each product we take the 7-day view count, the 7-day cart count, and the 7-day purchase count. Abandon rate is 1 minus the fraction of viewers who end up buying. Products are only listed when they have enough views to be statistically real, so one-off clicks don't pollute the list."
          inputs={[
            { label: "Products analyzed", value: `${data.products.length}` },
            {
              label: "Browse → cart drops",
              value: `${browseLeaks} product${browseLeaks === 1 ? "" : "s"}`,
            },
            {
              label: "Cart → purchase drops",
              value: `${cartLeaks} product${cartLeaks === 1 ? "" : "s"}`,
            },
          ]}
          note="Leak stage tells you where to look: browse-stage leaks usually mean the product page itself isn't convincing; cart-stage leaks usually mean shipping, price, or checkout friction is the problem."
        />

        {worst && (
          <DrawerNextAction
            headline="Start here"
            primary={{
              label: `Fix ${worst.product_name}`,
              description:
                worst.leak_point === "browse_to_cart"
                  ? "This product is losing visitors at the product page itself. Check the photos, price, description, and stock availability — the page isn't closing the sale."
                  : worst.leak_point === "cart_to_purchase"
                  ? "This product makes it to the cart but loses visitors before checkout. Shipping cost, unexpected fees, or checkout friction are the usual suspects."
                  : "Review the product page end-to-end and compare it to a product that's converting well for you.",
              onClick: () => setDrawerOpen(false),
            }}
          />
        )}
      </DetailDrawer>
    </>
  );
}
