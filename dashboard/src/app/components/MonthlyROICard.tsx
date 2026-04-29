"use client";

/**
 * MonthlyROICard — "Your ROI This Month"
 *
 * The retention killer. Shows the monthly math: subscription cost vs.
 * detected at-risk vs. prevented, with a net ROI number the merchant
 * can stare at. No competitor ships this.
 *
 * Data source: GET /pro/roi-report
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";

import { formatMoneyCompact } from "@/app/app/_lib/formatters";

type ROIData = {
  shop_domain: string;
  month: string;
  cost_eur: number;
  at_risk_detected_eur: number;
  prevented_eur: number;
  net_roi_eur: number;
  components: Array<{ source: string; loss_eur: number }>;
  headline: string;
  // Shop's native currency — all `_eur`-suffixed fields are native.
  currency?: string;
  generated_at: string;
};

function fmtMoney(n: number, currency?: string): string {
  return formatMoneyCompact(n, currency || "USD");
}

export function MonthlyROICard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [data, setData] = useState<ROIData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) { setLoading(false); return; }
    let active = true;
    setLoading(true);
    apiClient
      .GET("/pro/roi-report")
      .then(({ data: j, error: err }) => {
        if (!active) return;
        if (err || !j) setData(null);
        else setData(j as unknown as ROIData);
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiBase, shop, isProUser]);

  // Non-Pro merchants don't see this card at all (Pro-tier feature).
  if (!isProUser) return null;

  // Cold-start / loading / fetch-error → render a sample-preview block
  // instead of disappearing silently. Keeps the dashboard visually
  // populated on day-1 + signals what the card will look like once
  // RARS components + prevented-revenue accumulate.
  if (loading || !data) {
    return (
      <div className="rounded-2xl border border-dashed border-white/[0.12] bg-gradient-to-br from-[#0b0b14] via-[#121220] to-[#0b0b14] p-5">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-400">
              Your ROI this month · sample preview
            </div>
            <h3 className="text-[15px] font-bold leading-tight text-slate-200">
              Net ROI populates after the first prevented revenue event
            </h3>
          </div>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/[0.08] px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-emerald-300">
            <span className="relative inline-flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/60" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
            </span>
            Watching
          </span>
        </div>
        <div className="grid grid-cols-3 gap-2 opacity-50">
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
            <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-slate-400">You pay</div>
            <div className="mt-1 text-[18px] font-extrabold tabular-nums text-slate-300">$99</div>
          </div>
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
            <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-slate-400">Prevented</div>
            <div className="mt-1 text-[18px] font-extrabold tabular-nums text-emerald-300">$840</div>
          </div>
          <div className="rounded-xl border border-emerald-400/15 bg-emerald-500/[0.05] p-3">
            <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-emerald-300">Net ROI</div>
            <div className="mt-1 text-[18px] font-extrabold tabular-nums text-emerald-300">+$741</div>
          </div>
        </div>
        <p className="mt-3 text-[12px] leading-relaxed text-slate-400">
          Real numbers populate when RARS detects at-risk revenue and HedgeSpark prevents it via nudges, alerts, or the Night Shift agent.
        </p>
      </div>
    );
  }

  const positiveROI = data.net_roi_eur > 0;
  const roiColor = positiveROI ? "#34d399" : "#f87171";

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-gradient-to-br from-[#0b0b14] via-[#121220] to-[#0b0b14] p-5">
      <div className="mb-3">
        <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
          Your ROI this month · {data.month}
        </div>
        <h3 className="text-[15px] font-bold leading-tight text-white">
          {data.headline}
        </h3>
      </div>

      {/* The 3 numbers that matter */}
      <div className="grid grid-cols-3 gap-2">
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
          <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-slate-400">
            You pay
          </div>
          <div className="mt-1 text-[18px] font-extrabold tabular-nums text-slate-300">
            {fmtMoney(data.cost_eur, data.currency)}
          </div>
        </div>
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
          <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-slate-400">
            Detected at risk
          </div>
          <div className="mt-1 text-[18px] font-extrabold tabular-nums text-amber-300">
            {fmtMoney(data.at_risk_detected_eur, data.currency)}
          </div>
        </div>
        <div className="rounded-xl border border-emerald-400/20 bg-emerald-500/[0.06] p-3">
          <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-emerald-300">
            Prevented
          </div>
          <div className="mt-1 text-[18px] font-extrabold tabular-nums text-emerald-400">
            {fmtMoney(data.prevented_eur, data.currency)}
          </div>
        </div>
      </div>

      {/* The net ROI callout */}
      <div
        className="mt-3 flex items-center justify-between rounded-xl border px-4 py-3"
        style={{
          borderColor: roiColor + "30",
          background: roiColor + "10",
        }}
      >
        <div className="text-[11px] font-bold uppercase tracking-[0.14em]" style={{ color: roiColor }}>
          Net ROI
        </div>
        <div className="text-[22px] font-extrabold tabular-nums" style={{ color: roiColor }}>
          {data.net_roi_eur >= 0 ? "+" : ""}
          {fmtMoney(data.net_roi_eur, data.currency)}
        </div>
      </div>
    </div>
  );
}
