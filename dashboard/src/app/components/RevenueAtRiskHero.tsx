"use client";

/**
 * RevenueAtRiskHero — the new #1 headline of the Pro dashboard.
 *
 * One big loss number: "€X at risk this month" with a 5-way breakdown
 * (abandoned carts, refund trend, nudge gap, peer underperformance,
 * goal gap) plus the counter-punch: "HedgeSpark already prevented €Y".
 *
 * Data source: GET /pro/revenue-at-risk (cached 5 min server-side).
 * No LLM, no fancy interactions — just a clean hero with drill-down.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";

// Payload shape matches app.api.revenue_at_risk.RARSResponse — we keep
// a local type alias because this endpoint is not yet in api-types.ts.
type RARSComponent = {
  source: string;
  loss_eur: number;
  narrative: string;
  evidence?: Record<string, unknown>;
};

type RARSData = {
  shop_domain: string;
  total_at_risk_eur: number;
  prevented_eur_this_month: number;
  net_roi_eur: number;
  components: RARSComponent[];
  // Shop's native currency — `_eur`-suffixed fields are in this currency.
  currency?: string;
  generated_at: string | null;
  headline: string | null;
};

// Human-readable labels for each loss source. Copy is intentionally
// idiot-proof: a merchant who never saw the product should still
// understand what they are looking at.
const SOURCE_LABELS: Record<string, { label: string; icon: string }> = {
  abandoned_high_intent: { label: "People who almost bought", icon: "🛒" },
  refund_decline:        { label: "Products losing traction",  icon: "📉" },
  nudge_gap:             { label: "Nudges underperforming",    icon: "💬" },
  below_benchmark:       { label: "Below similar shops",       icon: "📊" },
  goal_gap:              { label: "Below your target",         icon: "🎯" },
};

import { formatMoneyCompact } from "@/app/app/_lib/formatters";

// Currency-aware formatter. RARS payload contains `currency` (native
// ISO code). Pass it through so a USD merchant sees "$1,234 at risk",
// a GBP merchant sees "£1,234", etc.
function fmtMoney(n: number, currency?: string): string {
  return formatMoneyCompact(n, currency || "USD");
}

export function RevenueAtRiskHero({
  apiBase,
  shop,
  isProUser,
  onUpgrade,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
  onUpgrade?: () => void;
}) {
  const [data, setData] = useState<RARSData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!shop || !apiBase) {
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    setError(false);

    // Both Lite and Pro fetch the same endpoint. Backend filters
    // components array to [] for non-Pro — we reflect that in the UI.
    apiClient
      .GET("/pro/revenue-at-risk")
      .then(({ data: json, error: err }) => {
        if (!active) return;
        if (err || !json) setError(true);
        else setData(json as unknown as RARSData);
      })
      .finally(() => { if (active) setLoading(false); });

    return () => { active = false; };
  }, [apiBase, shop]);

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <div className="h-3 w-40 rounded bg-white/[0.06]" />
        <div className="mt-3 h-12 w-60 rounded bg-white/[0.06]" />
        <div className="mt-4 grid grid-cols-5 gap-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="h-16 rounded bg-white/[0.04]" />
          ))}
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="rounded-2xl border border-amber-400/20 bg-amber-500/[0.05] px-5 py-4">
        <span className="text-[12px] text-amber-300">
          Revenue at Risk unavailable right now. Refreshing…
        </span>
      </div>
    );
  }

  const totalAtRisk = data.total_at_risk_eur || 0;
  const prevented = data.prevented_eur_this_month || 0;
  const netRoi = data.net_roi_eur || 0;
  const hasRisk = totalAtRisk > 0;
  const sortedComponents = [...(data.components || [])].sort(
    (a, b) => b.loss_eur - a.loss_eur,
  );

  return (
    <div className="rounded-2xl border border-white/[0.08] bg-gradient-to-br from-[#0b0b14] via-[#101017] to-[#0b0b14] p-6">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight text-[#e8a04e] sm:text-[2rem]">
            Revenue at risk — how much money is slipping through your store right now
          </h2>
        </div>
        {hasRisk && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex-shrink-0 rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-1 text-[11px] font-semibold text-slate-300 transition-colors hover:border-white/[0.2] hover:text-white"
          >
            {expanded ? "Hide details" : "Show breakdown"}
          </button>
        )}
      </div>

      {/* The hero number */}
      <div className="flex items-end gap-4">
        <div
          className="text-[56px] font-extrabold leading-[0.95] tabular-nums"
          style={{
            color: hasRisk ? "#fbbf24" : "#34d399",
            textShadow: hasRisk ? "0 0 30px rgba(251,191,36,0.25)" : "none",
          }}
        >
          {fmtMoney(totalAtRisk, data?.currency)}
        </div>
        {hasRisk && (
          <div className="mb-2 text-[12px] text-slate-400">at risk</div>
        )}
        {!hasRisk && (
          <div className="mb-2 text-[12px] text-emerald-400">✓ no losses detected</div>
        )}
      </div>

      {/* Narrative line */}
      <p className="mt-2 text-[13px] leading-relaxed text-slate-400">
        {data.headline || "All quiet across tracked signals."}
      </p>

      {/* Prevented + ROI counter-punch — only when prevented > 0.
          Earlier versions rendered this strip whenever netRoi !== 0,
          which ALWAYS evaluated true (netRoi = prevented − 99 for Pro,
          = prevented for Lite) and shipped a misleading "Already
          prevented €0" row on fresh shops. Rule: render the strip only
          when there's real prevented value to show. */}
      {prevented > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-emerald-400/15 bg-emerald-500/[0.04] px-4 py-3">
          <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-emerald-300">
            Already prevented
          </div>
          <div className="text-[20px] font-extrabold tabular-nums text-emerald-400">
            {fmtMoney(prevented, data?.currency)}
          </div>
          {isProUser && (
            <div className="ml-auto text-[11px] text-slate-400">
              Net ROI vs. subscription:
              <span
                className={`ml-1 font-bold tabular-nums ${netRoi >= 0 ? "text-emerald-400" : "text-rose-400"}`}
              >
                {netRoi >= 0 ? "+" : ""}
                {fmtMoney(netRoi, data?.currency)}
              </span>
            </div>
          )}
        </div>
      )}

      {/* Breakdown grid — Pro tier only. For Lite: the upgrade nudge
          replaces the 5-dim drill-down. Backend enforces this by
          returning components=[] for non-Pro plans. */}
      {hasRisk && isProUser && sortedComponents.length > 0 && (
        <div className={`mt-5 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5 ${expanded ? "" : "lg:grid-cols-5"}`}>
          {sortedComponents.map((c) => {
            const meta = SOURCE_LABELS[c.source] || { label: c.source, icon: "•" };
            const hasValue = c.loss_eur > 0;
            return (
              <div
                key={c.source}
                className={`rounded-xl border px-3 py-2.5 transition-colors ${
                  hasValue
                    ? "border-white/[0.08] bg-white/[0.025]"
                    : "border-white/[0.04] bg-white/[0.015] opacity-60"
                }`}
              >
                <div className="flex items-center gap-1.5">
                  <span className="text-[14px]">{meta.icon}</span>
                  <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">
                    {meta.label}
                  </span>
                </div>
                <div className="mt-1 text-[18px] font-extrabold tabular-nums text-white">
                  {fmtMoney(c.loss_eur, data?.currency)}
                </div>
                {expanded && (
                  <div className="mt-1 text-[10px] leading-snug text-slate-400">
                    {c.narrative || "—"}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Lite upgrade bridge — rendered for every Lite merchant, not
          only when hasRisk. A healthy shop (no losses detected) should
          STILL know what Pro unlocks; otherwise the only path to Pro
          disappears from the hero when things look good. Copy adapts:
          if there's risk the merchant sees the drill-down pitch; if
          healthy they see the preventive-deep-analytics pitch. */}
      {!isProUser && (
        <div className="mt-5 flex flex-wrap items-center gap-3 rounded-xl border border-[#d4893a]/20 bg-[#d4893a]/[0.05] px-4 py-3">
          <span className="text-[13px] leading-snug text-slate-300">
            {hasRisk
              ? "Pro unlocks the 5-dimension breakdown: abandoned carts, refund trend, nudge gap, peer benchmark, goal gap — each with its action plan."
              : "Pro adds the diagnostic layer: causal lift holdouts, peer benchmarks, and the 5-dim drill-down that surfaces risk before it compounds."}
          </span>
          {onUpgrade && (
            <button
              type="button"
              onClick={onUpgrade}
              className="ml-auto flex-shrink-0 rounded-lg bg-[#d4893a] px-4 py-1.5 text-[12px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-[#e8a04e]"
            >
              {hasRisk ? "See breakdown on Pro" : "See Pro"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
