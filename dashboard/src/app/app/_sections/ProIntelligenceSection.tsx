"use client";

/**
 * ProIntelligenceSection — Pro-tier killer cassettoni: Forecast, Attribution,
 * LTV, Profit Intelligence, Gateway Products, Predicted LTV, Price + Market.
 *
 * Extracted from app/page.tsx PageInner (Phase Ω⁷ split). Takes a typed
 * props bag; parent owns all state.
 */

import { SectionHeading } from "../_components/SectionHeading";
import { GatewayProducts } from "../../components/GatewayProducts";
import { PnlReport } from "../../components/PnlReport";
import { PredictedLtv } from "../../components/PredictedLtv";
import { formatDisplayMoney, type DisplayCurrency } from "../../lib/currency";
import { prettyText, formatMoneyCompact } from "../_lib/formatters";

// These types are loose on purpose — the parent passes through the OpenAPI-
// generated types, which are specific enough for the parent to bind safely.
// Using `any` here would give up type-checking the JSX; instead we accept
// unknown and narrow inside each branch with optional chaining.
/* eslint-disable @typescript-eslint/no-explicit-any */
export interface ProIntelligenceSectionProps {
  displayCurrency: DisplayCurrency;
  forecastData: any;
  attrSummary: any;
  ltvData: any;
  pnlData: any;
  gatewayProductsData: any;
  predictedLtvData: any;
  priceIntel: any[];
  marketIntel: any[];
}

