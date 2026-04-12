"use client";

/**
 * CausalLiftCard — "Proven Impact"
 *
 * THE competitive claim. Shows causal revenue lift from nudges
 * with holdout-measured statistical confidence.
 *
 * Data source: GET /pro/causal-lift
 */

import { useEffect, useState } from "react";

type CausalData = {
  total_lift_pct: number;
  attributed_revenue_eur: number;
  confidence: number;
  nudges_measured: number;
  exposed_visitors: number;
  holdout_visitors: number;
  detail: string;
};

export function CausalLiftCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [data, setData] = useState<CausalData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) { setLoading(false); return; }
    let active = true;
    fetch(`${apiBase}/pro/causal-lift`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((j) => { if (active) setData(j); })
      .catch(() => {})
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiBase, shop, isProUser]);

  if (!isProUser || loading) return null;
  if (!data || data.nudges_measured === 0) return null;

  const isSignificant = data.confidence >= 80;
  const isPositive = data.total_lift_pct > 0;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
        Proven Impact
      </div>
      <h3 className="text-[15px] font-bold text-white">Causal revenue lift</h3>
      <p className="mt-0.5 text-[11px] text-slate-500">
        Measured with holdout groups — not correlation, causation.
      </p>

      <div className="mt-4 grid grid-cols-3 gap-3">
        {/* Lift */}
        <div className="rounded-xl border border-emerald-400/15 bg-emerald-500/[0.05] px-3 py-3 text-center">
          <div className="text-[9px] font-bold uppercase text-emerald-400">CVR Lift</div>
          <div className={`text-[22px] font-extrabold tabular-nums ${isPositive ? "text-emerald-300" : "text-red-300"}`}>
            {data.total_lift_pct > 0 ? "+" : ""}{data.total_lift_pct.toFixed(1)}%
          </div>
        </div>

        {/* Revenue */}
        <div className="rounded-xl border border-violet-400/15 bg-violet-500/[0.05] px-3 py-3 text-center">
          <div className="text-[9px] font-bold uppercase text-violet-400">Attributed</div>
          <div className="text-[22px] font-extrabold tabular-nums text-violet-300">
            €{data.attributed_revenue_eur >= 1000
              ? (data.attributed_revenue_eur / 1000).toFixed(1) + "k"
              : Math.round(data.attributed_revenue_eur)}
          </div>
        </div>

        {/* Confidence */}
        <div className="rounded-xl border border-amber-400/15 bg-amber-500/[0.05] px-3 py-3 text-center">
          <div className="text-[9px] font-bold uppercase text-amber-400">Confidence</div>
          <div className={`text-[22px] font-extrabold tabular-nums ${isSignificant ? "text-amber-300" : "text-slate-400"}`}>
            {data.confidence}%
          </div>
        </div>
      </div>

      <div className="mt-3 rounded-lg border border-white/[0.04] bg-white/[0.02] px-3 py-2">
        <p className="text-[10px] text-slate-400">
          {data.nudges_measured} nudge{data.nudges_measured > 1 ? "s" : ""} measured · {data.exposed_visitors.toLocaleString()} exposed · {data.holdout_visitors.toLocaleString()} holdout
        </p>
        {isSignificant && isPositive && (
          <p className="mt-1 text-[10px] font-semibold text-emerald-400">
            Statistically significant — your nudges are generating real incremental revenue.
          </p>
        )}
      </div>
    </div>
  );
}
