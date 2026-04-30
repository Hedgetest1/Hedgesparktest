"use client";

/**
 * ProductPerformanceSection — "Where your traffic goes" product table.
 * Extracted from app/page.tsx PageInner (Phase Ω⁷ split).
 */

import { SectionHeading } from "../_components/SectionHeading";
import { Sparkline } from "../../components/Sparkline";
import {
  formatNumber,
  formatPct,
  formatDecimal,
  formatMoneyCompact,
} from "../_lib/formatters";

/* eslint-disable @typescript-eslint/no-explicit-any */
export interface ProductPerformanceSectionProps {
  mergedProducts: any[];
  isProUser: boolean;
  resolvedCcy: string;
  resolvedAov: number;
  resolvedAovIsReal: boolean;
  shortUrl: (url: string) => string;
  setUpgradeModalOpen: (v: boolean) => void;
}

export function ProductPerformanceSection(p: ProductPerformanceSectionProps) {
  const {
    mergedProducts,
    isProUser,
    resolvedCcy,
    resolvedAov,
    resolvedAovIsReal,
    shortUrl,
    setUpgradeModalOpen,
  } = p;

  return (
    <section id="section-product-performance">
      <h2 className="mb-6 text-[2.25rem] font-extrabold leading-[1.05] tracking-tight text-[#e8a04e] sm:text-[2.75rem]">
        Product performance
      </h2>
      <SectionHeading
        eyebrow="Products"
        title="Where your traffic goes"
        description="Sorted by what needs attention first."
      />

      {/* High-priority opportunity banner */}
      {(() => {
        const highRows = mergedProducts.filter((r) => r.priority === "HIGH");
        if (highRows.length === 0) return null;
        const totalLoss = highRows.reduce((sum, r) => sum + (r.estimated_loss ?? 0), 0);
        return (
          <div className="mb-4 flex items-center gap-3 rounded-xl border border-rose-400/20 bg-rose-500/[0.07] px-4 py-3">
            <span className="h-2 w-2 flex-shrink-0 rounded-full bg-rose-400 shadow-[0_0_6px_rgba(251,113,133,0.7)]" />
            <p className="text-[13px] text-rose-200/90">
              You&apos;re potentially leaving{" "}
              {isProUser ? (
                <>
                  <span className="font-semibold">~{formatMoneyCompact(totalLoss, resolvedCcy)}</span>
                  <span className="ml-1 text-[11px] text-rose-200/50">(est. 2% CVR × {formatMoneyCompact(resolvedAov, resolvedCcy)} AOV{!resolvedAovIsReal ? " est." : ""})</span>
                </>
              ) : (
                <span role="button" className="cursor-pointer text-rose-300/50 transition hover:text-rose-300/70" onClick={() => setUpgradeModalOpen(true)}>
                  revenue on the table<span className="ml-1.5 text-violet-400/70 text-[11px] font-normal"> — Unlock in Pro</span>
                </span>
              )}{" "}
              across{" "}
              <span className="font-semibold">{highRows.length}</span>{" "}
              {highRows.length === 1 ? "product" : "products"}
            </p>
          </div>
        );
      })()}

      <div className="overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.02]">
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-[14px]">
            <thead>
              <tr className="border-b border-white/[0.06] text-[12px] font-bold uppercase tracking-wide text-slate-400">
                <th className="px-5 py-4 font-bold">Product</th>
                <th className="px-5 py-4 font-bold">Views 24h</th>
                <th className="px-5 py-4 font-bold">7d Trend</th>
                <th className="px-5 py-4 font-bold">Cart Abandon</th>
                <th className="px-5 py-4 font-bold">Avg Dwell</th>
                <th className="px-5 py-4 font-bold">Avg Scroll</th>
                <th className="px-5 py-4 font-bold">Engagement</th>
                <th className="px-5 py-4 font-bold" title="Weighted priority score: views · engagement · cart abandonment">Priority</th>
                <th className="px-5 py-4 font-bold">
                  Est. Loss / Action
                  <span className="ml-2 text-[10px] text-[#d4893a]/70 border border-[#d4893a]/20 bg-[#d4893a]/10 px-1.5 py-[2px] rounded align-middle font-bold">PRO</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {(isProUser ? mergedProducts.slice(0, 20) : mergedProducts.slice(0, 3)).map((row, i) => (
                <tr
                  key={`pm-${row.product_url}-${i}`}
                  className={`border-t border-white/[0.04] transition-all duration-150 hover:bg-white/[0.03] hover:shadow-[0_1px_8px_rgba(0,0,0,0.15)] ${
                    row.priority === "HIGH"
                      ? "border-l-2 border-l-rose-400/60"
                      : row.priority === "MED"
                      ? "border-l-2 border-l-amber-400/50"
                      : "border-l-2 border-l-transparent"
                  } ${i === 0 ? "bg-violet-500/[0.04]" : ""}`}
                >
                  <td className="max-w-[280px] px-5 py-3.5">
                    <div className="flex items-start gap-2.5">
                      <span
                        className={`mt-[5px] h-2.5 w-2.5 flex-shrink-0 rounded-full ${
                          row.priority === "HIGH"
                            ? "bg-rose-400 shadow-[0_0_8px_rgba(251,113,133,0.7)]"
                            : row.priority === "MED"
                            ? "bg-amber-300 shadow-[0_0_8px_rgba(252,211,77,0.6)]"
                            : "bg-slate-600"
                        }`}
                        title={`${row.priority} priority`}
                      />
                      <div className="min-w-0">
                        <span className="block truncate text-[14px] font-medium text-slate-200" title={row.product_url}>
                          {shortUrl(row.product_url)}
                        </span>
                        {row.insight && (
                          <span className="mt-0.5 block text-[12px] leading-4 text-slate-400">
                            {row.insight}
                          </span>
                        )}
                        {row.action_suggestion && (
                          isProUser ? (
                            <button
                              className="mt-0.5 block text-left text-[10px] leading-3 text-violet-400/70 underline-offset-2 hover:text-violet-300 hover:underline"
                              onClick={() => { if (row.product_url) window.open(row.product_url, "_blank", "noopener,noreferrer"); }}
                            >
                              → {row.action_suggestion}
                            </button>
                          ) : (
                            <span
                              role="button"
                              className="mt-0.5 block cursor-pointer text-[10px] leading-3 text-slate-400 transition hover:text-slate-400"
                              onClick={() => setUpgradeModalOpen(true)}
                            >
                              This product needs attention<span className="ml-1.5 text-violet-400/70">Unlock in Pro</span>
                            </span>
                          )
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-5 py-3.5 text-[15px] font-semibold tabular-nums text-white">{formatNumber(row.views_24h)}</td>
                  <td className="px-5 py-3.5">
                    {!row.trend_is_synthetic && row.last_7_days_views.length > 0 ? (
                      <Sparkline values={row.last_7_days_views} />
                    ) : (
                      <span className="text-[13px] text-slate-400">—</span>
                    )}
                  </td>
                  <td className="px-5 py-3.5 tabular-nums">
                    {(row.cart_conversions_24h ?? 0) === 0 ? (
                      <span className="text-[14px] text-slate-500">No conversions</span>
                    ) : row.cart_abandonment_rate != null ? (
                      <span className={`text-[15px] font-semibold ${row.cart_abandonment_rate >= 0.8 ? "text-rose-400" : row.cart_abandonment_rate >= 0.5 ? "text-amber-400" : "text-slate-400"}`}>
                        {formatPct(row.cart_abandonment_rate)}
                      </span>
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                  <td className="px-5 py-3.5 text-[14px] tabular-nums text-slate-300">
                    {row.avg_dwell_24h != null ? `${formatDecimal(row.avg_dwell_24h, 1)}s` : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="px-5 py-3.5 text-[14px] tabular-nums text-slate-300">
                    {row.avg_scroll_24h != null ? `${formatDecimal(row.avg_scroll_24h, 0)}%` : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="px-5 py-3.5">
                    {row.engagement_score != null ? (
                      <span className="inline-flex items-center gap-2 tabular-nums" title="Engagement score 0–100%">
                        <span className={`text-[15px] font-semibold ${row.engagement_score >= 0.7 ? "text-emerald-400" : row.engagement_score >= 0.4 ? "text-amber-300" : "text-slate-500"}`}>
                          {Math.round(row.engagement_score * 100)}%
                        </span>
                        <span className={`text-[12px] font-bold ${row.engagement_score > 0.8 ? "text-emerald-500" : row.engagement_score >= 0.5 ? "text-amber-400/80" : "text-slate-600"}`}>
                          {row.engagement_score > 0.8 ? "High" : row.engagement_score >= 0.5 ? "Med" : "Low"}
                        </span>
                      </span>
                    ) : (
                      <span className="text-[13px] text-slate-400">No data</span>
                    )}
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-2">
                      <div className="h-2 w-20 overflow-hidden rounded-full bg-white/[0.07]">
                        <div
                          className={`h-full rounded-full ${row.priority === "HIGH" ? "bg-rose-400/70" : row.priority === "MED" ? "bg-amber-300/70" : "bg-slate-600/70"}`}
                          style={{ width: `${Math.round(row.attention_score * 100)}%` }}
                        />
                      </div>
                      <span className="text-[13px] font-semibold tabular-nums text-slate-400">{Math.round(row.attention_score * 100)}</span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    {row.estimated_loss != null ? (
                      isProUser ? (
                        <span className="cursor-default" title="Estimated from views × baseline conversion × AOV">
                          <span className="block text-[12px] tabular-nums text-amber-400/80">{formatMoneyCompact(row.estimated_loss, resolvedCcy)} potential lost</span>
                          <span className="block text-[10px] text-slate-400">based on 2% conversion · {formatMoneyCompact(resolvedAov, resolvedCcy)} AOV{!resolvedAovIsReal ? " (est.)" : ""}</span>
                        </span>
                      ) : (
                        <span
                          role="button"
                          className="cursor-pointer text-[12px] text-slate-400 transition hover:text-slate-400"
                          title="Upgrade to Pro to see estimated revenue loss"
                          onClick={() => setUpgradeModalOpen(true)}
                        >
                          Revenue at risk<span className="ml-2 text-[11px] text-slate-400">(visible in Pro)</span>
                        </span>
                      )
                    ) : (
                      <span className="text-[11px] text-slate-700">—</span>
                    )}
                  </td>
                </tr>
              ))}
              {!isProUser && mergedProducts.length > 3 && (
                <tr className="border-t border-white/[0.04]">
                  <td colSpan={9} className="px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-[12px] text-slate-400">
                        + {mergedProducts.length - 3} more product{mergedProducts.length - 3 !== 1 ? "s" : ""} tracked
                      </span>
                      <button
                        className="text-[11px] text-violet-400 transition hover:text-violet-300"
                        onClick={() => setUpgradeModalOpen(true)}
                      >
                        See all products →
                      </button>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
