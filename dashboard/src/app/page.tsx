"use client";

import Image from "next/image";
import { useEffect, useMemo, useRef, useState } from "react";

import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { UpgradeModal } from "./components/UpgradeModal";
import { ProGate } from "./components/ProGate";
import { MascotLoader, MascotEmpty } from "./components/MascotLoader";
import { SignalCard, type OpportunitySignal } from "./components/SignalCard";

// ---------------------------------------------------------------------------
// Environment
// ---------------------------------------------------------------------------
const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://147.93.127.248:8000";
const DASHBOARD_API_KEY = process.env.NEXT_PUBLIC_DASHBOARD_API_KEY || "";

function apiHeaders(): HeadersInit {
  return {
    "Content-Type": "application/json",
    ...(DASHBOARD_API_KEY ? { "X-API-Key": DASHBOARD_API_KEY } : {}),
  };
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
type Summary = {
  total_visitors?: number;
  total_sessions?: number;
  total_events?: number;
  hot_visitors?: number;
  warm_visitors?: number;
  cold_visitors?: number;
  wishlist_adds?: number;
  avg_intent_score?: number;
  conversion_ready_products?: number;
};

type HotVisitor = {
  visitor_id?: string;
  session_id?: string;
  product_id?: string;
  intent_score?: number;
  intent_level?: string;
  total_views?: number;
  total_dwell_seconds?: number;
  max_scroll_depth?: number;
  wishlist_added?: boolean;
};

type TopProduct = {
  product_id?: string;
  product_name?: string;
  total_views?: number;
  unique_visitors?: number;
  wishlist_adds?: number;
  avg_intent_score?: number;
  intent_level?: string;
};

type ProductOpportunity = {
  product_id?: string;
  product_name?: string;
  signal_type?: string;
  priority_score?: number;
  recommended_action?: string;
  explanation?: string;
  plan_required?: string;
  locked_for_lite?: boolean;
};

type PriceIntelligence = {
  product_url?: string;
  product_name?: string;
  avg_price?: number;
  competitor_price?: number;
  price_gap?: number;
  recommendation?: string;
  [key: string]: unknown;
};

type MarketLookup = {
  product_url?: string;
  product_name?: string;
  market_position?: string;
  uniqueness_score?: number;
  recommendation?: string;
  [key: string]: unknown;
};

type OverviewResponse = {
  summary?: Summary;
  top_hot_visitors?: HotVisitor[];
  top_products?: TopProduct[];
  product_opportunities?: ProductOpportunity[];
  price_intelligence?: PriceIntelligence[];
  market_lookup?: MarketLookup[];
};

type LiveVisitor = {
  visitor_id?: string;
  url?: string;
  intent_level?: string;
  intent_score?: number;
  dwell_seconds?: number;
};

type TrendPoint = {
  day?: string;
  visitors?: number;
  page_views?: number;
  clicks?: number;
  hot_visitors?: number;
};

type Alert = {
  type?: string;
  message?: string;
  priority?: string;
};

type TopPage = {
  url?: string;
  views?: number;
  visitors?: number;
  avg_dwell?: number;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function formatNumber(value: unknown): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US").format(Math.round(value));
}

function formatScore(value: unknown): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return Math.round(value).toString();
}

function formatDecimal(value: unknown, digits = 1): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

function prettyText(value?: string): string {
  if (!value) return "—";
  return value
    .toLowerCase()
    .split("_")
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(" ");
}

function impactClass(value?: string): string {
  switch ((value || "").toUpperCase()) {
    case "HIGH":
      return "bg-rose-500/15 text-rose-300 ring-1 ring-rose-400/30";
    case "MEDIUM":
      return "bg-amber-500/15 text-amber-300 ring-1 ring-amber-400/30";
    case "LOW":
      return "bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-400/30";
    default:
      return "bg-white/5 text-slate-400 ring-1 ring-white/10";
  }
}

function intentDotClass(intent?: string): string {
  switch ((intent || "").toUpperCase()) {
    case "HOT":
      return "bg-rose-400 shadow-[0_0_10px_rgba(251,113,133,0.7)]";
    case "WARM":
      return "bg-amber-300 shadow-[0_0_10px_rgba(252,211,77,0.7)]";
    default:
      return "bg-slate-400 shadow-[0_0_10px_rgba(148,163,184,0.5)]";
  }
}

