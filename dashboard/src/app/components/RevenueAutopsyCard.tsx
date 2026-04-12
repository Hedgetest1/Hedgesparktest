"use client";

/**
 * RevenueAutopsyCard — "Why Revenue Changed"
 *
 * Per-product decomposition: traffic delta + conversion delta + value delta.
 * Shows the PRIMARY CAUSE of each product's revenue change.
 *
 * Data source: GET /pro/revenue-autopsy
 */

import { useEffect, useState } from "react";

type AutopsyProduct = {
  product_name: string;
  revenue_delta_eur: number;
  direction: string;
  primary_cause: string;
  narrative: string;
  traffic: { change_pct: number; impact_eur: number };
  conversion: { cvr_recent_pct: number; cvr_prior_pct: number; delta_pp: number; impact_eur: number };
  value: { aov_recent: number; aov_prior: number; change_pct: number; impact_eur: number };
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
};

const CAUSE_COLORS: Record<string, string> = {
  traffic: "#60a5fa",
  conversion: "#f59e0b",
  value: "#a78bfa",
};

const CAUSE_LABELS: Record<string, string> = {
  traffic: "Traffic",
  conversion: "Conversion",
  value: "Value",
};

function fmtEur(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1000) return (n < 0 ? "-" : "+") + "€" + (abs / 1000).toFixed(1) + "k";
  return (n < 0 ? "-" : "+") + "€" + Math.round(abs);
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
  const [data, setData] = useState<AutopsyData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) { setLoading(false); return; }
    let active = true;
    fetch(`${apiBase}/pro/revenue-autopsy`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((j) => { if (active) setData(j); })
      .catch(() => {})
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiBase, shop, isProUser]);

  if (!isProUser) return null;

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
        <div className="h-3 w-40 rounded bg-white/[0.06]" />
        <div className="mt-3 space-y-2">
          {[0, 1, 2].map((i) => <div key={i} className="h-14 rounded bg-white/[0.04]" />)}
        </div>
      </div>
    );
  }

  if (!data || !data.products?.length) return null;

  const declining = data.products.filter((p) => p.direction === "declining");

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
            Revenue Autopsy
          </div>
          <h3 className="text-[15px] font-bold text-white">Why revenue changed</h3>
          <p className="mt-0.5 text-[11px] text-slate-500">{data.headline}</p>
        </div>
        {data.summary.total_loss_per_week > 0 && (
          <div className="flex-shrink-0 rounded-lg border border-red-400/20 bg-red-500/[0.06] px-3 py-2 text-right">
            <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-red-400">Leaking</div>
            <div className="text-[18px] font-extrabold tabular-nums text-red-300">
              -€{Math.round(data.summary.total_loss_per_week)}/wk
            </div>
          </div>
        )}
      </div>

      <div className="space-y-2">
        {data.products.slice(0, 6).map((p) => {
          const causeColor = CAUSE_COLORS[p.primary_cause] || "#94a3b8";
          return (
            <div key={p.product_name} className="rounded-xl border border-white/[0.04] bg-white/[0.015] p-3">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[12px] font-semibold text-slate-200 truncate">
                      {p.product_name}
                    </span>
                    <span
                      className="flex-shrink-0 rounded-full px-2 py-0.5 text-[9px] font-bold uppercase"
                      style={{ color: causeColor, background: causeColor + "20", border: `1px solid ${causeColor}40` }}
                    >
                      {CAUSE_LABELS[p.primary_cause] || p.primary_cause}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[10px] text-slate-500">{p.narrative}</p>
                </div>
                <div className={`flex-shrink-0 text-[14px] font-bold tabular-nums ${p.direction === "declining" ? "text-red-400" : "text-emerald-400"}`}>
                  {fmtEur(p.revenue_delta_eur)}/wk
                </div>
              </div>

              {/* Impact bars */}
              <div className="mt-2 flex gap-1">
                {(["traffic", "conversion", "value"] as const).map((cause) => {
                  const impact = Math.abs(p[cause].impact_eur);
                  const total = Math.abs(p.traffic.impact_eur) + Math.abs(p.conversion.impact_eur) + Math.abs(p.value.impact_eur);
                  const pct = total > 0 ? (impact / total) * 100 : 33;
                  return (
                    <div
                      key={cause}
                      className="h-1.5 rounded-full"
                      style={{
                        width: `${Math.max(5, pct)}%`,
                        background: cause === p.primary_cause ? CAUSE_COLORS[cause] : CAUSE_COLORS[cause] + "40",
                      }}
                    />
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
