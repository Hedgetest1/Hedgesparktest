"use client";

/**
 * CohortSummaryCard — "How well do customers come back?"
 *
 * Top-line retention metrics across all recent weekly acquisition
 * cohorts. Week-1 = fraction of first-time buyers who purchased again
 * within 7 days. Week-4 = same within 28 days. Best cohort = the
 * acquisition week with the strongest repeat curve.
 *
 * Complements the existing CohortTable which shows the full matrix.
 * This card is the "how are we doing overall?" glance; the matrix is
 * for diagnosis.
 *
 * Data source: GET /analytics/cohorts/summary (Lite + Pro accessible).
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
import type { components } from "@/app/lib/api-types";

type CohortSummary = components["schemas"]["CohortSummaryResponse"];

function retentionTheme(rate: number) {
  if (rate >= 0.3) return { label: "Strong retention", color: "#10b981" };
  if (rate >= 0.15) return { label: "Typical retention", color: "#e8a04e" };
  if (rate > 0) return { label: "Weak retention", color: "#f43f5e" };
  return { label: "No retention yet", color: "#94a3b8" };
}

export function CohortSummaryCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<CohortSummary>({
    url: `${apiBase}/analytics/cohorts/summary`,
    enabled: !!apiBase && !!shop,
    isEmpty: (d) => (d.cohorts_measured ?? 0) === 0 || (d.total_customers ?? 0) === 0,
  });

  // `isProUser` retained in signature for call-site back-compat but
  // no longer gates rendering — per founder directive 2026-04-20,
  // top-level retention is a Lite feature (strada 2 completista).
  // The per-customer LTV drill-down + full cohort matrix remain Pro.
  void isProUser;

  if (state === "loading") {
    return <CardSkeleton label="Measuring cohort retention" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Cohort retention unavailable"
        message="We couldn't load retention stats right now. Your cohorts are safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="violet"
        title="Not enough cohorts yet"
        body="We need at least one cohort with week-4 data visible to compute an average. First-time buyers from the last four weeks count in; the card turns on once a month of orders has accumulated."
        eta="Needs ~4 weeks of order history"
      />
    );
  }

  const w1 = data.avg_week_1_retention ?? 0;
  const w4 = data.avg_week_4_retention ?? 0;
  const total = data.total_customers ?? 0;
  const cohorts = data.cohorts_measured ?? 0;
  const w4Theme = retentionTheme(w4);

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open cohort retention details — week-4 retention ${(w4 * 100).toFixed(1)}%, ${cohorts} cohorts measured across ${total} customers`}
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
              Cohort retention
            </div>
            <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
              How well do customers come back?
            </h3>
            <p className="mt-2 text-[14px] leading-relaxed text-slate-400">
              Week-1 and week-4 repeat rates averaged across every acquisition cohort in the
              window. The strongest cohort is highlighted — that&apos;s the week your acquisition
              channel was working hardest.
            </p>
          </div>
          <div
            className="flex-shrink-0 rounded-xl border px-4 py-2 text-right"
            style={{
              borderColor: w4Theme.color + "55",
              background: w4Theme.color + "14",
            }}
          >
            <div
              className="text-[10px] font-bold uppercase tracking-wider"
              style={{ color: w4Theme.color }}
            >
              Verdict
            </div>
            <div className="mt-0.5 text-[13px] font-extrabold" style={{ color: w4Theme.color }}>
              {w4Theme.label}
            </div>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-xl border border-violet-400/20 bg-violet-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-violet-400">
              Week-1 retention
            </div>
            <div className="mt-1 text-[30px] font-extrabold tabular-nums text-violet-300">
              {(w1 * 100).toFixed(1)}%
            </div>
            <div className="mt-0.5 text-[10px] text-violet-400/70">
              bought again within 7d
            </div>
          </div>

          <div
            className="rounded-xl border px-4 py-4 text-center"
            style={{
              borderColor: w4Theme.color + "33",
              background: w4Theme.color + "0D",
            }}
          >
            <div className="text-[10px] font-bold uppercase tracking-wider" style={{ color: w4Theme.color }}>
              Week-4 retention
            </div>
            <div className="mt-1 text-[30px] font-extrabold tabular-nums" style={{ color: w4Theme.color }}>
              {(w4 * 100).toFixed(1)}%
            </div>
            <div
              className="mt-0.5 text-[10px]"
              style={{ color: w4Theme.color, opacity: 0.75 }}
            >
              within 28d
            </div>
          </div>

          <div className="rounded-xl border border-slate-400/20 bg-slate-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
              Customers measured
            </div>
            <div className="mt-1 text-[30px] font-extrabold tabular-nums text-slate-200">
              {total.toLocaleString("en")}
            </div>
            <div className="mt-0.5 text-[10px] text-slate-500">
              across {cohorts} cohorts
            </div>
          </div>

          <div className="rounded-xl border border-emerald-400/20 bg-emerald-500/[0.05] px-4 py-4 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-emerald-400">
              Best cohort
            </div>
            <div
              className="mt-1 font-extrabold tabular-nums text-emerald-300"
              style={{ fontSize: data.best_cohort ? "22px" : "28px" }}
            >
              {data.best_cohort || "—"}
            </div>
            <div className="mt-0.5 text-[10px] text-emerald-400/70">
              strongest retention curve
            </div>
          </div>
        </div>

        {/* Long-tail retention curve — Strada 4 (dominate).
            Week-1 and Week-4 are the headline (above); these are the
            "do they stick around a quarter later" data points where
            Peel's depth used to beat ours. Now we match + simpler. */}
        <div className="mt-4 rounded-xl border border-white/[0.05] bg-[#0b0b14]/40 px-4 py-3">
          <div className="mb-3 text-[10px] font-bold uppercase tracking-[0.16em] text-slate-500">
            Long-tail retention curve
          </div>
          <div className="grid grid-cols-5 gap-2 sm:gap-4">
            {[
              { label: "Week 1", rate: data.avg_week_1_retention ?? 0 },
              { label: "Week 4", rate: data.avg_week_4_retention ?? 0 },
              { label: "Week 8", rate: data.avg_week_8_retention ?? 0 },
              { label: "Week 12", rate: data.avg_week_12_retention ?? 0 },
              { label: "Week 26", rate: data.avg_week_26_retention ?? 0 },
            ].map((pt) => {
              const theme = retentionTheme(pt.rate);
              return (
                <div key={pt.label} className="text-center">
                  <div className="text-[9.5px] font-semibold uppercase tracking-wider text-slate-500">
                    {pt.label}
                  </div>
                  <div
                    className="mt-1 text-[17px] font-extrabold tabular-nums leading-none"
                    style={{ color: pt.rate > 0 ? theme.color : "#475569" }}
                  >
                    {pt.rate > 0 ? `${(pt.rate * 100).toFixed(1)}%` : "—"}
                  </div>
                  {/* Bar — visual anchor. Max height 20px; scales to %. */}
                  <div
                    className="mx-auto mt-2 w-2 rounded-full"
                    style={{
                      height: `${Math.max(2, Math.min(100, pt.rate * 100) * 0.2)}px`,
                      background: pt.rate > 0 ? theme.color : "rgba(148,163,184,0.15)",
                    }}
                    aria-hidden="true"
                  />
                </div>
              );
            })}
          </div>
          <p className="mt-3 text-[11.5px] leading-relaxed text-slate-500">
            The curve shape tells you the story: flat-and-high = compounding LTV; steep drop = one-and-done buyers.
          </p>
        </div>

        <div className="mt-3 text-[11px] font-semibold text-slate-500">
          Click for the per-cohort curves and what drives the spread →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="🧬"
        title="Cohort retention at a glance"
        subtitle={`${cohorts} acquisition cohorts · ${total.toLocaleString("en")} customers`}
      >
        <DrawerExplainer
          body={
            "For each week in the window, we group every customer who made their FIRST order in " +
            "that week into a cohort. We then track what share of them bought again within 1 week, " +
            "4 weeks, and beyond. Averaging across cohorts gives a single retention number that " +
            "tells you whether your product is sticky or leaky."
          }
          why={
            "Retention is the most honest signal of product-market fit. A high acquisition number " +
            "with low retention is a vanity metric — you're paying for first buyers and losing them. " +
            "A strong week-4 retention number compounds into LTV and is the hardest metric to fake, " +
            "which is why we surface it above every competitor-equivalent 'average customer value' " +
            "number."
          }
        />

        <DrawerBigStat
          label="Week-4 retention — the number that compounds"
          value={`${(w4 * 100).toFixed(1)}%`}
          sublabel={
            w4 >= 0.3
              ? "Strong retention — customers want to come back. Focus on acquiring more of the right kind."
              : w4 >= 0.15
                ? "Typical retention — there's headroom. Investigate what the best cohort did differently."
                : w4 > 0
                  ? "Weak retention — every cohort leaks. Diagnose the drop between week 1 and week 4 in the per-cohort table."
                  : "No retention data yet. First-time buyers need to come back once for anything to register."
          }
          color={w4Theme.color}
        />

        <DrawerKeyValueList
          items={[
            {
              label: "Week-1 retention (avg)",
              value: `${(w1 * 100).toFixed(1)}%`,
              color: "#a78bfa",
            },
            {
              label: "Week-4 retention (avg)",
              value: `${(w4 * 100).toFixed(1)}%`,
              color: w4Theme.color,
            },
            {
              label: "Customers measured",
              value: total.toLocaleString("en"),
            },
            { label: "Cohorts measured", value: `${cohorts}` },
            { label: "Best cohort", value: data.best_cohort || "—" },
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
          A customer&apos;s cohort is the ISO week of their FIRST order. Week-N retention for a
          cohort = fraction of its customers who placed at least one order between days 1..(7×N)
          after their first purchase. We average the rate across all cohorts in the window so
          short-lived promo weeks don&apos;t dominate. &quot;Best cohort&quot; picks the one with
          the highest week-4 retention among cohorts that have fully matured (≥28 days old).
        </div>

        <DrawerHowCalculated
          formula="cohort(customer) = ISO-week of customer's first order. week_N(cohort) = |{c ∈ cohort: any order in [day 1..7N] after first}| / |cohort|. Summary = mean of week_N across all cohorts."
          inputs={[
            { label: "Cohorts measured", value: `${cohorts}` },
            { label: "Customers in window", value: total.toLocaleString("en") },
            { label: "Week-1 bucket", value: "days 1..7 post-first-order" },
            { label: "Week-4 bucket", value: "days 1..28 post-first-order" },
          ]}
          note="We measure cumulative retention (any order in [1..7N]), not point retention (exactly week N). Cumulative is what matters for LTV and matches how real merchants think about 'did they come back'. Competitors that report point-retention show artificially lower numbers."
        />

        <DrawerNextAction
          headline={
            w4 >= 0.3
              ? "Scale what's working"
              : w4 >= 0.15
                ? "Study the best cohort"
                : "Diagnose the drop-off"
          }
          primary={{
            label:
              w4 >= 0.3
                ? "Open the full cohort table"
                : w4 >= 0.15
                  ? `See what happened in ${data.best_cohort || "the best cohort"}`
                  : "See week-by-week drop in the cohort table",
            description:
              w4 >= 0.3
                ? "Your customers are sticky. Find the acquisition channel that brings them in, and pour more fuel on that fire. The cohort table shows the per-week breakdown."
                : w4 >= 0.15
                  ? `The best cohort (${data.best_cohort || "—"}) is retaining materially better than average. Look at what changed that week — different channel, new product, price test — and replicate.`
                  : "Retention is weak. Open the cohort matrix and look at the curve between week 1 and week 4 — the drop tells you whether it's first-time-buyer quality or the post-purchase experience that's leaking.",
            onClick: () => setDrawerOpen(false),
          }}
        />
      </DetailDrawer>
    </>
  );
}
