"use client";

/**
 * ProductInsightPanel — floating insight card for a selected product.
 * Extracted from app/page.tsx (Phase Ω⁶ split).
 */

import { useEffect, useState } from "react";
import { formatNumber, currencySymbol } from "../_lib/formatters";

export type TopProductShape = {
  product_id?: string;
  product_name?: string;
  total_views?: number;
  unique_visitors?: number;
  wishlist_adds?: number;
  avg_intent_score?: number;
  intent_level?: string;
};

export type MergedProductRowShape = {
  product_url?: string;
  views_24h?: number;
  avg_dwell_24h?: number | null;
  avg_scroll_24h?: number | null;
  cart_conversions_24h?: number | null;
  engagement_score?: number | null;
  attention_score?: number | null;
  action_suggestion?: string | null;
};

export function ProductInsightPanel({
  product,
  mergedProducts,
  isProUser,
  onClose,
  shopAov,
  shopCurrency,
  aovIsReal,
}: {
  product: TopProductShape | null;
  mergedProducts: MergedProductRowShape[];
  isProUser: boolean;
  onClose: () => void;
  shopAov: number;
  shopCurrency: string;
  aovIsReal: boolean;
}) {
  useEffect(() => {
    if (!product) return;
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [product, onClose]);

  const [entered, setEntered] = useState(false);
  useEffect(() => {
    if (!product) { setEntered(false); return; }
    const id = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(id);
  }, [product]);

  if (!product) return null;

  const merged = mergedProducts.find((m) => {
    const pid = (product.product_id || "").toLowerCase().trim();
    const url = (m.product_url || "").toLowerCase();
    return pid.length > 0 && url.includes(pid);
  }) ?? null;

  const views24h = merged?.views_24h ?? Math.round((product.total_views ?? 0) / 7);
  const aov = shopAov || 50;
  const ccy = shopCurrency || "USD";
  const uplift1  = Math.round(views24h * 0.01 * aov);
  const uplift2  = Math.round(views24h * 0.02 * aov);

  const attScore: number = merged?.attention_score ?? 0;
  const leverage =
    attScore >= 0.70
      ? { label: "High leverage",   cls: "bg-rose-500/15 text-rose-300 ring-1 ring-rose-400/30" }
      : attScore >= 0.40
      ? { label: "Worth testing",   cls: "bg-amber-500/15 text-amber-300 ring-1 ring-amber-400/30" }
      : { label: "Lower priority",  cls: "bg-white/5 text-slate-400 ring-1 ring-white/10" };

  const v    = merged?.views_24h ?? 0;
  const conv = merged?.cart_conversions_24h ?? 0;
  const eng: number  = merged?.engagement_score ?? 0;
  const scrl: number = merged?.avg_scroll_24h ?? 0;
  const explanation =
    merged === null
      ? "Based on visitor intent signals, this product has meaningful conversion potential."
      : eng > 0.8 && conv === 0
      ? "Visitors are showing strong interest but the product is not converting."
      : v > 20 && conv === 0
      ? "Visitors are interested, but the product is not converting."
      : scrl > 70 && conv === 0
      ? "Users are reaching the page content but not taking action."
      : eng > 0.6
      ? "This product is getting strong attention from visitors."
      : "Monitor this product — it is showing early engagement signals.";

  const suggestion =
    merged?.action_suggestion
      ? merged.action_suggestion
      : conv === 0 && eng > 0.6
      ? "Review CTA placement and product page clarity."
      : conv === 0
      ? "Check pricing, urgency, or trust signals on the product page."
      : "Optimise the checkout flow to reduce drop-off.";

  const productLabel = product.product_name || product.product_id || "—";

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/25" onClick={onClose} />

      <div
        className="fixed right-6 top-6 z-50 w-[460px] max-w-[calc(100vw-3rem)] overflow-y-auto rounded-3xl bg-[#09091a]"
        style={{
          maxHeight: "calc(100vh - 48px)",
          border: "1px solid rgba(124,58,237,0.16)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.65), 0 0 0 1px rgba(124,58,237,0.06)",
          transform: entered ? "translateY(0) scale(1)" : "translateY(-8px) scale(0.98)",
          opacity: entered ? 1 : 0,
          transition: "transform 220ms cubic-bezier(0.16,1,0.3,1), opacity 180ms ease",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-white/[0.06] px-6 py-5">
          <div className="min-w-0 flex-1">
            <div className="mb-1">
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${leverage.cls}`}>
                {leverage.label}
              </span>
            </div>
            <h2 className="text-[15px] font-semibold text-white">Revenue Potential</h2>
            <p className="mt-0.5 truncate text-[11px] text-slate-400" title={productLabel}>
              {productLabel}
            </p>
          </div>
          <button
            onClick={onClose}
            className="ml-4 mt-0.5 flex-shrink-0 rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-white/[0.05] hover:text-slate-300"
            aria-label="Close"
          >
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="space-y-5 px-6 py-5">

          <div>
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
              Potential Impact
            </p>
            {isProUser ? (
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-xl border border-emerald-400/[0.15] bg-emerald-500/[0.06] px-4 py-3">
                  <div className="text-[10px] text-slate-400">+1% conversion / day</div>
                  <div className="mt-1 text-[20px] font-semibold tabular-nums text-emerald-300">{currencySymbol(ccy)}{uplift1}</div>
                  <div className="mt-0.5 text-[10px] text-slate-400">
                    {views24h} views × 1% × {ccy} {aov}{!aovIsReal && " (est.)"}
                  </div>
                </div>
                <div className="rounded-xl border border-emerald-400/[0.22] bg-emerald-500/[0.09] px-4 py-3">
                  <div className="text-[10px] text-slate-400">+2% conversion / day</div>
                  <div className="mt-1 text-[20px] font-semibold tabular-nums text-emerald-300">{currencySymbol(ccy)}{uplift2}</div>
                  <div className="mt-0.5 text-[10px] text-slate-400">
                    {views24h} views × 2% × {ccy} {aov}{!aovIsReal && " (est.)"}
                  </div>
                </div>
              </div>
            ) : (
              <div className="rounded-xl border border-violet-400/[0.10] bg-violet-500/[0.05] px-4 py-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-[12px] text-slate-300">This product may be generating untapped revenue.</p>
                    <p className="mt-1 text-[11px] text-slate-400">Upgrade to quantify the opportunity.</p>
                  </div>
                  <span className="flex-shrink-0 rounded-full border border-violet-400/25 bg-violet-500/10 px-2 py-0.5 text-[10px] font-semibold text-violet-400/70">
                    PRO
                  </span>
                </div>
              </div>
            )}
          </div>

          <div>
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
              Why It Matters
            </p>
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <div className="text-[10px] uppercase tracking-[0.1em] text-slate-400">Views 24h</div>
                <div className="mt-1 text-[15px] font-semibold tabular-nums text-white">
                  {merged ? formatNumber(merged.views_24h) : formatNumber(product.total_views)}
                </div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <div className="text-[10px] uppercase tracking-[0.1em] text-slate-400">Avg Dwell</div>
                <div className="mt-1 text-[15px] font-semibold tabular-nums text-white">
                  {merged?.avg_dwell_24h != null ? `${Math.round(merged.avg_dwell_24h)}s` : "—"}
                </div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <div className="text-[10px] uppercase tracking-[0.1em] text-slate-400">Avg Scroll</div>
                <div className="mt-1 text-[15px] font-semibold tabular-nums text-white">
                  {merged?.avg_scroll_24h != null ? `${Math.round(merged.avg_scroll_24h)}%` : "—"}
                </div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <div className="text-[10px] uppercase tracking-[0.1em] text-slate-400">Cart Conv.</div>
                <div className="mt-1 text-[15px] font-semibold tabular-nums text-white">
                  {merged?.cart_conversions_24h != null ? formatNumber(merged.cart_conversions_24h) : "—"}
                </div>
              </div>
            </div>
            <p className="mt-2 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3 text-[12px] leading-[1.6] text-slate-300">
              {explanation}
            </p>
          </div>

          <div>
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
              Suggested Focus
            </p>
            {isProUser ? (
              <div className="rounded-xl border border-violet-400/[0.12] bg-violet-500/[0.06] px-4 py-3.5">
                <p className="text-[12px] leading-[1.6] text-slate-300">{suggestion}</p>
              </div>
            ) : (
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3.5">
                <p className="text-[12px] leading-[1.6] text-slate-400">
                  This product likely needs improvements in conversion elements.
                </p>
              </div>
            )}
          </div>

        </div>
      </div>
    </>
  );
}
