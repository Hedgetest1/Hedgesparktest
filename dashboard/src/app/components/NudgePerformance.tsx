"use client";

/**
 * NudgePerformance — per-nudge revenue attribution surface for Pro merchants.
 *
 * Fetches GET /pro/nudges (list) + GET /pro/nudges/{id}/stats (per-nudge)
 * and renders a compact performance card per active nudge showing:
 *   - product + nudge type
 *   - impressions (shown count)
 *   - attributed conversions + revenue
 *   - exposed vs holdout CVR (when holdout is active)
 *   - lift percentage
 *
 * Data is entirely from existing backend endpoints — no new API needed.
 */

import { useEffect, useState } from "react";

type NudgeItem = {
  id: number;
  product_url: string;
  action_type: string;
  status: string;
  copy_variant: string;
  visitor_count: number | null;
  holdout_pct: number;
};

type NudgeStats = {
  shown: number;
  dismissed: number;
  exposed_purchases: number;
  exposed_revenue: number;
  exposed_cvr: number;
  holdout_cvr: number | null;
  lift_pct: number | null;
  currency: string;
};

type NudgeWithStats = NudgeItem & { stats: NudgeStats | null; loading: boolean };

function shortProduct(url: string): string {
  const m = url.match(/\/products\/(.+)/);
  return m ? m[1].replace(/-/g, " ") : url;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return (v * 100).toFixed(1) + "%";
}

function fmtMoney(v: number | null | undefined, currency: string): string {
  if (v == null || v === 0) return "—";
  const sym = currency === "EUR" ? "€" : currency === "GBP" ? "£" : "$";
  return `${sym}${Math.round(v).toLocaleString()}`;
}

export function NudgePerformance({
  apiBase,
  shop,
  apiHeaders,
}: {
  apiBase: string;
  shop: string;
  apiHeaders: () => HeadersInit;
}) {
  const [nudges, setNudges] = useState<NudgeWithStats[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!shop || !apiBase) return;
    let active = true;

    async function load() {
      setLoading(true);
      setError(false);
      try {
        // 1. Fetch nudge list
        const listRes = await fetch(
          `${apiBase}/pro/nudges?status=active&limit=20&shop=${encodeURIComponent(shop)}`,
          { headers: apiHeaders(), credentials: "include", cache: "no-store" }
        );
        if (!listRes.ok) { setError(true); setLoading(false); return; }
        const listJson = await listRes.json();
        const items: NudgeItem[] = listJson.nudges || [];

        if (!active) return;
        if (items.length === 0) { setNudges([]); setLoading(false); return; }

        // Initialize with loading state
        const initial: NudgeWithStats[] = items.map((n) => ({
          ...n,
          stats: null,
          loading: true,
        }));
        setNudges(initial);
        setLoading(false);

        // 2. Fetch stats for each nudge (parallel, max 10)
        const batch = items.slice(0, 10);
        const statsPromises = batch.map(async (n) => {
          try {
            const res = await fetch(
              `${apiBase}/pro/nudges/${n.id}/stats?window_hours=168&shop=${encodeURIComponent(shop)}`,
              { headers: apiHeaders(), credentials: "include", cache: "no-store" }
            );
            if (!res.ok) return { id: n.id, stats: null };
            const json = await res.json();

            const aggregate = json.aggregate || {};
            const attribution = json.attribution || {};
            const holdout = json.holdout_experiment || {};

            return {
              id: n.id,
              stats: {
                shown: aggregate.shown_count || 0,
                dismissed: aggregate.dismissed_count || 0,
                exposed_purchases: attribution.exposed_purchases || 0,
                exposed_revenue: attribution.exposed_revenue || 0,
                exposed_cvr: attribution.exposed_cvr || 0,
                holdout_cvr: holdout.holdout_active ? (holdout.holdout_rpv ?? null) : null,
                lift_pct: holdout.holdout_active ? (holdout.revenue_lift_pct ?? null) : null,
                currency: attribution.currency || "USD",
              } as NudgeStats,
            };
          } catch {
            return { id: n.id, stats: null };
          }
        });

        const results = await Promise.all(statsPromises);
        if (!active) return;

        setNudges((prev) =>
          prev.map((n) => {
            const found = results.find((r) => r.id === n.id);
            return found
              ? { ...n, stats: found.stats, loading: false }
              : { ...n, loading: false };
          })
        );
      } catch {
        if (active) { setError(true); setLoading(false); }
      }
    }

    load();
    return () => { active = false; };
  }, [shop, apiBase]);

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
        <div className="h-3 w-48 rounded bg-white/[0.06]" />
        <div className="mt-3 h-20 rounded bg-white/[0.04]" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-2xl border border-amber-400/20 bg-amber-500/[0.06] px-4 py-3">
        <span className="text-[12px] text-amber-300">Nudge data unavailable</span>
      </div>
    );
  }

  if (nudges.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-white/[0.08] bg-white/[0.02] px-6 py-8 text-center">
        <div className="text-sm text-slate-500">
          No active nudges yet. Nudges are created automatically when hot audience segments are detected, or manually via the AI composer.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {nudges.map((n) => (
        <div
          key={n.id}
          className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4"
        >
          {/* Header */}
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="min-w-0">
              <span className="text-[13px] font-medium capitalize text-white">
                {shortProduct(n.product_url)}
              </span>
              <span className="ml-2 rounded bg-violet-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-violet-400/80">
                {n.action_type.replace(/_/g, " ")}
              </span>
            </div>
            {n.holdout_pct > 0 && (
              <span className="flex-shrink-0 rounded bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.1em] text-emerald-400/70">
                {n.holdout_pct}% holdout
              </span>
            )}
          </div>

          {/* Stats grid */}
          {n.loading ? (
            <div className="h-12 animate-pulse rounded bg-white/[0.04]" />
          ) : n.stats ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div>
                <div className="text-[10px] uppercase text-slate-600">Shown</div>
                <div className="mt-0.5 text-sm font-semibold tabular-nums text-white">
                  {n.stats.shown.toLocaleString()}
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase text-slate-600">Conversions</div>
                <div className="mt-0.5 text-sm font-semibold tabular-nums text-white">
                  {n.stats.exposed_purchases}
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase text-slate-600">Revenue</div>
                <div className="mt-0.5 text-sm font-semibold tabular-nums text-emerald-400">
                  {fmtMoney(n.stats.exposed_revenue, n.stats.currency)}
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase text-slate-600">CVR</div>
                <div className="mt-0.5 text-sm font-semibold tabular-nums text-white">
                  {fmtPct(n.stats.exposed_cvr)}
                </div>
              </div>
            </div>
          ) : (
            <div className="text-[12px] text-slate-600">Stats unavailable</div>
          )}

          {/* Lift row — only when holdout data exists */}
          {n.stats && n.stats.lift_pct != null && (
            <div className="mt-3 flex items-center gap-3 rounded-xl border border-emerald-400/15 bg-emerald-500/[0.05] px-3 py-2">
              <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-emerald-300/60">Lift</span>
              <span className={`text-[13px] font-semibold tabular-nums ${
                n.stats.lift_pct > 0 ? "text-emerald-400" : n.stats.lift_pct < 0 ? "text-rose-400" : "text-slate-400"
              }`}>
                {n.stats.lift_pct > 0 ? "+" : ""}{n.stats.lift_pct.toFixed(1)}%
              </span>
              <span className="text-[11px] text-slate-500">
                vs {fmtPct(n.stats.holdout_cvr)} holdout
              </span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
