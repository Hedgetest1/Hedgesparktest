"use client";

/**
 * NudgeActionQueueCard — "Which nudge should I act on next, and how?"
 *
 * Every active nudge gets scored by estimated incremental revenue, then
 * passed through a 7-rule decision engine that produces a typed
 * recommendation (promote winner, investigate negative lift, expand
 * segment, enable holdout, collect more data, deactivate, monitor).
 *
 * The top N recommendations surface here — NOT sorted by raw revenue,
 * but by actionability: red flags first, then opportunities, then
 * monitor-only. Every row is one click away from the endpoint that
 * executes the recommendation.
 *
 * Complements the existing NudgeDnaCard (which shows which WORDS
 * convert). Where DNA is "what to write", this is "what to do next".
 *
 * Data source: GET /pro/nudges/rank
 */

import { useMemo, useState } from "react";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
  DrawerHowCalculated,
  DrawerNextAction,
} from "./DetailDrawer";
import type { components } from "@/app/lib/api-types";

type NudgeRankResponse = components["schemas"]["NudgeRankResponse"];
type NudgeRankEntry = components["schemas"]["NudgeRankEntry"];

type RecTheme = {
  label: string;
  color: string;
  urgency: number; // 0 = nothing to do, 3 = act now
  verb: string;
};

const REC_THEMES: Record<string, RecTheme> = {
  investigate_negative_lift: {
    label: "Investigate — may be hurting",
    color: "#f43f5e",
    urgency: 3,
    verb: "Investigate",
  },
  promote_winner_variant: {
    label: "Promote winning variant",
    color: "#10b981",
    urgency: 3,
    verb: "Promote",
  },
  expand_eligible_segment: {
    label: "Expand to a wider audience",
    color: "#e8a04e",
    urgency: 2,
    verb: "Expand",
  },
  enable_holdout: {
    label: "Enable holdout measurement",
    color: "#60a5fa",
    urgency: 2,
    verb: "Enable holdout",
  },
  collect_more_data: {
    label: "Collect more data",
    color: "#94a3b8",
    urgency: 1,
    verb: "Wait",
  },
  deactivate_low_value: {
    label: "Deactivate — low value",
    color: "#f87171",
    urgency: 2,
    verb: "Deactivate",
  },
  monitor: {
    label: "Monitor — performing normally",
    color: "#64748b",
    urgency: 0,
    verb: "Monitor",
  },
};

function recTheme(label: string): RecTheme {
  return (
    REC_THEMES[label] || {
      label: label.replace(/_/g, " "),
      color: "#94a3b8",
      urgency: 1,
      verb: "Review",
    }
  );
}