// ---------------------------------------------------------------------------
// Count-up animation component
// ---------------------------------------------------------------------------
function CountUp({
  value,
  format = (v: number) => v.toLocaleString(),
}: {
  value: number;
  format?: (v: number) => string;
}) {
  const [display, setDisplay] = useState(0);
  const prevRef = useRef(0);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const start = prevRef.current;
    prevRef.current = value;
    if (start === value) return;

    const began = Date.now();
    const duration = 520;

    function step() {
      const progress = Math.min((Date.now() - began) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(Math.round(start + (value - start) * eased));
      if (progress < 1) rafRef.current = requestAnimationFrame(step);
    }
    rafRef.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(rafRef.current);
  }, [value]);

  return <>{format(display)}</>;
}

// ---------------------------------------------------------------------------
// Small UI atoms
// ---------------------------------------------------------------------------
function SectionHeading({
  eyebrow,
  title,
  description,
  pro,
}: {
  eyebrow: string;
  title: string;
  description?: string;
  pro?: boolean;
}) {
  return (
    <div className="mb-5">
      <div className="mb-1.5 flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/70">
          {eyebrow}
        </span>
        {pro && (
          <span className="rounded-full border border-violet-400/30 bg-violet-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-violet-300">
            Pro
          </span>
        )}
      </div>
      <h2 className="text-lg font-semibold text-white">{title}</h2>
      {description && (
        <p className="mt-1 max-w-2xl text-sm text-slate-500">{description}</p>
      )}
    </div>
  );
}

function KpiCard({
  label,
  value,
  hint,
  numeric,
}: {
  label: string;
  value: string;
  hint: string;
  numeric?: number;
}) {
  return (
    <div className="hs-fade-up rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4 transition-all duration-150 hover:border-violet-400/20 hover:bg-white/[0.05]">
      <div className="text-[12px] text-slate-500">{label}</div>
      <div className="mt-2.5 text-2xl font-semibold tabular-nums text-white">
        {numeric !== undefined ? (
          <CountUp value={numeric} />
        ) : (
          value
        )}
      </div>
      <div className="mt-1 text-[11px] text-slate-600">{hint}</div>
    </div>
  );
}