export function ProIntelligenceSection(p: ProIntelligenceSectionProps) {
  const {
    displayCurrency, forecastData, attrSummary, ltvData, pnlData,
    gatewayProductsData, predictedLtvData, priceIntel, marketIntel,
  } = p;

  return (
    <section id="section-pro-intelligence">
      {/* Revenue Forecast */}
      <SectionHeading
        eyebrow="Forecast"
        title="Revenue outlook"
        description="Where your revenue is heading based on real order history."
        pro
      />
      {forecastData ? (
        <div className="mb-8 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
          {forecastData.confidence ? (
            <div className="grid gap-4 sm:grid-cols-3">
              <div>
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">7-day forecast</div>
                <div className="mt-1 text-2xl font-bold text-white">
                  {formatMoneyCompact(forecastData.forecast_7d?.revenue ?? 0, forecastData.currency)}
                </div>
                <div className="mt-0.5 text-[11px] text-slate-500">
                  range {formatMoneyCompact(forecastData.forecast_7d?.revenue_low ?? 0, forecastData.currency)}
                  {" — "}
                  {formatMoneyCompact(forecastData.forecast_7d?.revenue_high ?? 0, forecastData.currency)}
                </div>
              </div>
              <div>
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">30-day forecast</div>
                <div className="mt-1 text-2xl font-bold text-white">
                  {formatMoneyCompact(forecastData.forecast_30d?.revenue ?? 0, forecastData.currency)}
                </div>
                <div className="mt-0.5 text-[11px] text-slate-500">
                  range {formatMoneyCompact(forecastData.forecast_30d?.revenue_low ?? 0, forecastData.currency)}
                  {" — "}
                  {formatMoneyCompact(forecastData.forecast_30d?.revenue_high ?? 0, forecastData.currency)}
                </div>
              </div>
              <div>
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Trend</div>
                <div className={`mt-1 text-2xl font-bold ${
                  forecastData.trend?.direction === "up" ? "text-emerald-400" :
                  forecastData.trend?.direction === "down" ? "text-rose-400" :
                  "text-slate-300"
                }`}>
                  {forecastData.trend?.direction === "up" ? "↑" :
                   forecastData.trend?.direction === "down" ? "↓" : "→"}{" "}
                  {Math.abs(forecastData.trend?.weekly_change_pct ?? 0).toFixed(1)}% / week
                </div>
                <div className="mt-0.5 text-[11px] text-slate-500">
                  Confidence: {forecastData.confidence}
                  {forecastData.seasonality_available ? " · seasonality detected" : ""}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-[12px] text-slate-500">
              {forecastData.confidence_reason === "no_order_history"
                ? "Revenue forecasting activates once you have order history. Keep selling — your forecast will build automatically."
                : `Building forecast — need more order data. ${forecastData.confidence_reason || ""}`}
            </p>
          )}
        </div>
      ) : (
        <div className="mb-8 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
          <div className="h-4 w-48 animate-pulse rounded bg-white/[0.05]" />
        </div>
      )}

      {/* Attribution + LTV side by side */}
      <div className="grid gap-6 xl:grid-cols-2">

        {/* Attribution Intelligence */}
        <div>
          <SectionHeading eyebrow="Attribution" title="Where revenue comes from" />
          {attrSummary ? (() => {
            const ordersTotal = attrSummary.orders_total;
            const ordersAttributed = attrSummary.orders_attributed;
            const attrRate = attrSummary.attribution_rate;
            const sources = attrSummary.top_sources_first_touch;
            const matchRate = attrSummary.first_vs_last_match_rate;
            const maxRev = sources.length > 0 ? Math.max(...sources.map((s: any) => s.revenue), 1) : 1;
            const sourcePalette = ["#c4b5fd", "#e8a04e", "#34d399", "#fb923c", "#d946ef"];

            return (
              <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
                <div className="mb-5">
                  <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#c4b5fd]">
                    Attribution Intelligence
                  </div>
                  <h3 className="mt-1 text-[15px] font-bold leading-tight text-white">
                    Which channels actually drive revenue
                  </h3>
                  <p className="mt-1.5 text-[12px] leading-relaxed text-slate-400">
                    {ordersTotal > 0
                      ? `${ordersAttributed} of your ${ordersTotal} orders are attributed to a specific traffic source — ${Math.round(attrRate * 100)}% coverage. Keep the tracker active to close the gap.`
                      : "Attribution data builds as visitors convert. Keep the tracker active."}
                  </p>
                </div>

                <div className="mb-6 grid grid-cols-3 gap-3">
                  <div className="rounded-xl border px-4 py-3" style={{ borderColor: "rgba(196, 181, 253, 0.18)", backgroundColor: "rgba(196, 181, 253, 0.04)" }}>
                    <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Orders tracked</div>
                    <div className="mt-1 text-[22px] font-extrabold tabular-nums leading-none text-white">
                      {ordersTotal.toLocaleString()}
                    </div>
                    <div className="mt-1 text-[10px] text-slate-500">last 30 days</div>
                  </div>
                  <div className="rounded-xl border px-4 py-3" style={{ borderColor: "rgba(52, 211, 153, 0.22)", backgroundColor: "rgba(52, 211, 153, 0.05)" }}>
                    <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Attributed</div>
                    <div className="mt-1 text-[22px] font-extrabold tabular-nums leading-none text-emerald-400">
                      {ordersAttributed.toLocaleString()}
                    </div>
                    <div className="mt-1 text-[10px] text-slate-500">source identified</div>
                  </div>
                  <div
                    className="rounded-xl border px-4 py-3"
                    style={{
                      borderColor: attrRate > 0.7 ? "rgba(52, 211, 153, 0.22)" : "rgba(232, 160, 78, 0.22)",
                      backgroundColor: attrRate > 0.7 ? "rgba(52, 211, 153, 0.05)" : "rgba(232, 160, 78, 0.05)",
                    }}
                  >
                    <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Coverage</div>
                    <div
                      className="mt-1 text-[22px] font-extrabold tabular-nums leading-none"
                      style={{ color: attrRate > 0.7 ? "#34d399" : "#e8a04e" }}
                    >
                      {Math.round(attrRate * 100)}%
                    </div>
                    <div className="mt-1 text-[10px] text-slate-500">
                      {attrRate > 0.7 ? "strong signal" : "improving"}
                    </div>
                  </div>
                </div>

                {sources.length > 0 ? (
                  <div>
                    <div className="mb-2 flex items-center justify-between">
                      <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-500">
                        Top sources (first touch)
                      </div>
                      <div className="text-[10px] text-slate-600">revenue per channel</div>
                    </div>
                    <div className="space-y-2">
                      {sources.slice(0, 5).map((s: any, i: number) => {
                        const rev = s.revenue;
                        const width = Math.max(6, Math.round((rev / maxRev) * 100));
                        const color = sourcePalette[i % sourcePalette.length];
                        return (
                          <div key={`${s.source}-${i}`} className="group flex items-center gap-3 text-[11px]">
                            <span
                              className="h-1.5 w-1.5 flex-shrink-0 rounded-full"
                              style={{ backgroundColor: color, boxShadow: `0 0 6px ${color}66` }}
                            />
                            <span className="w-20 flex-shrink-0 truncate font-semibold text-slate-200">
                              {s.label || s.source || "—"}
                            </span>
                            <span className="w-14 flex-shrink-0 tabular-nums text-slate-500">
                              {s.orders} orders
                            </span>
                            <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-white/[0.04]">
                              <div
                                className="h-full rounded-full transition-all duration-500"
                                style={{
                                  width: `${width}%`,
                                  background: `linear-gradient(90deg, ${color} 0%, ${color}aa 100%)`,
                                  boxShadow: `0 0 10px -2px ${color}66`,
                                }}
                              />
                            </div>
                            <span className="w-16 flex-shrink-0 text-right font-bold tabular-nums text-white">
                              {formatDisplayMoney(rev, "USD", displayCurrency)}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : (
                  <p className="text-[12px] text-slate-500">
                    Attribution data builds as visitors convert. Keep the tracker active.
                  </p>
                )}

                {matchRate != null && ordersAttributed > 0 && (
                  <div
                    className="mt-5 rounded-xl border px-4 py-3"
                    style={{
                      borderColor: matchRate > 0.8 ? "rgba(52, 211, 153, 0.18)" : "rgba(232, 160, 78, 0.18)",
                      backgroundColor: matchRate > 0.8 ? "rgba(52, 211, 153, 0.04)" : "rgba(232, 160, 78, 0.04)",
                    }}
                  >
                    <div className="flex items-start gap-3">
                      <span
                        className="mt-1 h-1.5 w-1.5 flex-shrink-0 rounded-full"
                        style={{
                          backgroundColor: matchRate > 0.8 ? "#34d399" : "#e8a04e",
                          boxShadow: `0 0 6px ${matchRate > 0.8 ? "#34d39988" : "#e8a04e88"}`,
                        }}
                      />
                      <p className="text-[12px] leading-relaxed text-slate-300">
                        <strong className="text-white">{Math.round(matchRate * 100)}%</strong> of conversions had the same first and last touch source —{" "}
                        {matchRate > 0.8
                          ? "most customers buy from the channel that first brought them."
                          : "customers are discovering you on one channel and converting on another."}
                      </p>
                    </div>
                  </div>
                )}

                <div className="mt-5 inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3 py-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-[#c4b5fd] shadow-[0_0_8px_rgba(196,181,253,0.6)]" />
                  <span className="text-[10px] text-slate-400">
                    First-party tracking · no third-party cookies · visitor-to-order chain
                  </span>
                </div>
              </div>
            );
          })() : (
            <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-6">
              <div className="h-4 w-40 animate-pulse rounded bg-white/[0.05]" />
              <div className="mt-4 h-20 animate-pulse rounded bg-white/[0.03]" />
            </div>
          )}
        </div>

        {/* Customer Economics (LTV) */}
        <div>
          <SectionHeading eyebrow="Lifetime Value" title="Customer economics" />
          {ltvData ? (() => {
            const overall = ltvData.overall;
            const cohorts = ltvData.cohorts;
            const coverage = ltvData.customer_coverage;
            const totalCustomers = overall.total_customers;
            const repeatRate = overall.repeat_rate;
            const avgRevenue = overall.avg_revenue_per_customer;
            const avgOrders = overall.avg_orders_per_customer;
            const repeatCount = overall.repeat_customers;
            const repeatColor = repeatRate > 0.3 ? "#34d399" : repeatRate > 0.15 ? "#e8a04e" : "#fb923c";
            const maxCohortRevenue = cohorts.length > 0 ? Math.max(...cohorts.map((c: any) => c.revenue_total), 1) : 1;
            return (
              <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
                <div className="mb-5">
                  <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#e8a04e]">
                    Customer Economics
                  </div>
                  <h3 className="mt-1 text-[15px] font-bold leading-tight text-white">
                    Who keeps coming back, and what they&apos;re worth
                  </h3>
                  <p className="mt-1.5 text-[12px] leading-relaxed text-slate-400">
                    {totalCustomers > 0
                      ? `Your ${totalCustomers} identified customers average ${formatDisplayMoney(avgRevenue, "USD", displayCurrency)} lifetime revenue. ${repeatCount} have come back for a second order.`
                      : "Customer economics activate once your first orders are attributed to identifiable customers."}
                  </p>
                </div>

                <div className="mb-6 grid grid-cols-3 gap-3">
                  <div
                    className="rounded-xl border px-4 py-3"
                    style={{ borderColor: "rgba(232, 160, 78, 0.18)", backgroundColor: "rgba(232, 160, 78, 0.04)" }}
                  >
                    <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Customers</div>
                    <div className="mt-1 text-[26px] font-extrabold tabular-nums leading-none text-white">
                      {totalCustomers.toLocaleString()}
                    </div>
                    <div className="mt-1 text-[10px] text-slate-500">identified</div>
                  </div>
                  <div
                    className="rounded-xl border px-4 py-3"
                    style={{ borderColor: `${repeatColor}40`, backgroundColor: `${repeatColor}0f` }}
                  >
                    <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Repeat rate</div>
                    <div className="mt-1 text-[26px] font-extrabold tabular-nums leading-none" style={{ color: repeatColor }}>
                      {(repeatRate * 100).toFixed(0)}%
                    </div>
                    <div className="mt-1 text-[10px] text-slate-500">
                      {avgOrders.toFixed(1)} orders / customer
                    </div>
                  </div>
                  <div
                    className="rounded-xl border px-4 py-3"
                    style={{ borderColor: "rgba(52, 211, 153, 0.22)", backgroundColor: "rgba(52, 211, 153, 0.06)" }}
                  >
                    <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Avg customer value</div>
                    <div className="mt-1 text-[26px] font-extrabold tabular-nums leading-none text-emerald-400">
                      {formatDisplayMoney(avgRevenue, "USD", displayCurrency)}
                    </div>
                    <div className="mt-1 text-[10px] text-slate-500">lifetime revenue</div>
                  </div>
                </div>

                {cohorts.length > 0 ? (
                  <div>
                    <div className="mb-2 flex items-center justify-between">
                      <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-500">
                        Monthly cohorts
                      </div>
                      <div className="text-[10px] text-slate-600">revenue by acquisition month</div>
                    </div>
                    <div className="space-y-2">
                      {cohorts.slice(0, 6).map((c: any, i: number) => {
                        const revenue = c.revenue_total;
                        const width = Math.max(6, Math.round((revenue / maxCohortRevenue) * 100));
                        const isRecent = i === 0;
                        const barColor = isRecent ? "#e8a04e" : "rgba(232, 160, 78, 0.55)";
                        return (
                          <div key={c.cohort_month} className="group flex items-center gap-3 text-[11px]">
                            <span className="w-16 flex-shrink-0 font-mono text-slate-500">
                              {c.cohort_month}
                            </span>
                            <span className="w-14 flex-shrink-0 tabular-nums text-slate-500">
                              {c.size} cust
                            </span>
                            <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-white/[0.04]">
                              <div
                                className="h-full rounded-full transition-all duration-500"
                                style={{
                                  width: `${width}%`,
                                  backgroundColor: barColor,
                                  boxShadow: isRecent ? `0 0 10px -2px ${barColor}88` : undefined,
                                }}
                              />
                            </div>
                            <span className="w-16 flex-shrink-0 text-right font-bold tabular-nums text-white">
                              {formatDisplayMoney(revenue, "USD", displayCurrency)}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : (
                  <p className="text-[12px] text-slate-500">
                    Cohort data builds from orders with customer identifiers.
                  </p>
                )}

                {coverage.coverage_rate != null && totalCustomers > 0 && (
                  <div className="mt-5 inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3 py-1">
                    <span
                      className="h-1.5 w-1.5 rounded-full"
                      style={{ backgroundColor: coverage.coverage_rate > 0.7 ? "#34d399" : "#fb923c" }}
                    />
                    <span className="text-[10px] text-slate-400">
                      {Math.round(coverage.coverage_rate * 100)}% of orders have customer identity
                    </span>
                  </div>
                )}
              </div>
            );
          })() : (
            <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-6">
              <div className="h-4 w-40 animate-pulse rounded bg-white/[0.05]" />
              <div className="mt-4 h-20 animate-pulse rounded bg-white/[0.03]" />
            </div>
          )}
        </div>

      </div>

      {/* Profit Intelligence */}
      <div className="mt-6">
        <SectionHeading eyebrow="Profit Intelligence" title="What you actually keep" />
        <PnlReport data={pnlData} displayCurrency={displayCurrency} />
      </div>

      {/* Gateway Products + Predicted LTV */}
      <div className="mt-6 grid gap-4 xl:grid-cols-2">
        <GatewayProducts data={gatewayProductsData} displayCurrency={displayCurrency} />
        <PredictedLtv data={predictedLtvData} displayCurrency={displayCurrency} />
      </div>

      {/* Price + Market Intelligence */}
      <div className="mt-10">
        <SectionHeading eyebrow="Market Position" title="Know where you stand" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div id="section-price-intelligence">
          <div className="h-full rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
            <div className="mb-4 text-[16px] font-bold text-[#e8a04e]">Price Intelligence</div>
            {priceIntel.length === 0 ? (
              <p className="text-[14px] text-slate-500">No pricing data yet.</p>
            ) : (
              <div className="space-y-3">
                {priceIntel.slice(0, 3).map((item: any, i: number) => (
                  <div key={`price-compact-${i}`} className="border-t border-white/[0.05] pt-3 first:border-0 first:pt-0">
                    <div className="mb-1 flex flex-wrap items-center gap-1.5">
                      {item.market_status && (
                        <span className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-slate-400 ring-1 ring-white/10">
                          {prettyText(String(item.market_status))}
                        </span>
                      )}
                      {item.price_position && (
                        <span className="rounded-full bg-cyan-500/10 px-2 py-0.5 text-[10px] text-cyan-300 ring-1 ring-cyan-400/20">
                          {prettyText(String(item.price_position))}
                        </span>
                      )}
                    </div>
                    <div className="truncate text-[14px] font-medium text-white">{item.product_name || "—"}</div>
                    {item.recommended_price_action && (
                      <div className="mt-0.5 text-[13px] text-slate-500">{String(item.recommended_price_action)}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <div id="section-market-intelligence">
          <div className="h-full rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
            <div className="mb-4 text-[16px] font-bold text-[#e8a04e]">Market Intelligence</div>
            {marketIntel.length === 0 ? (
              <p className="text-[14px] text-slate-500">No market data yet.</p>
            ) : (
              <div className="space-y-3">
                {marketIntel.slice(0, 3).map((item: any, i: number) => (
                  <div key={`market-compact-${i}`} className="border-t border-white/[0.05] pt-3 first:border-0 first:pt-0">
                    <div className="mb-1 flex flex-wrap items-center gap-1.5">
                      {item.uniqueness_hint && (
                        <span className="rounded-full bg-violet-500/10 px-2 py-0.5 text-[10px] text-violet-300 ring-1 ring-violet-400/20">
                          {prettyText(String(item.uniqueness_hint))}
                        </span>
                      )}
                    </div>
                    <div className="truncate text-[14px] font-medium text-white">{item.product_name || "—"}</div>
                    {item.recommended_next_step && (
                      <div className="mt-0.5 text-[13px] text-slate-500">{prettyText(String(item.recommended_next_step))}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