function shortProduct(url: string): string {
  // "/products/my-handle" → "my-handle"
  const stripped = url.replace(/^\/products\//, "");
  if (stripped.length <= 28) return stripped;
  return stripped.slice(0, 25) + "…";
}

export function NudgeActionQueueCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<NudgeRankResponse>({
    url: `${apiBase}/pro/nudges/rank?limit=20&status=active`,
    enabled: !!apiBase && !!shop && isProUser,
    isEmpty: (d) => (d.total ?? 0) === 0 || (d.nudges?.length ?? 0) === 0,
  });

  const actionable = useMemo(() => {
    if (!data?.nudges) return [];
    return data.nudges.filter((n) => recTheme(n.recommendation).urgency >= 2);
  }, [data?.nudges]);

  const topThree = useMemo(() => {
    if (!data?.nudges) return [];
    const rows = [...data.nudges];
    rows.sort((a, b) => {
      const ua = recTheme(a.recommendation).urgency;
      const ub = recTheme(b.recommendation).urgency;
      if (ua !== ub) return ub - ua;
      return (b.ranking_signal ?? 0) - (a.ranking_signal ?? 0);
    });
    return rows.slice(0, 3);
  }, [data?.nudges]);

  if (!isProUser) return null;

  if (state === "loading") {
    return <CardSkeleton label="Ranking your nudges by impact" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Nudge action queue unavailable"
        message="We couldn't compute the nudge ranking right now. Your nudges are still running — the ranking will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="violet"
        title="No active nudges yet"
        body="Once HedgeSpark starts running nudges on your store, this card will rank them by estimated incremental revenue and tell you exactly which one to act on next."
        eta="Needs at least one active nudge"
      />
    );
  }

  const totalActive = data.total;
  const actionableCount = actionable.length;
  const topRec = topThree[0] ? recTheme(topThree[0].recommendation) : null;
  const heroColor = actionableCount > 0 && topRec ? topRec.color : "#10b981";
  const heroHeadline =
    actionableCount === 0
      ? "All nudges performing normally"
      : actionableCount === 1
        ? "1 nudge needs action"
        : `${actionableCount} nudges need action`;

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open nudge action queue — ${actionableCount} of ${totalActive} active nudges need action`}
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
              Nudge action queue
            </div>
            <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
              What to do next with your nudges
            </h3>
            <p className="mt-2 text-[14px] leading-relaxed text-slate-400">
              Every active nudge scored by estimated incremental revenue, then
              passed through a 7-rule decision engine that tells you whether to
              promote, investigate, expand, or leave alone. Sorted by urgency,
              not raw revenue.
            </p>
          </div>
          <div
            className="flex-shrink-0 rounded-xl border px-4 py-2 text-right"
            style={{
              borderColor: heroColor + "55",
              background: heroColor + "14",
            }}
          >
            <div
              className="text-[10px] font-bold uppercase tracking-wider"
              style={{ color: heroColor }}
            >
              Priority
            </div>
            <div
              className="mt-0.5 text-[13px] font-extrabold"
              style={{ color: heroColor }}
            >
              {heroHeadline}
            </div>
          </div>
        </div>

        <div className="mt-5 space-y-2">
          {topThree.map((n) => {
            const theme = recTheme(n.recommendation);
            return (
              <div
                key={n.nudge_id}
                className="rounded-xl border border-white/[0.05] bg-white/[0.015] p-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span
                        className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold tabular-nums"
                        style={{
                          color: theme.color,
                          background: theme.color + "20",
                          border: `1px solid ${theme.color}40`,
                        }}
                      >
                        #{n.rank ?? "—"}
                      </span>
                      <span
                        className="truncate text-[12px] font-semibold text-slate-200"
                        title={n.product_url}
                      >
                        {shortProduct(n.product_url)}
                      </span>
                      <span className="shrink-0 text-[10px] text-slate-400">
                        · {n.exposed_count.toLocaleString("en")} seen
                      </span>
                    </div>
                    <div className="mt-1 text-[11px]" style={{ color: theme.color }}>
                      {theme.label}
                    </div>
                  </div>
                  <div
                    className="flex-shrink-0 rounded-md px-2 py-1 text-[10px] font-bold"
                    style={{
                      color: theme.color,
                      background: theme.color + "14",
                      border: `1px solid ${theme.color}33`,
                    }}
                  >
                    {theme.verb}
                  </div>
                </div>
                {n.recommendation_reason && (
                  <div className="mt-1.5 text-[10px] text-slate-400">
                    {n.recommendation_reason}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="mt-3 text-[11px] font-semibold text-slate-400">
          Click to see the full queue · per-nudge methodology · callable
          recommendation endpoints →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="🎯"
        title="Nudge action queue"
        subtitle={`${totalActive} active nudges · ${actionableCount} need action · attribution window ${data.attribution_window_hours}h`}
      >
        <DrawerExplainer
          body={
            "Every active nudge is scored with 3 batch DB queries — event counts, exposed-group attribution, and holdout-group attribution — then passed through a 7-rule decision engine in strict priority order. The highest-urgency recommendation per nudge wins, and nudges are sorted so the ones that need action (investigate, promote, expand, enable holdout) surface above the ones already performing normally."
          }
          why={
            "Competitor tools either show you a list of nudges sorted by CVR (noisy — CVR differences at small sample sizes are coin-flips) or by revenue (misleading — a nudge with high revenue can still be hurting if the holdout group converts at a higher rate). HedgeSpark ranks by estimated INCREMENTAL revenue with p-value confidence, then produces a typed recommendation you can act on immediately."
          }
        />

        <DrawerBigStat
          label={heroHeadline}
          value={`${actionableCount} / ${totalActive}`}
          sublabel={
            actionableCount === 0
              ? "No urgent recommendations right now. The decision engine is monitoring every nudge and will surface anything that needs intervention as soon as sample sizes become meaningful."
              : actionableCount === 1
                ? "One nudge has a non-monitor recommendation. Review it below — the ranking engine already picked the specific action."
                : `${actionableCount} nudges have non-monitor recommendations. The queue is sorted by urgency, so start from the top.`
          }
          color={heroColor}
        />

        <DrawerSectionHeading>Full ranked queue</DrawerSectionHeading>
        <div className="flex flex-col gap-2">
          {(data.nudges ?? []).map((n) => {
            const theme = recTheme(n.recommendation);
            const lift = n.cvr_lift_pct;
            const pval = n.p_value;
            return (
              <div
                key={n.nudge_id}
                className="rounded-lg border p-3"
                style={{
                  background: "rgba(15,23,42,0.55)",
                  borderColor: theme.color + "33",
                }}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span
                        className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold tabular-nums"
                        style={{
                          color: theme.color,
                          background: theme.color + "20",
                          border: `1px solid ${theme.color}40`,
                        }}
                      >
                        #{n.rank ?? "—"}
                      </span>
                      <span
                        className="truncate text-[12px] font-semibold text-slate-200"
                        title={n.product_url}
                      >
                        {shortProduct(n.product_url)}
                      </span>
                      {n.is_ab_experiment && (
                        <span className="shrink-0 rounded bg-violet-500/15 px-1 py-0.5 text-[9px] font-bold uppercase text-violet-300">
                          A/B
                        </span>
                      )}
                      {n.is_holdout_active && (
                        <span className="shrink-0 rounded bg-blue-500/15 px-1 py-0.5 text-[9px] font-bold uppercase text-blue-300">
                          Holdout
                        </span>
                      )}
                    </div>
                    <div
                      className="mt-1 text-[11px]"
                      style={{ color: theme.color }}
                    >
                      {theme.label}
                    </div>
                    <div className="mt-0.5 text-[10px] text-slate-400">
                      {n.recommendation_reason}
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1 text-right tabular-nums">
                    <div className="text-[10px] text-slate-400">
                      seen {n.exposed_count.toLocaleString("en")}
                      {n.clicked_count > 0 && ` · clicks ${n.clicked_count}`}
                    </div>
                    {lift != null && (
                      <div
                        className="text-[11px] font-semibold"
                        style={{
                          color: lift > 0 ? "#10b981" : lift < 0 ? "#f43f5e" : "#94a3b8",
                        }}
                      >
                        {lift > 0 ? "+" : ""}
                        {lift.toFixed(1)}% CVR lift
                      </div>
                    )}
                    {pval != null && (
                      <div className="text-[10px] text-slate-400">
                        p = {pval.toFixed(3)}
                      </div>
                    )}
                    <div className="text-[10px] text-slate-400">
                      basis: {n.ranking_basis.replace(/_/g, " ")}
                    </div>
                  </div>
                </div>
                {n.agent_action.endpoint && (
                  <div className="mt-2 border-t border-white/[0.06] pt-2 text-[10px] text-slate-400">
                    <span className="font-mono text-slate-400">
                      {n.agent_action.method} {n.agent_action.endpoint}
                    </span>
                    {!n.agent_action.available && (
                      <span className="ml-2 text-amber-400">
                        (not available — {n.agent_action.description})
                      </span>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <DrawerKeyValueList
          items={[
            { label: "Active nudges", value: `${totalActive}` },
            { label: "Recommending action", value: `${actionableCount}`, color: heroColor },
            {
              label: "Attribution window",
              value: `${data.attribution_window_hours}h`,
            },
            {
              label: "Ranking bases in use",
              value: Array.from(
                new Set((data.nudges ?? []).map((n) => n.ranking_basis))
              )
                .map((b) => b.replace(/_/g, " "))
                .join(", ") || "—",
            },
          ]}
        />

        <DrawerHowCalculated
          formula="ranking_signal = estimated_incremental_revenue (primary) → incremental_rpv → post-exposure CVR → 0. recommendation = first matching rule from: investigate_negative_lift → promote_winner_variant → expand_eligible_segment → enable_holdout → collect_more_data → deactivate_low_value → monitor."
          inputs={[
            { label: "Active nudges considered", value: `${totalActive}` },
            {
              label: "DB queries per ranking pass",
              value: "3 (event counts + exposed + holdout)",
            },
            {
              label: "Minimum sample for significance",
              value: "≥ 30 per group (exposed & holdout)",
            },
            {
              label: "Significance threshold for promote",
              value: "p < 0.10",
            },
          ]}
          note="Revenue attribution is observational first-exposure (not causal). Holdout lift is quasi-experimental (hash-based deterministic assignment). Neither proves causation — the 'investigate_negative_lift' and 'promote_winner_variant' labels are priors, not proofs. Every number carries an honest ranking_basis and sample-sufficiency label."
        />

        <DrawerNextAction
          headline={
            actionableCount === 0
              ? "Keep monitoring"
              : topRec?.verb === "Promote"
                ? "Promote the winner"
                : topRec?.verb === "Investigate"
                  ? "Investigate the top nudge"
                  : "Act on the top recommendation"
          }
          primary={{
            label:
              actionableCount === 0
                ? "Close drawer"
                : topThree[0]?.agent_action?.endpoint
                  ? `Open ${topThree[0].agent_action.method} ${topThree[0].agent_action.endpoint}`
                  : "Review the top nudge",
            description:
              actionableCount === 0
                ? "No urgent recommendations right now. The queue refreshes every pipeline cycle and will surface new actions as sample sizes mature."
                : topRec?.verb === "Promote"
                  ? "The top nudge has a statistically promotable winner variant. Promoting graduates the winning copy into the control variant and lets HedgeSpark generate new challengers against it."
                  : topRec?.verb === "Investigate"
                    ? "The top nudge may be HURTING conversion. Open the full report, look at the copy and targeting, and decide whether to pause, re-compose, or let it run with fresh data."
                    : "The top nudge has an available action — one click away from the endpoint that executes the recommendation.",
            onClick: () => setDrawerOpen(false),
          }}
        />
      </DetailDrawer>
    </>
  );
}
