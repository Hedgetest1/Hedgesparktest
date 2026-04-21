"use client";

/**
 * LiteRarsHero — permanent Revenue-at-Risk hero above the cassettoni.
 *
 * Founder directive 2026-04-20: HedgeSpark's one true differentiator
 * is RARS (Revenue-at-Risk deterministic pricing). No other Shopify
 * tool tells a merchant "here's how much money is slipping through
 * your store THIS MONTH, broken into five quantified sources". It
 * should be unmistakable on day-1 that this is the reason to pay €39.
 *
 * Before v4, RARS was treated as one of six cassettoni — buried at
 * equal weight with Daily Brief, Hot Products, etc. This hero lifts
 * it to the permanent top slot with three ranked components visible
 * BEFORE any click, each linking to the drill-down that explains it.
 *
 * Real-data contract: every number comes from /pro/revenue-at-risk
 * (same endpoint the cassettone drill-down fetches). Cold-start shows
 * a labeled preview with a "watching" pulse — never fake numbers.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "../app/_lib/formatters";
import type { CassettoneId } from "./LiteCassettoniGrid";
import { ExportButton } from "./ExportButton";

// Map each RARS component source to the cassettone that drills into it.
// Most components go to the RARS cassettone (full methodology / key
// metrics / primary action for that specific leak). One exception:
// abandoned_high_intent has a direct sibling cassettone (Abandoned
// Intent) that's purpose-built for that leak type. goal_gap goes to
// Daily Brief because the brief is the cadence where the merchant
// discovers they're off-target day by day.
const SOURCE_TO_CASSETTONE: Record<string, CassettoneId> = {
  abandoned_high_intent: "abandoned-intent",
  refund_decline: "revenue-at-risk",
  nudge_gap: "revenue-at-risk",
  below_benchmark: "revenue-at-risk",
  goal_gap: "daily-brief",
};

const COMPONENT_COLORS: Record<string, string> = {
  abandoned_high_intent: "#f87171",
  refund_decline: "#fbbf24",
  nudge_gap: "#a78bfa",
  below_benchmark: "#60a5fa",
  goal_gap: "#e8a04e",
};

function humanizeSource(source: string): string {
  return (
    {
      abandoned_high_intent: "Abandoned high-intent carts",
      refund_decline: "Products losing traction",
      nudge_gap: "Nudges underperforming peers",
      below_benchmark: "Peers out-earning you",
      goal_gap: "Your monthly targets",
    } as Record<string, string>
  )[source] ?? source.replace(/_/g, " ");
}

type RarsComponent = {
  source: string;
  loss_eur: number;
  narrative?: string;
};

type RarsPayload = {
  total_at_risk_eur?: number;
  prevented_eur_this_month?: number;
  components?: RarsComponent[];
  currency?: string;
} | null;

export function LiteRarsHero({
  apiBase,
  shop,
  displayCurrency,
  onOpenCassettone,
}: {
  apiBase: string;
  shop: string;
  displayCurrency: "USD" | "EUR";
  onOpenCassettone: (id: CassettoneId) => void;
}) {
  const [data, setData] = useState<RarsPayload>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;
    apiClient
      .GET("/pro/revenue-at-risk")
      .then(({ data: raw }) => {
        if (!active) return;
        setData((raw as RarsPayload) ?? null);
      })
      .catch(() => {
        if (active) setData(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [apiBase, shop]);

  const total = data?.total_at_risk_eur ?? 0;
  const prevented = data?.prevented_eur_this_month ?? 0;
  const ccy = data?.currency ?? displayCurrency;
  const rawComponents = data?.components ?? [];
  const sortedComponents = [...rawComponents]
    .filter((c) => c.loss_eur > 0)
    .sort((a, b) => b.loss_eur - a.loss_eur);
  const top3 = sortedComponents.slice(0, 3);
  const componentsTotal = sortedComponents.reduce((s, c) => s + c.loss_eur, 0);
  const hasData = !loading && total > 0 && top3.length > 0;

  return (
    <section
      aria-labelledby="lite-rars-heading"
      className="relative mb-8 overflow-hidden rounded-3xl border border-[#fbbf24]/[0.18] bg-gradient-to-br from-[#1a1405] via-[#0d0a0a] to-[#0a0a14] p-7 shadow-[0_30px_100px_-30px_rgba(251,191,36,0.15)] sm:p-10"
    >
      {/* Amber stripe top — visual signature reserved for the single
          most important surface on the floor. */}
      <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-transparent via-[#fbbf24] to-transparent opacity-60" />
      <div className="pointer-events-none absolute -right-40 -top-40 h-[420px] w-[420px] rounded-full bg-[#fbbf24]/[0.06] blur-[180px]" />

      <div className="relative">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h2
              id="lite-rars-heading"
              className="text-[2.25rem] font-extrabold leading-[1.05] tracking-tight text-[#fbbf24] sm:text-[2.75rem]"
            >
              Money at risk · this month
            </h2>
            <div className="mt-1 text-[16px] font-medium leading-snug text-slate-200 sm:text-[17px]">
              The number no other Shopify tool shows you
            </div>
          </div>
          <div className="flex flex-shrink-0 flex-wrap items-start gap-2">
            <ExportButton surface="rars" accentColor="#fbbf24" label="CSV" />
            <ExportButton surface="rars" accentColor="#fbbf24" label="PDF" format="pdf" />
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-baseline gap-3">
          {prevented > 0 && (
            <div className="rounded-xl border border-emerald-400/25 bg-emerald-500/[0.06] px-3.5 py-2">
              <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-400">
                Prevented so far
              </div>
              <div className="mt-0.5 text-[15px] font-bold tabular-nums text-emerald-300">
                {formatMoneyCompact(prevented, ccy)}
              </div>
            </div>
          )}
        </div>

        {/* Hero total */}
        <div className="mt-6 flex items-end gap-5">
          <div
            className="text-[4.5rem] font-extrabold leading-[0.9] tabular-nums sm:text-[5.5rem]"
            style={{
              color: hasData ? "#fbbf24" : "#94a3b8",
              textShadow: hasData ? "0 0 60px rgba(251,191,36,0.2)" : "none",
            }}
          >
            {loading
              ? "…"
              : hasData
              ? formatMoneyCompact(total, ccy)
              : "—"}
          </div>
          {hasData && (
            <div className="mb-4 text-[13px] text-slate-500">at risk</div>
          )}
        </div>

        <p className="mt-4 max-w-2xl text-[14.5px] leading-relaxed text-slate-400">
          Five independent signals — abandoned high-intent carts, refund
          trends, nudges underperforming peers, benchmark gaps, monthly
          targets — summed in your store&apos;s currency. Updated every
          minute. Not yesterday&apos;s revenue; right-now risk.
        </p>

        {/* Top 3 components — permanent, no click needed to reveal */}
        <div className="mt-8 border-t border-white/[0.06] pt-7">
          <div className="mb-5 text-[11px] font-bold uppercase tracking-[0.2em] text-slate-500">
            Where it&apos;s leaking · top 3 sources, ranked
          </div>

          {hasData ? (
            <ul className="space-y-3">
              {top3.map((c, i) => {
                const pct = componentsTotal > 0
                  ? Math.round((c.loss_eur / componentsTotal) * 100)
                  : 0;
                const color = COMPONENT_COLORS[c.source] ?? "#fbbf24";
                const target = SOURCE_TO_CASSETTONE[c.source] ?? "revenue-at-risk";
                return (
                  <li key={c.source}>
                    <button
                      type="button"
                      onClick={() => onOpenCassettone(target)}
                      className="group flex w-full flex-col gap-3 rounded-2xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4 text-left transition-all hover:border-white/[0.14] hover:bg-[#0e0e1a] sm:flex-row sm:items-center sm:gap-5"
                    >
                      <span
                        className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full text-[12px] font-extrabold tabular-nums"
                        style={{
                          color,
                          background: `${color}1a`,
                          border: `1px solid ${color}40`,
                        }}
                        aria-hidden="true"
                      >
                        {i + 1}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline justify-between gap-3">
                          <span className="text-[14.5px] font-semibold text-white">
                            {humanizeSource(c.source)}
                          </span>
                          <span
                            className="flex-shrink-0 text-[16px] font-extrabold tabular-nums"
                            style={{ color }}
                          >
                            {formatMoneyCompact(c.loss_eur, ccy)}
                          </span>
                        </div>
                        {/* Bar */}
                        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.04]">
                          <div
                            className="h-full rounded-full transition-all"
                            style={{
                              width: `${pct}%`,
                              background: `linear-gradient(90deg, ${color} 0%, ${color}80 100%)`,
                            }}
                            aria-hidden="true"
                          />
                        </div>
                        <div className="mt-1.5 flex items-center justify-between gap-3">
                          <span className="text-[11.5px] text-slate-500 tabular-nums">
                            {pct}% of total at-risk
                          </span>
                          <span
                            className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-wider opacity-60 transition-opacity group-hover:opacity-100"
                            style={{ color }}
                          >
                            See the drill-down
                            <svg
                              xmlns="http://www.w3.org/2000/svg"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth={2.2}
                              className="h-3 w-3 transition-transform group-hover:translate-x-0.5"
                              aria-hidden="true"
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3"
                              />
                            </svg>
                          </span>
                        </div>
                      </div>
                    </button>
                  </li>
                );
              })}
              {sortedComponents.length > 3 && (
                <li>
                  <button
                    type="button"
                    onClick={() => onOpenCassettone("revenue-at-risk")}
                    className="group flex w-full items-center justify-between gap-3 rounded-xl border border-white/[0.04] bg-white/[0.02] px-4 py-3 text-left transition-colors hover:border-white/[0.12] hover:bg-white/[0.04]"
                  >
                    <span className="text-[13px] text-slate-400">
                      +{sortedComponents.length - 3} more{" "}
                      {sortedComponents.length - 3 === 1 ? "source" : "sources"} leaking below the top 3
                    </span>
                    <span className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-wider text-[#fbbf24] opacity-70 transition-opacity group-hover:opacity-100">
                      See the full breakdown
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth={2.2}
                        className="h-3 w-3 transition-transform group-hover:translate-x-0.5"
                        aria-hidden="true"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3"
                        />
                      </svg>
                    </span>
                  </button>
                </li>
              )}
            </ul>
          ) : (
            // Cold-start: labeled preview + "watching" pulse. Consistent
            // with the cassettone EmptyPreview pattern — day-1 feels
            // premium instead of a wall of nothing.
            <div className="rounded-2xl border border-dashed border-white/[0.12] bg-[#0b0b14]/40 p-5 sm:p-6">
              <div className="mb-3 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-500">
                <span
                  className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[#fbbf24]"
                  aria-hidden="true"
                />
                Preview — what this hero will show
              </div>
              <p className="mb-4 text-[13px] leading-relaxed text-slate-400">
                Once signals arrive, the three biggest leak sources rank
                here with a € amount, a share %, and a direct drill-down
                to fix each one. Example layout with sample numbers below.
              </p>
              <ul className="pointer-events-none space-y-3 opacity-50">
                {[
                  { label: "Abandoned high-intent carts", pct: 55, value: "€680", color: "#f87171" },
                  { label: "Products losing traction", pct: 26, value: "€320", color: "#fbbf24" },
                  { label: "Your monthly targets", pct: 19, value: "€240", color: "#e8a04e" },
                ].map((s, i) => (
                  <li
                    key={s.label}
                    className="flex items-center gap-5 rounded-2xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4"
                  >
                    <span
                      className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full text-[12px] font-extrabold tabular-nums"
                      style={{
                        color: s.color,
                        background: `${s.color}1a`,
                        border: `1px solid ${s.color}40`,
                      }}
                      aria-hidden="true"
                    >
                      {i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="text-[14.5px] font-semibold text-white">
                          {s.label}
                        </span>
                        <span
                          className="text-[16px] font-extrabold tabular-nums"
                          style={{ color: s.color }}
                        >
                          {s.value}
                        </span>
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.04]">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${s.pct}%`,
                            background: `linear-gradient(90deg, ${s.color} 0%, ${s.color}80 100%)`,
                          }}
                          aria-hidden="true"
                        />
                      </div>
                      <div className="mt-1.5 text-[11.5px] text-slate-500 tabular-nums">
                        {s.pct}% of total at-risk
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
              <div className="mt-5 flex items-center gap-2 rounded-lg border border-emerald-400/15 bg-emerald-500/[0.05] px-3 py-2 text-[11.5px] font-semibold text-emerald-300">
                <span
                  className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400"
                  aria-hidden="true"
                />
                Watching your storefront — real numbers will replace this preview within minutes.
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
