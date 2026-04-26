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

type DisplayCurrency = "USD" | "EUR" | string;

// ── Shared skeleton + error primitives ────────────────────────────
function TileSkeleton({ height = 80 }: { height?: number }) {
  return (
    <div
      className="animate-pulse rounded-xl border border-white/[0.05] bg-[#0e0e1a]/40"
      style={{ height: `${height}px` }}
    />
  );
}

function TileError({ retry }: { retry: () => void }) {
  return (
    <div className="rounded-xl border border-rose-400/20 bg-rose-500/[0.06] p-4 text-center">
      <div className="text-[12px] text-rose-300">Couldn't load data right now.</div>
      <button
        onClick={retry}
        className="mt-2 rounded-md border border-rose-400/30 bg-rose-500/10 px-3 py-1 text-[11px] font-semibold text-rose-200 hover:bg-rose-500/20"
      >
        Try again
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// 1. Device split — tile fits inside section-lite-today
// ─────────────────────────────────────────────────────────────────

type DeviceData = {
  days: number; total_sessions: number; has_data: boolean;
  slices: { device: string; sessions: number; pct: number }[];
};

const DEVICE_COLOR: Record<string, string> = {
  mobile: "#a78bfa", desktop: "#34d399", tablet: "#fbbf24", unknown: "#94a3b8",
};
const DEVICE_LABEL: Record<string, string> = {
  mobile: "Mobile", desktop: "Desktop", tablet: "Tablet", unknown: "Other",
};

export function DeviceSplitTile() {
  const [data, setData] = useState<DeviceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true); setError(false);
    apiClient.GET("/analytics/device-breakdown")
      .then(({ data: j, error: err }) => {
        if (!active) return;
        if (err || !j) setError(true);
        else setData(j as unknown as DeviceData);
      })
      .catch(() => { if (active) setError(true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tick]);

  if (loading) return <TileSkeleton height={120} />;
  if (error) return <TileError retry={() => setTick(t => t + 1)} />;
  if (!data || !data.has_data) {
    return (
      <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/40 p-4 text-center">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">Device split</div>
        <div className="mt-2 text-[12px] text-slate-300">No traffic in the last {data?.days ?? 14} days yet.</div>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-3">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Device split · last {data.days} days
        </div>
        <div className="text-[11px] tabular-nums text-slate-300">
          {data.total_sessions.toLocaleString("en-US")} sessions
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
      <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/40 p-4 text-center">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">Top customers · all-time</div>
        <div className="mt-2 text-[12px] text-slate-300">Once orders flow, your highest-LTV buyers rank here.</div>
      </div>
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
};

export function AbandonmentTrendTile() {
  const [data, setData] = useState<AbandonmentTrendData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true); setError(false);
    apiClient.GET("/analytics/abandonment-trend")
      .then(({ data: j, error: err }) => {
        if (!active) return;
        if (err || !j) setError(true);
        else setData(j as unknown as AbandonmentTrendData);
      })
      .catch(() => { if (active) setError(true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tick]);

  if (loading) return <TileSkeleton height={140} />;
  if (error) return <TileError retry={() => setTick(t => t + 1)} />;
  if (!data || !data.has_data) {
    return (
      <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/40 p-4 text-center">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">Cart abandonment trend</div>
        <div className="mt-2 text-[12px] text-slate-300">No cart events in the last {data?.days ?? 14} days yet.</div>
      </div>
    );
  }

  // Render small bar series, height proportional to abandonment_pct.
  const maxPct = Math.max(...data.series.map(s => s.abandonment_pct ?? 0), 1);
  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-3">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Cart abandonment · last {data.days} days
        </div>
        <div className="text-[14px] font-bold tabular-nums text-rose-300">
          {data.avg_abandonment_pct != null ? `${data.avg_abandonment_pct}% avg` : "—"}
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
// 4. First-vs-repeat AOV — tile fits inside section-lite-retention
// ─────────────────────────────────────────────────────────────────

type FirstVsRepeatData = {
  currency: string; has_data: boolean;
  first: { customers: number; orders: number; revenue: number; aov: number };
  repeat: { customers: number; orders: number; revenue: number; aov: number };
  aov_uplift_pct: number | null;
};

export function FirstVsRepeatAovTile({ displayCurrency }: { displayCurrency: DisplayCurrency }) {
  const [data, setData] = useState<FirstVsRepeatData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true); setError(false);
    apiClient.GET("/analytics/first-vs-repeat-aov")
      .then(({ data: j, error: err }) => {
        if (!active) return;
        if (err || !j) setError(true);
        else setData(j as unknown as FirstVsRepeatData);
      })
      .catch(() => { if (active) setError(true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tick]);

  const ccy = data?.currency ?? displayCurrency;

  if (loading) return <TileSkeleton height={150} />;
  if (error) return <TileError retry={() => setTick(t => t + 1)} />;
  if (!data || !data.has_data) {
    return (
      <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/40 p-4 text-center">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">First-time vs repeat AOV</div>
        <div className="mt-2 text-[12px] text-slate-300">Once you have repeat customers, the AOV uplift shows here.</div>
      </div>
    );
  }

  const maxAov = Math.max(data.first.aov, data.repeat.aov, 1);
  return (
    <div className="rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 p-4">
      <div className="flex items-baseline justify-between mb-4">
        <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          First-time vs repeat · AOV
        </div>
        {data.aov_uplift_pct != null && (
          <div className="text-[12px] font-bold tabular-nums" style={{
            color: data.aov_uplift_pct >= 0 ? "#34d399" : "#fb7185",
          }}>
            {data.aov_uplift_pct >= 0 ? "+" : ""}{data.aov_uplift_pct}% uplift
          </div>
        )}
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
