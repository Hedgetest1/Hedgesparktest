"use client";

/**
 * GatewayProductsCard — "which first-purchase unlocks the highest LTV?"
 *
 * Strada 4 dominance move (2026-04-20). Closes the "deep LTV slicing"
 * gap vs Peel/Lifetimely by surfacing product-level LTV contribution
 * on the Lite tier. Backend service get_product_ltv_contribution
 * already computes this for Pro consumption; new
 * /analytics/cohorts/ltv/products endpoint opens it to Lite.
 *
 * What this card shows:
 *   - Top products ranked by avg buyer LTV
 *   - "Gateway" tag on products where >50% of buyers are first-timers
 *     (i.e. this product IS the first thing people buy). Gateway
 *     products are the highest-leverage acquisition assets — a
 *     merchant should push ad spend, SEO, and bundles TO these
 *     products.
 *   - Repeat rate per product — how sticky is each product's buyer?
 *
 * This is where Peel's "LTV by product" feature has always lived. We
 * now ship it at the €39 tier.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "../app/_lib/formatters";
import type { components } from "../lib/api-types";

type GatewayProductsData = components["schemas"]["GatewayProductsResponse"];
type Product = components["schemas"]["GatewayProductRow"];

function ltvTierColor(ltv: number): string {
  if (ltv >= 500) return "#34d399";
  if (ltv >= 200) return "#fbbf24";
  if (ltv > 0) return "#94a3b8";
  return "#64748b";
}

export function GatewayProductsCard({
  apiBase,
  shop,
  displayCurrency = "USD",
}: {
  apiBase: string;
  shop: string;
  displayCurrency?: "USD" | "EUR";
}) {
  const [data, setData] = useState<GatewayProductsData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;
    apiClient
      .GET("/analytics/cohorts/ltv/products")
      .then(({ data: raw }) => {
        if (!active) return;
        setData((raw as GatewayProductsData) ?? null);
      })
      .catch(() => {
        if (active) setData(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [apiBase, shop]);

  const products = data?.products ?? [];
  const hasData = !loading && products.length > 0;

  // Sort: highest LTV first, but gateway products float a bit since they
  // carry acquisition-strategy weight beyond their raw LTV number.
  const sorted = [...products].sort((a, b) => {
    const aScore = (a.avg_buyer_ltv || 0) + (a.is_gateway ? 50 : 0);
    const bScore = (b.avg_buyer_ltv || 0) + (b.is_gateway ? 50 : 0);
    return bScore - aScore;
  });
  const top = sorted.slice(0, 8);
  const bestGateway = sorted.find((p) => p.is_gateway && (p.avg_buyer_ltv || 0) > 0);

  if (loading) {
    return (
      <div className="rounded-2xl border border-white/[0.05] bg-[#0b0b14]/50 p-8">
        <div className="text-[13px] text-slate-400">Analysing product-level LTV…</div>
      </div>
    );
  }

  if (!hasData) {
    return (
      <div className="rounded-2xl border border-dashed border-white/[0.12] bg-[#0b0b14]/40 p-6">
        <div className="mb-3 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
          <span
            className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-300"
            aria-hidden="true"
          />
          Preview — gateway products
        </div>
        <p className="mb-4 text-[13px] leading-relaxed text-slate-400">
          Once customers make repeat purchases, we&apos;ll rank your
          products by the average lifetime value of their buyers —
          spotlighting &quot;gateway&quot; products (where &gt;50% of buyers are
          first-timers) as the highest-leverage acquisition assets.
        </p>
      </div>
    );
  }

  return (
    <div>
      {bestGateway && (
        <div className="mb-5 rounded-2xl border border-emerald-400/[0.2] bg-emerald-500/[0.04] p-5">
          <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-emerald-300">
            Best gateway product
          </div>
          <div className="mt-2 flex flex-wrap items-baseline gap-4">
            <span className="text-[1.5rem] font-extrabold leading-none text-white">
              {bestGateway.title}
            </span>
            <span className="text-[14px] text-slate-400">
              · {(bestGateway.gateway_rate * 100).toFixed(0)}% of buyers start here ·{" "}
              <span className="text-emerald-300">
                avg LTV {formatMoneyCompact(bestGateway.avg_buyer_ltv, displayCurrency)}
              </span>
            </span>
          </div>
          <p className="mt-3 text-[12.5px] leading-relaxed text-slate-400">
            This is the product to push ads, SEO, and bundles towards —
            acquiring customers through it produces the highest lifetime
            value on your catalog. Landing a new customer on this
            product is worth {formatMoneyCompact(bestGateway.avg_buyer_ltv, displayCurrency)}{" "}
            on average over their lifetime.
          </p>
        </div>
      )}

      <ul className="space-y-2">
        {top.map((p) => {
          const color = ltvTierColor(p.avg_buyer_ltv);
          return (
            <li
              key={p.product || p.title}
              className="flex flex-wrap items-center gap-4 rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 px-4 py-3"
            >
              <div className="min-w-[120px] flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[13.5px] font-semibold text-white">
                    {p.title || "—"}
                  </span>
                  {p.is_gateway && (
                    <span className="flex-shrink-0 rounded-md border border-emerald-400/30 bg-emerald-500/[0.1] px-2 py-0.5 text-[9.5px] font-bold uppercase tracking-wider text-emerald-300">
                      Gateway
                    </span>
                  )}
                </div>
                <div className="mt-0.5 text-[11.5px] text-slate-400">
                  {p.buyer_count} buyer{p.buyer_count !== 1 ? "s" : ""} ·{" "}
                  {(p.buyer_repeat_rate * 100).toFixed(0)}% repeat · starts new{" "}
                  {(p.gateway_rate * 100).toFixed(0)}% of customers
                </div>
              </div>
              <div className="flex flex-shrink-0 items-baseline gap-5 text-right">
                <div>
                  <div className="text-[9.5px] font-semibold uppercase tracking-wider text-slate-500">Avg buyer LTV</div>
                  <div className="text-[14px] font-bold tabular-nums" style={{ color }}>
                    {formatMoneyCompact(p.avg_buyer_ltv, displayCurrency)}
                  </div>
                </div>
                <div>
                  <div className="text-[9.5px] font-semibold uppercase tracking-wider text-slate-500">Avg orders</div>
                  <div className="text-[14px] font-bold tabular-nums text-white">
                    {p.avg_buyer_orders.toFixed(1)}
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      <div className="mt-4 rounded-xl border border-white/[0.04] bg-[#0b0b14]/40 px-4 py-3">
        <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
          How to read this
        </div>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-400">
          <span className="text-emerald-300 font-semibold">Gateway</span>{" "}
          = first-purchase product for &gt;50% of buyers, the acquisition
          workhorse. <span className="text-white font-semibold">Avg buyer LTV</span>{" "}
          = total revenue per buyer of that product across their entire
          history with you. Rank by LTV to see what to push harder.
        </p>
      </div>
    </div>
  );
}
