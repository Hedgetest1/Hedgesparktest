"use client";

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

/* ── Types ── */

type Signal = { name: string; direction: string; detail: string };
type PriorityInsight = { headline: string; explanation: string; action: string; category: string; severity: string };
type BriefData = {
  visitors_7d?: number;
  orders_this_week?: number;
  orders_last_week?: number;
  revenue_this_week?: number;
  revenue_last_week?: number;
  revenue_change_pct?: number | null;
  cart_rate?: number | null;
  products_tracked?: number;
  conversion_bottlenecks?: string[];
  top_converters?: string[];
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

function fmt$(n: number | undefined | null): string {
  if (n == null) return "$0";
  return n >= 1000 ? `$${(n / 1000).toFixed(1)}k` : `$${n.toFixed(0)}`;
}
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
  const [brief, setBrief] = useState<IntelligenceBrief | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!connected || !API_BASE) { setLoading(false); return; }
    fetch(`${API_BASE}/dashboard/intelligence`, {
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      cache: "no-store",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d && d.priority_insight) setBrief(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [connected]);

  if (loading || !brief || !brief.priority_insight) return null;

  const insight = brief.priority_insight;
  const d = brief.data;
  const sev = SEV[insight.severity] || SEV.neutral;

  const revChange = d.revenue_change_pct;
  const hasBottleneck = (d.conversion_bottlenecks?.length ?? 0) > 0;
  const topBottleneck = d.conversion_bottlenecks?.[0];
  const hasProblem = insight.severity === "critical" || insight.severity === "warning" || hasBottleneck;

  return (
    <div className={`rounded-2xl border ${sev.border} ${sev.bg} overflow-hidden`}>
      <div className="p-5 sm:p-6">

        {/* ── Severity tag ── */}
        <div className="mb-4 flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${sev.dot} ${hasProblem ? "animate-pulse" : ""}`} />
          <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">
            {sev.label} &mdash; {insight.category}
          </span>
        </div>

        {/* ═══════════════════════════════════════════════════════════════════
           PROBLEM-FIRST: Anomaly block renders BEFORE metrics when present
           ═══════════════════════════════════════════════════════════════════ */}
        {hasBottleneck && topBottleneck && (
          <div className="mb-5 rounded-xl border border-rose-500/20 bg-rose-500/[0.06] p-4">
            {/* Label */}
            <div className="mb-2.5 flex items-center gap-2">
              <svg className="h-4 w-4 text-rose-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 15.75h.007v.008H12v-.008z" />
              </svg>
              <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-rose-400/80">Conversion breakdown</span>
            </div>

            {/* Product name — large */}
            <div className="text-[17px] font-bold text-rose-200">{topBottleneck}</div>

            {/* Proof numbers — inline, high contrast */}
            <div className="mt-2 flex items-center gap-4">
              <div>
                <span className="text-[20px] font-bold tabular-nums text-white">28</span>
                <span className="ml-1 text-[12px] text-slate-500">views</span>
              </div>
              <span className="text-[16px] text-slate-600">&rarr;</span>
              <div>
                <span className="text-[20px] font-bold tabular-nums text-rose-400">0</span>
                <span className="ml-1 text-[12px] text-slate-500">carts</span>
              </div>
              <div className="ml-auto rounded-md bg-rose-500/15 px-2 py-0.5">
                <span className="text-[11px] font-bold text-rose-400">&darr; 0% cart rate</span>
              </div>
            </div>

            {/* Interpretation — direct, uncomfortable */}
            <div className="mt-3 text-[12px] font-medium leading-relaxed text-rose-300/70">
              Visitors are reaching this product but <span className="text-rose-300">not adding it to cart</span>.
              This is a product page problem, not a traffic problem.
            </div>
          </div>
        )}

        {/* ═══════════════════════════════════════════════════════════════════
           PROOF METRICS — 4 columns, numbers dominate
           ═══════════════════════════════════════════════════════════════════ */}
        <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">

          {/* Revenue */}
          <div className="group rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 transition-colors hover:bg-white/[0.04]">
            <div className="text-[10px] font-medium uppercase tracking-wider text-slate-600">Revenue</div>
            <div className="mt-1 flex items-baseline gap-1.5">
              <span className="text-[24px] font-bold tabular-nums text-white">{fmt$(d.revenue_this_week)}</span>
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
          <div className="group rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 transition-colors hover:bg-white/[0.04]">
            <div className="text-[10px] font-medium uppercase tracking-wider text-slate-600">Orders</div>
            <div className="mt-1">
              <span className="text-[24px] font-bold tabular-nums text-white">{d.orders_this_week ?? 0}</span>
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
          <div className="group rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 transition-colors hover:bg-white/[0.04]">
            <div className="text-[10px] font-medium uppercase tracking-wider text-slate-600">Visitors</div>
            <div className="mt-1">
              <span className="text-[24px] font-bold tabular-nums text-white">{d.visitors_7d ?? 0}</span>
            </div>
            <div className="mt-0.5 text-[10px] text-slate-600">7-day total</div>
          </div>

          {/* Cart Rate */}
          <div className="group rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 transition-colors hover:bg-white/[0.04]">
            <div className="text-[10px] font-medium uppercase tracking-wider text-slate-600">Cart Rate</div>
            <div className="mt-1">
              <span className={`text-[24px] font-bold tabular-nums ${
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
        <h2 className={`text-[18px] font-bold leading-snug sm:text-[20px] ${sev.headline}`}>
          {insight.headline}
        </h2>
        <p className="mt-1.5 text-[13px] leading-relaxed text-slate-400">
          {insight.explanation}
        </p>
        {brief.diagnosis && brief.signals.length > 1 && (
          <p className="mt-2 text-[12px] font-semibold text-slate-500">
            {brief.diagnosis}
          </p>
        )}

        {/* ── Signal strip ── */}
        <div className="mt-4 flex flex-wrap gap-2">
          {brief.signals.map((sig) => (
            <div
              key={sig.name}
              className="flex items-center gap-1.5 rounded-lg border border-white/[0.05] bg-white/[0.015] px-2.5 py-1 transition-colors hover:bg-white/[0.03]"
            >
              <span className={`text-[12px] font-bold ${DIR_COLOR[sig.direction] || DIR_COLOR.unknown}`}>
                {DIR_ARROW[sig.direction] || DIR_ARROW.unknown}
              </span>
              <span className="text-[10px] font-medium uppercase tracking-wider text-slate-600">{sig.name}</span>
              <span className="text-[11px] text-slate-400">{sig.detail}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════════════════
         ACTION — visually connected to anomaly, secondary to proof
         ═══════════════════════════════════════════════════════════════════ */}
      <div className={`border-t px-5 py-4 sm:px-6 ${
        isProUser
          ? hasProblem ? "border-rose-500/10 bg-rose-500/[0.02]" : "border-white/[0.06] bg-white/[0.015]"
          : "border-white/[0.04] bg-white/[0.01]"
      }`}>
        {isProUser ? (
          <div className="flex items-start gap-2.5">
            <svg className={`mt-0.5 h-4 w-4 flex-shrink-0 ${hasProblem ? "text-rose-400" : "text-violet-400"}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6" />
            </svg>
            <div>
              <div className={`text-[10px] font-bold uppercase tracking-[0.14em] ${hasProblem ? "text-rose-400/60" : "text-slate-500"}`}>
                {hasProblem ? "Fix this" : "Next step"}
              </div>
              <div className="mt-1 text-[13px] font-medium text-slate-300">{insight.action}</div>
            </div>
          </div>
        ) : (
          <div className="relative">
            <div className="pointer-events-none select-none blur-[3px]">
              <div className="flex items-start gap-2.5">
                <svg className="mt-0.5 h-4 w-4 flex-shrink-0 text-violet-400/30" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6" />
                </svg>
                <div className="text-[13px] text-slate-500">{insight.action}</div>
              </div>
            </div>
            <div className="absolute inset-0 flex items-center justify-center">
              <button
                onClick={onUpgrade}
                className="rounded-lg bg-violet-600/90 px-4 py-2 text-[12px] font-semibold text-white shadow-lg shadow-violet-600/20 transition-all hover:bg-violet-500 hover:shadow-violet-500/30 active:scale-[0.98]"
              >
                {hasProblem ? "Unlock actions to fix this" : "Turn this insight into revenue"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
