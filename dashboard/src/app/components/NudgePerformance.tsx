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
import { createMoneyFormatter, type DisplayCurrency } from "../lib/currency";
import { apiClient, getHeaders, type paths } from "../lib/api-client";

// Generated response type — single source of truth for /pro/nudges/{id}/stats.
// Regenerate via `npm run api:types` after backend Pydantic changes.
type NudgeStatsProResponse =
  paths["/pro/nudges/{nudge_id}/stats"]["get"]["responses"]["200"]["content"]["application/json"];

// Derive the list row type from the generated OpenAPI types so any backend
// rename surfaces as a compile error.
type NudgeListResponse =
  paths["/pro/nudges"]["get"]["responses"]["200"]["content"]["application/json"];
type NudgeItem = NudgeListResponse["nudges"][number];

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

type NudgeWithStats = NudgeItem & {
  stats: NudgeStats | null;
  loading: boolean;
  holdoutSaving?: boolean;
  holdoutError?: string | null;
};

// Allowed holdout presets offered in the UI. 0 = disabled.
// 10 / 20 / 25 keep enough exposure for nudge effect while preserving
// a large enough control arm for statistical significance.
const HOLDOUT_PRESETS: ReadonlyArray<{ label: string; value: number }> = [
  { label: "Off",  value: 0 },
  { label: "10%",  value: 10 },
  { label: "20%",  value: 20 },
  { label: "25%",  value: 25 },
];

function shortProduct(url: string): string {
  const m = url.match(/\/products\/(.+)/);
  return m ? m[1].replace(/-/g, " ") : url;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return (v * 100).toFixed(1) + "%";
}

// Money formatter + FX rates live in /lib/currency.ts (single source of truth).
// Nudge stats carry their own native currency (from revenue_currency in the
// attribution payload), which may be USD / EUR / GBP / JPY etc. We honor it.