function Divider() {
  return <div className="border-t border-white/[0.06]" />;
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function Page() {
  // Layout state
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activeSection, setActiveSection] = useState("overview");

  // Tier state
  // accountTier: "pro" if ?plan=pro in URL, otherwise "lite"
  const [accountTier, setAccountTier] = useState<"lite" | "pro">("lite");
  const [tier, setTier] = useState<"lite" | "pro">("lite");
  const [upgradeModalOpen, setUpgradeModalOpen] = useState(false);

  // Shop
  const [shop, setShop] = useState("");

  // Overview data
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Live visitors
  const [liveVisitors, setLiveVisitors] = useState<LiveVisitor[]>([]);

  // Batch 9 opportunity signals
  const [signals, setSignals] = useState<OpportunitySignal[]>([]);

  // Analytics (alerts, trend, top pages)
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [topPages, setTopPages] = useState<TopPage[]>([]);

  // ---------------------------------------------------------------------------
  // Read ?shop= and ?plan= from URL on mount
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const shopParam = params.get("shop") || "";
    const planParam = params.get("plan") === "pro" ? "pro" : "lite";
    setShop(shopParam);
    setAccountTier(planParam);
    setTier(planParam);
  }, []);

  // ---------------------------------------------------------------------------
  // Primary overview fetch
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!shop) {
      setLoading(false);
      return;
    }

    let mounted = true;

    async function loadOverview() {
      try {
        setLoading(true);
        setError("");

        const res = await fetch(
          `${API_BASE}/dashboard/overview?shop=${encodeURIComponent(shop)}`,
          { method: "GET", headers: apiHeaders(), cache: "no-store" }
        );

        if (!res.ok) throw new Error(`Overview failed: ${res.status}`);
        const json = (await res.json()) as OverviewResponse;

        if (mounted) {
          setData({
            summary: json.summary || {},
            top_hot_visitors: Array.isArray(json.top_hot_visitors) ? json.top_hot_visitors : [],
            top_products: Array.isArray(json.top_products) ? json.top_products : [],
            product_opportunities: Array.isArray(json.product_opportunities) ? json.product_opportunities : [],
            price_intelligence: Array.isArray(json.price_intelligence) ? json.price_intelligence : [],
            market_lookup: Array.isArray(json.market_lookup) ? json.market_lookup : [],
          });
        }
      } catch (err) {
        if (mounted) {
          setError(err instanceof Error ? err.message : "Unable to load dashboard.");
        }
      } finally {
        if (mounted) setLoading(false);
      }
    }

    loadOverview();
    return () => { mounted = false; };
  }, [shop]);

  // ---------------------------------------------------------------------------
  // Live visitors poll (every 5s)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!shop) return;
    let active = true;

    async function loadLive() {
      try {
        const res = await fetch(
          `${API_BASE}/live/visitors?shop=${encodeURIComponent(shop)}`,
          { method: "GET", headers: apiHeaders(), cache: "no-store" }
        );
        if (!res.ok) return;
        const json = await res.json();
        if (active) setLiveVisitors(Array.isArray(json.visitors) ? json.visitors : []);
      } catch { /* silent */ }
    }

    loadLive();
    const id = setInterval(loadLive, 5000);
    return () => { active = false; clearInterval(id); };
  }, [shop]);

  // ---------------------------------------------------------------------------
  // Opportunity signals from /opportunities (Batch 9)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!shop) return;
    let active = true;

    async function loadSignals() {
      try {
        const res = await fetch(
          `${API_BASE}/opportunities?shop=${encodeURIComponent(shop)}`,
          { method: "GET", headers: apiHeaders(), cache: "no-store" }
        );
        if (!res.ok) return;
        const json = await res.json();
        if (active) setSignals(Array.isArray(json) ? json : []);
      } catch { /* silent */ }
    }

    loadSignals();
    const id = setInterval(loadSignals, 30000);
    return () => { active = false; clearInterval(id); };
  }, [shop]);

  // ---------------------------------------------------------------------------
  // Analytics: alerts, weekly trend, top pages
  // ---------------------------------------------------------------------------
  useEffect(() => {
    let active = true;

    async function loadAnalytics() {
      try {
        const [alertsRes, trendRes, pagesRes] = await Promise.all([
          fetch(`${API_BASE}/analytics/alerts`, { cache: "no-store" }),
          fetch(`${API_BASE}/analytics/weekly-trend`, { cache: "no-store" }),
          fetch(`${API_BASE}/analytics/top-pages`, { cache: "no-store" }),
        ]);

        const alertsJson = alertsRes.ok ? await alertsRes.json() : { alerts: [] };
        const trendJson = trendRes.ok ? await trendRes.json() : { trend: [] };
        const pagesJson = pagesRes.ok ? await pagesRes.json() : { pages: [] };

        if (!active) return;
        setAlerts(Array.isArray(alertsJson.alerts) ? alertsJson.alerts : []);
        setTrend(Array.isArray(trendJson.trend) ? trendJson.trend : []);
        setTopPages(Array.isArray(pagesJson.pages) ? pagesJson.pages : []);
      } catch { /* silent */ }
    }

    loadAnalytics();
    const id = setInterval(loadAnalytics, 30000);
    return () => { active = false; clearInterval(id); };
  }, []);

  // ---------------------------------------------------------------------------
  // Derived data
  // ---------------------------------------------------------------------------
  const summary = data?.summary || {};
  const hotVisitors = data?.top_hot_visitors || [];
  const topProducts = data?.top_products || [];
  const productOpportunities = data?.product_opportunities || [];
  const priceIntel = data?.price_intelligence || [];
  const marketIntel = data?.market_lookup || [];

  const maxTrend = useMemo(
    () => Math.max(...trend.map((p) => p.visitors || 0), 1),
    [trend]
  );

  // ---------------------------------------------------------------------------
  // Tier toggle
  // ---------------------------------------------------------------------------
  function handleTierToggle() {
    if (accountTier === "lite") {
      setUpgradeModalOpen(true);
    } else {
      setTier((t) => (t === "lite" ? "pro" : "lite"));
    }
  }

  // ---------------------------------------------------------------------------
  // Sidebar navigation
  // ---------------------------------------------------------------------------
  function handleNavigate(id: string) {
    setActiveSection(id);
    document
      .getElementById(`section-${id}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------------------------------------------------------------------------
  // Radar positions for live visitor dots
  // ---------------------------------------------------------------------------
  const RADAR_POSITIONS = [
    "left-[12%] top-[18%]", "left-[68%] top-[20%]",
    "left-[22%] top-[62%]", "left-[72%] top-[66%]",
    "left-[44%] top-[14%]", "left-[16%] top-[44%]",
    "left-[78%] top-[44%]", "left-[46%] top-[76%]",
  ];

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <div className="flex h-screen overflow-hidden bg-[#080811] text-white">
      {/* Sidebar */}
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((c) => !c)}
        activeSection={activeSection}
        onNavigate={handleNavigate}
      />

      {/* Main column */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <TopBar shop={shop} tier={tier} onTierToggle={handleTierToggle} />

        {/* Scrollable content */}
        <main className="flex-1 overflow-y-auto">
          {/* ── No shop state ── */}
          {!shop ? (
            <div className="flex flex-col items-center justify-center gap-6 py-24 px-6">
              <MascotLoader caption="Waiting for a shop connection…" />
              <div className="max-w-sm rounded-2xl border border-amber-400/20 bg-amber-500/10 p-6 text-center">
                <div className="text-sm font-semibold text-amber-200">No shop connected</div>
                <div className="mt-2 text-sm text-amber-200/70">
                  Append{" "}
                  <code className="rounded bg-white/10 px-1 text-amber-100">
                    ?shop=yourstore.myshopify.com
                  </code>{" "}
                  to the URL to load your dashboard.
                </div>
              </div>
            </div>
          ) : loading ? (
            /* ── Loading state ── */
            <MascotLoader caption="Reading the signals…" />
          ) : error ? (
            /* ── Error state ── */
            <div className="m-6 rounded-2xl border border-rose-400/20 bg-rose-500/10 p-6">
              <div className="text-sm font-semibold uppercase tracking-[0.14em] text-rose-300">
                Dashboard error
              </div>
              <div className="mt-2 text-sm text-rose-200">{error}</div>
              <div className="mt-2 text-xs text-rose-200/60">
                Backend: {API_BASE}/dashboard/overview
              </div>
            </div>
          ) : (
            /* ── Dashboard content ── */
            <div className="space-y-10 px-6 py-7 pb-16">

              {/* ────────────────────────────────────────────────────────────── */}
              {/* Row 1 — KPI Overview                                          */}
              {/* ────────────────────────────────────────────────────────────── */}
              <section id="section-overview">
                <SectionHeading
                  eyebrow="Lite Overview"
                  title="Merchant snapshot"
                  description="Core metrics at a glance: traffic, intent, and conversion-ready signals."
                />
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <KpiCard
                    label="Total Visitors"
                    value={formatNumber(summary.total_visitors)}
                    hint="Unique tracked visitors"
                    numeric={summary.total_visitors}
                  />
                  <KpiCard
                    label="Total Events"
                    value={formatNumber(summary.total_events)}
                    hint="Behavioral signals collected"
                    numeric={summary.total_events}
                  />
                  <KpiCard
                    label="Hot Visitors"
                    value={formatNumber(summary.hot_visitors)}
                    hint="High purchase intent"
                    numeric={summary.hot_visitors}
                  />
                  <KpiCard
                    label="Wishlist Adds"
                    value={formatNumber(summary.wishlist_adds)}
                    hint="Strong product desire signals"
                    numeric={summary.wishlist_adds}
                  />
                  <KpiCard
                    label="Average Intent"
                    value={formatScore(summary.avg_intent_score)}
                    hint="Average signal strength"
                    numeric={summary.avg_intent_score}
                  />
                  <KpiCard
                    label="Intent Distribution"
                    value={`${formatNumber(summary.hot_visitors)} / ${formatNumber(summary.warm_visitors)} / ${formatNumber(summary.cold_visitors)}`}
                    hint="Hot / Warm / Cold"
                  />
                  <KpiCard
                    label="Sessions"
                    value={formatNumber(summary.total_sessions)}
                    hint="Tracked browsing sessions"
                    numeric={summary.total_sessions}
                  />
                  <KpiCard
                    label="Conversion-ready Products"
                    value={formatNumber(summary.conversion_ready_products)}
                    hint="Products with action potential"
                    numeric={summary.conversion_ready_products}
                  />
                </div>
              </section>

              <Divider />

              {/* ────────────────────────────────────────────────────────────── */}
              {/* Row 2 — Signal Grid                                           */}
              {/* ────────────────────────────────────────────────────────────── */}
              <section id="section-live">
                <SectionHeading
                  eyebrow="Live"
                  title="Live visitor radar"
                  description="Real-time visitor positions by page and intent level."
                />

                <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
                  {/* Radar */}
                  {liveVisitors.length === 0 ? (
                    <MascotEmpty message="No live visitors yet." />
                  ) : (
                    <div className="relative min-h-[320px] overflow-hidden rounded-2xl border border-white/[0.07] bg-[radial-gradient(circle_at_center,rgba(56,189,248,0.12),transparent_22%)]">
                      <div className="absolute inset-0 flex items-center justify-center">
                        <div className="relative h-[280px] w-[280px] rounded-full border border-cyan-400/15">
                          <div className="absolute inset-[18%] rounded-full border border-cyan-400/10" />
                          <div className="absolute inset-[36%] rounded-full border border-cyan-400/10" />
                          <div className="absolute inset-[54%] rounded-full border border-cyan-400/10" />
                          <div className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-cyan-400/[0.08]" />
                          <div className="absolute left-0 top-1/2 h-px w-full -translate-y-1/2 bg-cyan-400/[0.08]" />

                          {liveVisitors.slice(0, 8).map((v, i) => (
                            <div
                              key={`${v.visitor_id || "v"}-${i}`}
                              className={`absolute ${RADAR_POSITIONS[i % RADAR_POSITIONS.length]} -translate-x-1/2 -translate-y-1/2`}
                            >
                              <div className={`h-2.5 w-2.5 rounded-full ${intentDotClass(v.intent_level)}`} />
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="absolute bottom-4 left-4 right-4">
                        <div className="flex items-center gap-4 text-[11px] text-slate-500">
                          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-rose-400" />Hot</span>
                          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-amber-300" />Warm</span>
                          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-slate-400" />Cold</span>
                          <span className="ml-auto font-medium text-slate-400">{liveVisitors.length} live</span>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Live visitor list */}
                  <div className="flex flex-col gap-2">
                    {liveVisitors.length === 0 ? (
                      <div />
                    ) : (
                      liveVisitors.slice(0, 6).map((v, i) => (
                        <div
                          key={`${v.visitor_id || "lv"}-${i}`}
                          className="flex items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5"
                        >
                          <span className={`h-2 w-2 flex-shrink-0 rounded-full ${intentDotClass(v.intent_level)}`} />
                          <span className="min-w-0 flex-1 truncate text-[12px] text-slate-300">
                            {v.url || "—"}
                          </span>
                          <span className="flex-shrink-0 rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-slate-500">
                            {v.intent_level || "—"}
                          </span>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </section>

              {/* Live Alerts */}
              <section className="-mt-4">
                <SectionHeading
                  eyebrow="Live Alerts"
                  title="Signals requiring attention"
                />
                {alerts.length === 0 ? (
                  <MascotEmpty message="No live alerts right now." />
                ) : (
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {alerts.slice(0, 6).map((alert, i) => (
                      <div
                        key={`${alert.type || "alert"}-${i}`}
                        className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4"
                      >
                        <div className="mb-2 flex items-center gap-2">
                          <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${impactClass(alert.priority)}`}>
                            {alert.priority || "Info"}
                          </span>
                          <span className="text-[11px] text-slate-500">{prettyText(alert.type)}</span>
                        </div>
                        <p className="text-sm leading-5 text-slate-300">{alert.message || "—"}</p>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <Divider />

              {/* ────────────────────────────────────────────────────────────── */}
              {/* Opportunity Signals (Batch 9)                                 */}
              {/* ────────────────────────────────────────────────────────────── */}
              <section id="section-signals">
                <SectionHeading
                  eyebrow="Signals"
                  title="Opportunity signals"
                  description="Rule-based signals detected from real visitor behavior on product pages."
                />
                {signals.length === 0 ? (
                  <MascotEmpty message="No sparks yet — check back soon." />
                ) : (
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {signals.map((sig, i) => (
                      <SignalCard key={`${sig.product_url}-${sig.signal_type}-${i}`} signal={sig} />
                    ))}
                  </div>
                )}
              </section>

              {/* Conversion Signals (from product opportunities) */}
              {productOpportunities.length > 0 && (
                <section className="-mt-4">
                  <SectionHeading
                    eyebrow="Conversion signals"
                    title="Product-level conversion signals"
                  />
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {productOpportunities.map((opp, i) => {
                      const isLocked = opp.locked_for_lite && tier === "lite";
                      const card = (
                        <div className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4 transition-all hover:border-violet-400/20">
                          <div className="mb-2 flex flex-wrap items-center gap-2">
                            <span className="rounded-full bg-violet-500/15 px-2.5 py-1 text-[11px] font-semibold text-violet-200 ring-1 ring-violet-400/20">
                              {prettyText(opp.signal_type)}
                            </span>
                            {opp.priority_score !== undefined && (
                              <span className="rounded-full bg-white/5 px-2.5 py-1 text-[11px] text-slate-400 ring-1 ring-white/10">
                                Score {Math.round(opp.priority_score)}
                              </span>
                            )}
                          </div>
                          <div className="truncate text-sm font-medium text-white">
                            {opp.product_name || opp.product_id || "—"}
                          </div>
                          {opp.explanation && (
                            <p className="mt-2 text-[12px] leading-5 text-slate-500">{opp.explanation}</p>
                          )}
                          {opp.recommended_action && tier === "pro" && (
                            <div className="mt-3 rounded-xl border border-emerald-400/15 bg-emerald-500/5 p-3">
                              <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-emerald-300/80">
                                Suggested action
                              </div>
                              <div className="mt-1 text-[12px] leading-5 text-slate-300">
                                {prettyText(opp.recommended_action)}
                              </div>
                            </div>
                          )}
                        </div>
                      );

                      return isLocked ? (
                        <ProGate
                          key={`opp-${i}`}
                          tier={tier}
                          onUpgradeClick={() => setUpgradeModalOpen(true)}
                          label="conversion signal details"
                        >
                          {card}
                        </ProGate>
                      ) : (
                        <div key={`opp-${i}`}>{card}</div>
                      );
                    })}
                  </div>
                </section>
              )}

              <Divider />

              {/* ────────────────────────────────────────────────────────────── */}
              {/* Row 3 — Intelligence (Pro gated)                              */}
              {/* ────────────────────────────────────────────────────────────── */}
              <section id="section-price-intelligence">
                <SectionHeading
                  eyebrow="Price Intelligence"
                  title="Competitor pricing signals"
                  description="Track price gaps and opportunities versus competitor data."
                  pro
                />
                <ProGate
                  tier={tier}
                  onUpgradeClick={() => setUpgradeModalOpen(true)}
                  label="Price Intelligence"
                >
                  {priceIntel.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-white/[0.08] bg-white/[0.02] p-8 text-center text-sm text-slate-500">
                      No price intelligence data available yet.
                    </div>
                  ) : (
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                      {priceIntel.slice(0, 9).map((item, i) => (
                        <div
                          key={`price-${i}`}
                          className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4"
                        >
                          <div className="truncate text-sm font-medium text-white">
                            {item.product_name || item.product_url || "—"}
                          </div>
                          {item.price_gap !== undefined && (
                            <div className="mt-2 text-[12px] text-slate-400">
                              Price gap: {formatDecimal(item.price_gap as number, 2)}
                            </div>
                          )}
                          {item.recommendation && (
                            <p className="mt-2 text-[12px] leading-5 text-slate-500">
                              {String(item.recommendation)}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </ProGate>
              </section>

              <section id="section-market-intelligence">
                <SectionHeading
                  eyebrow="Market Intelligence"
                  title="Competitive uniqueness"
                  description="Understand your position relative to the market."
                  pro
                />
                <ProGate
                  tier={tier}
                  onUpgradeClick={() => setUpgradeModalOpen(true)}
                  label="Market Intelligence"
                >
                  {marketIntel.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-white/[0.08] bg-white/[0.02] p-8 text-center text-sm text-slate-500">
                      No market intelligence data available yet.
                    </div>
                  ) : (
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                      {marketIntel.slice(0, 9).map((item, i) => (
                        <div
                          key={`market-${i}`}
                          className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4"
                        >
                          <div className="truncate text-sm font-medium text-white">
                            {item.product_name || item.product_url || "—"}
                          </div>
                          {item.uniqueness_score !== undefined && (
                            <div className="mt-2 text-[12px] text-slate-400">
                              Uniqueness: {formatScore(item.uniqueness_score as number)}%
                            </div>
                          )}
                          {item.recommendation && (
                            <p className="mt-2 text-[12px] leading-5 text-slate-500">
                              {String(item.recommendation)}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </ProGate>
              </section>

              <Divider />

              {/* ────────────────────────────────────────────────────────────── */}
              {/* Row 4 — Trends                                                */}
              {/* ────────────────────────────────────────────────────────────── */}
              <section>
                <SectionHeading
                  eyebrow="Weekly Trend"
                  title="Traffic across the week"
                  description="Visitor volume over the last tracked days."
                />
                {trend.length === 0 ? (
                  <MascotEmpty message="No trend data yet." />
                ) : (
                  <div className="grid gap-2 sm:grid-cols-4 md:grid-cols-7">
                    {trend.map((point, i) => {
                      const val = point.visitors || 0;
                      const barH = Math.max(12, Math.round((val / maxTrend) * 140));
                      return (
                        <div
                          key={`${point.day || "day"}-${i}`}
                          className="hs-fade-up rounded-xl border border-white/[0.07] bg-white/[0.02] p-3"
                          style={{ animationDelay: `${i * 40}ms` }}
                        >
                          <div className="mb-2 text-[11px] text-slate-500">
                            {point.day || `Day ${i + 1}`}
                          </div>
                          <div className="flex h-36 items-end">
                            <div
                              className="w-full rounded-lg bg-gradient-to-t from-violet-500/80 to-cyan-400/60"
                              style={{ height: barH }}
                            />
                          </div>
                          <div className="mt-2 text-base font-semibold tabular-nums text-white">
                            {formatNumber(val)}
                          </div>
                          <div className="text-[10px] text-slate-600">Visitors</div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>

              {/* Top Products */}
              {topProducts.length > 0 && (
                <section>
                  <SectionHeading eyebrow="Top Products" title="Highest-intent products" />
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {topProducts.slice(0, 6).map((product, i) => (
                      <div
                        key={`${product.product_id || "prod"}-${i}`}
                        className="hs-fade-up rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4"
                      >
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <span className="truncate text-sm font-medium text-white">
                            {product.product_name || product.product_id || "—"}
                          </span>
                          {product.intent_level && (
                            <span className={`flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ${impactClass(product.intent_level === "HOT" ? "HIGH" : product.intent_level === "WARM" ? "MEDIUM" : "LOW")}`}>
                              {product.intent_level}
                            </span>
                          )}
                        </div>
                        <div className="grid grid-cols-3 gap-2">
                          <div>
                            <div className="text-[10px] uppercase text-slate-600">Views</div>
                            <div className="mt-0.5 text-sm font-semibold text-white">{formatNumber(product.total_views)}</div>
                          </div>
                          <div>
                            <div className="text-[10px] uppercase text-slate-600">Visitors</div>
                            <div className="mt-0.5 text-sm font-semibold text-white">{formatNumber(product.unique_visitors)}</div>
                          </div>
                          <div>
                            <div className="text-[10px] uppercase text-slate-600">Intent</div>
                            <div className="mt-0.5 text-sm font-semibold text-white">{formatScore(product.avg_intent_score)}</div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Top Pages */}
              <section>
                <SectionHeading eyebrow="Top Pages" title="Where visitors spend time" />
                {topPages.length === 0 ? (
                  <MascotEmpty message="No page data available yet." />
                ) : (
                  <div className="overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.02]">
                    <div className="overflow-x-auto">
                      <table className="min-w-full text-left text-[13px]">
                        <thead>
                          <tr className="border-b border-white/[0.06] text-[11px] uppercase tracking-wide text-slate-600">
                            <th className="px-4 py-3 font-medium">URL</th>
                            <th className="px-4 py-3 font-medium">Views</th>
                            <th className="px-4 py-3 font-medium">Visitors</th>
                            <th className="px-4 py-3 font-medium">Avg Dwell</th>
                          </tr>
                        </thead>
                        <tbody>
                          {topPages.slice(0, 10).map((page, i) => (
                            <tr
                              key={`${page.url || "page"}-${i}`}
                              className="border-t border-white/[0.04] transition-colors hover:bg-white/[0.02]"
                            >
                              <td className="max-w-[380px] truncate px-4 py-2.5 text-slate-300">
                                {page.url || "—"}
                              </td>
                              <td className="px-4 py-2.5 tabular-nums text-slate-400">{formatNumber(page.views)}</td>
                              <td className="px-4 py-2.5 tabular-nums text-slate-400">{formatNumber(page.visitors)}</td>
                              <td className="px-4 py-2.5 tabular-nums text-slate-400">{formatDecimal(page.avg_dwell, 1)}s</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </section>

            </div>
          )}
        </main>
      </div>

      {/* Upgrade modal */}
      <UpgradeModal open={upgradeModalOpen} onClose={() => setUpgradeModalOpen(false)} />
    </div>
  );
}
