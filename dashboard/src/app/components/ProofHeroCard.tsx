"use client";

/**
 * ProofHeroCard — closed-loop proof of impact.
 *
 * Shows actions HedgeSpark took in the last 7 days that produced a
 * measurable improvement (revenue delta + conversion lift) on the
 * products they were applied to. This card and CausalLiftCard are
 * sister cards: CausalLift measures nudges against a holdout; this
 * one measures concrete action outcomes before/after.
 *
 * Data source: GET /actions/proof
 */

import { useState } from "react";
import type { paths } from "../lib/api-client";
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

// Source of truth: GET /actions/proof → ActionProofSummaryResponse.
type ProofData =
  paths["/actions/proof"]["get"]["responses"]["200"]["content"]["application/json"];

function shortProduct(url?: string | null): string {
  if (!url) return "a product";
  if (url.startsWith("/products/")) {
    return url.slice(10).replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }
  return url.length > 30 ? url.slice(0, 28) + "…" : url;
}

function fmtDelta(value: number): string {
  if (value >= 1000) return `+€${(value / 1000).toFixed(1)}k`;
  return `+€${Math.round(value)}`;
}

function fmtDeltaAbs(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1000) return `€${(abs / 1000).toFixed(1)}k`;
  return `€${Math.round(abs)}`;
}

