"use client";

import { useState } from "react";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
  DrawerHowCalculated,
  DrawerNextAction,
} from "./DetailDrawer";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

/* ── Types ── */

type Signal = { name: string; direction: string; detail: string };
type PriorityInsight = { headline: string; explanation: string; action: string; category: string; severity: string };

// Bottleneck shape matches the backend response: each entry carries the real
// views/carts/cart_rate for the product that's stuck in the funnel. No more
// hardcoded placeholder numbers in the hero.
type Bottleneck = {
  product_name: string;
  views_7d: number;
  carts_7d: number;
  cart_rate: number;
};

type BriefData = {
  visitors_7d?: number;
  orders_this_week?: number;
  orders_last_week?: number;
  revenue_this_week?: number;
  revenue_last_week?: number;
  revenue_change_pct?: number | null;
  cart_rate?: number | null;
  products_tracked?: number;
  conversion_bottlenecks?: Bottleneck[];
  top_converters?: string[];
  // Shop's native currency (USD/EUR/GBP/…) — `revenue_*` fields
  // above are denominated in this currency.
  currency?: string;
};
type IntelligenceBrief = {
  signals: Signal[];
  diagnosis: string;
  primary_signal: string;
  priority_insight: PriorityInsight | null;
  data: BriefData;
};

/* ── Design tokens ── */

const SEV: Record<string, { border: string; bg: string; dot: string; headline: string; label: string }> = {
  critical: { border: "border-rose-500/25",    bg: "bg-rose-500/[0.05]",    dot: "bg-rose-400",    headline: "text-rose-300",    label: "Urgent" },
  warning:  { border: "border-amber-500/20",   bg: "bg-amber-500/[0.04]",   dot: "bg-amber-400",   headline: "text-amber-200",   label: "Attention" },
  positive: { border: "border-emerald-500/20", bg: "bg-emerald-500/[0.04]", dot: "bg-emerald-400", headline: "text-emerald-300", label: "Growth" },
  neutral:  { border: "border-white/[0.08]",   bg: "bg-white/[0.02]",       dot: "bg-slate-400",   headline: "text-slate-200",   label: "Status" },
};

const DIR_ARROW: Record<string, string> = { up: "\u2191", down: "\u2193", stable: "\u2192", unknown: "\u2022" };
const DIR_COLOR: Record<string, string> = { up: "text-emerald-400", down: "text-rose-400", stable: "text-slate-500", unknown: "text-slate-600" };

/* ── Formatters ── */

const fmt$ = (n: number | undefined | null, currency?: string): string =>
  formatMoneyCompact(n ?? 0, currency || "USD");
function fmtPct(n: number | undefined | null): string {
  if (n == null) return "\u2014";
  return `${n >= 0 ? "+" : ""}${n.toFixed(0)}%`;
}
function fmtRate(n: number | undefined | null): string {
  if (n == null) return "\u2014";
  return `${(n * 100).toFixed(1)}%`;
}

/* ══════════════════════════════════════════════════════════════════════════
   COMPONENT
   ══════════════════════════════════════════════════════════════════════════ */

