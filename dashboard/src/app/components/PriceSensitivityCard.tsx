"use client";

/**
 * PriceSensitivityCard — "Price Elasticity"
 *
 * Shows CVR by price band and products with price barrier signals.
 *
 * Data source: GET /pro/price-sensitivity
 */

import { useEffect, useState } from "react";

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
  const [data, setData] = useState<PriceSensData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) { setLoading(false); return; }
    let active = true;
    fetch(`${apiBase}/pro/price-sensitivity`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((j) => { if (active) setData(j); })
      .catch(() => {})
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiBase, shop, isProUser]);

  if (!isProUser || loading) return null;
  if (!data || !data.bands?.length) return null;

  const maxCvr = Math.max(...data.bands.map((b) => b.cvr_pct), 1);

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
        Price Sensitivity
      </div>
      <h3 className="text-[15px] font-bold text-white">Conversion by price band</h3>
      <p className="mt-0.5 text-[11px] text-slate-500">{data.headline}</p>

      {/* Price band bars */}
      <div className="mt-4 space-y-2">
        {data.bands.map((b) => {
          const barWidth = Math.max(5, (b.cvr_pct / maxCvr) * 100);
          const isSweet = b.cvr_pct === Math.max(...data.bands.map((x) => x.cvr_pct));
          return (
            <div key={b.band} className="flex items-center gap-3">
              <div className="w-16 text-right text-[11px] font-medium text-slate-400">{b.band}</div>
              <div className="flex-1">
                <div className="h-5 overflow-hidden rounded-md bg-white/[0.04]">
                  <div
                    className="h-full rounded-md transition-all duration-700"
                    style={{
                      width: `${barWidth}%`,
                      background: isSweet ? "#34d399" : "#7c3aed",
                    }}
                  />
                </div>
              </div>
              <div className="w-14 text-right">
                <span className={`text-[12px] font-bold tabular-nums ${isSweet ? "text-emerald-400" : "text-slate-300"}`}>
                  {b.cvr_pct.toFixed(1)}%
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Barrier products */}
      {data.products.length > 0 && (
        <>
          <div className="mt-4 mb-2 text-[10px] font-bold uppercase tracking-[0.14em] text-amber-400">
            Price barrier detected
          </div>
          {data.products.slice(0, 3).map((p) => (
            <div key={p.product_name} className="mb-1.5 rounded-xl border border-amber-400/10 bg-amber-500/[0.03] px-3 py-2">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-semibold text-slate-200 truncate">{p.product_name}</span>
                <span className="text-[11px] font-bold text-amber-300">€{p.price.toFixed(0)}</span>
              </div>
              <p className="mt-0.5 text-[10px] text-amber-400/70">
                {p.views_7d} views, {p.cvr_pct.toFixed(1)}% CVR — high interest but visitors aren't buying
              </p>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
