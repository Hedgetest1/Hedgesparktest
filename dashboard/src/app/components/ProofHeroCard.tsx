"use client";

import { useEffect, useState } from "react";
import { apiClient, type paths } from "../lib/api-client";

// Source of truth: GET /actions/proof → ActionProofSummaryResponse.
type ProofData =
  paths["/actions/proof"]["get"]["responses"]["200"]["content"]["application/json"];

function shortProduct(url?: string): string {
  if (!url) return "a product";
  if (url.startsWith("/products/")) {
    return url.slice(10).replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }
  return url.length > 30 ? url.slice(0, 28) + "…" : url;
}

function fmtDelta(value: number): string {
  if (value >= 1000) return `+$${(value / 1000).toFixed(1)}k`;
  return `+$${Math.round(value)}`;
}

export function ProofHeroCard({
  apiBase: _apiBase,
  shop,
}: {
  apiBase: string;
  shop: string;
}) {
  const [data, setData] = useState<ProofData | null>(null);

  useEffect(() => {
    if (!shop) return;
    let active = true;

    apiClient
      .GET("/actions/proof", { params: { query: {} } })
      .then((res) => {
        if (active && res.data != null) setData(res.data);
      })
      .catch(() => {});

    return () => { active = false; };
  }, [shop]);

  // Only render when there are actual improvements
  if (!data || data.improvements.length === 0) return null;

  const top = data.improvements[0];
  const productName = shortProduct(top.product_url);
  const totalDelta = data.total_revenue_delta;
  const deltaCvr = top.delta_cvr;

  // Compute display values
  const cvrPctPoints = deltaCvr != null ? Math.abs(deltaCvr * 100) : null;
  const cvrSign = deltaCvr != null && deltaCvr > 0 ? "+" : "";

  return (
    <div className="hs-fade-up relative overflow-hidden rounded-2xl border border-emerald-400/25 bg-gradient-to-br from-emerald-500/[0.08] via-emerald-500/[0.03] to-transparent p-5 shadow-[0_0_40px_rgba(52,211,153,0.06)]">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-emerald-300/80">
            Proven Impact
          </div>
          <div className="text-[15px] font-semibold text-white">
            Your change worked
          </div>
        </div>
      </div>

      {/* Main metric row */}
      <div className="mt-4 flex items-end gap-6">
        {/* Revenue delta — dominant */}
        {totalDelta > 0 && (
          <div>
            <div className="text-[36px] font-bold leading-none tracking-tight text-emerald-300">
              {fmtDelta(totalDelta)}
            </div>
            <div className="mt-1 text-[12px] text-emerald-400/60">
              revenue recovered
            </div>
          </div>
        )}

        {/* CVR delta */}
        {cvrPctPoints != null && cvrPctPoints > 0 && (
          <div>
            <div className="text-[24px] font-bold leading-none tabular-nums text-white">
              {cvrSign}{cvrPctPoints.toFixed(1)}
              <span className="text-[14px] font-semibold text-slate-400">pp</span>
            </div>
            <div className="mt-1 text-[12px] text-slate-500">
              conversion lift
            </div>
          </div>
        )}
      </div>

      {/* Explanation */}
      <div className="mt-4 rounded-xl border border-emerald-400/10 bg-emerald-500/[0.04] px-4 py-3">
        <p className="text-[13px] leading-[1.5] text-slate-200">
          {top.summary}
        </p>
        <p className="mt-1 text-[12px] text-emerald-300/50">
          {productName}
          {data.improvements.length > 1 && (
            <> &middot; {data.improvements.length - 1} more improvement{data.improvements.length > 2 ? "s" : ""} measured</>
          )}
        </p>
      </div>
    </div>
  );
}