export function IntelligenceHero({
  connected,
  isProUser = false,
  onUpgrade,
}: {
  connected: boolean;
  isProUser?: boolean;
  onUpgrade?: () => void;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data: brief, state, retry } = useCardFetch<IntelligenceBrief>({
    url: `${API_BASE}/dashboard/intelligence`,
    enabled: connected && !!API_BASE,
    isEmpty: (b) => !b.priority_insight,
  });

  if (!connected) return null;

  if (state === "loading") {
    return <CardSkeleton label="Loading your intelligence brief" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Intelligence brief unavailable"
        message="We couldn't pull this week's intelligence brief. Your store data is safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !brief || !brief.priority_insight) {
    return (
      <CardEmpty
        accent="violet"
        title="Your intelligence brief is warming up"
        body="HedgeSpark needs a few days of visitor and order data before it can tell you what's actually driving your numbers. The first brief lands once the signals are strong enough to stand on their own."
        eta="First brief in ~48h"
      />
    );
  }

  const insight = brief.priority_insight;
  const d = brief.data;
  const sev = SEV[insight.severity] || SEV.neutral;

  const revChange = d.revenue_change_pct;
  const bottlenecks = d.conversion_bottlenecks ?? [];
  const hasBottleneck = bottlenecks.length > 0;
  const topBottleneck = bottlenecks[0];
  const hasProblem = insight.severity === "critical" || insight.severity === "warning" || hasBottleneck;

  return (
    <>
    <div
      role="button"
      tabIndex={0}
      aria-haspopup="dialog"
      aria-label={`Open intelligence brief details — ${insight.headline}`}
      onClick={() => setDrawerOpen(true)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setDrawerOpen(true);
        }
      }}
      className={`group rounded-2xl border ${sev.border} ${sev.bg} overflow-hidden cursor-pointer transition-shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-[#e8a04e] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220]`}
    >
      <div className="p-5 sm:p-6">

        {/* ── Severity tag ── */}
        <div className="mb-5 flex items-center gap-2.5">
          <span className={`h-2.5 w-2.5 rounded-full ${sev.dot} ${hasProblem ? "animate-pulse" : ""}`} />
          <span className="text-[13px] font-bold uppercase tracking-[0.14em] hs-brand-gradient">
            {sev.label}
          </span>
          <span className="text-[13px] font-bold uppercase tracking-[0.14em] text-slate-400">
            {insight.category}
          </span>
        </div>

        {/* ═══════════════════════════════════════════════════════════════════
           PROBLEM-FIRST: Anomaly block renders BEFORE metrics when present
           ═══════════════════════════════════════════════════════════════════ */}
        {hasBottleneck && topBottleneck && (
          <div className="mb-5 rounded-xl border border-rose-500/20 bg-rose-500/[0.06] p-4">
            {/* Label */}
            <div className="mb-2.5 flex items-center gap-2">
              <svg className="h-4 w-4 text-rose-400" aria-hidden="true" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 15.75h.007v.008H12v-.008z" />
              </svg>
              <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-rose-400/80">Conversion breakdown</span>
            </div>

            {/* Product name — large */}
            <div className="text-[20px] font-bold text-rose-200">{topBottleneck.product_name}</div>

            {/* Real views → carts from the backend — no more hardcoded placeholders */}
            <div className="mt-3 flex items-center gap-5">
              <div>
                <span className="text-[2rem] font-extrabold tabular-nums text-white">{topBottleneck.views_7d}</span>
                <span className="ml-1.5 text-[14px] text-slate-400">views</span>
              </div>
              <span className="text-[20px] text-slate-600" aria-hidden="true">&rarr;</span>
              <div>
                <span className={`text-[2rem] font-extrabold tabular-nums ${topBottleneck.carts_7d === 0 ? "text-rose-400" : "text-white"}`}>
                  {topBottleneck.carts_7d}
                </span>
                <span className="ml-1.5 text-[14px] text-slate-400">carts</span>
              </div>
              <div className="ml-auto rounded-lg bg-rose-500/15 px-3 py-1.5">
                <span className="text-[14px] font-bold text-rose-400">
                  &darr; {(topBottleneck.cart_rate * 100).toFixed(1)}% cart rate
                </span>
              </div>
            </div>

            <div className="mt-4 text-[15px] font-medium leading-relaxed text-rose-300/70">
              Traffic is fine. <span className="text-rose-200">The page isn&apos;t converting.</span>
            </div>
          </div>
        )}

        {/* ═══════════════════════════════════════════════════════════════════
           PROOF METRICS — 4 columns, numbers dominate
           ═══════════════════════════════════════════════════════════════════ */}
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">

          {/* Revenue */}
          <div className="group rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3.5 transition-colors hover:bg-white/[0.04]">
            <div className="text-[12px] font-bold uppercase tracking-wider text-slate-400">Revenue</div>
            <div className="mt-1.5 flex items-baseline gap-1.5">
              <span className="text-[1.75rem] font-extrabold tabular-nums text-white">{fmt$(d.revenue_this_week, d.currency)}</span>
              {revChange != null && (
                <span className={`text-[13px] font-bold ${revChange >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                  {fmtPct(revChange)}
                </span>
              )}
            </div>
            {d.orders_this_week != null && d.orders_last_week != null && d.orders_last_week > 0 && (
              <div className="mt-1 flex items-center gap-1 text-[11px]">
                <span className="tabular-nums text-slate-600">{d.orders_last_week}</span>
                <span className="text-slate-700">&rarr;</span>
                <span className="font-semibold tabular-nums text-slate-300">{d.orders_this_week} orders</span>
              </div>
            )}
          </div>

          {/* Orders */}
          <div className="group rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3.5 transition-colors hover:bg-white/[0.04]">
            <div className="text-[12px] font-bold uppercase tracking-wider text-slate-400">Orders</div>
            <div className="mt-1.5">
              <span className="text-[1.75rem] font-extrabold tabular-nums text-white">{d.orders_this_week ?? 0}</span>
            </div>
            {/* Mini bar — previous (gray) vs current (colored) */}
            {d.orders_last_week != null && (d.orders_this_week ?? 0) + d.orders_last_week > 0 && (() => {
              const max = Math.max(d.orders_this_week ?? 0, d.orders_last_week);
              const wLast = Math.max(6, (d.orders_last_week / max) * 100);
              const wThis = Math.max(6, ((d.orders_this_week ?? 0) / max) * 100);
              const growing = (d.orders_this_week ?? 0) > d.orders_last_week;
              return (
                <div className="mt-1.5 space-y-1">
                  <div className="flex items-center gap-1.5">
                    <div className="h-[5px] rounded-full bg-slate-700/80" style={{ width: `${wLast}%` }} />
                    <span className="text-[9px] tabular-nums text-slate-600">{d.orders_last_week}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <div className={`h-[5px] rounded-full ${growing ? "bg-emerald-500/70" : "bg-rose-500/70"}`} style={{ width: `${wThis}%` }} />
                    <span className={`text-[9px] font-semibold tabular-nums ${growing ? "text-emerald-400" : "text-rose-400"}`}>{d.orders_this_week ?? 0}</span>
                  </div>
                </div>
              );
            })()}
          </div>

          {/* Visitors */}
          <div className="group rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3.5 transition-colors hover:bg-white/[0.04]">
            <div className="text-[12px] font-bold uppercase tracking-wider text-slate-400">Visitors</div>
            <div className="mt-1.5">
              <span className="text-[1.75rem] font-extrabold tabular-nums text-white">{d.visitors_7d ?? 0}</span>
            </div>
            <div className="mt-1 text-[13px] text-slate-400">7-day total</div>
          </div>

          {/* Cart Rate */}
          <div className="group rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3.5 transition-colors hover:bg-white/[0.04]">
            <div className="text-[12px] font-bold uppercase tracking-wider text-slate-400">Cart Rate</div>
            <div className="mt-1.5">
              <span className={`text-[1.75rem] font-extrabold tabular-nums ${
                d.cart_rate != null
                  ? d.cart_rate > 0.05 ? "text-emerald-400" : d.cart_rate > 0.02 ? "text-white" : "text-rose-400"
                  : "text-slate-600"
              }`}>
                {fmtRate(d.cart_rate)}
              </span>
            </div>
            <div className={`mt-0.5 text-[10px] font-medium ${
              d.cart_rate != null && d.cart_rate > 0.05 ? "text-emerald-500/60" :
              d.cart_rate != null && d.cart_rate < 0.02 ? "text-rose-400/60" :
              "text-slate-600"
            }`}>
              {d.cart_rate != null && d.cart_rate > 0.05 ? "\u2191 healthy" :
               d.cart_rate != null && d.cart_rate > 0.02 ? "\u2192 average" :
               d.cart_rate != null ? "\u2193 needs attention" : "no data"}
            </div>
          </div>
        </div>

        {/* ═══════════════════════════════════════════════════════════════════
           INTERPRETATION — headline + explanation + diagnosis
           ═══════════════════════════════════════════════════════════════════ */}
        <h2 className={`text-[28px] font-extrabold leading-tight tracking-tight sm:text-[32px] ${sev.headline}`}>
          {insight.headline}
        </h2>
        <p className="mt-2 text-[15px] leading-relaxed text-slate-400">
          {insight.explanation}
        </p>
        {brief.diagnosis && brief.signals.length > 1 && (
          <p className="mt-2.5 text-[14px] font-semibold text-slate-500">
            {brief.diagnosis}
          </p>
        )}

        {/* ── Signal strip ── */}
        <div className="mt-5 flex flex-wrap gap-2.5">
          {brief.signals.map((sig) => (
            <div
              key={sig.name}
              className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-1.5 transition-colors hover:bg-white/[0.04]"
            >
              <span className={`text-[14px] font-bold ${DIR_COLOR[sig.direction] || DIR_COLOR.unknown}`}>
                {DIR_ARROW[sig.direction] || DIR_ARROW.unknown}
              </span>
              <span className="text-[12px] font-bold uppercase tracking-wider text-slate-400">{sig.name}</span>
              <span className="text-[13px] text-slate-400">{sig.detail}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════════════════
         ACTION — visually connected to anomaly, secondary to proof
         ═══════════════════════════════════════════════════════════════════ */}
      <div className={`border-t px-6 py-5 sm:px-7 ${
        isProUser
          ? hasProblem ? "border-rose-500/10 bg-rose-500/[0.02]" : "border-white/[0.06] bg-white/[0.015]"
          : "border-white/[0.04] bg-white/[0.01]"
      }`}>
        {isProUser ? (
          <div className="flex items-start gap-3">
            <svg className={`mt-0.5 h-5 w-5 flex-shrink-0 ${hasProblem ? "text-rose-400" : "text-[#d4893a]"}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
            </svg>
            <div>
              <div className={`text-[12px] font-bold uppercase tracking-[0.14em] ${hasProblem ? "text-rose-400/60" : "text-[#d4893a]"}`}>
                {hasProblem ? "Fix this" : "Next step"}
              </div>
              <div className="mt-1.5 text-[16px] font-medium leading-relaxed text-slate-300">{insight.action}</div>
            </div>
          </div>
        ) : (
          <div className="relative">
            <div className="pointer-events-none select-none blur-[3px]">
              <div className="flex items-start gap-3">
                <svg className="mt-0.5 h-5 w-5 flex-shrink-0 text-slate-500/30" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
                </svg>
                <div className="text-[15px] text-slate-500">{insight.action}</div>
              </div>
            </div>
            <div className="absolute inset-0 flex items-center justify-center">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onUpgrade?.();
                }}
                className="hs-cta-gradient rounded-xl px-6 py-3 text-[14px] font-bold text-white shadow-lg shadow-[#d4893a]/20 transition-all hover:shadow-[#d4893a]/30 active:scale-[0.98] focus:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220]"
              >
                {hasProblem ? "Unlock actions to fix this" : "Turn this insight into revenue"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>

    {/* Drawer — idiot-proof explainer + depth for the merchant */}
    <DetailDrawer
      open={drawerOpen}
      onClose={() => setDrawerOpen(false)}
      icon="🧠"
      title="This week's intelligence brief"
      subtitle={insight.headline}
    >
      <DrawerExplainer
        body={
          "This is what HedgeSpark saw across your traffic, conversion, and revenue signals this week, " +
          "synthesized into one answer: what's actually going on, and what to do about it. The brief is " +
          "built from real events in your store — orders, visitors, carts, product views — not from " +
          "industry averages or guesses."
        }
        why={
          "Most dashboards show you charts and leave you to figure out what matters. The brief picks the " +
          "one thing that matters most this week, proves it with your own numbers, and tells you the next step."
        }
      />

      <DrawerBigStat
        label="Revenue this week"
        value={fmt$(d.revenue_this_week, d.currency)}
        sublabel={
          revChange != null
            ? `${revChange >= 0 ? "+" : ""}${revChange.toFixed(0)}% vs the week before`
            : undefined
        }
        color={revChange != null && revChange >= 0 ? "#10b981" : "#e8a04e"}
      />

      <DrawerKeyValueList
        items={[
          { label: "Visitors (7 days)", value: `${d.visitors_7d ?? 0}` },
          {
            label: "Orders this week",
            value: `${d.orders_this_week ?? 0}${
              d.orders_last_week != null ? ` (from ${d.orders_last_week})` : ""
            }`,
          },
          { label: "Cart rate", value: fmtRate(d.cart_rate) },
          { label: "Products tracked", value: `${d.products_tracked ?? 0}` },
          {
            label: "Bottleneck products",
            value: `${bottlenecks.length}`,
            color: bottlenecks.length > 0 ? "#f43f5e" : "#94a3b8",
          },
        ]}
      />

      {hasBottleneck && topBottleneck && (
        <>
          <DrawerSectionHeading>Biggest bottleneck right now</DrawerSectionHeading>
          <div
            style={{
              padding: "14px 16px",
              borderRadius: "12px",
              background: "rgba(244,63,94,0.06)",
              border: "1px solid rgba(244,63,94,0.25)",
              color: "#fecdd3",
              fontSize: "14px",
              lineHeight: 1.55,
            }}
          >
            <div style={{ fontWeight: 700, color: "#fecaca", marginBottom: "6px" }}>
              {topBottleneck.product_name}
            </div>
            <div style={{ color: "#fda4af", fontSize: "13px" }}>
              {topBottleneck.views_7d} views · {topBottleneck.carts_7d} carts ·{" "}
              {(topBottleneck.cart_rate * 100).toFixed(1)}% cart rate
            </div>
            <div style={{ marginTop: "8px", color: "#cbd5e1", fontSize: "13px" }}>
              Traffic is reaching this product but the page isn&apos;t converting. Focus the fix here first —
              it&apos;s the single biggest lever in your store this week.
            </div>
          </div>
        </>
      )}

      <DrawerHowCalculated
        formula="The brief looks at three signals — traffic, conversion, revenue — compares this week to last, picks the signal that moved the most, and promotes the top-priority action from that signal."
        inputs={[
          { label: "Traffic signal", value: brief.signals.find((s) => s.name === "traffic")?.detail ?? "—" },
          { label: "Conversion signal", value: brief.signals.find((s) => s.name === "conversion")?.detail ?? "—" },
          { label: "Revenue signal", value: brief.signals.find((s) => s.name === "revenue")?.detail ?? "—" },
        ]}
        note={brief.diagnosis || undefined}
      />

      {isProUser && (
        <DrawerNextAction
          headline={hasProblem ? "Fix this first" : "Next step"}
          primary={{
            label: hasProblem ? "See recommended fix" : "Apply next step",
            description: insight.action,
            onClick: () => setDrawerOpen(false),
          }}
        />
      )}
    </DetailDrawer>
    </>
  );
}
