"use client";

import { useEffect, useState } from "react";

type Improvement = {
  product_url: string;
  action_type: string;
  summary: string;
  delta_cvr: number | null;
  delta_revenue: number | null;
  measured_at: string | null;
};

type ProofData = {
  actions_measured: number;
  improvements: Improvement[];
  total_revenue_delta: number;
};

export function ActionProof({
  apiBase,
  shop,
}: {
  apiBase: string;
  shop: string;
}) {
  const [data, setData] = useState<ProofData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!shop || !apiBase) return;
    let active = true;
    setLoading(true);

    fetch(`${apiBase}/actions/proof?shop=${encodeURIComponent(shop)}`, {
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      cache: "no-store",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => {
        if (active) setData(json);
      })
      .catch(() => {})
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [apiBase, shop]);

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
        <div className="h-4 w-40 rounded bg-white/[0.06]" />
        <div className="mt-3 h-12 rounded bg-white/[0.04]" />
      </div>
    );
  }

  // No proof data yet — show strong empty state
  if (!data || data.actions_measured === 0) {
    return (
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] px-5 py-4">
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-emerald-300/60">
          Proof of Impact
        </div>
        <p className="mt-2 text-[12px] leading-relaxed text-slate-500">
          When you take action on a signal, Hedge Spark captures the baseline
          metrics and measures the result 7 days later. Your first
          before-and-after report will appear here.
        </p>
      </div>
    );
  }

  const hasImprovements = data.improvements.length > 0;
  const revDelta = data.total_revenue_delta;

  return (
    <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/[0.04] p-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-emerald-300/70">
            Proof of Impact
          </div>
          <div className="mt-0.5 text-[11px] text-slate-500">
            {data.actions_measured} action{data.actions_measured !== 1 ? "s" : ""} measured
          </div>
        </div>
        {revDelta > 0 && (
          <div className="text-right">
            <div className="text-[18px] font-bold tabular-nums text-emerald-300">
              +${revDelta.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
            </div>
            <div className="text-[10px] text-emerald-400/60">revenue delta</div>
          </div>
        )}
      </div>

      {/* Improvements */}
      {hasImprovements && (
        <div className="mt-4 space-y-2">
          {data.improvements.slice(0, 3).map((imp, i) => (
            <div
              key={i}
              className="rounded-lg border border-emerald-500/10 bg-emerald-500/[0.03] px-3 py-2"
            >
              <div className="text-[12px] font-medium text-slate-300">
                {imp.summary}
              </div>
              {imp.delta_revenue !== null && imp.delta_revenue > 0 && (
                <div className="mt-1 text-[11px] text-emerald-400/80">
                  +${imp.delta_revenue.toLocaleString(undefined, { minimumFractionDigits: 2 })} revenue
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {!hasImprovements && (
        <p className="mt-3 text-[12px] text-slate-500">
          {data.actions_measured} action{data.actions_measured !== 1 ? "s" : ""} measured so far —
          improvements will appear here when conversion rates change.
        </p>
      )}
    </div>
  );
}
