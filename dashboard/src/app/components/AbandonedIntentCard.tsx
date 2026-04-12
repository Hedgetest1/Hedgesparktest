"use client";

/**
 * AbandonedIntentCard — "Where Intent Dies"
 *
 * Shows products with high interest but low conversion,
 * exit products, and buyer vs non-buyer session patterns.
 *
 * Data source: GET /pro/abandoned-intent
 */

import { useEffect, useState } from "react";

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

export function AbandonedIntentCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [data, setData] = useState<IntentData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) { setLoading(false); return; }
    let active = true;
    fetch(`${apiBase}/pro/abandoned-intent`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((j) => { if (active) setData(j); })
      .catch(() => {})
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiBase, shop, isProUser]);

  if (!isProUser || loading) return null;
  if (!data || !data.products?.length) return null;

  const si = data.session_insights;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
        Abandoned Intent
      </div>
      <h3 className="text-[15px] font-bold text-white">Where intent dies</h3>
      <p className="mt-0.5 text-[11px] text-slate-500">{data.headline}</p>

      {/* Buyer vs non-buyer comparison */}
      {si && si.buyer_avg_products_viewed > 0 && (
        <div className="mt-3 grid grid-cols-2 gap-2">
          <div className="rounded-xl border border-emerald-400/10 bg-emerald-500/[0.04] px-3 py-2">
            <div className="text-[9px] font-bold uppercase text-emerald-400">Buyers avg</div>
            <div className="text-[15px] font-bold text-emerald-300">{si.buyer_avg_products_viewed.toFixed(1)} products</div>
            <div className="text-[10px] text-emerald-400/70">{si.buyer_avg_events.toFixed(0)} events/session</div>
          </div>
          <div className="rounded-xl border border-slate-400/10 bg-slate-500/[0.04] px-3 py-2">
            <div className="text-[9px] font-bold uppercase text-slate-400">Non-buyers avg</div>
            <div className="text-[15px] font-bold text-slate-300">{si.nonbuyer_avg_products_viewed.toFixed(1)} products</div>
            <div className="text-[10px] text-slate-400/70">{si.nonbuyer_avg_events.toFixed(0)} events/session</div>
          </div>
        </div>
      )}

      {/* Products with highest abandoned intent */}
      <div className="mt-3 space-y-1.5">
        {data.products.slice(0, 5).map((p) => {
          const leakColor = LEAK_COLORS[p.leak_point] || "#94a3b8";
          return (
            <div key={p.product_name} className="flex items-center gap-3 rounded-xl border border-white/[0.04] bg-white/[0.015] px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="text-[12px] font-semibold text-slate-200 truncate">{p.product_name}</div>
                <div className="mt-0.5 text-[10px] text-slate-500">
                  {p.views_7d} views · {p.carts_7d} carts · {p.purchases_7d} purchases
                </div>
              </div>
              <div className="flex flex-col items-end gap-0.5">
                <span className="text-[13px] font-bold tabular-nums text-amber-300">{p.abandon_rate_pct.toFixed(0)}%</span>
                <span
                  className="rounded-full px-2 py-0.5 text-[8px] font-bold uppercase"
                  style={{ color: leakColor, background: leakColor + "18" }}
                >
                  {p.leak_point === "browse_to_cart" ? "Browse leak" : p.leak_point === "cart_to_purchase" ? "Cart leak" : "Healthy"}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
