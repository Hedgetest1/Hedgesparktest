"use client";

/* eslint-disable react-hooks/exhaustive-deps */

/**
 * DemoPreviewCard — Phase Ω'' pre-signup demo.
 *
 * Sits on the public landing page. Visitor types their Shopify domain →
 * /public/preview scrapes /products.json → vertical classifier + ROI
 * estimate → live preview narrative + estimated recovery €.
 *
 * The "no OAuth dance" killer.
 */

import { useState } from "react";
import { t } from "../lib/i18n";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "https://api.hedgesparkhq.com";

type RoiEstimate = {
  avg_price_eur: number;
  vertical_baseline_cvr_pct: number;
  assumed_monthly_visitors: number;
  estimated_monthly_orders: number;
  estimated_monthly_revenue_eur: number;
  estimated_recovery_eur: number;
};

type PreviewResponse = {
  ok: boolean;
  domain?: string;
  products_scanned?: number;
  vertical?: string;
  vertical_display?: string;
  vertical_confidence?: number;
  roi_estimate?: RoiEstimate;
  narrative?: string;
  next_step_cta?: string;
  error?: string;
  hint?: string;
};

// Pre-signup demo: we don't know the shop's currency (Shopify's public
// /products.json doesn't expose it), so we render raw compact numbers
// and let the narrative text add the "in your store's currency" note.
// Passing this through formatDisplayMoney would hardcode a symbol we
// can't verify — honesty wins.
function fmtMoney(n: number): string {
  if (n === 0) return "0";
  const a = Math.abs(n);
  if (a >= 1000) return (a / 1000).toFixed(a >= 10_000 ? 0 : 1) + "k";
  return String(Math.round(a));
}

export function DemoPreviewCard({ installUrl }: { installUrl: string }) {
  const [domain, setDomain] = useState("");
  const [data, setData] = useState<PreviewResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    if (!domain.trim()) return;
    setLoading(true);
    setData(null);
    try {
      const r = await fetch(`${API_BASE}/public/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain: domain.trim() }),
      });
      const j: PreviewResponse = await r.json();
      setData(j);
    } catch {
      setData({ ok: false, error: "network_error" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <section
      className="mx-auto mt-12 max-w-[44rem] rounded-3xl border border-white/[0.08] bg-white/[0.025] p-6 backdrop-blur-sm sm:p-8"
      aria-labelledby="demo-preview-heading"
      role="region"
    >
      <div className="text-center">
        <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.2em] text-[#e8a04e]">
          {t("demo.eyebrow")}
        </div>
        <h2 id="demo-preview-heading" className="text-[20px] font-extrabold text-white sm:text-[24px]">
          {t("demo.title")}
        </h2>
        <p className="mt-2 text-[13px] text-slate-400">
          {t("demo.sub")}
        </p>
      </div>

      <div className="mt-5 flex flex-col gap-2 sm:flex-row">
        <label htmlFor="demo-domain-input" className="sr-only">Shopify store domain</label>
        <input
          id="demo-domain-input"
          type="text"
          inputMode="url"
          autoComplete="off"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder={t("demo.placeholder")}
          aria-label="Enter your Shopify store domain"
          aria-busy={loading}
          className="min-h-[48px] min-w-0 flex-1 rounded-xl border border-white/[0.1] bg-black/30 px-4 py-3 text-[16px] text-white placeholder-slate-500 outline-none focus-visible:border-[#d4893a]/60 focus-visible:ring-2 focus-visible:ring-[#d4893a]/30"
        />
        <button
          onClick={run}
          disabled={loading || !domain.trim()}
          className="min-h-[48px] rounded-xl bg-[#d4893a] px-6 py-3 text-[14px] font-bold text-black hover:bg-[#e8a04e] disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[#d4893a]/60 focus-visible:ring-offset-2 focus-visible:ring-offset-black"
        >
          {loading ? t("demo.button_loading") : t("demo.button")}
        </button>
      </div>

      {data && data.ok && data.roi_estimate && (
        <div className="mt-5 space-y-4" role="status" aria-live="polite">
          <div className="rounded-2xl border border-emerald-400/20 bg-emerald-500/[0.05] p-5">
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <span className="rounded-full bg-emerald-500/15 px-3 py-1 text-[11px] font-bold uppercase tracking-wide text-emerald-300 ring-1 ring-emerald-400/30">
                {data.vertical_display}
              </span>
              <span className="text-[11px] text-slate-400">
                {data.products_scanned} products scanned · {data.domain}
              </span>
            </div>
            <p className="text-[14px] leading-relaxed text-slate-200">{data.narrative}</p>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-3 text-center">
              <div className="text-[10px] uppercase tracking-wide text-slate-500">Avg price</div>
              <div className="mt-1 text-[18px] font-extrabold text-white">{fmtMoney(data.roi_estimate.avg_price_eur)}</div>
            </div>
            <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-3 text-center">
              <div className="text-[10px] uppercase tracking-wide text-slate-500">Est. MRR</div>
              <div className="mt-1 text-[18px] font-extrabold text-white">{fmtMoney(data.roi_estimate.estimated_monthly_revenue_eur)}</div>
            </div>
            <div className="rounded-xl border border-amber-400/20 bg-amber-500/[0.06] p-3 text-center">
              <div className="text-[10px] uppercase tracking-wide text-amber-400">Recoverable</div>
              <div className="mt-1 text-[18px] font-extrabold text-amber-300">{fmtMoney(data.roi_estimate.estimated_recovery_eur)}/mo</div>
            </div>
          </div>

          <a
            href={installUrl}
            className="block rounded-xl border border-[#d4893a]/40 bg-[#d4893a]/15 px-6 py-3.5 text-center text-[14px] font-bold text-[#d4893a] hover:bg-[#d4893a]/25"
          >
            {data.next_step_cta || "Connect your store to see the real numbers →"}
          </a>
        </div>
      )}

      {data && !data.ok && (
        <div
          className="mt-5 rounded-xl border border-rose-400/20 bg-rose-500/[0.05] p-4 text-[13px] text-rose-300"
          role="alert"
          aria-live="assertive"
        >
          <div className="font-bold">Couldn't run the preview.</div>
          <div className="mt-1 text-slate-400">
            {data.error === "invalid_domain" && "That doesn't look like a valid Shopify domain. Try yourstore.myshopify.com."}
            {data.error === "no_products_found" && (data.hint || "We couldn't reach your store's product catalog. Make sure /products.json is public.")}
            {data.error === "rate_limited" && "Too many previews recently for this domain. Wait a minute and try again."}
            {data.error === "network_error" && "Network error — please try again."}
          </div>
        </div>
      )}
    </section>
  );
}
