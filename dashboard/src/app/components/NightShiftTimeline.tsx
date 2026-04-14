"use client";

/**
 * NightShiftTimeline — "While you slept".
 *
 * Shows the autonomous actions HedgeSpark actually took on the merchant's
 * store in the last 24 hours and 7 days. Unlike NightShiftCard (which is
 * predictive — "here's your first move today"), this component is
 * retrospective — "here's everything we did for you, how it went, and
 * whether it's still being measured". It is the visible proof-of-work
 * layer for the self-healing pipeline: every row is traceable back to
 * a real decision in the autonomous_actions table.
 *
 * Data source: GET /pro/night-shift/timeline
 */

import { useState } from "react";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
  DrawerHowCalculated,
} from "./DetailDrawer";

type TimelineAction = {
  id: number;
  at: string | null;
  status: string;
  action_type: string;
  nudge_type: string | null;
  signal_type: string;
  product_url: string;
  decision_reason: string;
  risk_level: string;
  lift_pct: number | null;
  p_value: number | null;
  visitors_measured: number | null;
  outcome: string | null;
  verdict: "win" | "loss" | "neutral" | "rollback" | "measuring" | "pending";
  rollback_reason: string | null;
};

type TimelineSummary = {
  actions_overnight: number;
  actions_week: number;
  wins_week: number;
  losses_week: number;
  neutral_week: number;
  measuring_week: number;
  avg_positive_lift_pct: number | null;
};

type TimelineResponse = {
  shop_domain: string;
  overnight: TimelineAction[];
  this_week: TimelineAction[];
  summary: TimelineSummary;
};

const VERDICT_COLORS: Record<TimelineAction["verdict"], string> = {
  win: "#10b981",
  loss: "#f43f5e",
  neutral: "#94a3b8",
  rollback: "#fb7185",
  measuring: "#a78bfa",
  pending: "#64748b",
};

const VERDICT_LABELS: Record<TimelineAction["verdict"], string> = {
  win: "Lifted sales",
  loss: "Hurt sales",
  neutral: "No lift",
  rollback: "Rolled back",
  measuring: "Measuring",
  pending: "Queued",
};

