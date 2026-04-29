"use client";

/**
 * RecommendationImpactCard — "Did your actions move the needle?"
 *
 * Quasi-experimental pre/post measurement of recommendation impact.
 * For every autonomous action with an outcome (win / measured / no_effect),
 * we compare 7-day revenue BEFORE the action's deploy date to 7-day
 * revenue AFTER. The delta, in %, is the action's measured impact.
 *
 * This is NOT true causal lift (use CausalLiftCard for that — it runs
 * against real holdout controls). It IS a trend-adjusted pre/post
 * comparison that works for non-nudge interventions like price moves,
 * copy changes, or pause decisions. If a competitor claims "our
 * recommendations helped", we show the number with the method.
 *
 * Data source: GET /pro/recommendation-impact
 */

import { useState } from "react";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerSectionHeading,
  DrawerHowCalculated,
  DrawerNextAction,
} from "./DetailDrawer";
import type { components } from "@/app/lib/api-types";

type ImpactData = components["schemas"]["RecommendationImpactResponse"];
type ImpactRow = components["schemas"]["RecommendationImpactRow"];

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("en", { month: "short", day: "numeric" });
  } catch {
    return iso.slice(0, 10);
  }
}

function formatMoneyCompact(n: number): string {
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toFixed(0);
}

export function RecommendationImpactCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<ImpactData>({
    url: `${apiBase}/pro/recommendation-impact`,
    enabled: !!apiBase && !!shop && isProUser,
    isEmpty: (d) => (d.actions_measured ?? 0) === 0,
  });

  if (!isProUser) return null;

  if (state === "loading") {
    return <CardSkeleton label="Measuring what your actions did" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Recommendation impact unavailable"
        message="We couldn't load the pre/post comparison right now. Your action history is safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <div className="rounded-2xl border border-dashed border-white/[0.10] bg-white/[0.015] p-6">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-[#e8a04e]">
              Recommendation impact
            </div>
            <h3 className="text-[24px] font-extrabold leading-tight tracking-tight text-slate-200">
              Did your actions move the needle?
            </h3>
          </div>
          <div className="flex items-center gap-2 rounded-full bg-amber-500/[0.08] px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-amber-300">
            <span className="relative inline-flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400/60" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-amber-400" />
            </span>
            Sample
          </div>
        </div>
        <div className="grid grid-cols-1 gap-3 opacity-50 sm:grid-cols-3">
          <div className="rounded-xl border border-amber-400/20 bg-amber-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-amber-400">
              Avg impact
            </div>
            <div className="mt-1 text-[32px] font-extrabold tabular-nums text-emerald-300">
              +6.4%
            </div>
            <div className="mt-0.5 text-[10px] text-amber-400/70">
              per action, on revenue
            </div>
          </div>
          <div className="rounded-xl border border-violet-400/20 bg-violet-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-violet-400">
              Actions measured
            </div>
            <div className="mt-1 text-[32px] font-extrabold tabular-nums text-violet-300">
              4
            </div>
            <div className="mt-0.5 text-[10px] text-violet-400/70">
              last 60 days
            </div>
          </div>
          <div className="rounded-xl border border-emerald-400/20 bg-emerald-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-emerald-400">
              Best single action
            </div>
            <div className="mt-1 text-[32px] font-extrabold tabular-nums text-emerald-300">
              +14.2%
            </div>
            <div className="mt-0.5 text-[10px] text-emerald-400/70">
              price_test
            </div>
          </div>
        </div>
        <p className="mt-4 text-[12px] leading-relaxed text-slate-400">
          We compare 7 days of revenue before each action against 7 days after, so every recommendation you act on earns a measured impact number. Deploy one and this card turns on with your real numbers.
        </p>
        <p className="mt-1 text-[11px] text-slate-400">
          Needs 1 deployed action with 14 days of order data.
        </p>
      </div>
    );
  }

  const avg = data.avg_impact_pct ?? 0;
  const count = data.actions_measured ?? 0;
  const isPositive = avg > 0;
  const isFlat = Math.abs(avg) < 0.5;
  const impacts: ImpactRow[] = data.impacts ?? [];
  const topImpact = impacts.reduce<ImpactRow | null>(
    (best, r) => (best === null || r.impact_pct > best.impact_pct ? r : best),
    null,
  );
  const worstImpact = impacts.reduce<ImpactRow | null>(
    (worst, r) => (worst === null || r.impact_pct < worst.impact_pct ? r : worst),
    null,
  );

  const verdictColor = isFlat ? "#94a3b8" : isPositive ? "#10b981" : "#f43f5e";
  const verdictLabel = isFlat
    ? "Flat so far"
    : isPositive
      ? "Actions lifting revenue"
      : "Actions costing revenue";

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open recommendation impact details — ${avg > 0 ? "+" : ""}${avg.toFixed(1)}% avg impact across ${count} actions`}
        onClick={() => setDrawerOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setDrawerOpen(true);
          }
        }}
        className="group cursor-pointer rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6 transition-shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-[#e8a04e] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220] hover:border-white/[0.12]"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-[#e8a04e]">
              Recommendation impact
            </div>
            <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
              Did your actions move the needle?
            </h3>
            <p className="mt-2 text-[14px] leading-relaxed text-slate-400">
              We compare revenue for the 7 days before each action against the 7 days after. Not a
              causal proof — a trend-adjusted check every competitor should be running and almost
              none do.
            </p>
          </div>
          <div
            className="flex-shrink-0 rounded-xl border px-4 py-2 text-right"
            style={{
              borderColor: verdictColor + "55",
              background: verdictColor + "14",
            }}
          >
            <div
              className="text-[10px] font-bold uppercase tracking-wider"
              style={{ color: verdictColor }}
            >
              Verdict
            </div>
            <div
              className="mt-0.5 text-[13px] font-extrabold"
              style={{ color: verdictColor }}
            >
              {verdictLabel}
            </div>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="rounded-xl border border-amber-400/20 bg-amber-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-amber-400">
              Avg impact
            </div>
            <div
              className={`mt-1 text-[32px] font-extrabold tabular-nums ${
                isFlat
                  ? "text-slate-300"
                  : isPositive
                    ? "text-emerald-300"
                    : "text-rose-300"
              }`}
            >
              {avg > 0 ? "+" : ""}
              {avg.toFixed(1)}%
            </div>
            <div className="mt-0.5 text-[10px] text-amber-400/70">
              per action, on revenue
            </div>
          </div>

          <div className="rounded-xl border border-violet-400/20 bg-violet-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-violet-400">
              Actions measured
            </div>
            <div className="mt-1 text-[32px] font-extrabold tabular-nums text-violet-300">
              {count}
            </div>
            <div className="mt-0.5 text-[10px] text-violet-400/70">
              last 60 days
            </div>
          </div>

          <div className="rounded-xl border border-emerald-400/20 bg-emerald-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-emerald-400">
              Best single action
            </div>
            <div
              className={`mt-1 text-[32px] font-extrabold tabular-nums ${
                topImpact && topImpact.impact_pct > 0 ? "text-emerald-300" : "text-slate-400"
              }`}
            >
              {topImpact
                ? `${topImpact.impact_pct > 0 ? "+" : ""}${topImpact.impact_pct.toFixed(1)}%`
                : "—"}
            </div>
            <div className="mt-0.5 text-[10px] text-emerald-400/70">
              {topImpact ? topImpact.action_type : "no impacts yet"}
            </div>
          </div>
        </div>

        {impacts.length > 0 && (
          <div className="mt-4 rounded-lg border border-white/[0.05] bg-white/[0.02] px-4 py-3">
            <div className="mb-2 text-[11px] font-bold uppercase tracking-wider text-slate-400">
              Recent actions
            </div>
            <ul className="space-y-1.5">
              {impacts.slice(0, 5).map((r, idx) => {
                const up = r.impact_pct > 0.5;
                const down = r.impact_pct < -0.5;
                const color = up ? "#10b981" : down ? "#f43f5e" : "#94a3b8";
                return (
                  <li
                    key={`${r.action_type}-${r.action_date}-${idx}`}
                    className="flex items-center justify-between gap-3 text-[12px] tabular-nums"
                  >
                    <div className="min-w-0 flex-1 truncate">
                      <span className="font-semibold text-slate-300">{r.action_type}</span>
                      <span className="ml-2 text-slate-500">· {formatDate(r.action_date)}</span>
                    </div>
                    <div className="shrink-0 text-slate-500">
                      {formatMoneyCompact(r.pre_revenue)} → {formatMoneyCompact(r.post_revenue)}
                    </div>
                    <div className="shrink-0 font-bold" style={{ color }}>
                      {r.impact_pct > 0 ? "+" : ""}
                      {r.impact_pct.toFixed(1)}%
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        <div className="mt-3 text-[11px] font-semibold text-slate-400">
          Click for the full method and what to do next →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="📐"
        title="How your actions changed revenue"
        subtitle="Quasi-experimental pre/post comparison"
      >
        <DrawerExplainer
          body={
            "For every action you deployed that finished with an outcome, we compare the 7 days of " +
            "revenue before its deploy date to the 7 days after. If the post window is higher, the " +
            "action helped. If it's lower, the action hurt. Averaged across all actions, you get a " +
            "single number: did acting on your recommendations tend to move revenue up, down, or not " +
            "at all."
          }
          why={
            "Pre/post is not causation — a lot can happen in 7 days. But it's a real, honest signal " +
            "and it's the one every competitor dashboard skips. For proven causation, use the Proven " +
            "Impact card (holdout-measured nudges). This card covers everything that's not a nudge: " +
            "price moves, copy changes, supplier swaps, pauses."
          }
        />

        <DrawerBigStat
          label="Average impact per action"
          value={`${avg > 0 ? "+" : ""}${avg.toFixed(1)}%`}
          sublabel={
            isFlat
              ? "Your actions are neither helping nor hurting on average. Either the actions are too small to move revenue, or the measurement window is too noisy."
              : isPositive
                ? "Your actions are tending to lift revenue. Keep acting on the recommendations — the trend is your friend."
                : "Your actions are tending to cost revenue. Review the losing actions below and reconsider how you respond to the recommendations."
          }
          color={verdictColor}
        />

        {worstImpact && worstImpact.impact_pct < -0.5 && (
          <>
            <DrawerSectionHeading>Worst single action</DrawerSectionHeading>
            <div
              style={{
                padding: "14px 16px",
                borderRadius: "10px",
                background: "rgba(244, 63, 94, 0.06)",
                border: "1px solid rgba(244, 63, 94, 0.2)",
                color: "#cbd5e1",
                fontSize: "13px",
                lineHeight: 1.6,
              }}
            >
              <div className="text-[11px] font-bold uppercase tracking-wider text-rose-400">
                {worstImpact.action_type}
              </div>
              <div className="mt-1 text-[15px] font-bold text-rose-300">
                {worstImpact.impact_pct.toFixed(1)}% · {formatDate(worstImpact.action_date)}
              </div>
              <div className="mt-2">
                Revenue went from {formatMoneyCompact(worstImpact.pre_revenue)} to{" "}
                {formatMoneyCompact(worstImpact.post_revenue)} in the 7 days after this action.
                Investigate before repeating the pattern.
              </div>
            </div>
          </>
        )}

        <DrawerHowCalculated
          formula="impact% = (post_revenue − pre_revenue) ÷ pre_revenue × 100. Pre = 7 days before action deploy date. Post = 7 days after. Average across all measured actions in the last 60 days."
          inputs={[
            { label: "Actions measured", value: `${count}` },
            { label: "Look-back window", value: "60 days" },
            { label: "Comparison window", value: "7 days pre / 7 days post" },
            { label: "Methodology", value: data.methodology },
          ]}
          note="This is a trend-adjusted pre/post check, not a causal proof. It will catch catastrophic actions (a price change that halved revenue) and it will confirm positive trends, but it can't distinguish your action from other things that happened in those 14 days. For a true causal number, use a holdout — see the Proven Impact card."
        />

        <DrawerNextAction
          headline={
            isFlat
              ? "Keep acting and keep measuring"
              : isPositive
                ? "Double down on what's working"
                : "Stop before the trend compounds"
          }
          primary={{
            label: isFlat
              ? "Review your action history"
              : isPositive
                ? "See top-performing actions"
                : "Review losing actions",
            description: isFlat
              ? "The signal is weak. Try more actions — especially larger ones (price moves, pauses) that are more likely to show up in a 7-day window."
              : isPositive
                ? "Your actions are earning their keep. Keep acting on the recommendations as they come in — compounding weekly lift beats one-off hero moves."
                : "The pattern is costing you revenue on average. Review the losing actions in the list above, identify what they have in common, and either pause that pattern or redesign how you respond to it.",
            onClick: () => setDrawerOpen(false),
          }}
        />
      </DetailDrawer>
    </>
  );
}
