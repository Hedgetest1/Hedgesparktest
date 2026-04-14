"use client";

/**
 * CompareProductsCard — "Compare Two Products"
 *
 * Side-by-side audience segment comparison. Merchant enters two product
 * URLs, sees the hot/warm/cold split, the winner with monetary gap.
 *
 * API: GET /pro/segments/compare?product_a=...&product_b=...
 */

import { useState } from "react";

type SegmentSnapshot = {
  product_url: string;
  hot_visitors: number;
  warm_visitors: number;
  cold_visitors: number;
  hot_cvr_estimate: number | null;
  estimated_revenue_window: number;
  total_active: number;
};

type CompareData = {
  shop_domain: string;
  window_hours: number;
  product_a: SegmentSnapshot;
  product_b: SegmentSnapshot;
  delta: {
    hot_visitors_delta: number;
    revenue_delta_eur: number;
    winner: "A" | "B" | "tie";
    loss_gap_eur: number;
    narrative: string;
  };
  generated_at: string;
};

function fmtMoney(n: number): string {
  return "€" + Math.round(Math.abs(n)).toLocaleString();
}

export function CompareProductsCard({
  apiBase,
  shop: _shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [productA, setProductA] = useState("");
  const [productB, setProductB] = useState("");
  const [data, setData] = useState<CompareData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCompare() {
    if (!productA.trim() || !productB.trim()) {
      setError("Enter both product URLs (e.g. /products/candle).");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const qs = new URLSearchParams({
        product_a: productA.trim(),
        product_b: productB.trim(),
        hours: "72",
      });
      const r = await fetch(`${apiBase}/pro/segments/compare?${qs}`, {
        credentials: "include",
        headers: { "Content-Type": "application/json" },
      });
      if (!r.ok) {
        setError("Compare failed — check the product URLs.");
        return;
      }
      const j = await r.json();
      setData(j);
    } catch {
      setError("Compare failed.");
    } finally {
      setLoading(false);
    }
  }

  if (!isProUser) return null;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-3">
        <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
          Compare Two Products
        </div>
        <h3 className="text-[15px] font-bold text-white">Which product is winning this week?</h3>
      </div>

      <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-[1fr_1fr_auto]">
        <input
          type="text"
          value={productA}
          onChange={(e) => setProductA(e.target.value)}
          placeholder="Product A URL, e.g. /products/red-candle"
          className="rounded-md border border-white/[0.08] bg-[#0b0b14] px-3 py-2 text-[12px] text-slate-200 placeholder-slate-600"
        />
        <input
          type="text"
          value={productB}
          onChange={(e) => setProductB(e.target.value)}
          placeholder="Product B URL, e.g. /products/blue-candle"
          className="rounded-md border border-white/[0.08] bg-[#0b0b14] px-3 py-2 text-[12px] text-slate-200 placeholder-slate-600"
        />
        <button
          type="button"
          onClick={handleCompare}
          disabled={loading}
          className="rounded-md bg-[#d4893a] px-4 py-2 text-[12px] font-bold text-white transition-colors hover:bg-[#e8a04e] disabled:opacity-50"
        >
          {loading ? "…" : "Compare"}
        </button>
      </div>

      {error && <p className="text-[11px] text-rose-400">{error}</p>}

      {data && (
        <div className="mt-4">
          <div className="grid grid-cols-2 gap-3">
            {[data.product_a, data.product_b].map((snap, i) => {
              const label = i === 0 ? "A" : "B";
              const isWinner = data.delta.winner === label;
              return (
                <div
                  key={label}
                  className={`rounded-xl border p-3 ${
                    isWinner
                      ? "border-emerald-400/30 bg-emerald-500/[0.05]"
                      : "border-white/[0.06] bg-white/[0.02]"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-[10px] font-bold uppercase tracking-[0.14em] ${
                        isWinner ? "text-emerald-400" : "text-slate-500"
                      }`}
                    >
                      Product {label}
                      {isWinner && " · winner"}
                    </span>
                  </div>
                  <div className="mt-1 truncate text-[11px] text-slate-400">{snap.product_url}</div>
                  <div className="mt-2 text-[18px] font-extrabold tabular-nums text-white">
                    {fmtMoney(snap.estimated_revenue_window)}
                  </div>
                  <div className="mt-0.5 text-[9px] text-slate-500">
                    {snap.hot_visitors} hot · {snap.warm_visitors} warm · {snap.cold_visitors} cold
                  </div>
                </div>
              );
            })}
          </div>
          <p className="mt-3 text-[12px] leading-relaxed text-slate-400">{data.delta.narrative}</p>
        </div>
      )}
    </div>
  );
}
