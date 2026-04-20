"use client";

/**
 * VisitorIntentCard — Phase 1.6 — Lite-accessible.
 *
 * Three numbers a merchant reads at a glance: how many visitors
 * right now are Hot (engaged + clicked), Warm (engaged but no click),
 * Cold (pass-through). Data from `/analytics/visitor-intent-
 * classification` which computes per-visitor conversion_score across
 * the shop and partitions by HOT/WARM thresholds.
 *
 * This is one of the Lite 6 features — the per-tier per-visitor
 * drill-down (full ranked list) stays a Pro moat via /visitor-scores.
 * Lite merchants see the three counts; Pro gets the drill-down CTA.
 *
 * Design: three colored pills. No hover interactions (click only per
 * CLAUDE.md §4). Amber/rose for hot, violet for warm, slate for cold
 * — matches palette intent where "rose → alert/high-signal", "violet
 * → intelligence/active", "slate → neutral/metadata".
 */

import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import type { components } from "../lib/api-types";

// Type pulled from the generated OpenAPI client rather than declared
// locally — keeps the frontend shape locked to the backend response
// model. If the backend changes VisitorIntentCounts, codegen regen +
// TypeScript catches the drift. The hardcoded-URL + local-type path
// this component had before was exactly the class of gap the audit
// 2026-04-19 flagged.
type VisitorIntentCounts = components["schemas"]["VisitorIntentCounts"];

export function VisitorIntentCard({
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
  const { data, state, retry } = useCardFetch<VisitorIntentCounts>({
    url: `${apiBase}/analytics/visitor-intent-classification`,
    enabled: !!apiBase && !!shop,
    isEmpty: (d) => !d || d.total_visitors === 0,
    component: "VisitorIntentCard",
  });

  if (state === "loading") {
    return <CardSkeleton label="Loading visitor intent" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Visitor intent unavailable"
        message="We couldn't load visitor intent right now. Your tracker is still capturing events — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data || data.total_visitors === 0) {
    return (
      <CardEmpty
        accent="violet"
        title="No visitors scored yet"
        body="Once visitors engage with your store (scroll, dwell, click), we'll classify each one Hot, Warm, or Cold and show you the right-now composition of intent."
        eta="Populates with the first visitor"
      />
    );
  }

  const total = data.total_visitors;
  const hot = data.hot_visitors;
  const warm = data.warm_visitors;
  const cold = data.cold_visitors;

  return (
    <section>
      {/* Unified section heading: ONE big amber H2 + slate subtitle. */}
      <div className="mb-6">
        <h3 className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight text-[#e8a04e] sm:text-[2rem]">
          Visitor intent — who&apos;s in your store right now
        </h3>
        <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-slate-400">
          Every visitor classified by scroll, dwell, and click behavior.
          {" "}
          <span className="text-slate-300">
            {total.toLocaleString()} visitors tracked.
          </span>
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <IntentPill
          label="Hot"
          count={hot}
          total={total}
          description="Engaged and clicked — ready to convert"
          color="#f87171"
          bg="rgba(248,113,113,0.08)"
          border="rgba(248,113,113,0.25)"
        />
        <IntentPill
          label="Warm"
          count={warm}
          total={total}
          description="Engaged but haven't clicked yet"
          color="#a78bfa"
          bg="rgba(167,139,250,0.08)"
          border="rgba(167,139,250,0.25)"
        />
        <IntentPill
          label="Cold"
          count={cold}
          total={total}
          description="Pass-through, minimal engagement"
          color="#94a3b8"
          bg="rgba(148,163,184,0.06)"
          border="rgba(148,163,184,0.18)"
        />
      </div>

      {/* Methodology footer — lives on the card so merchants never
          have to guess where the thresholds come from. */}
      <p className="mt-4 text-[11px] leading-relaxed text-slate-500">
        Hot = conversion score above {data.hot_threshold}. Warm =
        above {data.warm_threshold}. Cold = at or below {data.warm_threshold}.
        Score combines dwell time, scroll depth, and click count per visitor.
      </p>

      {/* Pro drill-down bridge — always visible for Lite, not only
          when hot+warm > 0. A merchant with cold-only traffic still
          deserves to know what Pro unlocks; hiding the upsell for
          them would be the only place in the dashboard where the
          path to Pro disappears based on data state. Copy adapts:
          if there are hot/warm visitors, pitch the ranked list; if
          all cold, pitch the per-visitor diagnosis. */}
      {!isProUser && (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-[#d4893a]/20 bg-[#d4893a]/[0.05] px-4 py-3">
          <span className="text-[12px] leading-snug text-slate-300">
            {hot + warm > 0
              ? `Pro unlocks the ranked list of your top ${Math.min(
                  hot + warm,
                  20,
                )} hot and warm visitors with per-visitor behavior detail and recommended next action.`
              : "Pro unlocks per-visitor behavior detail — even cold traffic becomes actionable once you see which pages they saw, how long they lingered, and what nudged them to leave."}
          </span>
          {onUpgrade && (
            <button
              type="button"
              onClick={onUpgrade}
              className="ml-auto flex-shrink-0 rounded-lg bg-[#d4893a] px-3 py-1.5 text-[11px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-[#e8a04e]"
            >
              {hot + warm > 0 ? "See ranked visitors on Pro" : "See Pro"}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function IntentPill({
  label,
  count,
  total,
  description,
  color,
  bg,
  border,
}: {
  label: string;
  count: number;
  total: number;
  description: string;
  color: string;
  bg: string;
  border: string;
}) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  return (
    <div
      className="flex flex-col rounded-2xl border p-5"
      style={{ background: bg, borderColor: border }}
    >
      <div className="flex items-baseline justify-between gap-2">
        <div
          className="text-[11px] font-bold uppercase tracking-[0.18em]"
          style={{ color }}
        >
          {label}
        </div>
        <div className="text-[11px] font-semibold tabular-nums text-slate-400">
          {pct}%
        </div>
      </div>
      <div
        className="mt-2 text-[36px] font-extrabold leading-none tabular-nums"
        style={{ color }}
      >
        {count.toLocaleString()}
      </div>
      <p className="mt-3 text-[12px] leading-snug text-slate-400">
        {description}
      </p>
    </div>
  );
}