export function ProofHeroCard({
  apiBase,
  shop,
}: {
  apiBase: string;
  shop: string;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<ProofData>({
    url: `${apiBase}/actions/proof`,
    enabled: !!shop && !!apiBase,
    isEmpty: (d) => !d.improvements || d.improvements.length === 0,
  });

  if (state === "loading") {
    return <CardSkeleton label="Loading your proof-of-impact report" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Proof-of-impact report unavailable"
        message="We couldn't load the actions we took for you this week. Your outcomes are safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="emerald"
        title="No proven wins yet this week"
        body="This card lights up after HedgeSpark takes an action on one of your products and measures the result against the 7 days before. Give it a little time to work — the first proven win usually shows up within a week of going live."
        eta="First win in ~5–7 days"
      />
    );
  }

  const top = data.improvements[0];
  const productName = shortProduct(top.product_url);
  const totalDelta = data.total_revenue_delta;
  const deltaCvr = top.delta_cvr ?? 0;
  const cvrPctPoints = Math.abs(deltaCvr * 100);
  const cvrSign = deltaCvr > 0 ? "+" : deltaCvr < 0 ? "-" : "";
  const improvements = data.improvements;
  const positiveCount = improvements.filter((i) => (i.delta_revenue ?? 0) > 0).length;

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open proof-of-impact details — ${fmtDelta(totalDelta)} total revenue recovered, ${improvements.length} improvements measured`}
        onClick={() => setDrawerOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setDrawerOpen(true);
          }
        }}
        className="hs-fade-up relative cursor-pointer overflow-hidden rounded-2xl border border-emerald-400/30 bg-gradient-to-br from-emerald-500/[0.08] via-emerald-500/[0.03] to-transparent p-6 shadow-[0_0_40px_rgba(52,211,153,0.06)] transition-shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220] hover:border-emerald-400/50"
      >
        {/* Header */}
        <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-emerald-400">
          Proven wins
        </div>
        <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-emerald-300">
          HedgeSpark recovered revenue this week
        </h3>
        <p className="mt-2 text-[14px] leading-relaxed text-emerald-200/70">
          Every number below comes from a real before/after measurement on a product we touched. No
          claims we can&apos;t back up.
        </p>

        {/* Main metric row */}
        <div className="mt-5 flex flex-wrap items-end gap-x-8 gap-y-4">
          {totalDelta > 0 && (
            <div>
              <div className="text-[42px] font-extrabold leading-none tracking-tight tabular-nums text-emerald-300">
                {fmtDelta(totalDelta)}
              </div>
              <div className="mt-1 text-[12px] font-semibold uppercase tracking-wider text-emerald-400/80">
                Revenue recovered
              </div>
            </div>
          )}

          {cvrPctPoints > 0 && (
            <div>
              <div className="text-[28px] font-extrabold leading-none tabular-nums text-white">
                {cvrSign}
                {cvrPctPoints.toFixed(1)}
                <span className="ml-1 text-[14px] font-semibold text-slate-400">points</span>
              </div>
              <div className="mt-1 text-[12px] font-semibold uppercase tracking-wider text-slate-500">
                Conversion lift
              </div>
            </div>
          )}
        </div>

        {/* Top-improvement explanation */}
        {top.summary && (
          <div className="mt-5 rounded-xl border border-emerald-400/15 bg-emerald-500/[0.05] px-4 py-3">
            <p className="text-[13px] leading-relaxed text-slate-200">{top.summary}</p>
            <p className="mt-1.5 text-[12px] text-emerald-300/60">
              {productName}
              {improvements.length > 1 && (
                <>
                  {" · "}
                  {improvements.length - 1} more improvement
                  {improvements.length > 2 ? "s" : ""} measured
                </>
              )}
            </p>
          </div>
        )}

        <div className="mt-4 text-[11px] font-semibold text-emerald-300/60">
          Click for the full list of actions and outcomes →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="🏆"
        title="Proof of impact"
        subtitle="Actions HedgeSpark took that moved the number"
      >
        <DrawerExplainer
          body={
            "Every time HedgeSpark takes an action on one of your products — optimizing a page, " +
            "pushing a nudge, adjusting a signal — we measure what happened to that product's " +
            "conversion rate and revenue over the 7 days before vs the 7 days after. If the delta " +
            "is real and positive, it shows up here."
          }
          why={
            "Most tools tell you what they did. We tell you what happened next. That difference is " +
            "the whole point of a closed-loop system: a recommendation that isn't measured is a " +
            "hope, not a fix."
          }
        />

        {totalDelta > 0 && (
          <DrawerBigStat
            label="Total revenue recovered"
            value={fmtDelta(totalDelta)}
            sublabel={`Across ${improvements.length} measured improvement${
              improvements.length === 1 ? "" : "s"
            } · ${positiveCount} positive outcome${positiveCount === 1 ? "" : "s"}`}
            color="#10b981"
          />
        )}

        <DrawerKeyValueList
          items={[
            {
              label: "Actions measured",
              value: `${data.actions_measured}`,
            },
            {
              label: "Improvements landed",
              value: `${improvements.length}`,
              color: improvements.length > 0 ? "#10b981" : "#94a3b8",
            },
            {
              label: "Positive outcomes",
              value: `${positiveCount}`,
              color: "#10b981",
            },
            {
              label: "Total revenue delta",
              value: `${totalDelta >= 0 ? "+" : "-"}${fmtDeltaAbs(totalDelta)}`,
              color: totalDelta >= 0 ? "#10b981" : "#f43f5e",
            },
          ]}
        />

        <DrawerSectionHeading>Full list of measured improvements</DrawerSectionHeading>
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          {improvements.map((imp, i) => {
            const dr = imp.delta_revenue ?? 0;
            const dcvr = (imp.delta_cvr ?? 0) * 100;
            const positive = dr >= 0;
            return (
              <div
                key={`${imp.product_url}-${i}`}
                style={{
                  padding: "12px 14px",
                  borderRadius: "10px",
                  background: "rgba(15,23,42,0.55)",
                  border: "1px solid rgba(148,163,184,0.12)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    gap: "12px",
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
                      {i + 1}. {shortProduct(imp.product_url)}
                    </div>
                    <div style={{ color: "#64748b", fontSize: "11px", marginTop: "2px" }}>
                      {imp.action_type}
                      {imp.measured_at && ` · ${new Date(imp.measured_at).toLocaleDateString()}`}
                    </div>
                    {imp.summary && (
                      <p
                        style={{
                          color: "#cbd5e1",
                          fontSize: "12px",
                          lineHeight: 1.55,
                          marginTop: "6px",
                        }}
                      >
                        {imp.summary}
                      </p>
                    )}
                  </div>
                  <div style={{ textAlign: "right", flexShrink: 0 }}>
                    <div
                      style={{
                        color: positive ? "#10b981" : "#f43f5e",
                        fontWeight: 800,
                        fontSize: "16px",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {dr >= 0 ? "+" : "-"}
                      {fmtDeltaAbs(dr)}
                    </div>
                    {dcvr !== 0 && (
                      <div
                        style={{
                          color: "#94a3b8",
                          fontSize: "11px",
                          fontVariantNumeric: "tabular-nums",
                          marginTop: "2px",
                        }}
                      >
                        {dcvr > 0 ? "+" : ""}
                        {dcvr.toFixed(1)} pts conv
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <DrawerHowCalculated
          formula="For each action we took, we snapshot the target product's conversion rate and revenue over the 7 days before the action. After the action we let it run for 7 days, then we recompute the same numbers. The difference between the two windows is the measured outcome — before/after on the same product, same audience, same seasonality."
          inputs={[
            {
              label: "Actions measured",
              value: `${data.actions_measured}`,
            },
            {
              label: "Improvements landed",
              value: `${improvements.length}`,
            },
            {
              label: "Measurement window",
              value: "7 days before vs 7 days after",
            },
          ]}
          note="Before/after on the same product is the simplest honest measurement — it isolates what changed from what didn't. Causal lift (the sister card) uses a holdout control group for nudges, which is even stricter. Together they cover the two main shapes of action HedgeSpark takes."
        />

        <DrawerNextAction
          headline="Scale what's working"
          primary={{
            label: "Apply the winning pattern to more products",
            description:
              "These actions are already proven on their target products. The same pattern typically works on adjacent products in the same category — duplicate the setup on the next three highest-traffic items and measure again in 7 days.",
            onClick: () => setDrawerOpen(false),
          }}
        />
      </DetailDrawer>
    </>
  );
}
