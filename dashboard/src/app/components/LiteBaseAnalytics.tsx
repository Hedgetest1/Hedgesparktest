"use client";

/**
 * LiteBaseAnalytics — 4 base-analytics tiles wired into existing
 * Lite-floor sections per founder directive 2026-04-26 ("B-super").
 *
 * Each tile closes a documented competitor-parity gap vs $0-70:
 *   - DeviceSplitTile          Shopify Free baseline
 *   - TopCustomersLtvTile      Lifetimely Free
 *   - AbandonmentTrendTile     Shopify Free
 *   - FirstVsRepeatAovTile     Lifetimely Free
 *
 * No new top-level sections — tiles fit inside existing
 * section-lite-{today, last7, retention} so the floor stays compact.
 *
 * Currency-aware via shared `displayCurrency` prop. CardError /
 * skeleton states inline (no SectionErrorBoundary swallow).
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "../app/_lib/formatters";
import { DeltaIndicator } from "./DeltaIndicator";
import { useTileFetch } from "./useTileFetch";

type DisplayCurrency = "USD" | "EUR" | string;

// ── Shared skeleton + error primitives ────────────────────────────
//
// These are intentionally compact variants of the canonical primitives
// in `_CardStates.tsx` — same a11y posture (role + aria-live), same
// retry semantics, but tighter padding and a height-prop for tiles
// that nest INSIDE larger Lite sections (instead of standing alone as
// full-width Pro cards). The a11y attributes match _CardStates exactly
// so screen readers behave identically across the dashboard.
function TileSkeleton({ height = 80, label }: { height?: number; label?: string }) {
  return (
    <div
      className="animate-pulse rounded-xl border border-white/[0.05] bg-[#0e0e1a]/40"
      style={{ height: `${height}px` }}
      role="status"
      aria-live="polite"
      aria-label={label || "Loading"}
    >
      <span className="sr-only">{label || "Loading content"}</span>
    </div>
  );
}

function TileError({ retry, message, label }: { retry: () => void; message?: string; label?: string }) {
  return (
    <div
      className="rounded-xl border border-rose-400/20 bg-rose-500/[0.06] p-4 text-center"
      role="alert"
      aria-label={label || "Tile failed to load"}
    >
      <div className="text-[12px] text-rose-300">
        {message || "Couldn't load this tile. Your other metrics are unaffected."}
      </div>
      <button
        type="button"
        onClick={retry}
        className="mt-2 rounded-md border border-rose-400/30 bg-rose-500/10 px-3 py-1 text-[11px] font-semibold text-rose-200 transition hover:bg-rose-500/20 focus:outline-none focus:ring-2 focus:ring-rose-300/50"
      >
        Try again
      </button>
    </div>
  );
}

function TileEmpty({ title, hint }: { title: string; hint: string }) {
  return (
    <div
      className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/40 p-4 text-center"
      role="status"
      aria-label={`${title}: ${hint}`}
    >
      <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
        {title}
      </div>
      <div className="mt-2 text-[12px] text-slate-300">{hint}</div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// 1. Device split — tile fits inside section-lite-today
// ─────────────────────────────────────────────────────────────────

type DeviceData = {
  days: number; total_sessions: number; has_data: boolean;
  slices: { device: string; sessions: number; pct: number }[];
  compare?: { total_sessions: number } | null;
};

const DEVICE_COLOR: Record<string, string> = {
  mobile: "#a78bfa", desktop: "#34d399", tablet: "#fbbf24", unknown: "#94a3b8",
};
const DEVICE_LABEL: Record<string, string> = {
  mobile: "Mobile", desktop: "Desktop", tablet: "Tablet", unknown: "Other",
};

export function DeviceSplitTile() {
  const { data, loading, error, retry } = useTileFetch<DeviceData>(
    (query) => apiClient.GET("/analytics/device-breakdown", { params: { query } })
      .then(r => ({ data: r.data as unknown as DeviceData, error: r.error })),
  );
  if (loading) return <TileSkeleton height={120} />;
  if (error) return <TileError retry={retry} />;
  if (!data || !data.has_data) {
    return (
      <TileEmpty title="Device split" hint={`No traffic in the last ${data?.days ?? 14} days yet.`} />
    );
  }

  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-3 gap-2">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Device split · last {data.days} days
        </div>
        <div className="flex items-center gap-2">
          <div className="text-[11px] tabular-nums text-slate-300">
            {data.total_sessions.toLocaleString("en-US")} sessions
          </div>
          {data.compare != null && (
            <DeltaIndicator
              value={data.total_sessions}
              prevValue={data.compare.total_sessions}
              format="count"
            />
          )}
        </div>
      </div>
      <div className="space-y-2">
        {data.slices.map(s => (
          <div key={s.device} className="flex items-center gap-3">
            <div className="w-16 flex-shrink-0 text-[12px] text-slate-300">{DEVICE_LABEL[s.device] ?? s.device}</div>
            <div className="flex-1 h-2 rounded-full bg-white/[0.04] overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{ width: `${s.pct}%`, background: DEVICE_COLOR[s.device] ?? "#94a3b8" }}
              />
            </div>
            <div className="w-12 flex-shrink-0 text-right text-[12px] font-semibold tabular-nums text-slate-200">
              {s.pct.toFixed(0)}%
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// 2. Top customers by LTV — tile fits inside section-lite-retention
// ─────────────────────────────────────────────────────────────────

type TopCustomersData = {
  currency: string; has_data: boolean;
  customers: {
    customer_email_hash: string; total_spent: number;
    order_count: number; first_order_at: string | null; last_order_at: string | null;
  }[];
};

export function TopCustomersLtvTile({ displayCurrency }: { displayCurrency: DisplayCurrency }) {
  const [data, setData] = useState<TopCustomersData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true); setError(false);
    apiClient.GET("/analytics/top-customers-ltv")
      .then(({ data: j, error: err }) => {
        if (!active) return;
        if (err || !j) setError(true);
        else setData(j as unknown as TopCustomersData);
      })
      .catch(() => { if (active) setError(true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tick]);

  const ccy = data?.currency ?? displayCurrency;

  if (loading) return <TileSkeleton height={280} />;
  if (error) return <TileError retry={() => setTick(t => t + 1)} />;
  if (!data || !data.has_data) {
    return (
      <TileEmpty title="Top customers · all-time" hint="Once orders flow, your highest-LTV buyers rank here." />
    );
  }

  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-3">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Top customers · ranked by lifetime spend
        </div>
        <div className="text-[10px] text-slate-300">PII-safe · email hashed</div>
      </div>
      <ul className="divide-y divide-white/[0.04]">
        {data.customers.map((c, i) => (
          <li key={c.customer_email_hash} className="flex items-center gap-3 py-2">
            <div className="w-6 text-right text-[12px] font-bold tabular-nums text-[#e8a04e]">
              {i + 1}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-[12px] font-mono text-slate-300 truncate">{c.customer_email_hash}</div>
              <div className="text-[11px] text-slate-300">
                {c.order_count} order{c.order_count !== 1 ? "s" : ""}
              </div>
            </div>
            <div className="text-[14px] font-bold tabular-nums text-emerald-300">
              {formatMoneyCompact(c.total_spent, ccy)}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// 3. Abandonment trend — tile fits inside section-lite-last7
// ─────────────────────────────────────────────────────────────────

type AbandonmentTrendData = {
  days: number; timezone: string; has_data: boolean;
  series: { day: string; cart_adds: number; purchases: number; abandonment_pct: number | null }[];
  avg_abandonment_pct: number | null;
  compare?: { avg_abandonment_pct: number | null } | null;
};

export function AbandonmentTrendTile() {
  const { data, loading, error, retry } = useTileFetch<AbandonmentTrendData>(
    (query) => apiClient.GET("/analytics/abandonment-trend", { params: { query } })
      .then(r => ({ data: r.data as unknown as AbandonmentTrendData, error: r.error })),
  );
  if (loading) return <TileSkeleton height={140} />;
  if (error) return <TileError retry={retry} />;
  if (!data || !data.has_data) {
    return (
      <TileEmpty title="Cart abandonment trend" hint={`No cart events in the last ${data?.days ?? 14} days yet.`} />
    );
  }

  // Render small bar series, height proportional to abandonment_pct.
  const maxPct = Math.max(...data.series.map(s => s.abandonment_pct ?? 0), 1);
  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-3 gap-2">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Cart abandonment · last {data.days} days
        </div>
        <div className="flex items-center gap-2">
          <div className="text-[14px] font-bold tabular-nums text-rose-300">
            {data.avg_abandonment_pct != null ? `${data.avg_abandonment_pct}% avg` : "—"}
          </div>
          {data.compare != null && data.avg_abandonment_pct != null && data.compare.avg_abandonment_pct != null && (
            <DeltaIndicator
              value={data.avg_abandonment_pct}
              prevValue={data.compare.avg_abandonment_pct}
              format="pct"
              inverse={true}
            />
          )}
        </div>
      </div>
      <div className="flex items-end gap-1 h-20">
        {data.series.map(s => {
          const h = s.abandonment_pct != null ? Math.max(2, (s.abandonment_pct / maxPct) * 100) : 0;
          return (
            <div
              key={s.day}
              className="flex-1 rounded-t bg-gradient-to-t from-rose-500/40 to-rose-400/80"
              style={{ height: `${h}%` }}
              title={`${s.day}: ${s.cart_adds} carts, ${s.purchases} buys, ${s.abandonment_pct ?? "—"}% abandoned`}
            />
          );
        })}
      </div>
      <div className="mt-2 flex justify-between text-[10px] text-slate-300">
        <span>{data.series[0]?.day}</span>
        <span>{data.series[data.series.length - 1]?.day}</span>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// 5. Order rhythm — hour-of-day + day-of-week (Class C1)
// ─────────────────────────────────────────────────────────────────

type RhythmData = {
  currency: string; timezone: string; days: number; has_data: boolean;
  by_hour: { hour: number; orders: number; revenue: number }[];
  by_dow: { dow: number; label: string; orders: number; revenue: number }[];
  peak_hour: number | null; peak_dow: number | null;
  compare?: { total_orders: number; total_revenue: number } | null;
};

export function OrderRhythmTile() {
  const { data, loading, error, retry } = useTileFetch<RhythmData>(
    (query) => apiClient.GET("/analytics/order-rhythm", { params: { query } })
      .then(r => ({ data: r.data as unknown as RhythmData, error: r.error })),
  );
  if (loading) return <TileSkeleton height={180} />;
  if (error) return <TileError retry={retry} />;
  if (!data || !data.has_data) {
    return (
      <TileEmpty title="When customers buy" hint="Once orders flow, peak hour + day surface here." />
    );
  }

  const maxHourOrders = Math.max(1, ...data.by_hour.map(h => h.orders));
  const maxDowOrders  = Math.max(1, ...data.by_dow.map(d => d.orders));

  const peakHourLabel = data.peak_hour != null
    ? `${data.peak_hour.toString().padStart(2, "0")}:00`
    : "—";
  const peakDowLabel = data.peak_dow != null ? data.by_dow[data.peak_dow].label : "—";

  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-3 gap-2 flex-wrap">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-300">
          When customers buy · last {data.days} days
        </div>
        <div className="flex items-center gap-2">
          <div className="text-[11px] text-slate-300">
            Peak: <span className="font-bold text-amber-300">{peakDowLabel} {peakHourLabel}</span>
          </div>
          {data.compare != null && (
            <DeltaIndicator
              value={data.by_hour.reduce((s, h) => s + h.revenue, 0)}
              prevValue={data.compare.total_revenue}
              format="currency"
            />
          )}
        </div>
      </div>

      <div className="mb-3">
        <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-300">Hour of day</div>
        <div className="flex items-end gap-[2px] h-16">
          {data.by_hour.map(h => {
            const isPeak = h.hour === data.peak_hour;
            const ratio = h.orders / maxHourOrders;
            return (
              <div key={h.hour} className="flex-1 flex flex-col justify-end" title={`${h.hour}:00 — ${h.orders} orders`}>
                <div
                  className="rounded-t"
                  style={{
                    height: `${Math.max(2, ratio * 100)}%`,
                    background: isPeak ? "#fbbf24" : "rgba(34,211,238,0.4)",
                    boxShadow: isPeak ? "0 0 6px rgba(251,191,36,0.6)" : "none",
                  }}
                />
              </div>
            );
          })}
        </div>
        <div className="mt-1 flex justify-between text-[9px] text-slate-300">
          <span>00</span><span>06</span><span>12</span><span>18</span><span>23</span>
        </div>
      </div>

      <div>
        <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-300">Day of week</div>
        <div className="grid grid-cols-7 gap-1">
          {data.by_dow.map(d => {
            const isPeak = d.dow === data.peak_dow;
            const ratio = d.orders / maxDowOrders;
            return (
              <div key={d.dow} className="flex flex-col items-center" title={`${d.label} — ${d.orders} orders`}>
                <div className="w-full h-8 flex items-end">
                  <div
                    className="w-full rounded-t"
                    style={{
                      height: `${Math.max(2, ratio * 100)}%`,
                      background: isPeak ? "#fbbf24" : "rgba(34,211,238,0.4)",
                    }}
                  />
                </div>
                <div className={`mt-1 text-[10px] font-semibold ${isPeak ? "text-amber-300" : "text-slate-300"}`}>
                  {d.label}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// 6. Repeat cadence — time between consecutive orders (Class C2)
// ─────────────────────────────────────────────────────────────────

type CadenceData = {
  has_data: boolean;
  customers_with_2plus: number;
  intervals_count: number;
  median_days: number | null;
  p25_days: number | null;
  p75_days: number | null;
  mean_days: number | null;
  compare?: { median_days: number | null; customers_with_2plus: number } | null;
};

export function RepeatCadenceTile() {
  const { data, loading, error, retry } = useTileFetch<CadenceData>(
    (query) => apiClient.GET("/analytics/repeat-cadence", { params: { query } })
      .then(r => ({ data: r.data as unknown as CadenceData, error: r.error })),
  );
  if (loading) return <TileSkeleton height={120} />;
  if (error) return <TileError retry={retry} />;
  if (!data || !data.has_data) {
    return (
      <TileEmpty title="Time between orders" hint="Once 2+ customers come back, the median cadence shows here." />
    );
  }

  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-3 gap-2 flex-wrap">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-300">
          Time between orders · {data.customers_with_2plus} repeat customers
        </div>
        {data.compare != null && data.median_days != null && data.compare.median_days != null && (
          <DeltaIndicator
            value={data.median_days}
            prevValue={data.compare.median_days}
            format="count"
            inverse={true}
          />
        )}
      </div>
      <div className="flex items-end gap-3">
        <div>
          <div className="text-[2.25rem] font-extrabold leading-none tabular-nums text-emerald-300">
            {data.median_days?.toFixed(0) ?? "—"}
          </div>
          <div className="text-[11px] text-slate-300">days median</div>
        </div>
        <div className="ml-auto text-right">
          <div className="text-[11px] text-slate-300">25-75 percentile range</div>
          <div className="text-[14px] font-semibold text-slate-300">
            {data.p25_days?.toFixed(0) ?? "—"} — {data.p75_days?.toFixed(0) ?? "—"} days
          </div>
          <div className="mt-1 text-[10px] text-slate-300">
            Mean {data.mean_days?.toFixed(0) ?? "—"}d · {data.intervals_count} intervals
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// 7. Top products — most-bought items by revenue (Class C3)
// ─────────────────────────────────────────────────────────────────

type TopProductsData = {
  currency: string; days: number; has_data: boolean;
  products: { title: string; orders: number; units: number; revenue: number }[];
  compare?: { top_revenue: number; total_orders: number } | null;
};

export function TopProductsTile({ displayCurrency }: { displayCurrency: DisplayCurrency }) {
  const { data, loading, error, retry } = useTileFetch<TopProductsData>(
    (query) => apiClient.GET("/analytics/top-products", { params: { query } })
      .then(r => ({ data: r.data as unknown as TopProductsData, error: r.error })),
  );
  const ccy = data?.currency ?? displayCurrency;
  if (loading) return <TileSkeleton height={220} />;
  if (error) return <TileError retry={retry} />;
  if (!data || !data.has_data) {
    return (
      <TileEmpty title="Top products · revenue" hint="Once line-items flow, your best sellers rank here." />
    );
  }

  const maxRev = Math.max(1, ...data.products.map(p => p.revenue));

  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-3 gap-2">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-300">
          Top products · last {data.days} days
        </div>
        <div className="flex items-center gap-2">
          <div className="text-[11px] text-slate-300">
            {data.products.length} ranked
          </div>
          {data.compare != null && data.products[0] != null && (
            <DeltaIndicator
              value={data.products[0].revenue}
              prevValue={data.compare.top_revenue}
              format="currency"
            />
          )}
        </div>
      </div>
      <ul className="space-y-2">
        {data.products.map((p, i) => (
          <li key={p.title + i} className="flex items-center gap-3">
            <div className="w-6 text-right text-[12px] font-bold tabular-nums text-[#e8a04e]">
              {i + 1}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-semibold text-slate-200 truncate">{p.title}</div>
              <div className="h-1.5 mt-1 rounded-full bg-white/[0.04] overflow-hidden">
                <div className="h-full bg-emerald-400/70" style={{ width: `${(p.revenue / maxRev) * 100}%` }} />
              </div>
            </div>
            <div className="text-right">
              <div className="text-[13px] font-bold tabular-nums text-emerald-300">
                {formatMoneyCompact(p.revenue, ccy)}
              </div>
              <div className="text-[10px] text-slate-300 tabular-nums">
                {p.units} unit{p.units !== 1 ? "s" : ""}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// 8-11. Class D — schema-enriched analytics (pixel v14+)
// ─────────────────────────────────────────────────────────────────

function CoverageBanner({ enriched, total }: { enriched: number; total: number }) {
  if (total === 0) return null;
  const pct = Math.round((enriched / total) * 100);
  return (
    <div className="mt-2 text-[10px] text-slate-300">
      Based on {enriched.toLocaleString("en-US")} of {total.toLocaleString("en-US")} orders ({pct}%) — older orders pre-pixel-v14 stay uncounted.
    </div>
  );
}

type DiscountData = {
  currency: string; days: number; has_data: boolean;
  enriched_orders: number; total_orders_window: number;
  codes: { code: string; orders: number; total_discount: number; total_revenue: number }[];
  compare?: { enriched_orders: number; total_discount: number } | null;
};

export function DiscountCodesTile({ displayCurrency }: { displayCurrency: DisplayCurrency }) {
  const { data, loading, error, retry } = useTileFetch<DiscountData>(
    (query) => apiClient.GET("/analytics/discount-codes", { params: { query } })
      .then(r => ({ data: r.data as unknown as DiscountData, error: r.error })),
  );
  const ccy = data?.currency ?? displayCurrency;
  if (loading) return <TileSkeleton height={180} />;
  if (error) return <TileError retry={retry} />;
  if (!data || !data.has_data) {
    return (
      <TileEmpty title="Discount codes" hint="Once orders flow with discount codes attached, the top performers rank here." />
    );
  }
  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-3 gap-2">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-300">
          Discount codes · last {data.days} days
        </div>
        {data.compare != null && (
          <DeltaIndicator
            value={data.enriched_orders}
            prevValue={data.compare.enriched_orders}
            format="count"
          />
        )}
      </div>
      <ul className="divide-y divide-white/[0.04]">
        {data.codes.slice(0, 5).map(c => (
          <li key={c.code} className="flex items-center gap-3 py-2">
            <div className="flex-1 min-w-0">
              <div className="text-[12px] font-mono font-bold text-amber-300 truncate">{c.code}</div>
              <div className="text-[10px] text-slate-300">
                {c.orders} order{c.orders !== 1 ? "s" : ""} · {formatMoneyCompact(c.total_discount, ccy)} discount given
              </div>
            </div>
            <div className="text-[13px] font-bold tabular-nums text-emerald-300">
              {formatMoneyCompact(c.total_revenue, ccy)}
            </div>
          </li>
        ))}
      </ul>
      <CoverageBanner enriched={data.enriched_orders} total={data.total_orders_window} />
    </div>
  );
}

type StatusData = {
  days: number; has_data: boolean; enriched_orders: number;
  financial: { label: string; orders: number; pct: number }[];
  fulfillment: { label: string; orders: number; pct: number }[];
  compare?: { enriched_orders: number } | null;
};

const STATUS_COLOR: Record<string, string> = {
  paid: "#34d399", pending: "#fbbf24", authorized: "#a78bfa", refunded: "#fb7185",
  fulfilled: "#34d399", unfulfilled: "#fbbf24", partial: "#a78bfa",
  unknown: "#94a3b8",
};

export function OrderStatusTile() {
  const { data, loading, error, retry } = useTileFetch<StatusData>(
    (query) => apiClient.GET("/analytics/order-status", { params: { query } })
      .then(r => ({ data: r.data as unknown as StatusData, error: r.error })),
  );
  if (loading) return <TileSkeleton height={180} />;
  if (error) return <TileError retry={retry} />;
  if (!data || !data.has_data) {
    return (
      <TileEmpty title="Order status breakdown" hint="New orders post pixel-v14 carry status; the breakdown surfaces here." />
    );
  }
  const renderBar = (b: { label: string; orders: number; pct: number }) => (
    <div key={b.label} className="flex items-center gap-3">
      <div className="w-24 flex-shrink-0 text-[12px] capitalize text-slate-300">{b.label}</div>
      <div className="flex-1 h-2 rounded-full bg-white/[0.04] overflow-hidden">
        <div className="h-full rounded-full"
             style={{ width: `${b.pct}%`, background: STATUS_COLOR[b.label] ?? "#94a3b8" }} />
      </div>
      <div className="w-20 text-right text-[12px] font-semibold tabular-nums text-slate-300">
        {b.orders} · {b.pct.toFixed(0)}%
      </div>
    </div>
  );
  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-3 gap-2">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-300">
          Order status · last {data.days} days
        </div>
        {data.compare != null && (
          <DeltaIndicator
            value={data.enriched_orders}
            prevValue={data.compare.enriched_orders}
            format="count"
          />
        )}
      </div>
      <div className="mb-3">
        <div className="mb-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-300">Financial</div>
        <div className="space-y-1.5">{data.financial.map(renderBar)}</div>
      </div>
      <div>
        <div className="mb-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-300">Fulfillment</div>
        <div className="space-y-1.5">{data.fulfillment.map(renderBar)}</div>
      </div>
      <div className="mt-2 text-[10px] text-slate-300">
        Updates live as refunds + fulfillments fire — orders/updated webhook ingests every state change.
      </div>
    </div>
  );
}

type TaxData = {
  currency: string; days: number; has_data: boolean;
  enriched_orders: number; total_orders_window: number;
  total_revenue: number; total_tax: number; tax_rate_pct: number | null;
  compare?: { total_tax: number; total_revenue: number } | null;
};

export function TaxBreakdownTile({ displayCurrency }: { displayCurrency: DisplayCurrency }) {
  const { data, loading, error, retry } = useTileFetch<TaxData>(
    (query) => apiClient.GET("/analytics/tax-breakdown", { params: { query } })
      .then(r => ({ data: r.data as unknown as TaxData, error: r.error })),
  );
  const ccy = data?.currency ?? displayCurrency;
  if (loading) return <TileSkeleton height={140} />;
  if (error) return <TileError retry={retry} />;
  if (!data || !data.has_data) {
    return (
      <TileEmpty title="Tax · paid by customers" hint="New orders post pixel-v14 carry tax; the total + effective rate surface here." />
    );
  }
  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-3 gap-2 flex-wrap">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-300">
          Tax collected · last {data.days} days
        </div>
        <div className="flex items-center gap-2">
          {data.tax_rate_pct != null && (
            <div className="text-[11px] text-slate-300">
              Effective rate: <span className="font-bold text-amber-300">{data.tax_rate_pct}%</span>
            </div>
          )}
          {data.compare != null && (
            <DeltaIndicator
              value={data.total_tax}
              prevValue={data.compare.total_tax}
              format="currency"
            />
          )}
        </div>
      </div>
      <div className="flex items-end gap-3">
        <div>
          <div className="text-[2.25rem] font-extrabold leading-none tabular-nums text-amber-300">
            {formatMoneyCompact(data.total_tax, ccy)}
          </div>
          <div className="text-[11px] text-slate-300">total tax</div>
        </div>
        <div className="ml-auto text-right">
          <div className="text-[11px] text-slate-300">on revenue of</div>
          <div className="text-[14px] font-semibold text-slate-300">
            {formatMoneyCompact(data.total_revenue, ccy)}
          </div>
        </div>
      </div>
      <CoverageBanner enriched={data.enriched_orders} total={data.total_orders_window} />
    </div>
  );
}

type PaymentData = {
  currency: string; days: number; has_data: boolean;
  enriched_orders: number; total_orders_window: number;
  methods: { method: string; orders: number; revenue: number; pct: number }[];
  compare?: { enriched_orders: number; total_revenue: number } | null;
};

export function PaymentMethodsTile({ displayCurrency }: { displayCurrency: DisplayCurrency }) {
  const { data, loading, error, retry } = useTileFetch<PaymentData>(
    (query) => apiClient.GET("/analytics/payment-methods", { params: { query } })
      .then(r => ({ data: r.data as unknown as PaymentData, error: r.error })),
  );
  const ccy = data?.currency ?? displayCurrency;
  if (loading) return <TileSkeleton height={180} />;
  if (error) return <TileError retry={retry} />;
  if (!data || !data.has_data) {
    return (
      <TileEmpty title="Payment methods" hint="Once gateway data flows in, the split surfaces here." />
    );
  }
  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-3 gap-2">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-300">
          Payment methods · last {data.days} days
        </div>
        {data.compare != null && (
          <DeltaIndicator
            value={data.methods.reduce((s, m) => s + m.revenue, 0)}
            prevValue={data.compare.total_revenue}
            format="currency"
          />
        )}
      </div>
      <ul className="space-y-2">
        {data.methods.slice(0, 6).map(m => (
          <li key={m.method} className="flex items-center gap-3">
            <div className="w-32 flex-shrink-0 text-[12px] capitalize text-slate-300 truncate">
              {m.method.replace(/_/g, " ")}
            </div>
            <div className="flex-1 h-2 rounded-full bg-white/[0.04] overflow-hidden">
              <div className="h-full rounded-full bg-emerald-400/70" style={{ width: `${m.pct}%` }} />
            </div>
            <div className="w-20 text-right text-[12px] font-semibold tabular-nums text-slate-300">
              {m.pct.toFixed(0)}%
            </div>
            <div className="w-20 text-right text-[12px] tabular-nums text-emerald-300">
              {formatMoneyCompact(m.revenue, ccy)}
            </div>
          </li>
        ))}
      </ul>
      <CoverageBanner enriched={data.enriched_orders} total={data.total_orders_window} />
    </div>
  );
}

type TopVariantsData = {
  currency: string; days: number; has_data: boolean;
  enriched_orders: number; total_orders_window: number;
  variants: { variant_id: string | null; product_title: string;
    variant_title: string | null; sku: string | null;
    units: number; revenue: number }[];
  compare?: { top_revenue: number; enriched_orders: number } | null;
};

export function TopVariantsTile({ displayCurrency }: { displayCurrency: DisplayCurrency }) {
  const { data, loading, error, retry } = useTileFetch<TopVariantsData>(
    (query) => apiClient.GET("/analytics/top-variants", { params: { query } })
      .then(r => ({ data: r.data as unknown as TopVariantsData, error: r.error })),
  );
  const ccy = data?.currency ?? displayCurrency;
  if (loading) return <TileSkeleton height={240} />;
  if (error) return <TileError retry={retry} />;
  if (!data || !data.has_data) {
    return (
      <TileEmpty title="Top variants · revenue" hint="New orders post pixel-v15 carry variant data; the top-sellers surface here." />
    );
  }
  const maxRev = Math.max(1, ...data.variants.map(v => v.revenue));
  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-3 gap-2">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-300">
          Top variants · last {data.days} days
        </div>
        {data.compare != null && data.variants[0] != null && (
          <DeltaIndicator
            value={data.variants[0].revenue}
            prevValue={data.compare.top_revenue}
            format="currency"
          />
        )}
      </div>
      <ul className="space-y-2">
        {data.variants.map((v, i) => (
          <li key={(v.variant_id ?? "") + i} className="flex items-center gap-3">
            <div className="w-6 text-right text-[12px] font-bold tabular-nums text-[#e8a04e]">
              {i + 1}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-semibold text-slate-200 truncate">
                {v.product_title}
                {v.variant_title && (
                  <span className="ml-1.5 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.06em] text-amber-300">
                    {v.variant_title}
                  </span>
                )}
              </div>
              <div className="h-1.5 mt-1 rounded-full bg-white/[0.04] overflow-hidden">
                <div className="h-full bg-emerald-400/70" style={{ width: `${(v.revenue / maxRev) * 100}%` }} />
              </div>
              {v.sku && <div className="mt-1 text-[10px] font-mono text-slate-300">SKU: {v.sku}</div>}
            </div>
            <div className="text-right">
              <div className="text-[13px] font-bold tabular-nums text-emerald-300">
                {formatMoneyCompact(v.revenue, ccy)}
              </div>
              <div className="text-[10px] text-slate-300 tabular-nums">
                {v.units} unit{v.units !== 1 ? "s" : ""}
              </div>
            </div>
          </li>
        ))}
      </ul>
      <CoverageBanner enriched={data.enriched_orders} total={data.total_orders_window} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// 4. First-vs-repeat AOV — tile fits inside section-lite-retention
// ─────────────────────────────────────────────────────────────────

type FirstVsRepeatData = {
  currency: string; has_data: boolean;
  first: { customers: number; orders: number; revenue: number; aov: number };
  repeat: { customers: number; orders: number; revenue: number; aov: number };
  aov_uplift_pct: number | null;
  compare?: {
    aov_uplift_pct: number | null;
    first_revenue: number;
    repeat_revenue: number;
  } | null;
};

export function FirstVsRepeatAovTile({ displayCurrency }: { displayCurrency: DisplayCurrency }) {
  const { data, loading, error, retry } = useTileFetch<FirstVsRepeatData>(
    (query) => apiClient.GET("/analytics/first-vs-repeat-aov", { params: { query } })
      .then(r => ({ data: r.data as unknown as FirstVsRepeatData, error: r.error })),
  );
  const ccy = data?.currency ?? displayCurrency;
  if (loading) return <TileSkeleton height={150} />;
  if (error) return <TileError retry={retry} />;
  if (!data || !data.has_data) {
    return (
      <TileEmpty title="First-time vs repeat AOV" hint="Once you have repeat customers, the AOV uplift shows here." />
    );
  }

  const maxAov = Math.max(data.first.aov, data.repeat.aov, 1);
  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-4 gap-2 flex-wrap">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          First-time vs repeat · AOV
        </div>
        <div className="flex items-center gap-2">
          {data.aov_uplift_pct != null && (
            <div className="text-[12px] font-bold tabular-nums" style={{
              color: data.aov_uplift_pct >= 0 ? "#34d399" : "#fb7185",
            }}>
              {data.aov_uplift_pct >= 0 ? "+" : ""}{data.aov_uplift_pct}% uplift
            </div>
          )}
          {data.compare != null && data.aov_uplift_pct != null && data.compare.aov_uplift_pct != null && (
            <DeltaIndicator
              value={data.aov_uplift_pct}
              prevValue={data.compare.aov_uplift_pct}
              format="pct"
            />
          )}
        </div>
      </div>
      <div className="space-y-3">
        <div>
          <div className="flex justify-between mb-1">
            <span className="text-[12px] text-slate-300">First-time ({data.first.customers} customers)</span>
            <span className="text-[13px] font-bold tabular-nums text-slate-200">
              {formatMoneyCompact(data.first.aov, ccy)}
            </span>
          </div>
          <div className="h-2 rounded-full bg-white/[0.04] overflow-hidden">
            <div className="h-full rounded-full bg-amber-400/70"
                 style={{ width: `${(data.first.aov / maxAov) * 100}%` }} />
          </div>
        </div>
        <div>
          <div className="flex justify-between mb-1">
            <span className="text-[12px] text-slate-300">Repeat ({data.repeat.customers} customers)</span>
            <span className="text-[13px] font-bold tabular-nums text-slate-200">
              {formatMoneyCompact(data.repeat.aov, ccy)}
            </span>
          </div>
          <div className="h-2 rounded-full bg-white/[0.04] overflow-hidden">
            <div className="h-full rounded-full bg-emerald-400/70"
                 style={{ width: `${(data.repeat.aov / maxAov) * 100}%` }} />
          </div>
        </div>
      </div>
    </div>
  );
}