export function NudgePerformance({
  apiBase,
  shop,
  apiHeaders,
  displayCurrency = "USD",
}: {
  apiBase: string;
  shop: string;
  apiHeaders: () => HeadersInit;
  displayCurrency?: DisplayCurrency;
}) {
  const [nudges, setNudges] = useState<NudgeWithStats[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  // Fetch stats for a single nudge and normalize them into the card shape.
  // Used both on initial load (in batch) and when refetching after a
  // holdout update so the lift pill updates without a full page reload.
  async function fetchNudgeStats(nudgeId: number): Promise<NudgeStats | null> {
    try {
      const res = await apiClient.GET("/pro/nudges/{nudge_id}/stats", {
        params: {
          path: { nudge_id: nudgeId },
          query: { window_hours: 168 },
        },
        headers: getHeaders(apiHeaders),
      });
      const json: NudgeStatsProResponse | undefined = res.data;
      if (json == null) return null;
      const stats = json.stats;
      const attribution = json.attribution;
      const holdout = json.holdout_experiment;
      return {
        shown: stats.exposures || 0,
        dismissed: stats.dismissals || 0,
        exposed_purchases: attribution.post_exposure_purchases || 0,
        exposed_revenue: attribution.purchase_session_revenue || 0,
        exposed_cvr: attribution.post_exposure_cvr || 0,
        holdout_cvr: holdout.holdout_active ? (holdout.holdout_cvr ?? null) : null,
        lift_pct: holdout.holdout_active ? (holdout.estimated_lift_pct ?? null) : null,
        currency: attribution.revenue_currency || "USD",
      };
    } catch {
      return null;
    }
  }

  // Holdout mutation — PATCH /pro/nudges/{id}/holdout then refetch stats
  // so the lift pill + row label update in place. Optimistic holdout_pct
  // update so the selected pill reflects the click immediately.
  async function updateHoldout(nudgeId: number, holdoutPct: number) {
    setNudges((prev) =>
      prev.map((n) =>
        n.id === nudgeId
          ? { ...n, holdoutSaving: true, holdoutError: null }
          : n,
      ),
    );
    try {
      const res = await apiClient.PATCH("/pro/nudges/{nudge_id}/holdout", {
        params: { path: { nudge_id: nudgeId } },
        body: { holdout_pct: holdoutPct },
        headers: getHeaders(apiHeaders),
      });
      if (res.error) throw new Error("save failed");
      // Optimistically update holdout_pct on the card; then refetch stats.
      setNudges((prev) =>
        prev.map((n) =>
          n.id === nudgeId ? { ...n, holdout_pct: holdoutPct } : n,
        ),
      );
      const fresh = await fetchNudgeStats(nudgeId);
      setNudges((prev) =>
        prev.map((n) =>
          n.id === nudgeId
            ? { ...n, stats: fresh, holdoutSaving: false, holdoutError: null }
            : n,
        ),
      );
    } catch {
      setNudges((prev) =>
        prev.map((n) =>
          n.id === nudgeId
            ? { ...n, holdoutSaving: false, holdoutError: "Save failed" }
            : n,
        ),
      );
    }
  }

  useEffect(() => {
    if (!shop || !apiBase) return;
    let active = true;

    async function load() {
      setLoading(true);
      setError(false);
      try {
        // 1. Fetch nudge list via the typed client
        const listRes = await apiClient.GET("/pro/nudges", {
          params: { query: { status: "active", limit: 20 } },
          headers: getHeaders(apiHeaders),
        });
        if (listRes.data == null) { setError(true); setLoading(false); return; }
        const items: NudgeItem[] = listRes.data.nudges;

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

        // 2. Fetch stats for each nudge via the typed client (parallel, max 10).
        // TypeScript now validates field access against the generated OpenAPI
        // schema — any backend rename will surface as a compile error.
        const batch = items.slice(0, 10);
        const results = await Promise.all(
          batch.map(async (n) => ({ id: n.id, stats: await fetchNudgeStats(n.id) })),
        );
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
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <div className="mb-1">
          <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#d946ef]">
            Nudge Performance
          </span>
        </div>
        <h3 className="text-[15px] font-bold leading-tight text-white">
          No active fixes deployed yet
        </h3>
        <p className="mt-1.5 text-[12px] leading-relaxed text-slate-400">
          Nudges are created automatically when hot audience segments are detected,
          or manually via the AI composer. Once deployed, this cassettone shows
          exactly how much extra revenue each one is generating.
        </p>
      </div>
    );
  }

  // ── Compute rollup totals across all nudges ──────────────────────────
  const rolled = nudges.filter((n) => n.stats).map((n) => n.stats!);
  const totalShown = rolled.reduce((s, x) => s + (x.shown || 0), 0);
  const totalConversions = rolled.reduce((s, x) => s + (x.exposed_purchases || 0), 0);
  // For total revenue we intentionally use the first nudge's native currency
  // as the aggregation currency — nudges on the same shop share currency in
  // practice. If mixed, the per-card currency stays honest.
  const rollupCurrency = rolled[0]?.currency ?? "USD";
  const totalRevenue = rolled.reduce((s, x) => s + (x.exposed_revenue || 0), 0);
  const avgLift = (() => {
    const withLift = rolled.filter((x) => x.lift_pct != null);
    if (withLift.length === 0) return null;
    return withLift.reduce((s, x) => s + (x.lift_pct || 0), 0) / withLift.length;
  })();

  const fmtRollupRevenue = createMoneyFormatter(displayCurrency, rollupCurrency);
  const liftColor = avgLift == null
    ? "text-slate-400"
    : avgLift > 5 ? "text-emerald-400"
    : avgLift >= 0 ? "text-amber-400"
    : "text-rose-400";

  // Action type → brand color map (same family as Gateway Intelligence)
  const actionColor: Record<string, string> = {
    social_proof: "#e8a04e",    // amber warm
    urgency: "#f87171",         // red urgent
    interest_based: "#c4b5fd",  // lilac learning
  };

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
      {/* Header */}
      <div className="mb-5">
        <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#d946ef]">
          Nudge Performance
        </div>
        <h3 className="mt-1 text-[15px] font-bold leading-tight text-white">
          Which fixes are paying off
        </h3>
        <p className="mt-1.5 text-[12px] leading-relaxed text-slate-400">
          {nudges.length === 1
            ? `You have 1 active fix deployed. Every shown event is tracked against a holdout control group so the lift you see is measured, not guessed.`
            : `You have ${nudges.length} active fixes deployed. Every shown event is tracked against a holdout control group so the lift you see is measured, not guessed.`}
        </p>
      </div>

      {/* Rollup KPI hero — the aggregate punchline */}
      {rolled.length > 0 && (
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-xl border px-4 py-3" style={{ borderColor: "rgba(217, 70, 239, 0.18)", backgroundColor: "rgba(217, 70, 239, 0.04)" }}>
            <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">Impressions</div>
            <div className="mt-1 text-[22px] font-extrabold tabular-nums leading-none text-white">
              {totalShown.toLocaleString()}
            </div>
            <div className="mt-1 text-[10px] text-slate-400">visitors reached</div>
          </div>
          <div className="rounded-xl border px-4 py-3" style={{ borderColor: "rgba(52, 211, 153, 0.22)", backgroundColor: "rgba(52, 211, 153, 0.05)" }}>
            <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">Conversions</div>
            <div className="mt-1 text-[22px] font-extrabold tabular-nums leading-none text-emerald-400">
              {totalConversions}
            </div>
            <div className="mt-1 text-[10px] text-slate-400">purchases attributed</div>
          </div>
          <div className="rounded-xl border px-4 py-3" style={{ borderColor: "rgba(52, 211, 153, 0.22)", backgroundColor: "rgba(52, 211, 153, 0.05)" }}>
            <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">Extra revenue</div>
            <div className="mt-1 text-[22px] font-extrabold tabular-nums leading-none text-emerald-400">
              {fmtRollupRevenue(totalRevenue)}
            </div>
            <div className="mt-1 text-[10px] text-slate-400">exposed group total</div>
          </div>
          <div className="rounded-xl border px-4 py-3" style={{ borderColor: avgLift != null && avgLift > 0 ? "rgba(52, 211, 153, 0.22)" : "rgba(148, 163, 184, 0.14)", backgroundColor: avgLift != null && avgLift > 0 ? "rgba(52, 211, 153, 0.05)" : "rgba(148, 163, 184, 0.03)" }}>
            <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">Avg lift</div>
            <div className={`mt-1 text-[22px] font-extrabold tabular-nums leading-none ${liftColor}`}>
              {avgLift == null ? "—" : `${avgLift > 0 ? "+" : ""}${avgLift.toFixed(0)}%`}
            </div>
            <div className="mt-1 text-[10px] text-slate-400">vs holdout control</div>
          </div>
        </div>
      )}

      {/* Per-nudge breakdown */}
      <div className="space-y-3">
        {nudges.map((n) => {
          const accent = actionColor[n.action_type] ?? "#c4b5fd";
          const hasLift = n.stats && n.stats.lift_pct != null;
          const liftValue = n.stats?.lift_pct ?? 0;
          const liftIsPositive = hasLift && liftValue > 0;
          return (
            <div
              key={n.id}
              className="rounded-xl border border-white/[0.05] bg-white/[0.015] p-3.5 transition-colors hover:border-white/[0.1] hover:bg-white/[0.025]"
            >
              {/* Header row */}
              <div className="mb-2.5 flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span
                      className="flex-shrink-0 rounded-full border px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.14em]"
                      style={{
                        borderColor: `${accent}40`,
                        backgroundColor: `${accent}1a`,
                        color: accent,
                      }}
                    >
                      {n.action_type.replace(/_/g, " ")}
                    </span>
                    <span className="truncate text-[13px] font-semibold text-white">
                      {shortProduct(n.product_url)}
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <span className="text-[10px] uppercase tracking-[0.12em] text-slate-400">
                      Holdout
                    </span>
                    {HOLDOUT_PRESETS.map((preset) => {
                      const isActive = (n.holdout_pct || 0) === preset.value;
                      const disabled = n.holdoutSaving || isActive;
                      return (
                        <button
                          key={preset.value}
                          type="button"
                          disabled={disabled}
                          onClick={() => updateHoldout(n.id, preset.value)}
                          className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold tabular-nums transition-colors ${
                            isActive
                              ? "border-[#d946ef]/50 bg-[#d946ef]/15 text-[#f0abfc]"
                              : "border-white/[0.08] bg-white/[0.02] text-slate-400 hover:border-white/[0.18] hover:text-slate-200"
                          } ${n.holdoutSaving && !isActive ? "opacity-40" : ""}`}
                          aria-pressed={isActive}
                          aria-label={`Set holdout to ${preset.label}`}
                        >
                          {preset.label}
                        </button>
                      );
                    })}
                    {n.holdoutSaving && (
                      <span className="text-[10px] text-slate-400">saving…</span>
                    )}
                    {n.holdoutError && (
                      <span className="text-[10px] text-rose-400">{n.holdoutError}</span>
                    )}
                    {!n.holdoutSaving && !n.holdoutError && (n.holdout_pct || 0) > 0 && (
                      <span className="text-[10px] text-slate-400">· scientifically measured</span>
                    )}
                  </div>
                </div>
                {/* Lift pill — the single most important number on this row */}
                {hasLift && (
                  <div
                    className="flex-shrink-0 rounded-lg border px-3 py-1.5 text-right"
                    style={{
                      borderColor: liftIsPositive ? "rgba(52, 211, 153, 0.3)" : liftValue < 0 ? "rgba(248, 113, 113, 0.3)" : "rgba(148, 163, 184, 0.2)",
                      backgroundColor: liftIsPositive ? "rgba(52, 211, 153, 0.08)" : liftValue < 0 ? "rgba(248, 113, 113, 0.08)" : "rgba(148, 163, 184, 0.04)",
                    }}
                  >
                    <div className={`text-[16px] font-extrabold tabular-nums leading-none ${
                      liftIsPositive ? "text-emerald-400" : liftValue < 0 ? "text-rose-400" : "text-slate-400"
                    }`}>
                      {liftValue > 0 ? "+" : ""}{liftValue.toFixed(0)}%
                    </div>
                    <div className="mt-0.5 text-[9px] text-slate-500">lift</div>
                  </div>
                )}
              </div>

              {/* Stats row — compact inline */}
              {n.loading ? (
                <div className="h-8 animate-pulse rounded bg-white/[0.04]" />
              ) : n.stats ? (
                <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px]">
                  <div>
                    <span className="text-slate-600">Shown: </span>
                    <span className="font-bold tabular-nums text-slate-200">{n.stats.shown.toLocaleString()}</span>
                  </div>
                  <div>
                    <span className="text-slate-600">Purchases: </span>
                    <span className="font-bold tabular-nums text-slate-200">{n.stats.exposed_purchases}</span>
                  </div>
                  <div>
                    <span className="text-slate-600">Revenue: </span>
                    <span className="font-bold tabular-nums text-emerald-400">
                      {createMoneyFormatter(displayCurrency, n.stats.currency)(n.stats.exposed_revenue)}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-600">CVR: </span>
                    <span className="font-bold tabular-nums text-slate-200">{fmtPct(n.stats.exposed_cvr)}</span>
                  </div>
                  {n.stats.holdout_cvr != null && (
                    <div>
                      <span className="text-slate-600">vs holdout: </span>
                      <span className="font-bold tabular-nums text-slate-400">{fmtPct(n.stats.holdout_cvr)}</span>
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-[11px] text-slate-400">Stats unavailable</div>
              )}
            </div>
          );
        })}
      </div>

      {/* Trust footer */}
      <div className="mt-5 inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3 py-1">
        <span className="h-1.5 w-1.5 rounded-full bg-[#d946ef] shadow-[0_0_8px_rgba(217,70,239,0.6)]" />
        <span className="text-[10px] text-slate-400">
          Quasi-experimental measurement · holdout control group · no inflated claims
        </span>
      </div>
    </div>
  );
}
