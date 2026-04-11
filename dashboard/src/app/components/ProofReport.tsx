"use client";

import { useEffect, useState } from "react";
import { apiClient, getHeaders, type paths } from "../lib/api-client";

// Source of truth: GET /pro/proof-report → ProofReportResponse.
// Regenerate via `npm run api:types` after backend changes.
type ProofReportData =
  paths["/pro/proof-report"]["get"]["responses"]["200"]["content"]["application/json"];

function formatRevenue(v: number, currency: string): string {
  if (v >= 10000) return `${currency} ${(v / 1000).toFixed(1)}k`;
  if (v >= 1000) return `${currency} ${Math.round(v).toLocaleString()}`;
  return `${currency} ${v.toFixed(0)}`;
}

function formatPct(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return "\u2014";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

function shortProduct(url: string): string {
  if (url.startsWith("/products/")) {
    return url
      .slice(10)
      .replace(/-/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase())
      .slice(0, 30);
  }
  return url.length > 30 ? url.slice(0, 28) + "\u2026" : url;
}

function confidenceColor(level: string): string {
  if (level === "strong") return "text-emerald-300 bg-emerald-500/15 border-emerald-400/30";
  if (level === "moderate") return "text-amber-300 bg-amber-500/10 border-amber-400/25";
  if (level === "early") return "text-slate-300 bg-slate-500/10 border-slate-400/20";
  return "text-slate-500 bg-slate-500/5 border-slate-600/20";
}

function liftColor(lift: number | null | undefined): string {
  if (lift == null) return "text-slate-400";
  if (lift > 5) return "text-emerald-300";
  if (lift >= 0) return "text-amber-300";
  return "text-rose-300";
}

export function ProofReport({
  apiBase,
  shop,
  apiHeaders,
}: {
  apiBase: string;
  shop: string;
  apiHeaders: () => HeadersInit;
}) {
  const [data, setData] = useState<ProofReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [showNudges, setShowNudges] = useState(false);

  useEffect(() => {
    if (!shop) return;
    let active = true;

    async function load() {
      try {
        setLoading(true);
        const res = await apiClient.GET("/pro/proof-report", {
          params: { query: { window_hours: 168 } },
          headers: getHeaders(apiHeaders),
        });
        if (active && res.data != null) setData(res.data);
      } catch {
        /* silent */
      } finally {
        if (active) setLoading(false);
      }
    }

    load();
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop]);

  if (loading) {
    return (
      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5 animate-pulse">
        <div className="h-4 w-48 rounded bg-white/[0.05]" />
        <div className="mt-3 h-3 w-full rounded bg-white/[0.03]" />
      </div>
    );
  }

  if (!data || !data.has_proof) return null;

  const { confidence, holdout_proof: hp, action_proof: ap, currency } = data;
  const totalRev = data.total_incremental_revenue;
  const liftPct = hp.lift_pct;
  const showRevenue = totalRev > 0 && (confidence.level === "strong" || confidence.level === "moderate");

  return (
    <div className="rounded-2xl border border-emerald-400/[0.14] bg-gradient-to-br from-emerald-950/20 via-[#0a0f1a] to-[#09091a] p-5">
      {/* Header row — internal Pro badge removed (parent owns Pro context) */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-emerald-400/60">
            Proof of Impact
          </span>
        </div>
        <span
          className={`rounded-full border px-2.5 py-0.5 text-[10px] font-semibold ${confidenceColor(confidence.level)}`}
        >
          {confidence.label}
        </span>
      </div>

      {/* Hero metric */}
      <div className="mt-4 flex items-end gap-6">
        {showRevenue && (
          <div>
            <div className="text-[36px] font-bold leading-none tracking-tight text-emerald-300">
              +{formatRevenue(totalRev, currency)}
            </div>
            <div className="mt-1 text-[12px] text-emerald-400/60">
              incremental revenue this week
            </div>
          </div>
        )}
        {liftPct != null && liftPct !== 0 && (
          <div>
            <div className={`text-[24px] font-bold leading-none tabular-nums ${liftColor(liftPct)}`}>
              {formatPct(liftPct)}
            </div>
            <div className="mt-1 text-[12px] text-slate-500">conversion lift vs control</div>
          </div>
        )}
      </div>

      {/* Headline + detail */}
      <div className="mt-4 rounded-xl border border-emerald-400/10 bg-emerald-500/[0.04] px-4 py-3">
        <p className="text-[14px] font-semibold text-white">{data.headline}</p>
        <p className="mt-1.5 text-[13px] leading-[1.5] text-slate-300">{data.detail}</p>
      </div>

      {/* Exposed vs Control CVR (holdout data) */}
      {hp.has_data && (
        <div className="mt-3 grid grid-cols-2 gap-2">
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
            <div className="text-[10px] uppercase tracking-[0.1em] text-slate-600">
              Nudge Recipients
            </div>
            <div className="mt-0.5 text-[14px] font-semibold tabular-nums text-white">
              {(hp.pooled_exposed_cvr * 100).toFixed(2)}% CVR
            </div>
            <div className="text-[10px] text-slate-600">{hp.total_exposed.toLocaleString()} visitors</div>
          </div>
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
            <div className="text-[10px] uppercase tracking-[0.1em] text-slate-600">
              Control Group
            </div>
            <div className="mt-0.5 text-[14px] font-semibold tabular-nums text-white">
              {(hp.pooled_holdout_cvr * 100).toFixed(2)}% CVR
            </div>
            <div className="text-[10px] text-slate-600">{hp.total_holdout.toLocaleString()} visitors</div>
          </div>
        </div>
      )}

      {/* Action improvements */}
      {ap.improvements_count > 0 && (
        <div className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
          <div className="text-[11px] font-semibold text-slate-400">
            {ap.improvements_count} action{ap.improvements_count !== 1 ? "s" : ""} improved results
          </div>
          {ap.improvements.slice(0, 2).map((imp, i) => (
            <p key={`imp-${i}`} className="mt-1 text-[12px] text-slate-300">
              {imp.summary}
            </p>
          ))}
        </div>
      )}

      {/* Per-nudge breakdown */}
      {hp.nudges.length > 0 && (
        <div className="mt-3">
          <button
            onClick={() => setShowNudges((x) => !x)}
            className="flex items-center gap-1.5 text-[11px] text-slate-500 hover:text-slate-300 transition-colors"
          >
            <svg
              className={`h-3.5 w-3.5 transition-transform ${showNudges ? "rotate-90" : ""}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
            {showNudges ? "Hide" : "Show"} nudge breakdown ({hp.nudges.length})
          </button>

          {showNudges && (
            <div className="mt-2 space-y-2">
              {hp.nudges.map((n) => (
                <div
                  key={`np-${n.nudge_id}`}
                  className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5"
                >
                  <div className="flex items-center justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[12px] font-medium text-white">
                        {shortProduct(n.product_url)}
                      </div>
                      <div className="mt-0.5 text-[10px] text-slate-500">
                        {n.exposed_count.toLocaleString()} exposed &middot;{" "}
                        {n.holdout_count.toLocaleString()} control
                      </div>
                    </div>
                    <div className="ml-3 text-right">
                      <div className={`text-[13px] font-semibold tabular-nums ${liftColor(n.lift_pct)}`}>
                        {formatPct(n.lift_pct)}
                      </div>
                      {n.incremental_revenue > 0 && (
                        <div className="text-[11px] text-emerald-300/70">
                          +{formatRevenue(n.incremental_revenue, n.currency)}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Trust note */}
      <p className="mt-3 text-[10px] leading-[1.5] text-slate-600">{data.trust_note}</p>
    </div>
  );
}
