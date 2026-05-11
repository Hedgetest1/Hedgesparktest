"use client";

/**
 * RecurringBuyersCard — Pro mid-band parity vs Glew Pro / Putler Plus.
 *
 * Surfaces cadence-based recurring buyer analytics from
 * /pro/recurring-buyers/summary. Honest framing: NOT
 * "Subscriptions" (we don't integrate Shopify Subscriptions Admin
 * API) — we detect REGULAR-CADENCE buyers heuristically from
 * shop_orders.customer_email + created_at.
 *
 * 3 KPI mini-cards (recurring count / MRR estimate / at-risk count)
 * + buyer list with cadence label + lifetime revenue + next-expected
 * date. Empty/insufficient-data states are explicit.
 */

import { useEffect, useState } from "react";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";


type Buyer = {
  email_masked: string;
  cadence_kind: string;
  cadence_days: number;
  orders_count: number;
  lifetime_revenue: number;
  currency: string;
  last_order_at: string;
  next_expected_at: string;
  is_at_risk: boolean;
};


type Report = {
  shop_domain: string;
  currency: string;
  lookback_days: number;
  has_data: boolean;
  recurring_count: number;
  recurring_revenue_30d: number;
  mrr_estimate: number;
  at_risk_count: number;
  churned_30d: number;
  buyers: Buyer[];
  note?: string | null;
};


const CADENCE_LABEL: Record<string, string> = {
  weekly: "Weekly",
  biweekly: "Biweekly",
  monthly: "Monthly",
  quarterly: "Quarterly",
};


function fmtRelative(iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = d.getTime() - now.getTime();
    const diffDays = Math.round(diffMs / (1000 * 60 * 60 * 24));
    if (diffDays === 0) return "today";
    if (diffDays > 0) return `in ${diffDays}d`;
    return `${Math.abs(diffDays)}d ago`;
  } catch {
    return "—";
  }
}


export function RecurringBuyersCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(
          `${apiBase}/pro/recurring-buyers/summary`,
          { credentials: "include" }
        );
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const data = (await res.json()) as Report;
        if (!cancelled) setReport(data);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "load failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [apiBase, shop, isProUser]);

  if (!isProUser) return null;

  // Loading skeleton
  if (loading) {
    return (
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
        <div className="mb-3 h-4 w-40 animate-pulse rounded bg-white/[0.06]" />
        <div className="grid grid-cols-3 gap-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-20 animate-pulse rounded-xl bg-white/[0.04]" />
          ))}
        </div>
      </div>
    );
  }

  // Error
  if (error || !report) {
    return (
      <div className="rounded-2xl border border-rose-500/[0.20] bg-rose-500/[0.04] p-5">
        <div className="text-[12px] text-rose-300">
          Recurring buyers couldn’t load. Retry in a moment.
        </div>
      </div>
    );
  }

  // Empty / insufficient data
  if (!report.has_data) {
    return (
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
        <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.18em] text-[#fbbf24]">
          Recurring buyers · Heuristic detection
        </div>
        <h3 className="text-[15px] font-bold text-white">
          Not enough signal yet to detect cadence
        </h3>
        <p className="mt-2 text-[13px] leading-relaxed text-slate-400">
          {report.note ||
            "Recurring buyer detection requires at least 10 distinct customers in the lookback window."}
        </p>
        <p className="mt-3 text-[11px] text-slate-400">
          We look at email + order timestamps over the last {report.lookback_days}{" "}
          days. As orders accumulate, regular-cadence buyers surface here.
        </p>
      </div>
    );
  }

  // Happy path
  const c = report.currency || "USD";
  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#fbbf24]">
            Recurring buyers · Heuristic detection
          </div>
          <h3 className="text-[15px] font-bold text-white">
            Cadence-detected returning customers
          </h3>
          <p className="mt-1 text-[11px] text-slate-400">
            Patterns from last {report.lookback_days} days · not Shopify Subscriptions native
          </p>
        </div>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-xl border border-emerald-400/[0.15] bg-emerald-500/[0.04] p-3">
          <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-emerald-300">
            Recurring buyers
          </div>
          <div className="mt-1 text-[24px] font-extrabold tabular-nums text-white">
            {report.recurring_count}
          </div>
          <div className="text-[10px] text-slate-400">
            {report.churned_30d > 0
              ? `${report.churned_30d} churned in last 30d`
              : "no churn last 30d"}
          </div>
        </div>
        <div className="rounded-xl border border-amber-400/[0.15] bg-amber-500/[0.04] p-3">
          <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-amber-300">
            MRR estimate
          </div>
          <div className="mt-1 text-[24px] font-extrabold tabular-nums text-white">
            {formatMoneyCompact(report.mrr_estimate, c)}
          </div>
          <div className="text-[10px] text-slate-400">extrapolated from cadence</div>
        </div>
        <div
          className={`rounded-xl border p-3 ${
            report.at_risk_count > 0
              ? "border-rose-400/[0.20] bg-rose-500/[0.06]"
              : "border-white/[0.07] bg-white/[0.02]"
          }`}
        >
          <div
            className={`text-[10px] font-bold uppercase tracking-[0.14em] ${
              report.at_risk_count > 0 ? "text-rose-300" : "text-slate-400"
            }`}
          >
            At risk
          </div>
          <div className="mt-1 text-[24px] font-extrabold tabular-nums text-white">
            {report.at_risk_count}
          </div>
          <div className="text-[10px] text-slate-400">overdue next-expected order</div>
        </div>
      </div>

      {/* Buyer list */}
      {report.buyers.length > 0 && (
        <div className="mt-5">
          <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
            Top recurring buyers
          </div>
          <div className="space-y-2">
            {report.buyers
              .slice(0, 8)
              .sort((a, b) => b.lifetime_revenue - a.lifetime_revenue)
              .map((b) => (
                <div
                  key={b.email_masked + b.last_order_at}
                  className="rounded-xl border border-white/[0.04] bg-white/[0.015] p-3"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-[12px] font-semibold text-slate-200">
                        <span className="font-mono">{b.email_masked}</span>
                        <span
                          className="rounded-full px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.12em]"
                          style={{
                            background: b.is_at_risk
                              ? "rgba(248, 113, 113, 0.12)"
                              : "rgba(52, 211, 153, 0.12)",
                            color: b.is_at_risk ? "#fca5a5" : "#6ee7b7",
                          }}
                        >
                          {CADENCE_LABEL[b.cadence_kind] || b.cadence_kind}
                        </span>
                      </div>
                      <div className="mt-0.5 text-[10px] text-slate-400">
                        {b.orders_count} orders · LTV{" "}
                        <span className="font-mono tabular-nums text-slate-300">
                          {formatMoneyCompact(b.lifetime_revenue, b.currency)}
                        </span>{" "}
                        · last {fmtRelative(b.last_order_at)}
                      </div>
                    </div>
                    <div
                      className={`flex-shrink-0 text-right text-[10px] tabular-nums ${
                        b.is_at_risk ? "text-rose-300" : "text-slate-400"
                      }`}
                    >
                      next {fmtRelative(b.next_expected_at)}
                    </div>
                  </div>
                </div>
              ))}
          </div>
          {report.buyers.length > 8 && (
            <div className="mt-2 text-[10px] text-slate-400">
              + {report.buyers.length - 8} more recurring buyers
            </div>
          )}
        </div>
      )}
    </div>
  );
}