function shortProduct(url: string | null): string {
  if (!url) return "a product";
  if (url.startsWith("/products/")) {
    return url
      .slice(10)
      .replace(/-/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }
  return url.length > 40 ? url.slice(0, 38) + "…" : url;
}

function humanAction(actionType: string, nudgeType: string | null): string {
  // Translate internal action_type slugs into plain-merchant labels.
  const base: Record<string, string> = {
    nudge_deploy: "Launched a nudge",
    nudge_suppress: "Paused a nudge",
    nudge_promote: "Scaled a nudge",
    nudge_rotate: "Rotated nudge copy",
  };
  const label = base[actionType] || actionType.replace(/_/g, " ");
  if (nudgeType) {
    const kind: Record<string, string> = {
      social_proof: "social proof",
      high_interest: "high interest",
      return_visitor: "return visitor",
      engagement_depth: "engagement",
    };
    const k = kind[nudgeType] || nudgeType.replace(/_/g, " ");
    return `${label} · ${k}`;
  }
  return label;
}

function relativeTime(iso: string | null): string {
  if (!iso) return "";
  try {
    const then = new Date(iso).getTime();
    if (!isFinite(then)) return "";
    const diffMs = Math.max(0, Date.now() - then);
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch {
    return "";
  }
}

export function NightShiftTimeline({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<TimelineResponse>({
    url: `${apiBase}/pro/night-shift/timeline`,
    enabled: !!apiBase && !!shop && isProUser,
    isEmpty: (d) =>
      (d.overnight?.length ?? 0) === 0 && (d.this_week?.length ?? 0) === 0,
  });

  if (!isProUser) return null;

  if (state === "loading") {
    return <CardSkeleton label="Loading what we did while you slept" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Night shift timeline unavailable"
        message="We couldn't load the timeline of actions we took for you. Nothing was lost — the actions themselves ran, this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="violet"
        title="Nothing needed fixing overnight"
        body="HedgeSpark monitored your store and didn't find anything worth touching. That's a good sign — when the radar is clear, the best action is no action."
      />
    );
  }

  const summary = data.summary;
  const overnightCount = summary.actions_overnight;
  const weekCount = summary.actions_week;
  const winsWeek = summary.wins_week;
  const lossesWeek = summary.losses_week;
  const avgLift = summary.avg_positive_lift_pct;
  const topOvernight = data.overnight.slice(0, 5);

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open night shift timeline — ${overnightCount} actions overnight, ${weekCount} this week, ${winsWeek} wins`}
        onClick={() => setDrawerOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setDrawerOpen(true);
          }
        }}
        className="group cursor-pointer rounded-2xl border border-violet-400/20 bg-gradient-to-br from-violet-500/[0.05] via-white/[0.02] to-transparent p-6 transition-shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-[#e8a04e] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220] hover:border-violet-400/40"
      >
        <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-[#e8a04e]">
          While you slept
        </div>
        <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
          Here&apos;s what HedgeSpark fixed for you
        </h3>
        <p className="mt-2 text-[14px] leading-relaxed text-slate-400">
          Every row is a real decision the autonomous loop made on your store, with the measured
          outcome. No theatre — if it&apos;s here, it happened.
        </p>

        {/* Summary row */}
        <div className="mt-5 grid gap-3 sm:grid-cols-4">
          <div className="rounded-xl border border-violet-400/20 bg-violet-500/[0.05] px-4 py-3">
            <div className="text-[10px] font-bold uppercase tracking-wider text-violet-400">
              Overnight
            </div>
            <div className="mt-1 text-[26px] font-extrabold tabular-nums text-violet-200">
              {overnightCount}
            </div>
            <div className="text-[11px] text-violet-400/70">actions taken</div>
          </div>
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
            <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
              This week
            </div>
            <div className="mt-1 text-[26px] font-extrabold tabular-nums text-slate-200">
              {weekCount}
            </div>
            <div className="text-[11px] text-slate-500">actions total</div>
          </div>
          <div className="rounded-xl border border-emerald-400/20 bg-emerald-500/[0.05] px-4 py-3">
            <div className="text-[10px] font-bold uppercase tracking-wider text-emerald-400">
              Wins · 7d
            </div>
            <div className="mt-1 text-[26px] font-extrabold tabular-nums text-emerald-300">
              {winsWeek}
            </div>
            <div className="text-[11px] text-emerald-400/70">
              {avgLift != null ? `avg lift +${avgLift.toFixed(1)}%` : "measured positive"}
            </div>
          </div>
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
            <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
              Still measuring
            </div>
            <div className="mt-1 text-[26px] font-extrabold tabular-nums text-slate-200">
              {summary.measuring_week}
            </div>
            <div className="text-[11px] text-slate-500">verdict pending</div>
          </div>
        </div>

        {/* Overnight list */}
        {topOvernight.length > 0 ? (
          <div className="mt-5 space-y-2">
            <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-500">
              Last 24 hours · most recent first
            </div>
            {topOvernight.map((action) => {
              const color = VERDICT_COLORS[action.verdict];
              const label = VERDICT_LABELS[action.verdict];
              return (
                <div
                  key={action.id}
                  className="flex items-center gap-3 rounded-xl border border-white/[0.05] bg-white/[0.015] px-4 py-3"
                >
                  <div
                    className="h-2 w-2 flex-shrink-0 rounded-full"
                    style={{ background: color }}
                    aria-hidden="true"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[13px] font-semibold text-slate-200">
                        {humanAction(action.action_type, action.nudge_type)}
                      </span>
                      <span className="text-[11px] text-slate-600" aria-hidden="true">·</span>
                      <span className="truncate text-[12px] text-slate-500">
                        {shortProduct(action.product_url)}
                      </span>
                    </div>
                    <p className="mt-0.5 text-[11px] leading-relaxed text-slate-500">
                      {action.decision_reason}
                    </p>
                  </div>
                  <div className="flex-shrink-0 text-right">
                    <div
                      className="text-[11px] font-bold uppercase tracking-wider"
                      style={{ color }}
                    >
                      {label}
                    </div>
                    <div className="mt-0.5 text-[10px] tabular-nums text-slate-600">
                      {relativeTime(action.at)}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="mt-5 rounded-xl border border-white/[0.05] bg-white/[0.015] px-4 py-4 text-center">
            <p className="text-[12px] text-slate-400">
              Nothing ran in the last 24 hours. This week&apos;s actions are still visible in the full
              timeline.
            </p>
          </div>
        )}

        <div className="mt-4 text-[11px] font-semibold text-slate-500">
          Click for the full week and per-action reasoning →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="🌙"
        title="While you slept"
        subtitle={`${weekCount} action${weekCount === 1 ? "" : "s"} this week · ${winsWeek} measured as wins`}
        widthPx={640}
      >
        <DrawerExplainer
          body={
            "Every row in this timeline is a real decision the autonomous loop made on your store, " +
            "logged in the source-of-truth table the rest of the pipeline reads from. When a decision " +
            "has had enough time to be measured against a holdout, the verdict is shown; when it's " +
            "still collecting visitors, it's marked as 'measuring'. No action is counted as a win " +
            "until it proves itself."
          }
          why={
            "Most automation hides its work behind a status light. This card shows the work — every " +
            "action, every reason, every outcome — so you can tell at a glance what's been fixed " +
            "for you and what's still in progress."
          }
        />

        <DrawerBigStat
          label="Wins this week"
          value={`${winsWeek}`}
          sublabel={
            avgLift != null
              ? `Average lift on winning actions: +${avgLift.toFixed(1)}%`
              : "No measured lifts yet — keep reading"
          }
          color={winsWeek > 0 ? "#10b981" : "#94a3b8"}
        />

        <DrawerKeyValueList
          items={[
            {
              label: "Actions overnight (24h)",
              value: `${overnightCount}`,
            },
            {
              label: "Actions this week (7d)",
              value: `${weekCount}`,
            },
            {
              label: "Wins",
              value: `${winsWeek}`,
              color: winsWeek > 0 ? "#10b981" : "#94a3b8",
            },
            {
              label: "Losses",
              value: `${lossesWeek}`,
              color: lossesWeek > 0 ? "#f43f5e" : "#94a3b8",
            },
            {
              label: "Neutral outcomes",
              value: `${summary.neutral_week}`,
            },
            {
              label: "Still measuring",
              value: `${summary.measuring_week}`,
              color: summary.measuring_week > 0 ? "#a78bfa" : "#94a3b8",
            },
            {
              label: "Average positive lift",
              value: avgLift != null ? `+${avgLift.toFixed(1)}%` : "—",
              color: avgLift != null && avgLift > 0 ? "#10b981" : "#94a3b8",
            },
          ]}
        />

        <DrawerSectionHeading>Full week · every action, newest first</DrawerSectionHeading>
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          {data.this_week.map((action) => {
            const color = VERDICT_COLORS[action.verdict];
            const label = VERDICT_LABELS[action.verdict];
            return (
              <div
                key={action.id}
                style={{
                  padding: "13px 15px",
                  borderRadius: "10px",
                  background: "rgba(15,23,42,0.55)",
                  border: `1px solid ${color}25`,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    gap: "12px",
                    marginBottom: "6px",
                  }}
                >
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div
                      style={{
                        color: "#e2e8f0",
                        fontWeight: 600,
                        fontSize: "13px",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {humanAction(action.action_type, action.nudge_type)}
                    </div>
                    <div style={{ color: "#64748b", fontSize: "11px", marginTop: "2px" }}>
                      {shortProduct(action.product_url)} · {relativeTime(action.at)}
                    </div>
                  </div>
                  <div
                    style={{
                      color,
                      fontWeight: 700,
                      fontSize: "10px",
                      textTransform: "uppercase",
                      letterSpacing: "0.08em",
                      flexShrink: 0,
                      padding: "3px 8px",
                      borderRadius: "6px",
                      background: color + "14",
                      border: `1px solid ${color}40`,
                    }}
                  >
                    {label}
                  </div>
                </div>
                <p
                  style={{
                    color: "#cbd5e1",
                    fontSize: "12px",
                    lineHeight: 1.55,
                    margin: "0 0 8px 0",
                  }}
                >
                  {action.decision_reason}
                </p>
                {(action.lift_pct != null ||
                  action.visitors_measured != null ||
                  action.p_value != null) && (
                  <div
                    style={{
                      display: "flex",
                      flexWrap: "wrap",
                      gap: "8px",
                      fontSize: "11px",
                      color: "#94a3b8",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {action.lift_pct != null && (
                      <span>
                        Lift:{" "}
                        <b
                          style={{
                            color: action.lift_pct >= 0 ? "#34d399" : "#fb7185",
                          }}
                        >
                          {action.lift_pct >= 0 ? "+" : ""}
                          {action.lift_pct.toFixed(1)}%
                        </b>
                      </span>
                    )}
                    {action.visitors_measured != null && (
                      <span>
                        Visitors measured:{" "}
                        <b style={{ color: "#cbd5e1" }}>
                          {action.visitors_measured.toLocaleString("en")}
                        </b>
                      </span>
                    )}
                    {action.p_value != null && (
                      <span>
                        p-value:{" "}
                        <b style={{ color: "#cbd5e1" }}>{action.p_value.toFixed(3)}</b>
                      </span>
                    )}
                    {action.risk_level && (
                      <span>
                        Risk:{" "}
                        <b style={{ color: "#cbd5e1" }}>{action.risk_level}</b>
                      </span>
                    )}
                  </div>
                )}
                {action.rollback_reason && (
                  <div
                    style={{
                      marginTop: "6px",
                      padding: "6px 10px",
                      borderRadius: "6px",
                      background: "rgba(251,113,133,0.08)",
                      border: "1px solid rgba(251,113,133,0.25)",
                      color: "#fda4af",
                      fontSize: "11px",
                    }}
                  >
                    Rolled back: {action.rollback_reason}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <DrawerHowCalculated
          formula="Every row is read straight from the autonomous_actions table, keyed by shop_domain. A decision is counted as a 'win' only when measurement_end has passed AND the outcome column is 'positive' (i.e. the holdout comparison was statistically positive). 'Measuring' means the decision is deployed but the measurement window is still open. 'Rolled back' means the action was reverted — we show it anyway for transparency."
          inputs={[
            { label: "Window", value: "Last 7 days" },
            { label: "Source table", value: "autonomous_actions" },
            {
              label: "Max rows",
              value: "200 per request (newest first)",
            },
          ]}
          note="No aggregate, no estimate, no narrative text invented by an LLM. If the timeline says an action won, it means measurement_end passed, the holdout comparison came out positive, and the outcome column was set by the measurement pipeline — not by a copywriter."
        />
      </DetailDrawer>
    </>
  );
}
