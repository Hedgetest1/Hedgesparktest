"use client";

/**
 * CausalLiftCard — "Proven Impact"
 *
 * THE marketing claim card. Shows the revenue lift from nudges measured
 * against a real holdout control group, with statistical confidence.
 * Distinguishes causation from correlation: the nudge group is compared
 * to visitors who were intentionally left untouched, so the delta is
 * attributable to the nudge and not to background noise.
 *
 * Data source: GET /pro/causal-lift
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
  DrawerNextAction,
} from "./DetailDrawer";

import { formatMoneyCompact } from "@/app/app/_lib/formatters";

type CausalData = {
  total_lift_pct: number;
  attributed_revenue_eur: number;
  confidence: number;
  nudges_measured: number;
  exposed_visitors: number;
  holdout_visitors: number;
  detail: string;
  // Shop's native currency — `_eur` field is in this currency.
  currency?: string;
};

function fmtEur(n: number, currency?: string): string {
  return formatMoneyCompact(n, currency || "USD");
}

export function CausalLiftCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<CausalData>({
    url: `${apiBase}/pro/causal-lift`,
    enabled: !!apiBase && !!shop && isProUser,
    isEmpty: (d) => d.nudges_measured === 0,
  });

  if (!isProUser) return null;

  if (state === "loading") {
    return <CardSkeleton label="Loading your proven impact report" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Proven-impact report unavailable"
        message="We couldn't load this week's causal lift report. Your nudge measurements are safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="emerald"
        title="Waiting for the first holdout measurement"
        body="We need at least one active nudge running against a real control group before we can prove causal lift. Once a nudge has shown to enough visitors and the control group has had a chance to convert on its own, the first measured result appears here."
        eta="Needs 1 active nudge + 200 exposed visitors"
      />
    );
  }

  const isSignificant = data.confidence >= 80;
  const isPositive = data.total_lift_pct > 0;
  const significanceLabel = isSignificant
    ? isPositive
      ? "Proven win"
      : "Proven loss"
    : "Still gathering data";
  const significanceColor = isSignificant
    ? isPositive
      ? "#10b981"
      : "#f43f5e"
    : "#94a3b8";

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open proven-impact details — ${data.total_lift_pct > 0 ? "+" : ""}${data.total_lift_pct.toFixed(
          1,
        )}% lift with ${data.confidence}% confidence`}
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
              Proven impact
            </div>
            <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
              Your nudges, measured
            </h3>
            <p className="mt-2 text-[14px] leading-relaxed text-slate-400">
              Causation, not correlation. Every number here is the gap between visitors who saw your
              nudges and a real control group that didn&apos;t.
            </p>
          </div>
          <div
            className="flex-shrink-0 rounded-xl border px-4 py-2 text-right"
            style={{
              borderColor: significanceColor + "55",
              background: significanceColor + "14",
            }}
          >
            <div
              className="text-[10px] font-bold uppercase tracking-wider"
              style={{ color: significanceColor }}
            >
              Verdict
            </div>
            <div
              className="mt-0.5 text-[13px] font-extrabold"
              style={{ color: significanceColor }}
            >
              {significanceLabel}
            </div>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-3 gap-3">
          <div className="rounded-xl border border-emerald-400/20 bg-emerald-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-emerald-400">
              Conversion lift
            </div>
            <div
              className={`mt-1 text-[32px] font-extrabold tabular-nums ${
                isPositive ? "text-emerald-300" : "text-rose-300"
              }`}
            >
              {data.total_lift_pct > 0 ? "+" : ""}
              {data.total_lift_pct.toFixed(1)}%
            </div>
            <div className="mt-0.5 text-[10px] text-emerald-400/70">
              vs control group
            </div>
          </div>

          <div className="rounded-xl border border-violet-400/20 bg-violet-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-violet-400">
              Revenue proven
            </div>
            <div className="mt-1 text-[32px] font-extrabold tabular-nums text-violet-300">
              {fmtEur(data.attributed_revenue_eur, data.currency)}
            </div>
            <div className="mt-0.5 text-[10px] text-violet-400/70">
              from your nudges
            </div>
          </div>

          <div className="rounded-xl border border-amber-400/20 bg-amber-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-amber-400">
              Confidence
            </div>
            <div
              className={`mt-1 text-[32px] font-extrabold tabular-nums ${
                isSignificant ? "text-amber-300" : "text-slate-400"
              }`}
            >
              {data.confidence}%
            </div>
            <div className="mt-0.5 text-[10px] text-amber-400/70">
              {isSignificant ? "above threshold" : "below threshold"}
            </div>
          </div>
        </div>

        <div className="mt-4 rounded-lg border border-white/[0.05] bg-white/[0.02] px-4 py-3">
          <p className="text-[12px] tabular-nums text-slate-400">
            {data.nudges_measured} nudge{data.nudges_measured === 1 ? "" : "s"} measured ·{" "}
            {data.exposed_visitors.toLocaleString()} visitors saw them ·{" "}
            {data.holdout_visitors.toLocaleString()} held back as control
          </p>
          {isSignificant && isPositive && (
            <p className="mt-1 text-[12px] font-semibold text-emerald-400">
              Statistically solid — this lift is your nudges doing real work.
            </p>
          )}
          {isSignificant && !isPositive && (
            <p className="mt-1 text-[12px] font-semibold text-rose-400">
              The nudges are hurting conversion. Review and pause the underperformers.
            </p>
          )}
          {!isSignificant && (
            <p className="mt-1 text-[12px] font-semibold text-slate-400">
              Not enough data yet to call it. Keep the nudges running and the confidence will rise as more
              visitors flow through.
            </p>
          )}
        </div>

        <div className="mt-3 text-[11px] font-semibold text-slate-500">
          Click for the full measurement method and next action →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="⚖️"
        title="Your nudges, measured against a control group"
        subtitle="Causation, not correlation"
      >
        <DrawerExplainer
          body={
            "Most dashboards tell you 'your nudges drove €X in revenue' by counting purchases from " +
            "visitors who happened to see a nudge. That's correlation — it can't tell you whether " +
            "those visitors would have bought anyway. HedgeSpark holds back a small fraction of " +
            "eligible visitors at random and shows them nothing. The gap between the two groups is " +
            "the real lift — and it's the only number you can take to your accountant."
          }
          why={
            "Without a holdout you can't know if the nudge worked or if the visitor was going to buy " +
            "regardless. One uses real science; the other uses wishful thinking. This card refuses " +
            "to claim anything we can't prove."
          }
        />

        <DrawerBigStat
          label="Conversion lift vs control"
          value={`${data.total_lift_pct > 0 ? "+" : ""}${data.total_lift_pct.toFixed(1)}%`}
          sublabel={
            isSignificant
              ? isPositive
                ? "Above the 80% confidence threshold — this is a real win"
                : "Above the 80% confidence threshold — but pointing the wrong way"
              : "Below the 80% confidence threshold — keep running to tighten the signal"
          }
          color={isSignificant ? (isPositive ? "#10b981" : "#f43f5e") : "#94a3b8"}
        />

        <DrawerKeyValueList
          items={[
            {
              label: "Revenue proven",
              value: fmtEur(data.attributed_revenue_eur, data.currency),
              color: "#a78bfa",
            },
            {
              label: "Confidence",
              value: `${data.confidence}%`,
              color: isSignificant ? "#e8a04e" : "#94a3b8",
            },
            {
              label: "Verdict",
              value: significanceLabel,
              color: significanceColor,
            },
            {
              label: "Nudges measured",
              value: `${data.nudges_measured}`,
            },
            {
              label: "Exposed visitors",
              value: data.exposed_visitors.toLocaleString("en"),
            },
            {
              label: "Control (holdout)",
              value: data.holdout_visitors.toLocaleString("en"),
            },
          ]}
        />

        <DrawerSectionHeading>How the method works</DrawerSectionHeading>
        <div
          style={{
            padding: "14px 16px",
            borderRadius: "10px",
            background: "rgba(15,23,42,0.55)",
            border: "1px solid rgba(148,163,184,0.1)",
            color: "#cbd5e1",
            fontSize: "13px",
            lineHeight: 1.6,
          }}
        >
          For every nudge we mark a fixed random share of eligible visitors as <b>control</b> — they
          never see the nudge, no matter what. The rest see it as normal. We then compare the
          conversion rate of the two groups over the same time window, for the same nudge, for
          visitors with the same eligibility. The delta between them, normalized and tested for
          statistical significance, is what this card calls &quot;lift&quot;. When the confidence is
          below 80% we refuse to call it — a result that might be noise is not a result.
        </div>

        <DrawerHowCalculated
          formula="Lift = (conversion rate of exposed group − conversion rate of control group) ÷ conversion rate of control group × 100. Confidence comes from a two-sided proportion test comparing the two groups."
          inputs={[
            {
              label: "Exposed visitors",
              value: data.exposed_visitors.toLocaleString("en"),
            },
            {
              label: "Control visitors",
              value: data.holdout_visitors.toLocaleString("en"),
            },
            {
              label: "Significance threshold",
              value: "80% confidence",
            },
          ]}
          note="We picked 80% confidence deliberately — it's strong enough to act on while not requiring the absurd sample sizes that more academic thresholds (95%, 99%) would demand from a small Shopify store. If a merchant asks for 95% we can tighten the threshold."
        />

        <DrawerNextAction
          headline={isSignificant ? (isPositive ? "Scale what's working" : "Stop the bleed") : "Keep measuring"}
          primary={{
            label: isSignificant
              ? isPositive
                ? "See your winning nudges"
                : "Review losing nudges"
              : "Check nudge progress",
            description: isSignificant
              ? isPositive
                ? "Your nudges are proven to lift conversion. Scaling them to more visitors compounds the effect — duplicate the winners onto adjacent products or cohorts."
                : "Your nudges are hurting conversion. Review the worst performers and either pause them or rewrite the copy. Revisit this card in 48 hours to confirm the fix."
              : "Confidence is still climbing. Let the nudges run for another day or two and this number will sharpen. No action needed yet.",
            onClick: () => setDrawerOpen(false),
          }}
        />
      </DetailDrawer>
    </>
  );
}
