"use client";

/**
 * PriceSensitivityCard — "Price Elasticity"
 *
 * Shows which price bands convert best and flags products whose price
 * may be acting as a purchase barrier (high view traffic, low cart rate).
 * The merchant gets two signals in one card: where the sweet spot is,
 * and which specific products are sitting above it.
 *
 * Data source: GET /pro/price-sensitivity
 */

import { useState } from "react";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import { currencySymbol } from "@/app/app/_lib/formatters";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
  DrawerHowCalculated,
  DrawerNextAction,
} from "./DetailDrawer";

type PriceBand = {
  band: string;
  products: number;
  views: number;
  cvr_pct: number;
  cart_rate_pct: number;
};

type BarrierProduct = {
  product_name: string;
  price: number;
  views_7d: number;
  cvr_pct: number;
  price_barrier_gap: number;
  interest_score: number;
  signal: string;
};

type PriceSensData = {
  bands: PriceBand[];
  products: BarrierProduct[];
  headline: string;
  // Shop's native currency — product.price is in this currency.
  // Band labels are already native-aware on the backend (the price_
  // sensitivity service renders bucket labels with the shop symbol
  // at compute time).
  currency?: string;
};

export function PriceSensitivityCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<PriceSensData>({
    url: `${apiBase}/pro/price-sensitivity`,
    enabled: !!apiBase && !!shop && isProUser,
    isEmpty: (d) => !d.bands || d.bands.length === 0,
  });

  if (!isProUser) return null;

  if (state === "loading") {
    return <CardSkeleton label="Loading your price sensitivity report" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Price sensitivity report unavailable"
        message="We couldn't load this week's price sensitivity report. Your product catalog is safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="violet"
        title="Not enough data to spot a sweet spot"
        body="To read price sensitivity we group your products into price bands and need enough traffic per band to compare them. Once each band has seen a few dozen visitors we can tell you which price point converts best."
        eta="Needs more visitor traffic per band"
      />
    );
  }

  const allBands = data.bands;
  const maxCvr = Math.max(...allBands.map((b) => b.cvr_pct), 1);
  const bestBand = allBands.reduce((a, b) => (a.cvr_pct >= b.cvr_pct ? a : b), allBands[0]);
  const worstBand = allBands.reduce((a, b) => (a.cvr_pct <= b.cvr_pct ? a : b), allBands[0]);
  const topBarrier = data.products[0];
  const sym = currencySymbol(data.currency);
  const barrierCount = data.products.length;

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open price sensitivity details — sweet spot is ${bestBand.band} at ${bestBand.cvr_pct.toFixed(
          1,
        )}% conversion`}
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
          Price sensitivity
        </div>
        <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
          Your conversion sweet spot
        </h3>
        <p className="mt-2 text-[14px] leading-relaxed text-slate-400">{data.headline}</p>

        {/* Price band bars */}
        <div className="mt-5 space-y-2.5">
          {allBands.map((b) => {
            const barWidth = Math.max(5, (b.cvr_pct / maxCvr) * 100);
            const isSweet = b.band === bestBand.band && b.cvr_pct > 0;
            return (
              <div key={b.band} className="flex items-center gap-3">
                <div className="w-20 flex-shrink-0 text-right text-[12px] font-semibold tabular-nums text-slate-400">
                  {b.band}
                </div>
                <div className="flex-1">
                  <div className="h-6 overflow-hidden rounded-md bg-white/[0.04]">
                    <div
                      className="h-full rounded-md transition-all duration-700"
                      style={{
                        width: `${barWidth}%`,
                        background: isSweet ? "#34d399" : "#7c3aed",
                      }}
                    />
                  </div>
                </div>
                <div className="w-16 flex-shrink-0 text-right">
                  <span
                    className={`text-[14px] font-extrabold tabular-nums ${
                      isSweet ? "text-emerald-400" : "text-slate-300"
                    }`}
                  >
                    {b.cvr_pct.toFixed(1)}%
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Barrier products */}
        {barrierCount > 0 && (
          <>
            <div className="mt-5 mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-amber-400">
              Price barrier detected · {barrierCount} product{barrierCount === 1 ? "" : "s"}
            </div>
            <div className="space-y-2">
              {data.products.slice(0, 3).map((p) => (
                <div
                  key={p.product_name}
                  className="rounded-xl border border-amber-400/15 bg-amber-500/[0.04] px-4 py-3"
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="truncate text-[13px] font-semibold text-slate-200">
                      {p.product_name}
                    </span>
                    <span className="flex-shrink-0 text-[15px] font-extrabold tabular-nums text-amber-300">
                      {sym}{p.price.toFixed(0)}
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] leading-relaxed text-amber-400/70">
                    {p.views_7d} views, {p.cvr_pct.toFixed(1)}% conversion — high interest, visitors
                    aren&apos;t buying
                  </p>
                </div>
              ))}
            </div>
          </>
        )}

        <div className="mt-4 text-[11px] font-semibold text-slate-500">
          Click for the full band breakdown and next action →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="💰"
        title="Price sensitivity"
        subtitle="Which price points your visitors actually convert at"
      >
        <DrawerExplainer
          body={
            "We group all of your products into price bands and measure how often visitors to each " +
            "band end up buying. The band with the highest conversion rate is your sweet spot — the " +
            "price range where your audience is most comfortable committing. Products priced above " +
            "the sweet spot with strong traffic but weak conversion are flagged as price barriers: " +
            "people want them, but the price is in the way."
          }
          why={
            "Moving a single flagged product down into the sweet spot, or bundling it so the effective " +
            "price falls, can unlock a chunk of revenue that's already sitting in your funnel. You " +
            "don't need more visitors — you need to remove the friction between the visitors you " +
            "already have and the products they already like."
          }
        />

        <DrawerBigStat
          label="Sweet spot"
          value={bestBand.band}
          sublabel={`${bestBand.cvr_pct.toFixed(1)}% conversion · ${bestBand.products} product${
            bestBand.products === 1 ? "" : "s"
          } in this band · ${bestBand.views.toLocaleString("en")} visitors seen`}
          color="#10b981"
        />

        <DrawerKeyValueList
          items={[
            {
              label: "Sweet-spot band",
              value: `${bestBand.band} (${bestBand.cvr_pct.toFixed(1)}%)`,
              color: "#10b981",
            },
            {
              label: "Weakest band",
              value: `${worstBand.band} (${worstBand.cvr_pct.toFixed(1)}%)`,
              color: worstBand.cvr_pct < 1 ? "#f43f5e" : "#94a3b8",
            },
            {
              label: "Bands measured",
              value: `${allBands.length}`,
            },
            {
              label: "Price barriers detected",
              value: `${barrierCount}`,
              color: barrierCount > 0 ? "#f59e0b" : "#94a3b8",
            },
          ]}
        />

        {barrierCount > 0 && (
          <>
            <DrawerSectionHeading>Flagged price barriers</DrawerSectionHeading>
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              {data.products.slice(0, 5).map((p, i) => (
                <div
                  key={p.product_name}
                  style={{
                    padding: "12px 14px",
                    borderRadius: "10px",
                    background: "rgba(245,158,11,0.05)",
                    border: "1px solid rgba(245,158,11,0.2)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "flex-start",
                      gap: "12px",
                      marginBottom: "6px",
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
                          color: "#94a3b8",
                          fontSize: "11px",
                          marginTop: "2px",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {p.views_7d} views · {p.cvr_pct.toFixed(1)}% conversion
                      </div>
                    </div>
                    <div
                      style={{
                        color: "#fbbf24",
                        fontWeight: 800,
                        fontSize: "16px",
                        fontVariantNumeric: "tabular-nums",
                        flexShrink: 0,
                      }}
                    >
                      {sym}{p.price.toFixed(0)}
                    </div>
                  </div>
                  <div
                    style={{
                      color: "#cbd5e1",
                      fontSize: "11px",
                      lineHeight: 1.5,
                    }}
                  >
                    Visitors want this product but they&apos;re not buying at this price. Either the
                    sweet spot is lower, or something else on the page is breaking the pitch.
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        <DrawerHowCalculated
          formula="We group products into five equal-width price bands covering your catalog. For each band we compute conversion rate = purchases ÷ views over the last 7 days. The band with the highest rate is the sweet spot. A product is flagged as a price barrier when it's priced above the sweet spot, gets meaningful traffic, and converts below the band average."
          inputs={[
            { label: "Bands analyzed", value: `${allBands.length}` },
            { label: "Sweet-spot band", value: bestBand.band },
            {
              label: "Barrier threshold",
              value: `below band median`,
            },
          ]}
          note={`Price bands are relative to your own catalog, not to industry averages. A ${sym}500 product can be in the sweet spot for one store and a barrier for another — what matters is the distribution inside your own store, not absolute price.`}
        />

        {topBarrier ? (
          <DrawerNextAction
            headline="Start here"
            primary={{
              label: `Review ${topBarrier.product_name}`,
              description: `This product is priced at ${sym}${topBarrier.price.toFixed(
                0,
              )} and getting ${topBarrier.views_7d} views with only ${topBarrier.cvr_pct.toFixed(
                1,
              )}% conversion. Test a temporary discount, bundle it with a popular item, or split-test a lower effective price (free shipping threshold, buy-one-get-one) and measure the lift.`,
              onClick: () => setDrawerOpen(false),
            }}
          />
        ) : (
          <DrawerNextAction
            headline="Lean into what's working"
            primary={{
              label: `Feature the ${bestBand.band} band`,
              description: `Your sweet spot converts at ${bestBand.cvr_pct.toFixed(
                1,
              )}%. Highlight products in this band at the top of your collection pages and in your hero sections — you'll be pushing visitors toward the price point they're already comfortable with.`,
              onClick: () => setDrawerOpen(false),
            }}
          />
        )}
      </DetailDrawer>
    </>
  );
}
