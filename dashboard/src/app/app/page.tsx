"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";

import { Sidebar } from "../components/Sidebar";
import { TopBar, type TrialInfo } from "../components/TopBar";
import { UpgradeModal } from "../components/UpgradeModal";
import { ProGate } from "../components/ProGate";
import { MascotLoader, MascotEmpty } from "../components/MascotLoader";
import { SignalCard, type OpportunitySignal } from "../components/SignalCard";
import { BriefHero, type DailyBrief } from "../components/BriefHero";
import { RevenueWindowPro, RevenueWindowLite } from "../components/RevenueWindowBanner";
import { LiftReport } from "../components/LiftReport";
import { HeatmapCard } from "../components/HeatmapCard";
import { CohortTable } from "../components/CohortTable";
import { OnboardingHub } from "../components/OnboardingHub";
import { NudgePerformance } from "../components/NudgePerformance";
import { AudienceSegments } from "../components/AudienceSegments";
import { OrdersSummary } from "../components/OrdersSummary";
import { ProductConversions } from "../components/ProductConversions";
import { ActionProof } from "../components/ActionProof";
import { RevenueHero } from "../components/RevenueHero";
import { TopSignalCard, loadRecentActions, type RecentAction } from "../components/TopSignalCard";
import { RecentActions } from "../components/RecentActions";
import { ProofHeroCard } from "../components/ProofHeroCard";
import { SystemStatusBar } from "../components/SystemStatusBar";
import { DashboardHero } from "../components/DashboardHero";
import { computeActions, type SparkAction } from "../lib/actionEngine";
import { updateReputation } from "../lib/sparkReputation";
import { generateNotifications, loadSettings, type SparkNotification } from "../lib/sparkNotifications";
import { SparkToast } from "../components/NotificationBell";
import { SparkInline } from "../components/SparkCompanion";
import { SupportChat } from "../components/SupportChat";
import { IntelligenceHero } from "../components/IntelligenceHero";
import { Sparkline } from "../components/Sparkline";
import { GatewayProducts } from "../components/GatewayProducts";
import { PredictedLtv } from "../components/PredictedLtv";
import { PnlReport } from "../components/PnlReport";
// Killer feature components (2026-04-11 sprint) — loss-framed hero + drill-downs
import { RevenueAtRiskHero } from "../components/RevenueAtRiskHero";
import { PeerBenchmarksCard } from "../components/PeerBenchmarksCard";
import { CausalWhyCard } from "../components/CausalWhyCard";
import { NightShiftCard } from "../components/NightShiftCard";
import { CountUp } from "./_components/CountUp";
import { KpiCard } from "./_components/KpiCard";
import { SectionHeading } from "./_components/SectionHeading";
import { Divider, KpiSkeleton, TableSkeleton } from "./_components/Skeletons";
import { FunnelVisualization } from "./_components/FunnelVisualization";
import { TrafficSourceBox } from "./_components/TrafficSourceBox";
import { LiveRadarMap } from "./_components/LiveRadarMap";
import { KpiInsightModal } from "./_components/KpiInsightModal";
import { ProductInsightPanel } from "./_components/ProductInsightPanel";
import {
  formatNumber,
  formatScore,
  formatDecimal,
  formatPct,
  prettyText,
  impactClass,
  intentDotClass,
} from "./_lib/formatters";
import { AnomalyFusionCard } from "../components/AnomalyFusionCard";
import { VerticalBenchmarksCard } from "../components/VerticalBenchmarksCard";
import { AskHedgeSparkCard } from "../components/AskHedgeSparkCard";
import { IntegrationsCard } from "../components/IntegrationsCard";
import { ProductsInDecline } from "../components/ProductsInDecline";
import { MonthlyTargetsCard } from "../components/MonthlyTargetsCard";
import { MonthlyROICard } from "../components/MonthlyROICard";
import { TimelineNotes } from "../components/TimelineNotes";
import { CompareProductsCard } from "../components/CompareProductsCard";
import { ConnectToolsPanel } from "../components/ConnectToolsPanel";
import { YourTeamPanel } from "../components/YourTeamPanel";

// R-series killer features (2026-04-12)
import { RevenueAutopsyCard } from "../components/RevenueAutopsyCard";
import { AbandonedIntentCard } from "../components/AbandonedIntentCard";
import { PriceSensitivityCard } from "../components/PriceSensitivityCard";
import { CausalLiftCard } from "../components/CausalLiftCard";
import { RevenueGenomeCard } from "../components/RevenueGenomeCard";

// α-series killer features (2026-04-12) — elite roadmap
import { TrustControlCenter } from "../components/TrustControlCenter";
import { ROIHeroBanner } from "../components/ROIHeroBanner";
import { InstantIntelligenceCard } from "../components/InstantIntelligenceCard";
import { DailyNarrativeBlock } from "../components/DailyNarrativeBlock";
// β-series killer features (2026-04-12) — elite roadmap
import { MtaCompareCard } from "../components/MtaCompareCard";
import { UnitEconomicsCard } from "../components/UnitEconomicsCard";
import { MarginHealthCard } from "../components/MarginHealthCard";
// ζ1 — first-login tour
import { ProductTour } from "../components/ProductTour";
// ζ2 — rule builder
import { RuleBuilderCard } from "../components/RuleBuilderCard";
// δ3 α6 — probabilistic revenue forecast
import { RevenueForecastCard } from "../components/RevenueForecastCard";
// δ4 — per-customer churn table
import { CustomerChurnCard } from "../components/CustomerChurnCard";
// δ5 — nudge DNA patterns
import { NudgeDnaCard } from "../components/NudgeDnaCard";
import {
  type DisplayCurrency,
  formatDisplayMoney,
  readSavedDisplayCurrency,
  writeSavedDisplayCurrency,
} from "../lib/currency";
import { apiClient, getHeaders, type paths } from "../lib/api-client";

// Typed response aliases — extracted from the generated OpenAPI types so the
// entire data flow from backend → state → component props is type-checked.
// When the backend's Pydantic response_model changes, `npm run api:types`
// refreshes these aliases and tsc surfaces every affected call site.
type GatewayProductsData =
  paths["/pro/cohorts/ltv/products"]["get"]["responses"]["200"]["content"]["application/json"];
type PredictedLtvData =
  paths["/pro/cohorts/ltv/customers"]["get"]["responses"]["200"]["content"]["application/json"];
type AttributionSummaryData =
  paths["/attribution/summary/pro"]["get"]["responses"]["200"]["content"]["application/json"];
type MonthlyCohortsData =
  paths["/pro/cohorts/monthly"]["get"]["responses"]["200"]["content"]["application/json"];
type RevenueForecastData =
  paths["/orders/forecast/pro"]["get"]["responses"]["200"]["content"]["application/json"];
type BehavioralCohortsData =
  paths["/pro/cohorts/behavioral"]["get"]["responses"]["200"]["content"]["application/json"];
type PnlReportData =
  paths["/pro/pnl"]["get"]["responses"]["200"]["content"]["application/json"];

// ---------------------------------------------------------------------------
// Environment
// ---------------------------------------------------------------------------
const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "";

// Auth: session cookie set by backend OAuth callback (httpOnly, Secure).
// credentials: "include" sends the cookie with every fetch.
// The shared DASHBOARD_API_KEY pattern has been removed — per-merchant
// session tokens are the sole authentication mechanism.
function apiHeaders(): HeadersInit {
  return { "Content-Type": "application/json" };
}

function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  return fetch(url, {
    ...init,
    headers: { ...apiHeaders(), ...(init?.headers || {}) },
    credentials: "include",
    cache: "no-store",
  }).then((res) => {
    if (res.status === 401 || res.status === 403) dispatchSessionExpired();
    return res;
  });
}

// Session-expired event: dispatched when any fetch returns 401/403 mid-session.
// Listened by a single handler that sets sessionExpired state.
const SESSION_EXPIRED_EVENT = "hedgespark:session-expired";
function dispatchSessionExpired() {
  window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
type Summary = {
  total_visitors?: number;
  total_visitors_24h?: number;
  total_visitors_all?: number;
  total_events?: number;
  total_events_24h?: number;
  total_events_all?: number;
  hot_visitors?: number;
  warm_visitors?: number;
  cold_visitors?: number;
  wishlist_adds?: number;
  avg_intent_score?: number;
  conversion_ready_products?: number;
  visitor_metric_note?: string;
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

type PriceIntelligence = {
  product_name?: string;
  market_status?: string;
  price_position?: string;
  price_opportunity?: string;
  recommended_price_action?: string;
  intelligence_explanation?: string;
  confidence_score?: number;
  plan_required?: string;
  locked_for_lite?: boolean;
  [key: string]: unknown;
};

type MarketLookup = {
  product_name?: string;
  uniqueness_hint?: string;
  comparable_presence?: string;
  lookup_confidence?: number;
  market_summary?: string;
  recommended_next_step?: string;
  plan_required?: string;
  locked_for_lite?: boolean;
  [key: string]: unknown;
};

type RevenueWindowTease = {
  estimated_revenue_at_risk?: number;
  active_opportunity_count?: number;
  note?: string;
};

type RevenueWindowOpportunity = {
  product_url?: string;
  action_type?: string;
  visitor_count?: number;
  revenue_window?: number;
  calibration_state?: string;
};

type RevenueWindows = {
  total_revenue_at_risk?: number;
  opportunities?: RevenueWindowOpportunity[];
  currency?: string;
};

type CalibrationInfo = {
  state: string;
  is_empirical: boolean;
  label: string;
  sample_size?: number;
  converter_count?: number;
};

type OverviewResponse = {
  summary?: Summary;
  top_products?: TopProduct[];
  price_intelligence?: PriceIntelligence[];
  market_lookup?: MarketLookup[];
  revenue_window_tease?: RevenueWindowTease;
  revenue_windows?: RevenueWindows;
  shop_aov?: number;
  shop_currency?: string;
  aov_is_real?: boolean;
  calibration?: CalibrationInfo;
};

type LiveVisitor = {
  visitor_id?: string;
  url?: string;
  intent_level?: string;
  intent_score?: number;
  dwell_seconds?: number;
  country?: string;
  country_code?: string;
  city?: string;
  lat?: number;
  lon?: number;
};

// Analytics types — single source of truth is the generated OpenAPI schema.
// The Pro alert row carries an `action` field the Lite row lacks; we use the
// Pro row as the superset so legacy call sites that read `action` still work.
type TrendPoint =
  paths["/analytics/weekly-trend"]["get"]["responses"]["200"]["content"]["application/json"]["trend"][number];
// Alert is the Lite row (always present) plus an optional `action` field the
// Pro row adds. We model it from the Lite row directly so Lite responses type
// clean; the Pro-only `action` is attached as optional.
type Alert =
  paths["/analytics/alerts"]["get"]["responses"]["200"]["content"]["application/json"]["alerts"][number] & {
    action?: string;
  };
type TopPage =
  paths["/analytics/top-pages"]["get"]["responses"]["200"]["content"]["application/json"]["pages"][number];
type SessionRow =
  paths["/analytics/sessions"]["get"]["responses"]["200"]["content"]["application/json"]["sessions"][number];
type FunnelStep =
  paths["/analytics/funnel"]["get"]["responses"]["200"]["content"]["application/json"]["steps"][number];
type ClickRow =
  paths["/analytics/clicks"]["get"]["responses"]["200"]["content"]["application/json"]["clicks"][number];

type ProductMetricsRow = {
  product_url: string;
  views_1h?: number;
  views_24h?: number;
  views_7d?: number;
  unique_visitors_24h?: number;
  unique_visitors_7d?: number;
  return_visitor_count_7d?: number;
  cart_conversions_24h?: number;
  cart_conversions_7d?: number;
  avg_dwell_24h?: number | null;
  avg_scroll_24h?: number | null;
  cart_abandonment_rate?: number | null;
  return_visitor_rate?: number | null;
  engagement_score?: number | null;
  // Device segmentation
  views_mobile?: number;
  views_desktop?: number;
  carts_mobile?: number;
  carts_desktop?: number;
  // Source segmentation
  views_paid?: number;
  views_organic?: number;
  views_direct?: number;
  carts_paid?: number;
  carts_organic?: number;
  carts_direct?: number;
  // Temporal trend
  cart_rate_24h?: number | null;
  cart_rate_7d?: number | null;
  cart_rate_trend?: string | null;
  // Purchase attribution
  purchases_24h?: number;
  purchases_7d?: number;
  revenue_24h?: number;
  purchases_mobile?: number;
  purchases_desktop?: number;
  purchases_paid?: number;
  purchases_organic?: number;
  purchases_direct?: number;
  // Time-of-day
  peak_hour_views?: number;
  peak_hour_carts?: number;
  off_peak_hour_views?: number;
  off_peak_hour_carts?: number;
  peak_conversion_label?: string | null;
  // Session context
  landing_views_24h?: number;
  browsing_views_24h?: number;
  landing_carts_24h?: number;
  browsing_carts_24h?: number;
  landing_cart_rate?: number | null;
  browsing_cart_rate?: number | null;
};

type ProductTrendRow = {
  product_url: string;
  last_7_days_views: number[];
  total_views: number;
};

type SourceRow = {
  source_type: string;
  visitors: number;
  views: number;
  avg_dwell: number | null;
  avg_scroll: number | null;
  cart_conversions: number;
  hot_visitors: number;
  quality_label: "Strong intent" | "Mixed intent" | "Low quality";
  quality_score: number;
  attention_label: "Best source" | "Needs work" | "Low signal";
  // PRO ONLY — present only when fetched from /analytics/source-quality/pro.
  // Lite response omits this field entirely; Lite UI must never render it.
  action_insight?: string;
};

type SourceQualityData = {
  product_url: string | null;
  sources: SourceRow[];
  insight: string;
};

type MergedProductRow = ProductMetricsRow & {
  last_7_days_views: number[];
  trend_is_synthetic: boolean;
  attention_score: number;
  insight: string | null;
  action_suggestion: string | null;
  estimated_loss: number | null;
  priority: "HIGH" | "MED" | "LOW";
};

type ActionCandidate = {
  rank: number;
  product_url: string;
  action_type: string;
  reason: string;
  action_hint: string;
  confidence: number;
  urgency: number;
  expected_loss: number;
  loss_band: string;
  ready_now: boolean;
  supporting_signals: string[];
};

type ActionTask = {
  id: number;
  shop_domain: string;
  product_url: string;
  action_type: string;
  status: string;
  triggered_by: string;
  claimed_by: string | null;
  source_candidate: Record<string, unknown>;
  task_payload: Record<string, unknown>;
  expected_loss: number | null;
  confidence: number | null;
  urgency: number | null;
  created_at: string;
  updated_at: string;
  executed_at: string | null;
  completed_at: string | null;
  result_detail: string | null;
};

// ---------------------------------------------------------------------------
// Local helpers (small utilities used only here; larger helpers live in
// ./_lib/formatters.ts and ./_components/)
// ---------------------------------------------------------------------------

function formatDuration(seconds: number): string {
  if (seconds <= 0) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

function fmtLoss(value: number): string {
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(value);
  } catch {
    return `$${Math.round(value)}`;
  }
}

// Display currency formatter moved to /lib/currency.ts as part of the
// DRY refactor (Sprint A hardening, 11 April 2026). Imported at the top
// of this file as `formatDisplayMoney` from "../lib/currency".

function shortUrl(url: string): string {
  try {
    const path = new URL(url).pathname.replace(/\/$/, "");
    const parts = path.split("/").filter(Boolean);
    return "/" + parts.slice(-2).join("/");
  } catch {
    return url.length > 48 ? "…" + url.slice(-46) : url;
  }
}

// ---------------------------------------------------------------------------
// Phase Ω⁶ split — all extracted components live in ./_components/
// CountUp, KpiCard, SectionHeading, Divider, KpiSkeleton, TableSkeleton,
// FunnelVisualization, KpiInsightModal, TrafficSourceBox, ProductInsightPanel,
// LiveRadarMap (+ LiveRadarMap.data), LAND_PATHS, DEMO_VISITORS, geoToMapXY.
// Formatters live in ./_lib/formatters.ts.

// ---------------------------------------------------------------------------
// Error Boundary — prevents a single component crash from white-screening
// the entire dashboard.  Shows a recovery UI instead.
// ---------------------------------------------------------------------------
class DashboardErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error: Error | null }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Sentry captures this automatically when installed;
    // console.error ensures it's visible even without Sentry
    console.error("[HedgeSpark] Dashboard render error:", error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-[#07070f] px-6">
          <div className="max-w-md rounded-2xl border border-rose-400/20 bg-rose-500/[0.07] p-8 text-center">
            <div className="text-lg font-semibold text-rose-200">Something went wrong</div>
            <p className="mt-3 text-sm text-rose-200/70">
              The dashboard hit an unexpected error. Your store data is safe — this is a display issue only.
            </p>
            {this.state.error && (
              <p className="mt-2 rounded bg-black/30 p-2 text-xs text-rose-300/60 font-mono break-all">
                {this.state.error.message}
              </p>
            )}
            <button
              onClick={() => window.location.reload()}
              className="mt-5 rounded-lg bg-rose-500/25 px-5 py-2.5 text-sm font-semibold text-rose-100 ring-1 ring-rose-400/25 transition hover:bg-rose-500/35"
            >
              Refresh dashboard
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
function PageInner() {
  // Layout state
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activeSection, setActiveSection] = useState("brief");
  const mainRef = useRef<HTMLElement | null>(null);
  // Track whether user just clicked nav — suppresses observer updates briefly
  const isScrollingRef = useRef(false);

  // ── Scroll-synced sidebar via IntersectionObserver ──
  // Re-runs when loading/tier change (sections appear conditionally)
  const observerRef = useRef<IntersectionObserver | null>(null);
  const reobserve = useCallback(() => {
    const main = mainRef.current;
    if (!main) return;

    observerRef.current?.disconnect();

    const obs = new IntersectionObserver(
      (entries) => {
        if (isScrollingRef.current) return;
        let topEntry: IntersectionObserverEntry | null = null;
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          if (!topEntry || entry.boundingClientRect.top < topEntry.boundingClientRect.top) {
            topEntry = entry;
          }
        }
        if (topEntry) {
          const sectionId = topEntry.target.id.replace("section-", "");
          setActiveSection(sectionId);
        }
      },
      {
        root: main,
        rootMargin: "-10% 0px -70% 0px",
        threshold: 0,
      }
    );

    const sections = main.querySelectorAll("[id^='section-']");
    sections.forEach((s) => obs.observe(s));
    observerRef.current = obs;
  }, []);

  // Tier state
  const [tier, setTier] = useState<"lite" | "pro">("lite");
  const [upgradeModalOpen, setUpgradeModalOpen] = useState(false);

  // Billing callback toast (shown after Shopify billing redirect returns)
  const [billingToast, setBillingToast] = useState<{
    type: "activated" | "declined" | "pending" | "error";
    visible: boolean;
  } | null>(null);
  // True when the page loaded with ?billing=activated — triggers deep re-check in OnboardingHub
  const [billingJustActivated, setBillingJustActivated] = useState(false);
  // True when the page loaded with ?installed=1 — triggers grace period in OnboardingHub
  const [freshInstall, setFreshInstall] = useState(false);
  // Setup readiness — populated by OnboardingHub callback
  // (setupChecks state was previously stored here but never read; the
  // readiness string + billing upgrade are the only bits we actually
  // branch on. Dropped to cut wasted renders.)
  const [setupReadiness, setSetupReadiness] = useState<string | null>(null);
  // Pro billing config — from /merchant/plan response, used for trial-aware CTAs
  const [proTrialDays, setProTrialDays] = useState(14);
  const [proPrice, setProPrice] = useState(49);
  // billing_confirmed_at — ISO string from backend, used to derive trial countdown
  const [billingConfirmedAt, setBillingConfirmedAt] = useState<string | null>(null);
  const [activeKpi, setActiveKpi] = useState<string | null>(null);
  const [activeTopProduct, setActiveTopProduct] = useState<TopProduct | null>(null);
  const [sourceQuality, setSourceQuality] = useState<SourceQualityData | null>(null);

  // Shop
  const [shop, setShop] = useState("");

  // Overview data
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Live visitors
  const [liveVisitors, setLiveVisitors] = useState<LiveVisitor[]>([]);

  // Opportunity signals
  const [signals, setSignals] = useState<OpportunitySignal[]>([]);
  const [heroRevenue, setHeroRevenue] = useState<{ revenue: number; orders: number; aov: number; currency: string } | null>(null);
  const [recentActions, setRecentActions] = useState<RecentAction[]>([]);

  // Analytics
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [topPages, setTopPages] = useState<TopPage[]>([]);

  // Behavioral analytics — Session Replay Lite, Funnel, Click Insights
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [funnelSteps, setFunnelSteps] = useState<FunnelStep[]>([]);
  const [clicks, setClicks] = useState<ClickRow[]>([]);

  // Daily brief
  const [brief, setBrief] = useState<DailyBrief | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);

  // Product-level metrics and trend
  const [productMetrics, setProductMetrics] = useState<ProductMetricsRow[]>([]);
  const [productTrend, setProductTrend] = useState<ProductTrendRow[]>([]);

  // Action candidates + tasks (Pro only)
  // Previously stored a `taskMap` state + `expandedTaskKey` — both were
  // written to but never read (the UI renders from `candidates` directly
  // and the poll only needs hasExecutingRef to gate network calls).
  // Removed to cut wasted re-renders on every poll tick.
  const [candidates, setCandidates] = useState<ActionCandidate[]>([]);
  const hasExecutingRef = useRef(false);

  // Pro intelligence modules
  const [attrSummary, setAttrSummary] = useState<AttributionSummaryData | null>(null);
  // Display currency preference — merchant-chosen visualization currency.
  // Storage + FX conversion helpers live in /lib/currency.ts (single source
  // of truth shared by all dashboard components).
  const [displayCurrency, setDisplayCurrencyState] = useState<DisplayCurrency>("USD");
  useEffect(() => {
    setDisplayCurrencyState(readSavedDisplayCurrency("USD"));
  }, []);
  const setDisplayCurrency = useCallback((c: DisplayCurrency) => {
    setDisplayCurrencyState(c);
    writeSavedDisplayCurrency(c);
  }, []);

  const [ltvData, setLtvData] = useState<MonthlyCohortsData | null>(null);
  const [forecastData, setForecastData] = useState<RevenueForecastData | null>(null);
  const [behavioralData, setBehavioralData] = useState<BehavioralCohortsData | null>(null);
  const [gatewayProductsData, setGatewayProductsData] = useState<GatewayProductsData | null>(null);
  const [predictedLtvData, setPredictedLtvData] = useState<PredictedLtvData | null>(null);
  const [pnlData, setPnlData] = useState<PnlReportData | null>(null);

  // Session expiry detection — set when any fetch returns 401/403 mid-session
  const [sessionExpired, setSessionExpired] = useState(false);

  // Analytics error surfacing — non-empty when analytics fetch fails
  const [analyticsError, setAnalyticsError] = useState("");

  // Data freshness — timestamp of last successful analytics load
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // System health — light warning when backend is degraded
  const [systemHealthIssues, setSystemHealthIssues] = useState<string[]>([]);

  // ---------------------------------------------------------------------------
  // Session bootstrap — resolve shop from backend session cookie
  //
  // Flow:
  //   1. Call GET /merchant/me (sends httpOnly cookie automatically)
  //   2. If 200 → backend returned shop_domain from the signed JWT.
  //      Set shop + tier from the response.  This is the trusted path.
  //   3. If 401 → no valid session.  Fall back to ?shop= from URL
  //      (covers the OAuth redirect landing where cookie was just set
  //      but the redirect to the frontend is a new navigation).
  //   4. If still no shop → show "No shop connected" UI.
  //
  // The ?shop= query param is NEVER trusted as identity.  It is used
  // only as a display hint while the session resolves, and only when
  // the backend session call fails (e.g. first load after OAuth redirect
  // where the browser may not have sent the cookie yet due to SameSite).
  // ---------------------------------------------------------------------------
  const [sessionResolved, setSessionResolved] = useState(false);

  useEffect(() => {
    // 1. Handle billing callback toast (URL params, non-auth)
    const params = new URLSearchParams(window.location.search);
    const billingResult = params.get("billing");
    if (billingResult && ["activated", "declined", "pending", "error"].includes(billingResult)) {
      setBillingToast({ type: billingResult as "activated" | "declined" | "pending" | "error", visible: true });
      if (billingResult === "activated") setBillingJustActivated(true);
      params.delete("billing");
      const clean = params.toString();
      window.history.replaceState({}, "", `/app${clean ? `?${clean}` : ""}`);
      setTimeout(() => setBillingToast((prev) => prev ? { ...prev, visible: false } : null), 8000);
    }

    // 1b. Detect fresh install (grace period for OnboardingHub)
    if (params.get("installed") === "1") {
      setFreshInstall(true);
    }

    // 2. Resolve shop from session cookie via /merchant/me
    if (!API_BASE) {
      setSessionResolved(true);
      return;
    }

    apiClient
      .GET("/merchant/me", { headers: getHeaders(apiHeaders) })
      .then(async (res) => {
        const json = res.data;
        if (json != null) {
          const shopDomain = json.shop_domain;
          if (shopDomain) {
            setShop(shopDomain);
            // Remember shop for future re-auth if cookie expires
            try {
              localStorage.setItem("hs_last_shop", shopDomain);
            } catch {}
            const isPro = json.plan === "pro" && json.billing_active === true;
            setTier(isPro ? "pro" : "lite");
            if (json.pro_trial_days != null) setProTrialDays(json.pro_trial_days);
            if (json.pro_price != null) setProPrice(json.pro_price);
            setBillingConfirmedAt(json.billing_confirmed_at ?? null);

            // Clean ?shop= from URL if present — session is the source of truth
            if (params.get("shop")) {
              params.delete("shop");
              params.delete("installed");
              params.delete("webhook");
              params.delete("tracker");
              const cleaned = params.toString();
              window.history.replaceState({}, "", `/app${cleaned ? `?${cleaned}` : ""}`);
            }
            return;
          }
        }

        // 3. No valid session — check for ?shop= param OR remembered shop
        const urlShop = params.get("shop") || "";
        const rememberedShop = (() => {
          try {
            return localStorage.getItem("hs_last_shop") || "";
          } catch {
            return "";
          }
        })();
        const justInstalled = params.get("installed") === "1";

        // Auto-redirect to bootstrap if we remember the shop from before
        // (returning merchant, session cookie expired). Skip if URL already
        // has shop param — that path is handled below.
        if (!urlShop && rememberedShop && API_BASE) {
          window.location.href = `${API_BASE}/auth/session?shop=${encodeURIComponent(rememberedShop)}`;
          return;
        }

        if (urlShop && justInstalled) {
          // Just came from OAuth callback — shop param is trusted, proceed
          setShop(urlShop);
          try {
            const planRes = await apiClient.GET("/merchant/plan", {
              headers: getHeaders(apiHeaders),
            });
            const planJson = planRes.data;
            if (planJson != null) {
              const isPro = planJson.plan === "pro" && planJson.billing_active === true;
              setTier(isPro ? "pro" : "lite");
              if (planJson.pro_trial_days != null) setProTrialDays(planJson.pro_trial_days);
              if (planJson.pro_price != null) setProPrice(planJson.pro_price);
              setBillingConfirmedAt(planJson.billing_confirmed_at ?? null);
            }
          } catch { /* tier stays lite */ }
        } else if (urlShop) {
          // Has shop param but no session — redirect to bootstrap endpoint
          // This creates a session cookie and redirects back here
          window.location.href = `${API_BASE}/auth/session?shop=${encodeURIComponent(urlShop)}`;
          return;  // stop processing — page will reload after redirect
        }
      })
      .catch(() => {
        // Network error — try URL fallback
        const urlShop = params.get("shop") || "";
        if (urlShop) setShop(urlShop);
      })
      .finally(() => setSessionResolved(true));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Listen for session-expired events from any fetch
  useEffect(() => {
    const handler = () => setSessionExpired(true);
    window.addEventListener(SESSION_EXPIRED_EVENT, handler);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, handler);
  }, []);

  // ---------------------------------------------------------------------------
  // Deep-link: scroll to section from ?section= query param (e.g., from digest email)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!sessionResolved) return;
    const params = new URLSearchParams(window.location.search);

    // Handle ?section= deep-link (from digest CTA)
    const sectionParam = params.get("section");
    if (sectionParam) {
      setTimeout(() => {
        const el = document.getElementById(`section-${sectionParam}`);
        if (el) {
          setActiveSection(sectionParam);
          el.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      }, 500);
      params.delete("section");
    }

    // Handle ?upgrade=1 (from digest Lite teaser)
    if (params.get("upgrade") === "1" && tier === "lite") {
      setTimeout(() => setUpgradeModalOpen(true), 600);
      params.delete("upgrade");
    }

    // Clean URL
    const clean = params.toString();
    window.history.replaceState({}, "", `/app${clean ? `?${clean}` : ""}`);

    // Load recent actions from localStorage
    setRecentActions(loadRecentActions());
  }, [sessionResolved, tier]);

  // ---------------------------------------------------------------------------
  // Setup status → tier sync callback
  // When OnboardingHub detects pro_active, upgrade tier immediately
  // instead of waiting for the separate /merchant/plan fetch.
  // ---------------------------------------------------------------------------
  const handleReadinessChange = useCallback(
    (readiness: string, checks: {
      merchant_exists: boolean; install_active: boolean; token_ok: boolean;
      webhook_ok: boolean; tracker_ok: boolean;
      billing_active: boolean; billing_plan: string; billing_charge_pending: boolean;
    }) => {
      setSetupReadiness(readiness);
      if (readiness === "pro_active" && checks.billing_active && checks.billing_plan === "pro") {
        setTier("pro");
      }
    },
    []
  );

  // ---------------------------------------------------------------------------
  // Daily brief fetch
  // ---------------------------------------------------------------------------
  // System health check — light warning when workers stale or ingestion broken
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!API_BASE) return;
    let active = true;
    async function checkHealth() {
      try {
        const res = await fetch(`${API_BASE}/system/health`, { credentials: "include", cache: "no-store" });
        if (!res.ok) return;
        const json = await res.json();
        if (active && json.status !== "ok" && Array.isArray(json.issues)) {
          setSystemHealthIssues(json.issues);
        } else if (active) {
          setSystemHealthIssues([]);
        }
      } catch { /* silent */ }
    }
    checkHealth();
    const id = setInterval(checkHealth, 300_000); // every 5 min
    return () => { active = false; clearInterval(id); };
  }, []);

  // ---------------------------------------------------------------------------
  // Daily brief fetch
  //
  // Lite users  → /brief/today        (diagnostic fields; top_action,
  //                                    summary_text, and human_action inside
  //                                    metrics_snapshot entries are absent)
  // Pro users   → /brief/today/pro    (+ top_action, summary_text,
  //                                    and human_action per snapshot entry)
  //
  // Depends on [shop, tier] so the correct endpoint is fetched once tier
  // resolves from the plan fetch.  All Pro fields are optional in DailyBrief
  // so both response shapes are handled by the same type and components.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!shop) return;
    let active = true;
    setBriefLoading(true);

    // PRO ONLY: fetch the Pro endpoint when tier is confirmed as "pro".
    // Lite endpoint shape is a strict subset of Pro — DailyBrief covers both.
    const endpoint = tier === "pro"
      ? `${API_BASE}/brief/today/pro?shop=${encodeURIComponent(shop)}`
      : `${API_BASE}/brief/today?shop=${encodeURIComponent(shop)}`;

    fetch(endpoint, { headers: apiHeaders(), credentials: "include", cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((json) => { if (active) setBrief(json); })
      .catch(() => { if (active) setBrief(null); })
      .finally(() => { if (active) setBriefLoading(false); });
    return () => { active = false; };
  }, [shop, tier]);

  // ---------------------------------------------------------------------------
  // Primary overview fetch
  //
  // Lite users  → /dashboard/overview        (summary + top_products only)
  // Pro users   → /dashboard/overview/pro    (+ price_intelligence, market_lookup)
  //
  // Depends on [shop, tier] so the correct endpoint is fetched once tier
  // resolves from the plan fetch.  All Pro fields are optional in OverviewResponse
  // so the same setData() call handles both response shapes — missing Pro sections
  // are simply absent.
  //
  // OverviewResponse only contains fields that are actively rendered.
  // The Pro overview also returns top_hot_visitors, product_opportunities, and
  // ai_recommended_actions but these have no render code and are intentionally
  // not parsed — see backend /dashboard/overview/pro for the full payload.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!shop) {
      setLoading(false);
      return;
    }

    let mounted = true;

    // PRO ONLY: fetch the Pro endpoint when tier is confirmed as "pro".
    // Lite endpoint shape is a strict subset of Pro — OverviewResponse covers both.
    const overviewEndpoint = tier === "pro"
      ? `${API_BASE}/dashboard/overview/pro?shop=${encodeURIComponent(shop)}`
      : `${API_BASE}/dashboard/overview?shop=${encodeURIComponent(shop)}`;

    async function loadOverview() {
      try {
        setLoading(true);
        setError("");

        const res = await fetch(
          overviewEndpoint,
          { headers: apiHeaders(), credentials: "include", cache: "no-store" }
        );

        if (res.status === 401 || res.status === 403) {
          dispatchSessionExpired();
          return;
        }
        if (!res.ok) throw new Error(`Overview failed: ${res.status}`);
        const json = (await res.json()) as OverviewResponse;

        if (mounted) {
          setData({
            summary:              json.summary || {},
            top_products:         Array.isArray(json.top_products) ? json.top_products : [],
            price_intelligence:   Array.isArray(json.price_intelligence) ? json.price_intelligence : [],
            market_lookup:        Array.isArray(json.market_lookup) ? json.market_lookup : [],
            revenue_window_tease: json.revenue_window_tease ?? undefined,
            revenue_windows:      json.revenue_windows ?? undefined,
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

    // Fetch hero revenue (lightweight, from /orders/summary)
    async function loadHeroRevenue() {
      try {
        const res = await fetch(
          `${API_BASE}/orders/summary?shop=${encodeURIComponent(shop)}`,
          { headers: apiHeaders(), credentials: "include", cache: "no-store" }
        );
        if (res.ok) {
          const json = await res.json();
          if (mounted && json.last_7d) {
            setHeroRevenue({
              revenue: json.last_7d.total_revenue ?? 0,
              orders: json.last_7d.order_count ?? 0,
              aov: json.last_7d.avg_order_value ?? 0,
              currency: json.currency ?? "USD",
            });
          }
        }
      } catch { /* non-critical */ }
    }
    loadHeroRevenue();

    return () => { mounted = false; };
  }, [shop, tier]);

  // ---------------------------------------------------------------------------
  // Product metrics + product trend (parallel, refreshed every 5 min)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!shop) return;
    let active = true;

    async function loadProductData() {
      try {
        const [metricsRes, trendRes] = await Promise.all([
          fetch(
            `${API_BASE}/products/metrics?shop=${encodeURIComponent(shop)}`,
            { headers: apiHeaders(), credentials: "include", cache: "no-store" }
          ),
          fetch(
            `${API_BASE}/products/trend?shop=${encodeURIComponent(shop)}`,
            { headers: apiHeaders(), credentials: "include", cache: "no-store" }
          ),
        ]);

        const metricsJson = metricsRes.ok ? await metricsRes.json() : { products: [] };
        const trendJson = trendRes.ok ? await trendRes.json() : { products: [] };

        // Debug logging removed for production

        if (!active) return;

        const metrics: ProductMetricsRow[] = Array.isArray(metricsJson.products)
          ? metricsJson.products
          : [];

        // Normalise trend rows: guard against missing or non-array last_7_days_views
        const trendRows: ProductTrendRow[] = (
          Array.isArray(trendJson.products) ? trendJson.products : []
        ).map((t: ProductTrendRow) => ({
          ...t,
          last_7_days_views: Array.isArray(t.last_7_days_views)
            ? t.last_7_days_views.map((v) => (typeof v === "number" ? v : 0))
            : [],
        }));

        // Debug logging removed for production

        setProductMetrics(metrics);
        setProductTrend(trendRows);
      } catch (err) {
        console.warn("[WishSpark] loadProductData error:", err);
      }
    }

    loadProductData();
    const id = setInterval(loadProductData, 300_000);
    return () => { active = false; clearInterval(id); };
  }, [shop]);

  // ---------------------------------------------------------------------------
  // Source quality (refreshed every 5 min, same cadence as product data)
  //
  // Lite users  → /analytics/source-quality        (diagnostic fields only)
  // Pro users   → /analytics/source-quality/pro    (+ action_insight per source)
  //
  // The endpoint choice depends on `tier`, so this effect re-runs when tier
  // resolves (plan fetch completes after shop is set).  Pro users will see a
  // brief Lite response until the plan fetch resolves, then upgrade to Pro data.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!shop) return;
    let active = true;

    // PRO ONLY: fetch the Pro endpoint when tier is confirmed as "pro".
    // Lite endpoint shape is a strict subset of Pro — SourceQualityData covers both.
    const endpoint = tier === "pro"
      ? `${API_BASE}/analytics/source-quality/pro?shop=${encodeURIComponent(shop)}`
      : `${API_BASE}/analytics/source-quality?shop=${encodeURIComponent(shop)}`;

    async function loadSourceQuality() {
      try {
        const res = await fetch(endpoint, { headers: apiHeaders(), credentials: "include", cache: "no-store" });
        if (!res.ok) return;
        const json = await res.json();
        if (active) setSourceQuality(json as SourceQualityData);
      } catch { /* silent */ }
    }

    loadSourceQuality();
    const id = setInterval(loadSourceQuality, 300_000);
    return () => { active = false; clearInterval(id); };
  }, [shop, tier]);

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
          { headers: apiHeaders(), credentials: "include", cache: "no-store" }
        );
        if (res.status === 401 || res.status === 403) { dispatchSessionExpired(); return; }
        if (!res.ok) return;
        const json = await res.json();
        if (active) setLiveVisitors(Array.isArray(json.visitors) ? json.visitors : []);
      } catch { /* silent */ }
    }

    loadLive();
    const id = setInterval(loadLive, 15_000);  // 15s — matches backend 15-min recency + 10s cache
    return () => { active = false; clearInterval(id); };
  }, [shop]);

  // ---------------------------------------------------------------------------
  // Opportunity signals (every 30s)
  //
  // Lite users  → /opportunities        (diagnostic fields, human_action absent)
  // Pro users   → /opportunities/pro    (+ human_action per signal)
  //
  // Depends on [shop, tier] so the correct endpoint is fetched once tier
  // resolves from the plan fetch.  human_action is optional in OpportunitySignal
  // so both response shapes are handled by the same type and components.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!shop) return;
    let active = true;

    // PRO ONLY: fetch the Pro endpoint when tier is confirmed as "pro".
    // Backend enforces the plan check — a 403 from the Pro endpoint is
    // silently swallowed and signals remain empty until tier resolves.
    const endpoint = tier === "pro"
      ? `${API_BASE}/opportunities/pro?shop=${encodeURIComponent(shop)}`
      : `${API_BASE}/opportunities?shop=${encodeURIComponent(shop)}`;

    async function loadSignals() {
      try {
        const res = await fetch(endpoint, { headers: apiHeaders(), credentials: "include", cache: "no-store" });
        if (res.status === 401 || res.status === 403) { dispatchSessionExpired(); return; }
        if (!res.ok) return;
        const json = await res.json();
        if (active) setSignals(Array.isArray(json) ? json : []);
      } catch { /* silent */ }
    }

    loadSignals();
    const id = setInterval(loadSignals, 30000);
    return () => { active = false; clearInterval(id); };
  }, [shop, tier]);

  // ---------------------------------------------------------------------------
  // Action candidates + tasks (Pro only, initial load)
  //
  // Fetches /actions/candidates/pro and /actions/tasks in parallel when the
  // shop is Pro.  Re-runs if shop or tier changes (e.g. plan upgrade mid-session).
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!shop || tier !== "pro") return;
    let active = true;

    async function loadActionsData() {
      try {
        const typedHeaders = getHeaders(apiHeaders);
        const [candRes, taskRes] = await Promise.all([
          fetch(
            `${API_BASE}/actions/candidates/pro?shop=${encodeURIComponent(shop)}`,
            { headers: apiHeaders(), credentials: "include", cache: "no-store" }
          ),
          apiClient.GET("/actions/tasks", {
            params: { query: { limit: 50 } },
            headers: typedHeaders,
          }),
        ]);
        if (!active) return;
        if (candRes.ok) {
          const j = await candRes.json();
          setCandidates(Array.isArray(j.candidates) ? j.candidates : []);
        }
        if (taskRes.data != null) {
          const tasks = taskRes.data.tasks as unknown as ActionTask[];
          hasExecutingRef.current = tasks.some((t) => t.status === "executing");
        }
      } catch { /* silent */ }
    }

    loadActionsData();
    return () => { active = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop, tier]);

  // ---------------------------------------------------------------------------
  // Action tasks poll (every 30s, skipped when no executing tasks)
  //
  // Sets up a permanent 30s interval but bails out of the fetch when
  // hasExecutingRef.current is false — zero network cost when idle.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!shop || tier !== "pro") return;
    let active = true;

    async function pollActionTasks() {
      if (!hasExecutingRef.current) return;
      try {
        const res = await apiClient.GET("/actions/tasks", {
          params: { query: { limit: 50 } },
          headers: getHeaders(apiHeaders),
        });
        if (!active || res.data == null) return;
        const tasks = res.data.tasks as unknown as ActionTask[];
        hasExecutingRef.current = tasks.some((t) => t.status === "executing");
      } catch { /* silent */ }
    }

    const id = setInterval(pollActionTasks, 30_000);
    return () => { active = false; clearInterval(id); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop, tier]);

  // ---------------------------------------------------------------------------
  // Pro intelligence: attribution summary, LTV cohorts, revenue forecast
  // Loaded once when Pro tier is confirmed (not polled — these are snapshots)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!shop || tier !== "pro") return;
    let active = true;

    async function loadProIntelligence() {
      const typedHeaders = getHeaders(apiHeaders);

      // All six Pro intelligence endpoints now go through the typed client.
      // TypeScript validates the URL path, query params, AND response body
      // shape against the generated OpenAPI types — any backend rename
      // surfaces as a compile error on the next `npm run api:types`.
      const [attrRes, ltvRes, fcRes, behRes, gwRes, predRes, pnlRes] = await Promise.allSettled([
        apiClient.GET("/attribution/summary/pro", {
          params: { query: { days: 30 } },
          headers: typedHeaders,
        }),
        apiClient.GET("/pro/cohorts/monthly", {
          params: { query: { months: 6 } },
          headers: typedHeaders,
        }),
        apiClient.GET("/orders/forecast/pro", {
          params: { query: {} },
          headers: typedHeaders,
        }),
        apiClient.GET("/pro/cohorts/behavioral", {
          params: { query: { days: 90 } },
          headers: typedHeaders,
        }),
        apiClient.GET("/pro/cohorts/ltv/products", {
          params: { query: { limit: 12 } },
          headers: typedHeaders,
        }),
        apiClient.GET("/pro/cohorts/ltv/customers", {
          params: { query: { limit: 10 } },
          headers: typedHeaders,
        }),
        apiClient.GET("/pro/pnl", {
          params: { query: { window_days: 30 } },
          headers: typedHeaders,
        }),
      ]);

      if (!active) return;

      if (attrRes.status === "fulfilled" && attrRes.value.data != null) {
        setAttrSummary(attrRes.value.data);
      }
      if (ltvRes.status === "fulfilled" && ltvRes.value.data != null) {
        setLtvData(ltvRes.value.data);
      }
      if (fcRes.status === "fulfilled" && fcRes.value.data != null) {
        setForecastData(fcRes.value.data);
      }
      if (behRes.status === "fulfilled" && behRes.value.data != null) {
        setBehavioralData(behRes.value.data);
      }
      if (gwRes.status === "fulfilled" && gwRes.value.data != null) {
        setGatewayProductsData(gwRes.value.data);
      }
      if (predRes.status === "fulfilled" && predRes.value.data != null) {
        setPredictedLtvData(predRes.value.data);
      }
      if (pnlRes.status === "fulfilled" && pnlRes.value.data != null) {
        setPnlData(pnlRes.value.data);
      }
    }

    loadProIntelligence().catch(() => {});
    return () => { active = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop, tier]);

  // ---------------------------------------------------------------------------
  // Analytics: alerts, weekly trend, top pages (every 30s)
  //
  // Lite users  → /analytics/alerts        (type, priority, message only;
  //                                          action field absent)
  // Pro users   → /analytics/alerts/pro    (+ action per alert)
  //
  // Depends on [shop, tier] so the alerts re-fetch with the correct endpoint
  // once tier resolves from the plan fetch.  action is optional in Alert so
  // both response shapes are handled by the same type and render logic.
  // The other analytics endpoints (trend, pages, sessions, funnel, clicks)
  // are not tier-gated and always use the same URL.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!shop) return;
    let active = true;

    // PRO ONLY: fetch the Pro endpoint when tier is confirmed as "pro".
    // Lite endpoint shape is a strict subset of Pro — Alert covers both.
    const alertsEndpoint = tier === "pro"
      ? `${API_BASE}/analytics/alerts/pro?shop=${encodeURIComponent(shop)}`
      : `${API_BASE}/analytics/alerts?shop=${encodeURIComponent(shop)}`;

    async function loadAnalytics() {
      try {
        const typedHeaders = getHeaders(apiHeaders);
        // Core analytics: alerts + weekly trend (all tiers) via typed client.
        const alertsPromise = tier === "pro"
          ? apiClient.GET("/analytics/alerts/pro", { headers: typedHeaders })
          : apiClient.GET("/analytics/alerts",     { headers: typedHeaders });
        const [alertsRes, trendRes] = await Promise.all([
          alertsPromise,
          apiClient.GET("/analytics/weekly-trend", { headers: typedHeaders }),
        ]);

        // Detect session expiry on core analytics (most reliable signal)
        const alertsStatus = alertsRes.response.status;
        const trendStatus = trendRes.response.status;
        if ([alertsStatus, trendStatus].some((s) => s === 401 || s === 403)) {
          dispatchSessionExpired();
          return;
        }

        if (!active) return;
        setAlerts(alertsRes.data?.alerts ?? []);
        setTrend(trendRes.data?.trend ?? []);
        setAnalyticsError("");
        setLastUpdated(new Date());

        // Top pages — available to all tiers
        try {
          const pagesRes = await apiClient.GET("/analytics/top-pages", { headers: typedHeaders });
          if (active) setTopPages(pagesRes.data?.pages ?? []);
        } catch { /* top pages is supplementary — degrade silently */ }

        // Funnel, sessions, clicks — Pro only
        if (tier === "pro") {
          const [sessionsRes, funnelRes, clicksRes] = await Promise.all([
            apiClient.GET("/analytics/sessions", { headers: typedHeaders }),
            apiClient.GET("/analytics/funnel",   { headers: typedHeaders }),
            apiClient.GET("/analytics/clicks",   { headers: typedHeaders }),
          ]);

          if (!active) return;
          setSessions(sessionsRes.data?.sessions ?? []);
          setFunnelSteps(funnelRes.data?.steps ?? []);
          setClicks(clicksRes.data?.clicks ?? []);
        }
      } catch {
        if (active) setAnalyticsError("Unable to load analytics — retrying…");
      }
    }

    loadAnalytics();
    const id = setInterval(loadAnalytics, 30000);
    return () => { active = false; clearInterval(id); };
  }, [shop, tier]);

  // ---------------------------------------------------------------------------
  // Derived data
  // ---------------------------------------------------------------------------
  const summary              = data?.summary || {};
  const topProducts          = data?.top_products || [];
  const priceIntel           = data?.price_intelligence || [];
  const marketIntel          = data?.market_lookup || [];
  const revenueWindowTease   = data?.revenue_window_tease ?? null;
  const revenueWindows       = data?.revenue_windows ?? null;

  // ---------------------------------------------------------------------------
  // Feature gating — derived from plan tier, replace with billing check later
  // ---------------------------------------------------------------------------
  // ⚠️ DEV OVERRIDE — founder is reviewing all dashboard sections during build.
  // Remove the `|| true` before production / before re-enabling Lite/Pro gating.
  // Ref: founder request 2026-04-10 "sblocca tutto, devo vedere tutto".
  const isProUser = tier === "pro" || true;

  // ---------------------------------------------------------------------------
  // Signal dedup + split: early (low confidence) vs strong (high confidence)
  // ---------------------------------------------------------------------------
  const { earlySignals, strongSignals } = useMemo(() => {
    // Dedup mapping: early signal types that are superseded by strong equivalents
    const EARLY_SUPERSEDED_BY: Record<string, string[]> = {
      EARLY_BROWSING_NO_CART: ["HIGH_TRAFFIC_NO_CART", "LOW_CONVERSION_ATTENTION", "DEAD_TRAFFIC"],
      FIRST_VISITOR_ENGAGEMENT: ["HIGH_ENGAGEMENT_NO_ACTION", "SCROLL_HIGH_NO_CLICK"],
      EARLY_DROP_OFF: ["DEAD_TRAFFIC", "HIGH_ENGAGEMENT_NO_ACTION"],
      SINGLE_PRODUCT_FOCUS: ["HIGH_TRAFFIC_NO_CART", "HIGH_ENGAGEMENT_NO_ACTION", "TRAFFIC_SPIKE"],
    };

    const strong: OpportunitySignal[] = [];
    const early: OpportunitySignal[] = [];

    // Collect strong signals first
    for (const s of signals) {
      if (s.signal_confidence !== "low") strong.push(s);
    }

    // Build set of (product_url, strong_signal_type) pairs for dedup
    const strongKeys = new Set(
      strong.map((s) => `${s.product_url}::${s.signal_type}`)
    );

    // Filter early signals: suppress if a strong equivalent exists for same product
    for (const s of signals) {
      if (s.signal_confidence !== "low") continue;
      const supersedors = EARLY_SUPERSEDED_BY[s.signal_type || ""] || [];
      const suppressed = supersedors.some((st) => strongKeys.has(`${s.product_url}::${st}`));
      if (!suppressed) early.push(s);
    }

    // Deduplicate early: one signal per product_url (keep highest strength)
    const seenProducts = new Set<string>();
    const dedupedEarly: OpportunitySignal[] = [];
    for (const s of early.sort((a, b) => (b.signal_strength ?? 0) - (a.signal_strength ?? 0))) {
      const key = s.product_url || "";
      if (!seenProducts.has(key)) {
        seenProducts.add(key);
        dedupedEarly.push(s);
      }
    }

    return {
      earlySignals: dedupedEarly.sort((a, b) =>
        new Date(b.detected_at || 0).getTime() - new Date(a.detected_at || 0).getTime()
      ).slice(0, 5),
      strongSignals: strong,
    };
  }, [signals]);

  // ---------------------------------------------------------------------------
  // Cost Configuration state (Settings section) — Sprint B Phase 2
  // Powers the Profit Intelligence cassettone by overriding the pnl_engine
  // module defaults with real shop-level cost data. Every field is optional
  // and merges with the existing row on PATCH.
  // ---------------------------------------------------------------------------
  type CostDefaults =
    paths["/pro/costs/defaults"]["get"]["responses"]["200"]["content"]["application/json"];

  const [costDefaults, setCostDefaults] = useState<CostDefaults | null>(null);
  const [costFormCogsPct,  setCostFormCogsPct]  = useState<string>("");
  const [costFormShipping, setCostFormShipping] = useState<string>("");
  const [costFormPayPct,   setCostFormPayPct]   = useState<string>("");
  const [costFormPayFlat,  setCostFormPayFlat]  = useState<string>("");
  const [costFormAdSpend,  setCostFormAdSpend]  = useState<string>("");
  const [costSaving,       setCostSaving]       = useState(false);
  const [costSavedMsg,     setCostSavedMsg]     = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [costSyncing,      setCostSyncing]      = useState(false);
  const [costSyncMsg,      setCostSyncMsg]      = useState<{ type: "ok" | "err"; text: string } | null>(null);

  // Privacy — Art. 22 automated-targeting opt-out toggle
  const [privacyOptedOut,  setPrivacyOptedOut]  = useState(false);
  const [privacyLoading,   setPrivacyLoading]   = useState(false);

  // Fetch current cost defaults when shop resolves.
  useEffect(() => {
    if (!shop) return;
    let active = true;
    apiClient
      .GET("/pro/costs/defaults", { headers: getHeaders(apiHeaders) })
      .then((res) => {
        if (!active || res.data == null) return;
        setCostDefaults(res.data);
        // Seed form inputs with current values (or empty when NULL).
        // COGS % is stored as fraction (0.40) but displayed as 40 in the input.
        setCostFormCogsPct(
          res.data.default_cogs_pct != null
            ? String(Math.round(res.data.default_cogs_pct * 100))
            : ""
        );
        setCostFormShipping(
          res.data.default_shipping_per_order != null
            ? String(res.data.default_shipping_per_order)
            : ""
        );
        setCostFormPayPct(
          res.data.payment_pct != null
            ? String((res.data.payment_pct * 100).toFixed(2))
            : ""
        );
        setCostFormPayFlat(
          res.data.payment_flat != null
            ? String(res.data.payment_flat)
            : ""
        );
        setCostFormAdSpend(
          res.data.ad_spend_manual_monthly != null
            ? String(res.data.ad_spend_manual_monthly)
            : ""
        );
      })
      .catch(() => { /* silent */ });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop]);

  // Fetch privacy preferences (Art. 22 opt-out state)
  useEffect(() => {
    if (!shop) return;
    let active = true;
    fetch(`${API_BASE}/merchant/privacy/preferences`, {
      credentials: "include",
    })
      .then((r) => r.json())
      .then((d) => {
        if (active && d.opt_out_automated_targeting != null) {
          setPrivacyOptedOut(!!d.opt_out_automated_targeting);
        }
      })
      .catch(() => {});
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop]);

  async function handlePrivacyToggle() {
    if (!shop || privacyLoading) return;
    setPrivacyLoading(true);
    const endpoint = privacyOptedOut ? "/merchant/unobject" : "/merchant/object";
    try {
      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (res.ok) {
        setPrivacyOptedOut(!privacyOptedOut);
      }
    } catch {}
    setPrivacyLoading(false);
  }

  // Save handler — PATCH the row, refresh cached cost defaults, re-fetch P&L
  // so the cassettone updates without a page reload.
  async function handleCostDefaultsSave() {
    if (!shop) return;
    setCostSaving(true);
    setCostSavedMsg(null);

    // Convert form strings to numbers, treating empty as "leave unchanged".
    // We send every field (model_dump(exclude_unset=True) on backend) so
    // fields with empty strings come through as null to clear existing values.
    const parseOrNull = (s: string): number | null => {
      if (!s || !s.trim()) return null;
      const n = parseFloat(s.trim());
      return Number.isFinite(n) ? n : null;
    };

    const cogsPctNum = parseOrNull(costFormCogsPct);
    const payPctNum  = parseOrNull(costFormPayPct);

    try {
      const res = await apiClient.PATCH("/pro/costs/defaults", {
        params: {},
        headers: { ...getHeaders(apiHeaders), "Content-Type": "application/json" },
        body: {
          default_cogs_pct:
            cogsPctNum != null ? cogsPctNum / 100 : null, // 40 → 0.40
          default_shipping_per_order: parseOrNull(costFormShipping),
          payment_pct:
            payPctNum != null ? payPctNum / 100 : null,   // 2.9 → 0.029
          payment_flat: parseOrNull(costFormPayFlat),
          ad_spend_manual_monthly: parseOrNull(costFormAdSpend),
          currency: null,
        },
      });
      if (res.data != null) {
        setCostDefaults(res.data);
        setCostSavedMsg({ type: "ok", text: "Saved — Profit Intelligence updating..." });
        // Re-fetch P&L to reflect new precision and numbers.
        try {
          const pnlRes = await apiClient.GET("/pro/pnl", {
            params: { query: { window_days: 30 } },
            headers: getHeaders(apiHeaders),
          });
          if (pnlRes.data != null) setPnlData(pnlRes.data);
        } catch { /* pnl refetch is best-effort */ }
      } else {
        setCostSavedMsg({ type: "err", text: "Save failed — please retry." });
      }
    } catch {
      setCostSavedMsg({ type: "err", text: "Save failed — please retry." });
    } finally {
      setCostSaving(false);
      setTimeout(() => setCostSavedMsg(null), 4000);
    }
  }

  // Shopify Admin auto-import of product COGS. Hits the new backend endpoint
  // which pulls inventory_items.cost for every variant, aggregates by product,
  // and upserts into product_costs. On success we also re-fetch /pro/pnl so
  // the Profit Intelligence cassettone reflects the new precision immediately.
  async function handleShopifyCogsSync() {
    if (!shop) return;
    setCostSyncing(true);
    setCostSyncMsg(null);
    try {
      const res = await apiClient.POST("/pro/costs/sync-from-shopify", {
        headers: getHeaders(apiHeaders),
      });
      if (res.data == null) {
        setCostSyncMsg({ type: "err", text: "Sync failed — please retry." });
      } else if (res.data.status === "ok") {
        setCostSyncMsg({
          type: "ok",
          text: `${res.data.message}`,
        });
        // Refresh P&L so the precision badge and waterfall update.
        try {
          const pnlRes = await apiClient.GET("/pro/pnl", {
            params: { query: { window_days: 30 } },
            headers: getHeaders(apiHeaders),
          });
          if (pnlRes.data != null) setPnlData(pnlRes.data);
        } catch { /* best effort */ }
      } else {
        setCostSyncMsg({
          type: "err",
          text: res.data.message || "Sync returned no data.",
        });
      }
    } catch {
      setCostSyncMsg({ type: "err", text: "Sync failed — please retry." });
    } finally {
      setCostSyncing(false);
      setTimeout(() => setCostSyncMsg(null), 6000);
    }
  }

  // ---------------------------------------------------------------------------
  // Klaviyo integration state (Settings section)
  // ---------------------------------------------------------------------------
  const [klaviyoStatus, setKlaviyoStatus] = useState<{
    status: string; has_key: boolean; key_hint: string | null;
    last_verified_at: string | null; last_error: string | null;
    last_sync_at: string | null; last_sync_error: string | null;
  } | null>(null);
  const [klaviyoKeyInput, setKlaviyoKeyInput] = useState("");
  const [klaviyoConnecting, setKlaviyoConnecting] = useState(false);
  const [klaviyoShowReplace, setKlaviyoShowReplace] = useState(false);
  const [klaviyoMessage, setKlaviyoMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  // Fetch Klaviyo status for all tiers (403 from backend = no status, handled in UI)
  useEffect(() => {
    if (!shop) return;
    apiFetch(`${API_BASE}/merchant/integrations`)
      .then((r) => {
        if (r.status === 401 || r.status === 403) { dispatchSessionExpired(); return null; }
        return r.ok ? r.json() : null;
      })
      .then((d) => { if (d?.klaviyo) setKlaviyoStatus(d.klaviyo); })
      .catch(() => { /* integrations status is supplementary */ });
  }, [shop]);

  const klaviyoIsConnected = klaviyoStatus?.status === "connected";

  async function handleKlaviyoConnect() {
    if (!klaviyoKeyInput.trim()) return;
    setKlaviyoConnecting(true);
    setKlaviyoMessage(null);
    try {
      // Step 1: Save key
      const saveRes = await apiFetch(`${API_BASE}/merchant/integrations/klaviyo`, {
        method: "PUT",
        body: JSON.stringify({ klaviyo_private_key: klaviyoKeyInput.trim() }),
      });
      if (!saveRes.ok) {
        const err = await saveRes.json().catch(() => ({ detail: "Save failed" }));
        setKlaviyoMessage({ type: "err", text: err.detail || "Save failed" });
        return;
      }

      // Step 2: Auto-verify immediately
      const testRes = await apiFetch(`${API_BASE}/merchant/integrations/klaviyo/test`, { method: "POST" });
      const testData = await testRes.json();

      // Step 3: Refresh status
      const s = await apiFetch(`${API_BASE}/merchant/integrations`).then((r) => r.json());
      if (s?.klaviyo) setKlaviyoStatus(s.klaviyo);

      if (testData.status === "connected") {
        setKlaviyoKeyInput("");
        setKlaviyoShowReplace(false);
        setKlaviyoMessage({ type: "ok", text: "Klaviyo connected successfully" });
      } else {
        setKlaviyoMessage({ type: "err", text: testData.detail || "Key saved but verification failed" });
      }
    } catch { setKlaviyoMessage({ type: "err", text: "Network error" }); }
    finally { setKlaviyoConnecting(false); }
  }

  async function handleKlaviyoDisconnect() {
    setKlaviyoMessage(null);
    try {
      const res = await apiFetch(`${API_BASE}/merchant/integrations/klaviyo`, { method: "DELETE" });
      if (res.ok) {
        const data = await res.json();
        setKlaviyoStatus(data);
        setKlaviyoShowReplace(false);
        setKlaviyoMessage({ type: "ok", text: "Klaviyo disconnected" });
      }
    } catch { setKlaviyoMessage({ type: "err", text: "Disconnect failed" }); }
  }

  // ---------------------------------------------------------------------------
  // Trial countdown — derived from billing_confirmed_at + trial days
  // ---------------------------------------------------------------------------
  const trialInfo = useMemo<TrialInfo>(() => {
    if (!billingConfirmedAt || !isProUser) {
      return { daysRemaining: null, isPaidPro: isProUser };
    }
    const confirmedMs = new Date(billingConfirmedAt).getTime();
    if (isNaN(confirmedMs)) return { daysRemaining: null, isPaidPro: true };
    const trialEndMs = confirmedMs + proTrialDays * 86_400_000;
    const nowMs = Date.now();
    // If trial period has been exceeded by more than trial duration,
    // this is a paid Pro user (trial long over, still active = paid).
    if (nowMs > trialEndMs) {
      return { daysRemaining: null, isPaidPro: true };
    }
    const daysLeft = Math.ceil((trialEndMs - nowMs) / 86_400_000);
    return { daysRemaining: daysLeft, isPaidPro: false };
  }, [billingConfirmedAt, isProUser, proTrialDays]);

  // ---------------------------------------------------------------------------
  // Cold-start phase — derived from real state, used for contextual empty states
  // ---------------------------------------------------------------------------
  // Phase 0: setup incomplete (degraded/needs_repair)
  // Phase 1: setup done, no visitors yet
  // Phase 2: visitors arriving, no signals yet
  // Phase 3: signals exist — dashboard has real data, hide all cold-start UI
  const coldStartPhase = useMemo(() => {
    if (setupReadiness === "degraded" || setupReadiness === "needs_repair") return 0;
    const visitors = summary.total_visitors ?? 0;
    const events = summary.total_events ?? 0;
    if (visitors === 0 && events === 0) return 1;
    if (strongSignals.length === 0) return 2;
    return 3;
  }, [setupReadiness, summary.total_visitors, summary.total_events, strongSignals.length]);

  const isColdStart = coldStartPhase < 3;

  // ---------------------------------------------------------------------------
  // First-signal celebration — fires once when signals transition from 0 to >0
  // ---------------------------------------------------------------------------
  const [firstSignalToast, setFirstSignalToast] = useState(false);
  const prevSignalCountRef = useRef(0);
  const firstSignalKey = shop ? `hs_first_signal_${shop}` : "";

  useEffect(() => {
    const prev = prevSignalCountRef.current;
    prevSignalCountRef.current = strongSignals.length;
    // Only fire on the 0 → >0 transition for STRONG signals, once per session
    if (prev === 0 && strongSignals.length > 0 && firstSignalKey) {
      try {
        if (sessionStorage.getItem(firstSignalKey)) return; // already fired
        sessionStorage.setItem(firstSignalKey, "1");
      } catch { return; }
      setFirstSignalToast(true);
      // Auto-dismiss after 12s
      setTimeout(() => setFirstSignalToast(false), 12000);
    }
  }, [strongSignals.length, firstSignalKey]);

  // ---------------------------------------------------------------------------
  // Trial expiry interstitial — fires once per session on last day of trial
  // ---------------------------------------------------------------------------
  const [trialExpiryModal, setTrialExpiryModal] = useState(false);
  const [trialBillingLoading, setTrialBillingLoading] = useState(false);
  const [trialBillingError, setTrialBillingError] = useState("");

  useEffect(() => {
    // Only trigger for active Pro trial users on their last day
    if (!isProUser || trialInfo.isPaidPro || trialInfo.daysRemaining === null) return;
    if (trialInfo.daysRemaining > 1) return;
    if (!shop) return;
    const key = `hs_trial_expiry_${shop}`;
    try {
      if (sessionStorage.getItem(key)) return;
      sessionStorage.setItem(key, "1");
    } catch { return; }
    // Small delay so the dashboard loads first — feels natural, not ambush
    const timer = setTimeout(() => setTrialExpiryModal(true), 1500);
    return () => clearTimeout(timer);
  }, [isProUser, trialInfo.isPaidPro, trialInfo.daysRemaining, shop]);

  async function handleTrialConvert() {
    if (!shop || !API_BASE) return;
    setTrialBillingLoading(true);
    setTrialBillingError("");
    try {
      const res = await fetch(
        `${API_BASE}/billing/subscribe?shop=${encodeURIComponent(shop)}`,
        { method: "POST", headers: apiHeaders(), credentials: "include" }
      );
      const json = await res.json();
      if (res.ok && json.confirmation_url) {
        window.location.href = json.confirmation_url;
        return;
      }
      if (json?.detail?.includes("already on the Pro plan")) {
        // Already subscribed — just close
        setTrialExpiryModal(false);
        return;
      }
      setTrialBillingError(json?.detail || "Could not start billing. Please try again.");
    } catch {
      setTrialBillingError("Network error. Please check your connection.");
    }
    setTrialBillingLoading(false);
  }

  const maxTrend = useMemo(
    () => Math.max(...trend.map((p) => p.visitors || 0), 1),
    [trend]
  );

  // ---------------------------------------------------------------------------
  // Synthetic brief — shown when the real brief hasn't been generated yet.
  // Built from live signals + summary so the top of the page is never blank.
  // ---------------------------------------------------------------------------
  const effectiveBrief = useMemo<DailyBrief | null>(() => {
    if (brief) return brief;
    if (briefLoading) return null;
    const topSig = strongSignals[0] ?? null;
    const totalViews = productMetrics.reduce((s, m) => s + (m.views_24h ?? 0), 0);
    if (!topSig && totalViews === 0 && earlySignals.length === 0) return null;
    const today = new Date().toISOString().split("T")[0];

    // Spark voice: translate signal type into what it means for the merchant
    const productName = topSig?.human_label || (topSig?.product_url ? shortUrl(topSig.product_url) : null);

    function sparkHeadline(signal: string | undefined | null, product: string | null): string {
      const p = product || "a product";
      switch (signal) {
        case "HIGH_TRAFFIC_NO_CART":
          return `${p} is getting traffic. No carts.`;
        case "HIGH_ENGAGEMENT_NO_ACTION":
          return `Visitors spend time on ${p}. No actions taken.`;
        case "DEAD_TRAFFIC":
          return `${p} loses visitors in under 5 seconds.`;
        case "HIGH_RETURN_LOW_CONVERSION":
          return `${p} gets repeat visits. No purchases.`;
        case "LOW_CONVERSION_ATTENTION":
          return `${p} converts below expected baseline.`;
        case "TRAFFIC_SPIKE":
          return `${p} traffic increased sharply.`;
        case "SCROLL_HIGH_NO_CLICK":
          return `Visitors scroll deep on ${p}. No interaction follows.`;
        case "RETURN_VISITOR_INTEREST":
          return `${p} attracts repeat visits.`;
        default:
          return product ? `${p} needs attention.` : "Signals detected across your store.";
      }
    }

    const headline = topSig
      ? sparkHeadline(topSig.signal_type, productName)
      : earlySignals.length > 0
      ? "Early activity detected. Signals sharpen as traffic builds."
      : `${formatNumber(totalViews)} views tracked. Scanning for conversion issues.`;

    return {
      brief_date: today,
      headline,
      signals_count: strongSignals.length || undefined,
      top_signal_type: topSig?.signal_type ?? null,
      top_product_url: topSig?.product_url ?? null,
      top_product_label: topSig?.human_label ?? topSig?.product_url ?? null,
      summary_generated: false,
    };
  }, [brief, briefLoading, strongSignals, earlySignals, productMetrics]);

  // Normalise product_url for reliable Map lookups
  // (trim whitespace, lowercase, strip trailing slash)
  function normalizeUrl(url: string): string {
    return (url || "").trim().toLowerCase().replace(/\/$/, "");
  }

  // Task map key — matches backend dedup triple (shop is implicit from auth)
  function taskKey(productUrl: string, actionType: string): string {
    return normalizeUrl(productUrl) + "::" + actionType;
  }

  // Defensively parse result_detail JSON from a completed task.
  // Returns only the fields present; never throws.
  // Falls back to { summary: rawText } if the string is not valid JSON.
  function parseResultDetail(raw: string | null | undefined): {
    outcome?: string;
    agent_id?: string;
    summary?: string;
  } {
    if (!raw) return {};
    try {
      const parsed = JSON.parse(raw);
      if (parsed !== null && typeof parsed === "object") return parsed as { outcome?: string; agent_id?: string; summary?: string };
      return {};
    } catch {
      return { summary: String(raw) };
    }
  }

  // buildTaskMap previously indexed tasks into a Map; removed along
  // with the dead taskMap state.

  // ---------------------------------------------------------------------------
  // Attention score — weighted composite for sort priority
  // ---------------------------------------------------------------------------
  function computeAttentionScore(m: ProductMetricsRow): number {
    const views = Math.min((m.views_24h ?? 0) / 100, 1);           // 0–1, capped at 100 views
    const eng   = m.engagement_score ?? 0;                          // already 0–1
    const aband = m.cart_abandonment_rate ?? 0;                     // already 0–1
    return views * 0.35 + eng * 0.40 + aband * 0.25;
  }

  // Priority band from attention score
  function computePriority(score: number): "HIGH" | "MED" | "LOW" {
    if (score >= 0.55) return "HIGH";
    if (score >= 0.25) return "MED";
    return "LOW";
  }

  // Insight badge — first matching rule wins
  function computeInsight(m: ProductMetricsRow): string | null {
    if ((m.engagement_score ?? 0) > 0.8 && (m.cart_conversions_24h ?? 0) === 0)
      return "🔥 High intent, zero conversions";
    if ((m.views_24h ?? 0) > 20 && (m.cart_abandonment_rate ?? 0) > 0.8)
      return "⚠️ Strong traffic, weak conversion";
    if ((m.return_visitor_count_7d ?? 0) > 0)
      return "🔁 Returning visitors detected";
    return null;
  }

  // Action suggestion keyed to insight rule
  function computeActionSuggestion(insight: string | null): string | null {
    if (!insight) return null;
    if (insight.startsWith("🔥")) return "Check product page UX";
    if (insight.startsWith("⚠️")) return "Improve CTA or pricing";
    if (insight.startsWith("🔁")) return "Add urgency or discount";
    return null;
  }

  // Estimated daily revenue loss.
  // Only applies when there are zero cart conversions — uses real AOV from
  // shop_orders when available, falls back to $50.
  const resolvedAov = data?.shop_aov ?? 50;
  const resolvedCcy = data?.shop_currency ?? "USD";
  const resolvedAovIsReal = data?.aov_is_real ?? false;
  const BASELINE_CVR = 0.02;

  function computeEstimatedLoss(m: ProductMetricsRow): number | null {
    if ((m.cart_conversions_24h ?? 0) !== 0) return null;
    const views = m.views_24h ?? 0;
    if (views === 0) return null;
    return Math.round(views * BASELINE_CVR * resolvedAov);
  }

  // Returns true when a trend array carries no real signal
  function isTrendEmpty(arr: number[]): boolean {
    return !Array.isArray(arr) || arr.length === 0 || arr.every((v) => v === 0);
  }

  // Synthetic fallback — 7 plausible points anchored on views_24h.
  // Shape: gentle curve peaking near today (index 6) so it reads as
  // "traffic building toward now" without being misleading.
  function syntheticTrend(views24h: number | undefined): number[] {
    const base = Math.max(views24h ?? 0, 1);
    // Multipliers sum to ~7 with a natural ramp; kept low-contrast
    const weights = [0.45, 0.55, 0.65, 0.80, 0.90, 0.95, 1.0];
    return weights.map((w) => Math.round(base * w));
  }

  // Merge productMetrics ← productTrend by product_url (normalised)
  const mergedProducts = useMemo<MergedProductRow[]>(() => {
    const trendMap = new Map<string, ProductTrendRow>(
      productTrend.map((t) => [normalizeUrl(t.product_url), t])
    );
    return productMetrics.map((m) => {
      const key = normalizeUrl(m.product_url);
      const matched = trendMap.get(key);
      const raw =
        matched && Array.isArray(matched.last_7_days_views)
          ? matched.last_7_days_views
          : [];
      const trendEmpty = isTrendEmpty(raw);
      const views = trendEmpty ? syntheticTrend(m.views_24h) : raw;
      const attention_score = computeAttentionScore(m);
      const insight = computeInsight(m);
      return {
        ...m,
        last_7_days_views: views,
        trend_is_synthetic: trendEmpty,
        attention_score,
        priority: computePriority(attention_score),
        insight,
        action_suggestion: computeActionSuggestion(insight),
        estimated_loss: computeEstimatedLoss(m),
      };
    }).sort((a, b) => b.attention_score - a.attention_score);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [productMetrics, productTrend]);

  // ---------------------------------------------------------------------------
  // Spark Action Engine — ranked actions from product metrics + signals
  // ---------------------------------------------------------------------------
  const sparkActions = useMemo<SparkAction[]>(() => {
    if (mergedProducts.length === 0) return [];
    return computeActions(
      mergedProducts,
      signals,
      { aov: resolvedAov, currency: resolvedCcy, aovIsReal: resolvedAovIsReal, baselineCvr: BASELINE_CVR },
    );
  }, [mergedProducts, signals, resolvedAov, resolvedCcy, resolvedAovIsReal]);

  // ---------------------------------------------------------------------------
  // Spark reputation — cumulative accuracy from proof events
  // ---------------------------------------------------------------------------
  const sparkReputation = useMemo(() => {
    if (sparkActions.length === 0) return null;
    return updateReputation(sparkActions);
  }, [sparkActions]);

  // ---------------------------------------------------------------------------
  // Spark notifications — high-value, throttled
  // ---------------------------------------------------------------------------
  const [activeToasts, setActiveToasts] = useState<SparkNotification[]>([]);
  useEffect(() => {
    if (sparkActions.length === 0) return;
    const settings = loadSettings();
    const notifs = generateNotifications(sparkActions, settings);
    if (notifs.length > 0) setActiveToasts(notifs);
  }, [sparkActions]);

  // ---------------------------------------------------------------------------
  // Spark companion context — drives the dynamic sidebar message
  // ---------------------------------------------------------------------------
  const sparkContext = useMemo(() => {
    const highPriorityCount = mergedProducts.filter((r) => r.priority === "HIGH").length;
    const topSig = strongSignals.length > 0 ? strongSignals[0] : null;
    // Use top action for the most impactful Spark message
    const topAction = sparkActions.length > 0 ? sparkActions[0] : null;
    // Find first improving action for proof celebration
    const improvingAction = sparkActions.find(a => a.proofStatus === "improving");
    return {
      signalCount: strongSignals.length,
      highPriorityCount,
      topSignalLabel: topAction ? topAction.title : (topSig?.human_label || topSig?.explanation || undefined),
      topSignalProduct: topAction?.targetProduct ? shortUrl(topAction.targetProduct) : (topSig?.product_url ? shortUrl(topSig.product_url) : undefined),
      topActionImpact: topAction?.impact,
      topActionIsPattern: topAction?.isPattern ?? false,
      hasImproving: !!improvingAction,
      improvingDetail: improvingAction?.proofDetail,
      revenue7d: heroRevenue?.revenue ?? 0,
      orders7d: heroRevenue?.orders ?? 0,
      liveVisitorCount: liveVisitors.length,
      hotVisitorCount: summary.hot_visitors ?? 0,
      coldStartPhase,
      isProUser,
    };
  }, [signals, strongSignals, earlySignals, mergedProducts, sparkActions, heroRevenue, liveVisitors.length, summary.hot_visitors, coldStartPhase, isProUser]);

  // ---------------------------------------------------------------------------
  // UI handlers
  // ---------------------------------------------------------------------------
  function handleTierToggle() {
    if (tier === "lite") setUpgradeModalOpen(true);
  }

  function handleNavigate(id: string) {
    setActiveSection(id);
    isScrollingRef.current = true;
    document
      .getElementById(`section-${id}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
    // Re-enable observer after scroll animation completes
    setTimeout(() => { isScrollingRef.current = false; }, 800);
  }

  // Re-observe sections when content loads/tier changes (conditional sections appear)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (!loading) requestAnimationFrame(reobserve); }, [loading, tier, reobserve]);
  useEffect(() => { return () => observerRef.current?.disconnect(); }, []);

  async function executeCandidate(candidate: ActionCandidate) {
    try {
      const res = await fetch(
        `${API_BASE}/actions/execute?shop=${encodeURIComponent(shop)}`,
        {
          method: "POST",
          headers: apiHeaders(),
          credentials: "include",
          body: JSON.stringify({ candidate, triggered_by: "manual" }),
        }
      );
      if (!res.ok) return;
      const j = await res.json();
      // Task update received but no longer stored; hasExecutingRef gates
      // the poll interval and candidates state renders the UI.
      const task: ActionTask | undefined = j.task;
      if (!task) return;
    } catch { /* silent */ }
  }

  async function dismissTask(taskId: number, key: string) {
    try {
      const res = await fetch(
        `${API_BASE}/actions/tasks/${taskId}?shop=${encodeURIComponent(shop)}`,
        {
          method: "PATCH",
          headers: apiHeaders(),
          credentials: "include",
          body: JSON.stringify({ status: "dismissed" }),
        }
      );
      if (!res.ok) return;
    } catch { /* silent */ }
  }

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
    <div className="flex h-screen overflow-hidden bg-[#07070f] text-white">
      <ProductTour isProUser={isProUser} />
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((c) => !c)}
        activeSection={activeSection}
        onNavigate={handleNavigate}
        tier={tier}
      />

      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar shop={shop} tier={tier} onTierToggle={handleTierToggle} trial={trialInfo} notifications={activeToasts} reputation={sparkReputation} />

        <main ref={mainRef} className="flex-1 overflow-y-auto hs-scroll-smooth">
          {!sessionResolved ? (
            <MascotLoader caption="Connecting to your store..." state="loading" />
          ) : !shop ? (
            <MascotLoader caption="Looking for your store..." state="loading">
              <div className="rounded-2xl border border-amber-400/20 bg-amber-500/10 p-5 text-center max-w-md mx-auto">
                <div className="text-[14px] font-semibold text-amber-200">No store connected</div>
                <div className="mt-2 text-[13px] leading-relaxed text-amber-200/70">
                  Your session expired or your browser is blocking cookies. If HedgeSpark is already
                  installed on your store, click below to reconnect.
                </div>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    const input = (e.currentTarget.elements.namedItem("shop") as HTMLInputElement)?.value?.trim();
                    if (!input) return;
                    // Normalize: accept "foo", "foo.myshopify.com", "https://foo.myshopify.com"
                    let domain = input.replace(/^https?:\/\//, "").replace(/\/$/, "").toLowerCase();
                    if (!domain.endsWith(".myshopify.com")) domain = `${domain}.myshopify.com`;
                    try {
                      localStorage.setItem("hs_last_shop", domain);
                    } catch {}
                    window.location.href = `${API_BASE}/auth/session?shop=${encodeURIComponent(domain)}`;
                  }}
                  className="mt-4 flex flex-col gap-2"
                >
                  <input
                    type="text"
                    name="shop"
                    defaultValue={
                      typeof window !== "undefined"
                        ? localStorage.getItem("hs_last_shop") || ""
                        : ""
                    }
                    placeholder="your-store.myshopify.com"
                    className="w-full rounded-lg border border-amber-400/30 bg-black/40 px-3 py-2 text-center text-[13px] text-amber-100 placeholder:text-amber-200/40 focus:border-amber-400 focus:outline-none"
                  />
                  <button
                    type="submit"
                    className="rounded-lg bg-gradient-to-r from-[#e8a04e] to-[#f59e0b] px-4 py-2 text-[13px] font-bold text-[#0f172a] transition-transform hover:-translate-y-0.5"
                  >
                    Reconnect my store
                  </button>
                </form>
                <div className="mt-3 text-[11px] text-amber-200/50">
                  Not installed yet? Install HedgeSpark from the Shopify App Store first.
                </div>
              </div>
            </MascotLoader>
          ) : loading ? (
            <MascotLoader caption="Loading your store data..." state="loading" />
          ) : error ? (
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
            <div className="space-y-6 px-6 py-6 pb-[70vh]">

              {/* DashboardHero removed — logo + shop + active status already in sidebar + topbar */}

              {/* Session expired banner — shown when any fetch returns 401/403 */}
              {sessionExpired && (
                <div className="rounded-2xl border border-amber-400/20 bg-amber-500/[0.07] px-4 py-3">
                  <div className="text-sm font-medium text-amber-200">
                    <span>Your session has expired or your browser is blocking cookies.</span>
                    <p className="mt-1 text-[12px] font-normal text-amber-200/60">
                      If refreshing doesn&apos;t help, try disabling strict tracking protection for this site.
                    </p>
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <button
                      onClick={() => window.location.reload()}
                      className="rounded-lg bg-amber-500/20 px-3 py-1 text-xs font-semibold text-amber-100 hover:bg-amber-500/30 transition"
                    >
                      Refresh to reconnect
                    </button>
                  </div>
                </div>
              )}

              {/* Analytics error banner — shown when analytics polling fails */}
              {analyticsError && !sessionExpired && (
                <div className="rounded-2xl border border-rose-400/10 bg-rose-500/[0.05] px-4 py-2 text-xs text-rose-300/80">
                  {analyticsError}
                </div>
              )}

              {/* Data freshness — subtle timestamp below header */}
              {lastUpdated && !sessionExpired && (
                <div className="text-[10px] tracking-wide text-slate-600 -mt-5">
                  Updated {lastUpdated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </div>
              )}

              {/* Billing callback toast — shown once after Shopify redirect */}
              {billingToast?.visible && (
                <div
                  className={`flex items-center gap-3 rounded-2xl border px-4 py-3 transition-all ${
                    billingToast.type === "activated"
                      ? "border-emerald-400/25 bg-emerald-500/[0.07]"
                      : billingToast.type === "declined"
                      ? "border-rose-400/25 bg-rose-500/[0.07]"
                      : "border-amber-400/25 bg-amber-500/[0.07]"
                  }`}
                >
                  <span
                    className={`h-2 w-2 flex-shrink-0 rounded-full ${
                      billingToast.type === "activated"
                        ? "bg-emerald-400"
                        : billingToast.type === "declined"
                        ? "bg-rose-400"
                        : "bg-amber-400"
                    }`}
                  />
                  <span
                    className={`text-[13px] font-medium ${
                      billingToast.type === "activated"
                        ? "text-emerald-200"
                        : billingToast.type === "declined"
                        ? "text-rose-200"
                        : "text-amber-200"
                    }`}
                  >
                    {billingToast.type === "activated" &&
                      "Pro activated — welcome to HedgeSpark Pro. Your dashboard is upgrading now."}
                    {billingToast.type === "declined" &&
                      "Billing was declined. You can try again anytime from the setup panel below."}
                    {billingToast.type === "pending" &&
                      "Billing is pending confirmation from Shopify. This usually resolves within a few minutes."}
                    {billingToast.type === "error" &&
                      "Something went wrong with billing. Please try again or contact support."}
                  </span>
                  <button
                    onClick={() => setBillingToast((prev) => prev ? { ...prev, visible: false } : null)}
                    className="ml-auto flex-shrink-0 rounded p-1 text-slate-600 transition hover:text-slate-400"
                    aria-label="Dismiss"
                  >
                    <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              )}

              {/* First-signal celebration toast */}
              {firstSignalToast && (
                <div className="hs-fade-up flex items-center gap-3 rounded-2xl border border-emerald-400/25 bg-emerald-500/[0.07] px-4 py-3">
                  <span className="flex-shrink-0 text-[15px]">&#x1F994;</span>
                  <div className="min-w-0 flex-1">
                    <span className="text-[14px] font-semibold text-emerald-200">
                      First finding ready
                    </span>
                    <span className="ml-1.5 text-[13px] text-emerald-300/60">
                      — <span className="hs-brand-gradient font-semibold">Spark</span> found something.
                    </span>
                  </div>
                  <button
                    onClick={() => { setFirstSignalToast(false); setActiveSection("brief"); }}
                    className="flex-shrink-0 rounded-lg bg-emerald-500/15 px-3 py-1.5 text-[12px] font-semibold text-emerald-300 ring-1 ring-emerald-400/20 transition hover:bg-emerald-500/25"
                  >
                    View finding
                  </button>
                  <button
                    onClick={() => setFirstSignalToast(false)}
                    className="flex-shrink-0 rounded p-1 text-slate-600 transition hover:text-slate-400"
                    aria-label="Dismiss"
                  >
                    <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              )}

              {/* System health warning — shown when backend is degraded */}
              {systemHealthIssues.length > 0 && (
                <div className="mb-3 rounded-xl border border-amber-400/20 bg-amber-500/[0.06] px-4 py-3">
                  <div className="flex items-start gap-2">
                    <span className="mt-px text-amber-400">⚠</span>
                    <div>
                      <div className="text-[12px] font-medium text-amber-300">System health: degraded</div>
                      <div className="mt-0.5 text-[11px] text-amber-200/60">
                        {systemHealthIssues.slice(0, 3).join(" · ")}
                        {" — "}some data may be stale.
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Unified onboarding hub — setup status, progress, pixel, Pro upsell */}
              <OnboardingHub
                shop={shop}
                apiBase={API_BASE}
                apiHeaders={apiHeaders}
                onReadinessChange={handleReadinessChange}
                billingJustActivated={billingJustActivated}
                freshInstall={freshInstall}
                trialDays={proTrialDays}
                price={proPrice}
                totalVisitors={data ? (data.summary?.total_visitors ?? 0) : null}
                signalCount={strongSignals.length > 0 ? strongSignals.length : (data ? 0 : null)}
              />

              {/* ═══ SYSTEM STATUS BAR — intelligence progress + aliveness ═══ */}
              <SystemStatusBar apiBase={API_BASE} shop={shop} />

              {/* ═══ INTELLIGENCE HERO — decision-first: what matters most right now ═══ */}
              <IntelligenceHero
                connected={!!shop}
                isProUser={isProUser}
                onUpgrade={() => setUpgradeModalOpen(true)}
              />

              {/* ═══ REVENUE HERO ═══ */}
              <RevenueHero
                revenue={heroRevenue?.revenue ?? 0}
                orders={heroRevenue?.orders ?? 0}
                currency={heroRevenue?.currency ?? "USD"}
                signalCount={strongSignals.length}
                topSignalMessage={
                  strongSignals.length > 0
                    ? (strongSignals[0]?.human_label || strongSignals[0]?.explanation || undefined)
                    : earlySignals.length > 0
                    ? "We're starting to see activity in your store"
                    : undefined
                }
                isProUser={isProUser}
                onViewSignals={() => handleNavigate("signals")}
                onUpgrade={() => setUpgradeModalOpen(true)}
                coldStartPhase={coldStartPhase}
                displayCurrency={displayCurrency}
                apiBase={API_BASE}
                shop={shop}
              />

              {/* ═══ INSTANT INTELLIGENCE — 60s aha moment (α3) ═══ */}
              {isProUser && <InstantIntelligenceCard apiBase={API_BASE} />}

              {/* ═══ ROI HERO BANNER — the retention weapon (α2) ═══ */}
              <div data-tour="roi-hero">
                <ROIHeroBanner apiBase={API_BASE} isProUser={isProUser} />
              </div>

              {/* ═══ DAILY NARRATIVE — storytelling block (α7) ═══ */}
              <DailyNarrativeBlock apiBase={API_BASE} isProUser={isProUser} />

              {/* ═══ REVENUE AT RISK HERO — the new #1 headline ═══ */}
              <RevenueAtRiskHero
                apiBase={API_BASE}
                shop={shop}
                isProUser={isProUser}
                onUpgrade={() => setUpgradeModalOpen(true)}
              />

              {/* ═══ TRUST CONTROL CENTER — delegated autonomy (α1) ═══ */}
              <div data-tour="trust-center">
                <TrustControlCenter apiBase={API_BASE} isProUser={isProUser} />
              </div>

              {/* ═══ UNIT ECONOMICS + PROFIT HEADROOM + FORECAST (β1+β3+α6) ═══ */}
              {isProUser && (
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
                    gap: "16px",
                    marginBottom: "8px",
                  }}
                >
                  <UnitEconomicsCard apiBase={API_BASE} isProUser={isProUser} />
                  <div data-tour="margin-health">
                    <MarginHealthCard apiBase={API_BASE} isProUser={isProUser} />
                  </div>
                  <RevenueForecastCard apiBase={API_BASE} isProUser={isProUser} />
                </div>
              )}

              {/* ═══ CUSTOMER CHURN TABLE (δ4) ═══ */}
              <CustomerChurnCard apiBase={API_BASE} isProUser={isProUser} />

              {/* ═══ NUDGE DNA — what words actually sell (δ5) ═══ */}
              <NudgeDnaCard apiBase={API_BASE} isProUser={isProUser} />

              {/* ═══ WHAT BRINGS THE SALE — multi-touch attribution (β2) ═══ */}
              <div data-tour="mta">
                <MtaCompareCard apiBase={API_BASE} isProUser={isProUser} />
              </div>

              {/* ═══ AUTOMATION RULES — low-code (ζ2) ═══ */}
              <RuleBuilderCard apiBase={API_BASE} isProUser={isProUser} />

              {/* ═══ REVENUE GENOME — the DNA of your revenue ═══ */}
              {isProUser && (
                <RevenueGenomeCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
              )}

              {/* ═══ PHASE Ω⁵ — NIGHT SHIFT AGENT (morning reveal) ═══ */}
              {isProUser && (
                <NightShiftCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
              )}

              {/* ═══ PHASE Ω — THE WHY ENGINE + ANOMALY RADAR (causal layer) ═══ */}
              {isProUser && (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <CausalWhyCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <AnomalyFusionCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                </div>
              )}

              {/* ═══ PHASE Ω — ASK HEDGE SPARK (knowledge graph NL query) ═══ */}
              {isProUser && (
                <AskHedgeSparkCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
              )}

              {/* ═══ KILLER FEATURE GRID — drill-downs from the RARS hero ═══ */}
              {isProUser && (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <VerticalBenchmarksCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <PeerBenchmarksCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <ProductsInDecline apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <MonthlyTargetsCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <MonthlyROICard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <IntegrationsCard apiBase={API_BASE} isProUser={isProUser} />
                </div>
              )}

              {/* ═══ DEEP INTELLIGENCE GRID — R-series features ═══ */}
              {isProUser && (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <RevenueAutopsyCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <AbandonedIntentCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <PriceSensitivityCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <CausalLiftCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                </div>
              )}

              {/* ═══ SECONDARY WIDGETS — smaller but useful ═══ */}
              {isProUser && (
                <>
                  <TimelineNotes apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <CompareProductsCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                </>
              )}

              {/* ═══ TOP SIGNAL CARD — only strong signals (not early) ═══ */}
              {strongSignals.length > 0 && strongSignals[0] && (
                <TopSignalCard
                  signal={strongSignals[0]}
                  isProUser={isProUser}
                  onUpgrade={() => setUpgradeModalOpen(true)}
                  onViewSignals={() => handleNavigate("signals")}
                  onActionDone={() => setRecentActions(loadRecentActions())}
                />
              )}

              {/* ═══ PROOF HERO — shows only when improvements exist ═══ */}
              {isProUser && (
                <ProofHeroCard apiBase={API_BASE} shop={shop} />
              )}

              {/* ═══ RECENT ACTIONS — localStorage memory ═══ */}
              <RecentActions actions={recentActions} />

              {/* 1 — Daily Brief */}
              <section id="section-brief">
                <BriefHero
                  brief={effectiveBrief}
                  loading={briefLoading}
                  tier={tier}
                  onUpgradeClick={() => setUpgradeModalOpen(true)}
                  emptyHint={
                    coldStartPhase === 0
                      ? "Complete setup to start tracking."
                      : coldStartPhase === 1
                      ? "Tracker live. First findings within minutes."
                      : coldStartPhase === 2
                      ? "Visitors arriving. Analyzing behavior to find your first revenue opportunity."
                      : undefined
                  }
                  sparkInsight={(() => {
                    const top = strongSignals[0];
                    if (!top) return earlySignals.length > 0 ? "Early visitor activity detected. Patterns forming." : undefined;
                    // Build a specific data line from the signal
                    const parts: string[] = [];
                    if (top.human_label) parts.push(top.human_label);
                    else if (top.explanation) parts.push(top.explanation);
                    return parts.join("") || undefined;
                  })()}
                  sparkDetail={(() => {
                    if (strongSignals.length <= 1) return undefined;
                    return `${strongSignals.length} findings across your store.`;
                  })()}
                />
              </section>

              {/* ── Level 2 separator ── */}
              <div className="h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />

              {/* 2 — Store pulse */}
              <section id="section-overview">
                <SectionHeading
                  eyebrow="Store Pulse"
                  title="How your store is performing"
                />

                {/* Phase 0-1: hide zero-wall KPI cards, show waiting message instead */}
                {coldStartPhase <= 1 ? (
                  <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-5 py-6">
                    <p className="text-[13px] text-slate-500">
                      {coldStartPhase === 0
                        ? "Complete setup to see your metrics."
                        : "Tracker active — data appears with your next visitor."}
                    </p>
                  </div>
                ) : (
                <>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <KpiCard label="Visitors (24h)" value={formatNumber(summary.total_visitors)} hint="Last 24 hours" numeric={summary.total_visitors} onClick={() => setActiveKpi("visitors")} />
                  <KpiCard label="Events (24h)" value={formatNumber(summary.total_events)} hint="Browsing events" numeric={summary.total_events} onClick={() => setActiveKpi("events")} />
                  <KpiCard label="Hot Visitors" value={formatNumber(summary.hot_visitors)} hint="Strong purchase intent" numeric={summary.hot_visitors} onClick={() => setActiveKpi("hot")} />
                  <KpiCard label="Wishlist Adds" value={formatNumber(summary.wishlist_adds)} hint="Products saved" numeric={summary.wishlist_adds} onClick={() => setActiveKpi("wishlist")} />
                  <KpiCard label="Avg Intent" value={formatScore(summary.avg_intent_score)} hint="Purchase intent score" numeric={summary.avg_intent_score} onClick={() => setActiveKpi("intent")} />
                  <KpiCard label="Intent Split" value={`${formatNumber(summary.hot_visitors)} / ${formatNumber(summary.warm_visitors)} / ${formatNumber(summary.cold_visitors)}`} hint="Hot / Warm / Cold" onClick={() => setActiveKpi("distribution")} />
                  <KpiCard label="All-Time Visitors" value={formatNumber(summary.total_visitors_all)} hint="Total ever tracked" numeric={summary.total_visitors_all} onClick={() => setActiveKpi("visitors")} />
                  <KpiCard label="Ready Products" value={formatNumber(summary.conversion_ready_products)} hint="With action potential" numeric={summary.conversion_ready_products} onClick={() => setActiveKpi("products")} />
                </div>
                </>)}
              </section>

              {/* Real orders / revenue section */}
              <section id="section-revenue">
                <OrdersSummary apiBase={API_BASE} shop={shop} displayCurrency={displayCurrency} />
                <ProductConversions apiBase={API_BASE} shop={shop} />
              </section>

              {/* Revenue at risk banner — below KPI grid, above signals */}
              {isProUser
                ? (revenueWindows && (revenueWindows.total_revenue_at_risk ?? 0) > 0) && (
                    <RevenueWindowPro data={revenueWindows} displayCurrency={displayCurrency} />
                  )
                : (revenueWindowTease && (revenueWindowTease.estimated_revenue_at_risk ?? 0) > 0) && (
                    <RevenueWindowLite
                      data={revenueWindowTease}
                      onUpgradeClick={() => setUpgradeModalOpen(true)}
                      displayCurrency={displayCurrency}
                    />
                  )
              }

              {/* Calibration quality indicator */}
              {data?.calibration && (
                <div className="mb-1 flex items-center gap-2 text-[11px]">
                  <span className={`inline-flex h-1.5 w-1.5 rounded-full ${data.calibration.is_empirical ? "bg-emerald-400" : "bg-amber-400"}`} />
                  <span className="text-slate-500">
                    Conversion estimates: <span className={data.calibration.is_empirical ? "text-emerald-300/80" : "text-amber-300/80"}>{data.calibration.label}</span>
                  </span>
                </div>
              )}

              {/* 2.5 — Early signals (low confidence, first activity) */}
              {earlySignals.length > 0 && strongSignals.length === 0 && (
                <div className="rounded-2xl border border-white/[0.05] bg-white/[0.015] p-5">
                  <div className="mb-3">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/50">Early Findings</span>
                      <span className="rounded-full bg-white/[0.04] px-2 py-0.5 text-[9px] font-medium uppercase tracking-[0.08em] text-slate-500 ring-1 ring-white/[0.06]">Live</span>
                    </div>
                    <h3 className="text-[15px] font-semibold text-slate-200">Early findings</h3>
                    <p className="mt-0.5 text-[13px] text-slate-500">Sharpen as traffic grows.</p>
                  </div>
                  <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
                    {earlySignals.map((s, i) => (
                      <SignalCard key={`early-${s.signal_type}-${s.product_url}-${i}`} signal={s} tier={tier} onUpgradeClick={() => setUpgradeModalOpen(true)} />
                    ))}
                  </div>
                </div>
              )}

              {/* Early signals as companion when strong signals also exist */}
              {earlySignals.length > 0 && strongSignals.length > 0 && (
                <div className="rounded-xl border border-white/[0.04] bg-white/[0.01] p-4">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-600">Early findings</span>
                    <span className="rounded-full bg-white/[0.03] px-1.5 py-0.5 text-[9px] text-slate-600 ring-1 ring-white/[0.05]">{earlySignals.length}</span>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {earlySignals.slice(0, 3).map((s, i) => (
                      <SignalCard key={`early-${s.signal_type}-${s.product_url}-${i}`} signal={s} tier={tier} onUpgradeClick={() => setUpgradeModalOpen(true)} />
                    ))}
                  </div>
                </div>
              )}

              {/* 3 — Signals requiring attention + Highest-intent products */}
              {/* ═══ FINDINGS — 3 cards per row ═══ */}
              <section id="section-signals">
                <SectionHeading eyebrow="Findings" title={strongSignals.length > 0 ? "What we found" : "Needs attention"} />

                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {alerts.length === 0 ? (
                    <p className="lg:col-span-3 rounded-xl border border-white/[0.05] bg-white/[0.02] px-5 py-4 text-[15px] text-slate-500">
                      {isColdStart && earlySignals.length === 0
                        ? "Waiting for first visitors..."
                        : isColdStart
                        ? "Analyzing behavior — findings shortly."
                        : "All clear — store looks healthy."}
                    </p>
                  ) : (
                    <>
                      {alerts.slice(0, 2).map((alert, i) => (
                        <div
                          key={`${alert.type || "alert"}-${i}`}
                          className="flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5"
                        >
                          <div className="mb-3 flex items-center gap-2.5">
                            <span className={`rounded-lg px-2.5 py-1 text-[12px] font-bold uppercase tracking-wide ${impactClass(alert.priority)}`}>
                              {alert.priority || "Info"}
                            </span>
                            <span className="text-[13px] font-medium text-slate-400">{prettyText(alert.type)}</span>
                          </div>
                          <p className="flex-1 text-[15px] leading-[1.6] text-slate-300">{alert.message || "—"}</p>
                          {alert.action && (
                            <div className="mt-3 rounded-xl border border-emerald-400/15 bg-emerald-500/[0.05] px-4 py-3">
                              <div className="mb-1 flex items-center gap-2">
                                <svg className="h-3.5 w-3.5 text-emerald-400/70" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
                                </svg>
                                <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-emerald-300/70">Fix</span>
                                <span className="rounded border border-[#d4893a]/25 bg-[#d4893a]/10 px-1.5 py-[2px] text-[10px] font-bold text-[#d4893a]/70">PRO</span>
                              </div>
                              <p className="text-[14px] leading-[1.5] text-slate-200">{alert.action}</p>
                            </div>
                          )}
                        </div>
                      ))}

                      {/* Revenue at Risk — summary card */}
                      <div className="flex flex-col rounded-2xl border border-[#d4893a]/15 bg-gradient-to-br from-[#d4893a]/[0.04] to-transparent p-5">
                        <div className="mb-3 flex items-center gap-2.5">
                          <span className="rounded-lg bg-[#d4893a]/15 px-2.5 py-1 text-[12px] font-bold uppercase tracking-wide text-[#e8a04e] ring-1 ring-[#d4893a]/25">
                            Summary
                          </span>
                        </div>
                        <div className="flex-1">
                          <div className="text-[2rem] font-extrabold text-[#e8a04e]">
                            {alerts.length}
                          </div>
                          <div className="text-[15px] font-medium text-slate-300">
                            finding{alerts.length !== 1 ? "s" : ""} on your store
                          </div>
                          <p className="mt-2 text-[14px] text-slate-500">
                            {strongSignals.length > 0
                              ? `${strongSignals.length} confirmed signal${strongSignals.length !== 1 ? "s" : ""} requiring attention.`
                              : "Monitoring your products for issues."}
                          </p>
                        </div>
                        {strongSignals.length > 0 && (
                          <div className="mt-3 flex items-center gap-2">
                            <div className="h-2 w-2 rounded-full bg-[#d4893a] shadow-[0_0_8px_rgba(212,137,58,0.6)]" />
                            <span className="text-[13px] font-semibold text-[#e8a04e]">Active</span>
                          </div>
                        )}
                      </div>
                    </>
                  )}
                </div>
              </section>

              {/* ═══ HOT PRODUCTS — 3 cards per row ═══ */}
              {topProducts.length > 0 && (
                <section>
                  <SectionHeading eyebrow="Hot Products" title="Where buyers are active" />

                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {topProducts.slice(0, 3).map((product, i) => (
                      <div
                        key={`${product.product_id || "prod"}-${i}`}
                        className="hs-fade-up flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5 cursor-pointer select-none transition-all duration-200 hover:border-[#d4893a]/20 hover:bg-white/[0.05] hover:shadow-[0_4px_24px_rgba(212,137,58,0.06)]"
                        onClick={() => setActiveTopProduct(product)}
                      >
                        <div className="mb-3 flex items-center justify-between gap-2">
                          <span className="truncate text-[15px] font-semibold text-white">
                            {product.product_name || product.product_id || "—"}
                          </span>
                          {product.intent_level && (
                            <span className={`flex-shrink-0 rounded-lg px-2.5 py-1 text-[12px] font-bold uppercase tracking-wide ring-1 ${impactClass(product.intent_level === "HOT" ? "HIGH" : product.intent_level === "WARM" ? "MEDIUM" : "LOW")}`}>
                              {product.intent_level}
                            </span>
                          )}
                        </div>
                        <div className="mt-auto grid grid-cols-3 gap-2 border-t border-white/[0.05] pt-3">
                          <div>
                            <div className="text-[12px] font-medium uppercase text-slate-500">Views</div>
                            <div className="mt-1 text-[18px] font-bold text-white">{formatNumber(product.total_views)}</div>
                          </div>
                          <div>
                            <div className="text-[12px] font-medium uppercase text-slate-500">Visitors</div>
                            <div className="mt-1 text-[18px] font-bold text-white">{formatNumber(product.unique_visitors)}</div>
                          </div>
                          <div>
                            <div className="text-[12px] font-medium uppercase text-slate-500">Intent</div>
                            <div className="mt-1 text-[18px] font-bold text-white">{formatScore(product.avg_intent_score)}</div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Traffic source companion */}
                  <div className="mt-3">
                    <TrafficSourceBox
                      sourceQuality={sourceQuality}
                      isProUser={isProUser}
                      onUpgradeClick={() => setUpgradeModalOpen(true)}
                    />
                  </div>
                </section>
              )}

              {/* 4 — Product Performance */}
              {mergedProducts.length > 0 && (
                <section id="section-product-performance">
                  <SectionHeading
                    eyebrow="Products"
                    title="Where your traffic goes"
                    description="Sorted by what needs attention first."
                  />

                  {/* Urgency signal — Lite only, when high-priority rows exist */}

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
                              <span className="font-semibold">~€{totalLoss}</span>
                              <span className="ml-1 text-[11px] text-rose-200/50">(est. 2% CVR × {resolvedCcy} {resolvedAov} AOV{!resolvedAovIsReal ? " est." : ""})</span>
                            </>
                          ) : (
                            <span role="button" className="cursor-pointer text-rose-300/50 transition hover:text-rose-300/70" onClick={() => setUpgradeModalOpen(true)}>revenue on the table<span className="ml-1.5 text-violet-400/70 text-[11px] font-normal"> — Unlock in Pro</span></span>
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
                          <tr className="border-b border-white/[0.06] text-[12px] font-bold uppercase tracking-wide text-slate-500">
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
                                      <span className="mt-0.5 block text-[12px] leading-4 text-slate-500">
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
                                          className="mt-0.5 block cursor-pointer text-[10px] leading-3 text-slate-500 transition hover:text-slate-400"
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
                                  <span className="text-[13px] text-slate-600">—</span>
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
                                  <span className="text-[13px] text-slate-500">No data</span>
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
                                      <span className="block text-[12px] tabular-nums text-amber-400/80">€{row.estimated_loss} potential lost</span>
                                      <span className="block text-[10px] text-slate-500">based on 2% conversion · {resolvedCcy} {resolvedAov} AOV{!resolvedAovIsReal ? " (est.)" : ""}</span>
                                    </span>
                                  ) : (
                                    <span
                                      role="button"
                                      className="cursor-pointer text-[12px] text-slate-500 transition hover:text-slate-400"
                                      title="Upgrade to Pro to see estimated revenue loss"
                                      onClick={() => setUpgradeModalOpen(true)}
                                    >
                                      Revenue at risk<span className="ml-2 text-[11px] text-slate-600">(visible in Pro)</span>
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
                                  <span className="text-[12px] text-slate-500">
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
              )}

              {/* 5 — WHAT TO DO NEXT — Spark Action Engine */}
              {sparkActions.length > 0 && (
                <section id="section-what-next">
                  <SectionHeading
                    eyebrow="Actions"
                    title="What to do next"
                    description="Ranked by revenue impact. Each action is derived from your real store data."
                  />
                  <div className="space-y-3">
                    {sparkActions.slice(0, isProUser ? 5 : 2).map((act, i) => (
                      <div
                        key={act.id}
                        className={`group overflow-hidden rounded-2xl border transition-all duration-150 hover:shadow-[0_2px_16px_rgba(0,0,0,0.15)] ${
                          act.priority === "CRITICAL"
                            ? "border-rose-400/20 bg-gradient-to-r from-rose-500/[0.04] to-transparent"
                            : act.priority === "HIGH"
                            ? "border-amber-400/15 bg-white/[0.02]"
                            : "border-white/[0.07] bg-white/[0.02]"
                        }`}
                      >
                        <div className="px-5 py-4">
                          {/* Header: priority + title + impact */}
                          <div className="mb-2.5 flex items-start justify-between gap-3">
                            <div className="flex items-center gap-2.5">
                              <span className={`flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] ${
                                act.priority === "CRITICAL"
                                  ? "bg-rose-500/20 text-rose-300 ring-1 ring-rose-400/30"
                                  : act.priority === "HIGH"
                                  ? "bg-amber-500/15 text-amber-300 ring-1 ring-amber-400/25"
                                  : "bg-white/5 text-slate-400 ring-1 ring-white/10"
                              }`}>
                                {i === 0 ? "#1 Priority" : act.priority}
                              </span>
                              {act.isPattern && (
                                <span className="flex-shrink-0 rounded-full bg-violet-500/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-violet-300 ring-1 ring-violet-400/25">
                                  Store-wide
                                </span>
                              )}
                              {act.proofStatus === "improving" && (
                                <span className="flex-shrink-0 rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-emerald-300 ring-1 ring-emerald-400/25">
                                  Improving
                                </span>
                              )}
                              {act.proofStatus === "worsening" && (
                                <span className="flex-shrink-0 rounded-full bg-rose-500/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-rose-300 ring-1 ring-rose-400/25">
                                  Worsening
                                </span>
                              )}
                              {act.trend === "falling" && !act.isPattern && (
                                <span className="flex-shrink-0 rounded-full bg-rose-500/10 px-2 py-0.5 text-[10px] font-medium text-rose-300/70">
                                  ↓ Traffic falling
                                </span>
                              )}
                              {act.trend === "rising" && !act.isPattern && (
                                <span className="flex-shrink-0 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300/70">
                                  ↑ Traffic rising
                                </span>
                              )}
                              {act.segment && !act.isPattern && (
                                <span className={`flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${
                                  act.segment === "Mobile issue" || act.segment === "Desktop issue"
                                    ? "bg-pink-500/10 text-pink-300/80 ring-pink-400/20"
                                    : act.segment === "Paid traffic"
                                    ? "bg-yellow-500/10 text-yellow-300/80 ring-yellow-400/20"
                                    : act.segment === "Worsening"
                                    ? "bg-red-500/10 text-red-300/80 ring-red-400/20"
                                    : act.segment === "Improving"
                                    ? "bg-emerald-500/10 text-emerald-300/80 ring-emerald-400/20"
                                    : act.segment === "Landing page issue"
                                    ? "bg-orange-500/10 text-orange-300/80 ring-orange-400/20"
                                    : act.segment === "Timing mismatch"
                                    ? "bg-indigo-500/10 text-indigo-300/80 ring-indigo-400/20"
                                    : act.segment === "Upsell opportunity"
                                    ? "bg-violet-500/10 text-violet-300/80 ring-violet-400/20"
                                    : act.segment === "Bundle opportunity"
                                    ? "bg-cyan-500/10 text-cyan-300/80 ring-cyan-400/20"
                                    : act.segment === "Revenue concentration"
                                    ? "bg-amber-500/10 text-amber-200/80 ring-amber-400/20"
                                    : "bg-white/[0.04] text-slate-500 ring-white/[0.06]"
                                }`}>
                                  {act.segment}
                                </span>
                              )}
                              <h3 className="text-[14px] font-semibold text-white">{act.title}</h3>
                            </div>
                            {act.impactValue > 0 && (
                              <span className="flex-shrink-0 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[11px] font-semibold tabular-nums text-emerald-300">
                                ~{formatDisplayMoney(act.impactValue, "USD", displayCurrency)}/wk
                              </span>
                            )}
                          </div>

                          {/* Context (WHY) */}
                          <p className="mb-2 text-[12px] leading-[1.6] text-slate-400">
                            {act.context}
                          </p>

                          {/* Action (WHAT TO DO) */}
                          {isProUser ? (
                            <div className="mb-2 rounded-lg border border-violet-400/15 bg-violet-500/[0.04] px-3 py-2">
                              <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-[0.14em] text-violet-300/60">What to do</div>
                              <p className="text-[12px] leading-[1.55] text-slate-300">{act.action}</p>
                            </div>
                          ) : (
                            <div
                              className="mb-2 cursor-pointer rounded-lg border border-violet-400/10 bg-violet-500/[0.03] px-3 py-2 transition hover:border-violet-400/25"
                              onClick={() => setUpgradeModalOpen(true)}
                            >
                              <p className="text-[12px] text-slate-500">Specific action available <span className="text-violet-400/70">in Pro</span></p>
                            </div>
                          )}

                          {/* Proof detail — shown when baseline comparison detected change */}
                          {act.proofDetail && (
                            <div className={`mb-2 flex items-center gap-2 rounded-lg px-3 py-1.5 text-[11px] ${
                              act.proofStatus === "improving"
                                ? "border border-emerald-400/15 bg-emerald-500/[0.05] text-emerald-300/80"
                                : "border border-rose-400/15 bg-rose-500/[0.05] text-rose-300/80"
                            }`}>
                              <span>{act.proofStatus === "improving" ? "↑" : "↓"}</span>
                              <span>{act.proofDetail}</span>
                            </div>
                          )}

                          {/* Impact (WHAT HAPPENS NEXT) */}
                          <div className="flex items-start gap-2">
                            <Image src="/branding/hedgespark/spark.png" alt="" width={16} height={16} className="mt-0.5 flex-shrink-0" />
                            <p className="text-[11px] leading-[1.5] text-emerald-300/60">{act.impact}</p>
                          </div>
                        </div>
                      </div>
                    ))}

                    {/* Lite upsell: show count of locked actions */}
                    {!isProUser && sparkActions.length > 2 && (
                      <div
                        className="flex cursor-pointer items-center justify-between rounded-xl border border-violet-400/15 bg-violet-500/[0.04] px-5 py-3 transition hover:border-violet-400/25"
                        onClick={() => setUpgradeModalOpen(true)}
                      >
                        <span className="text-[12px] text-slate-400">
                          + {sparkActions.length - 2} more action{sparkActions.length - 2 !== 1 ? "s" : ""} identified
                        </span>
                        <span className="text-[11px] text-violet-400 transition hover:text-violet-300">Unlock with Pro →</span>
                      </div>
                    )}
                  </div>
                </section>
              )}

              {/* Proof of Impact — Pro only */}
              {isProUser && (
                <section id="section-proof">
                  <ActionProof apiBase={API_BASE} shop={shop} />
                </section>
              )}

              {/* 6 — Weekly Trend */}
              <section>
                <SectionHeading
                  eyebrow="Trend"
                  title="Your week in traffic"
                />
                {trend.length === 0 ? (
                  <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-5 py-4 text-[15px] text-slate-500">
                    {coldStartPhase <= 1 ? "Waiting for visitors..." : "No trend data yet."}
                  </p>
                ) : (
                  <div className="grid gap-2.5 sm:grid-cols-4 md:grid-cols-7">
                    {trend.map((point, i) => {
                      const val = point.visitors || 0;
                      const barH = Math.max(12, Math.round((val / maxTrend) * 100));
                      const isMax = val === maxTrend && val > 0;
                      return (
                        <div
                          key={`${point.day || "day"}-${i}`}
                          className={`hs-fade-up rounded-2xl border p-4 transition-all ${
                            isMax
                              ? "border-[#d4893a]/20 bg-[#d4893a]/[0.04]"
                              : "border-white/[0.07] bg-white/[0.02]"
                          }`}
                          style={{ animationDelay: `${i * 40}ms` }}
                        >
                          <div className="mb-2 text-[13px] font-medium text-slate-400">{point.day || `Day ${i + 1}`}</div>
                          <div className="flex h-24 items-end">
                            <div
                              className={`w-full rounded-lg ${
                                isMax
                                  ? "bg-gradient-to-t from-[#d4893a] to-[#e8a04e] shadow-[0_0_12px_rgba(212,137,58,0.3)]"
                                  : "bg-gradient-to-t from-violet-500/80 to-cyan-400/60"
                              }`}
                              style={{ height: barH }}
                            />
                          </div>
                          <div className={`mt-2 text-[18px] font-bold tabular-nums ${isMax ? "text-[#e8a04e]" : "text-white"}`}>
                            {formatNumber(val)}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>

              {/* 7 — Top Pages */}
              <section>
                <SectionHeading eyebrow="Pages" title="Where visitors spend time" />
                {topPages.length === 0 ? (
                  <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-5 py-4 text-[15px] text-slate-500">
                    {coldStartPhase <= 1 ? "Waiting for visitors..." : "No page data yet."}
                  </p>
                ) : (
                  <div className="space-y-2.5">
                    {topPages.slice(0, 8).map((page, i) => {
                      const maxViews = topPages[0]?.views || 1;
                      const barPct = Math.max(4, Math.round(((page.views ?? 0) / maxViews) * 100));
                      return (
                        <div key={`${page.url || "page"}-${i}`} className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4 transition-colors hover:bg-white/[0.04]">
                          <div className="mb-2 flex items-center justify-between gap-4">
                            <span className="min-w-0 flex-1 truncate text-[15px] font-medium text-slate-200" title={page.url}>{page.url || "—"}</span>
                            <span className="flex-shrink-0 text-[16px] font-bold tabular-nums text-white">{formatNumber(page.views)} <span className="text-[13px] font-normal text-slate-500">views</span></span>
                          </div>
                          <div className="mb-2 h-2.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
                            <div
                              className={`h-full rounded-full ${i === 0 ? "bg-gradient-to-r from-[#d4893a] to-[#e8a04e]" : "bg-gradient-to-r from-violet-500/70 to-cyan-400/50"}`}
                              style={{ width: `${barPct}%` }}
                            />
                          </div>
                          <div className="flex items-center gap-4 text-[13px] text-slate-500">
                            <span>{formatNumber(page.visitors)} visitors</span>
                            <span>{formatDecimal(page.avg_dwell, 1)}s avg dwell</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>

              {/* 8 — Conversion Funnel (Pro) */}
              <section id="section-funnel">
                {isProUser && (
                <SectionHeading
                  eyebrow="Funnel"
                  title="Where you lose buyers"
                  description="Drop-off from view to purchase."
                />
                )}
                {!isProUser ? null : funnelSteps.length === 0 ? (
                  <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-600">
                    No funnel data yet — events will appear once your tracker is active.
                  </p>
                ) : (
                  <FunnelVisualization steps={funnelSteps} />
                )}
              </section>

              {/* 9 — Session Timeline + Click Insights (Pro) */}
              <section id="section-sessions">
                {!isProUser ? null : (
                <div className="grid gap-4 xl:grid-cols-2">

                  {/* Left — Session Replay */}
                  <div>
                    <SectionHeading
                      eyebrow="Sessions"
                      title="Recent visitor journeys"
                    />
                    {sessions.length === 0 ? (
                      <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-600">
                        No session data yet.
                      </p>
                    ) : (
                      <div className="overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.02]">
                        <table className="min-w-full text-left text-[13px]">
                          <thead>
                            <tr className="border-b border-white/[0.06] text-[11px] uppercase tracking-wide text-slate-600">
                              <th className="px-4 py-2.5 font-medium">Visitor</th>
                              <th className="px-4 py-2.5 font-medium">Pages</th>
                              <th className="px-4 py-2.5 font-medium">Duration</th>
                              <th className="px-4 py-2.5 font-medium">Last Page</th>
                            </tr>
                          </thead>
                          <tbody>
                            {sessions.map((s, i) => (
                              <tr
                                key={`sess-${s.visitor_id}-${i}`}
                                className="border-t border-white/[0.04] transition-colors hover:bg-white/[0.02]"
                              >
                                <td className="px-4 py-2.5">
                                  <span className="font-mono text-[11px] text-slate-400">
                                    {s.visitor_id.slice(0, 8)}
                                  </span>
                                </td>
                                <td className="px-4 py-2.5">
                                  <span className="tabular-nums text-slate-300">
                                    {s.pages_visited.length}
                                  </span>
                                  <span className="ml-1 text-[10px] text-slate-600">pg</span>
                                </td>
                                <td className="px-4 py-2.5 tabular-nums text-slate-400">
                                  {formatDuration(s.total_duration_seconds)}
                                </td>
                                <td className="max-w-[160px] px-4 py-2.5">
                                  <span
                                    className="block truncate text-[11px] text-slate-500"
                                    title={s.last_page || "—"}
                                  >
                                    {s.last_page ? shortUrl(s.last_page) : "—"}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>

                  {/* Right — Click Insights */}
                  <div>
                    <SectionHeading
                      eyebrow="Clicks"
                      title="What visitors click"
                    />
                    {clicks.length === 0 ? (
                      <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-600">
                        No click data yet — track click events to see this.
                      </p>
                    ) : (
                      <div className="space-y-1.5">
                        {clicks.map((row, i) => {
                          const maxClicks = clicks[0]?.clicks || 1;
                          const barWidth = Math.round((row.clicks / maxClicks) * 100);
                          return (
                            <div
                              key={`click-${i}`}
                              className="flex items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2"
                            >
                              <span className="w-4 flex-shrink-0 text-center text-[11px] tabular-nums text-slate-700">
                                {i + 1}
                              </span>
                              <span
                                className="min-w-0 flex-1 truncate text-[12px] text-slate-300"
                                title={row.url}
                              >
                                {shortUrl(row.url)}
                              </span>
                              <div className="w-14 flex-shrink-0">
                                <div className="h-1 w-full overflow-hidden rounded-full bg-white/[0.07]">
                                  <div
                                    className="h-full rounded-full bg-cyan-400/50"
                                    style={{ width: `${barWidth}%` }}
                                  />
                                </div>
                              </div>
                              <span className="w-8 flex-shrink-0 text-right text-[11px] tabular-nums text-slate-500">
                                {row.clicks}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>

                </div>
                )}
              </section>

              {/* 10 — Live Radar + World Map */}
              <section id="section-live">
                <SectionHeading
                  eyebrow="Live Radar"
                  title="Right now in your store"
                />
                <LiveRadarMap
                  visitors={liveVisitors}
                  radarPositions={RADAR_POSITIONS}
                  coldStartPhase={coldStartPhase}
                />
              </section>


              {/* ═══ PRO ZONE SEPARATOR ═══ */}
              {!isProUser && (
                <div className="relative overflow-hidden rounded-3xl border border-[#d4893a]/15 bg-gradient-to-br from-[#d4893a]/[0.04] via-transparent to-[#7c3aed]/[0.03] p-8 sm:p-10">
                  <div className="absolute inset-x-0 top-0 h-[3px] bg-gradient-to-r from-[#7c3aed] via-[#c026d3] to-[#f97316]" />
                  <div className="pointer-events-none absolute -right-20 -top-20 h-[300px] w-[300px] rounded-full bg-[#d4893a]/[0.06] blur-[120px]" />
                  <div className="relative">
                    <h2 className="text-[1.75rem] font-extrabold text-white sm:text-[2rem]">
                      Unlock <span className="hs-brand-gradient">Pro Intelligence</span>
                    </h2>
                    <p className="mt-2 max-w-lg text-[16px] leading-relaxed text-slate-400">
                      Everything above stays free. Pro adds the tools that turn findings into revenue.
                    </p>
                    <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                      {[
                        { icon: "🎯", title: "Smart Nudges", desc: "Auto-deploy fixes on problem products" },
                        { icon: "📊", title: "Proof Engine", desc: "Control groups prove what works" },
                        { icon: "👥", title: "Audience Intel", desc: "See who's hot, warm, and cold" },
                        { icon: "🔍", title: "Deep Analytics", desc: "Funnels, sessions, scroll depth" },
                        { icon: "💰", title: "Price Intelligence", desc: "How your pricing compares to competitors" },
                        { icon: "🏆", title: "Market Position", desc: "Where you stand vs the competition" },
                      ].map((f) => (
                        <div key={f.title} className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
                          <div className="text-[24px]">{f.icon}</div>
                          <h3 className="mt-3 text-[16px] font-bold text-white">{f.title}</h3>
                          <p className="mt-1 text-[14px] text-slate-400">{f.desc}</p>
                        </div>
                      ))}
                    </div>
                    <button
                      onClick={() => setUpgradeModalOpen(true)}
                      className="hs-cta-gradient mt-8 rounded-2xl px-8 py-3.5 text-[16px] font-bold text-white shadow-[0_0_30px_rgba(212,137,58,0.25)] transition-all hover:shadow-[0_0_40px_rgba(212,137,58,0.35)]"
                    >
                      Start 14-day free trial
                    </button>
                  </div>
                </div>
              )}

              {/* Pro Intelligence narrative hero — sets up the story the merchant
                  is about to read across the sections below. Designed to pass
                  the "stupid test": zero jargon, plain English, answers
                  "what am I looking at?" in one glance. */}
              {isProUser && (
                <div className="relative overflow-hidden rounded-3xl border border-white/[0.06] bg-gradient-to-br from-white/[0.025] via-transparent to-white/[0.02] px-6 py-7 sm:px-10 sm:py-9">
                  {/* Ambient brand gradient stripe on top */}
                  <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-[#7c3aed] via-[#c026d3] to-[#f97316]" />
                  {/* Subtle glow */}
                  <div className="pointer-events-none absolute -right-24 -top-24 h-[320px] w-[320px] rounded-full bg-[#d946ef]/[0.04] blur-[140px]" />

                  <div className="relative">
                    <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.03] px-3 py-1">
                      <span className="h-1.5 w-1.5 rounded-full bg-[#e8a04e] shadow-[0_0_8px_rgba(232,160,78,0.6)]" />
                      <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-300">
                        Pro Intelligence
                      </span>
                    </div>

                    <h2 className="text-[1.75rem] font-extrabold leading-[1.1] text-white sm:text-[2.25rem]">
                      Know your customers{" "}
                      <span className="hs-brand-gradient">better than they know themselves.</span>
                    </h2>

                    <p className="mt-3 max-w-[46rem] text-[15px] leading-[1.65] text-slate-400">
                      Everything below answers one question:{" "}
                      <strong className="text-slate-200">
                        who are the people shopping your store, and how do you keep them coming back?
                      </strong>{" "}
                      Five chapters, one story — the economics of your customer base, the products that pull them in,
                      the ones most likely to return, the behavior patterns that separate buyers from browsers,
                      and the retention curves that tell you if you&apos;re actually growing.
                    </p>

                    {/* 5-chapter preview strip — the "table of contents" */}
                    <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                      {[
                        {
                          num: "01",
                          label: "Customer Economics",
                          q: "Who are they?",
                          color: "#e8a04e",
                        },
                        {
                          num: "02",
                          label: "Gateway Products",
                          q: "What pulls them in?",
                          color: "#e8a04e",
                        },
                        {
                          num: "03",
                          label: "Predicted Value",
                          q: "Who's worth the most?",
                          color: "#34d399",
                        },
                        {
                          num: "04",
                          label: "Behavioral DNA",
                          q: "Why do they buy?",
                          color: "#d946ef",
                        },
                        {
                          num: "05",
                          label: "Retention Curves",
                          q: "Are they coming back?",
                          color: "#c4b5fd",
                        },
                      ].map((chapter) => (
                        <div
                          key={chapter.num}
                          className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 transition-colors hover:border-white/[0.1]"
                        >
                          <div className="flex items-center gap-2">
                            <span
                              className="text-[9px] font-bold tabular-nums"
                              style={{ color: chapter.color }}
                            >
                              {chapter.num}
                            </span>
                            <span className="text-[11px] font-bold uppercase tracking-[0.08em] text-slate-300">
                              {chapter.label}
                            </span>
                          </div>
                          <div className="mt-1 text-[11px] italic text-slate-500">
                            {chapter.q}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Pro — Audience Segments */}
              {isProUser && (
                <section id="section-audience">
                  <SectionHeading
                    eyebrow="Audience"
                    title="Who's ready to buy"
                    pro
                  />
                  <AudienceSegments
                    apiBase={API_BASE}
                    shop={shop}
                    apiHeaders={apiHeaders}
                    topProducts={topProducts}
                  />
                </section>
              )}

              {/* Pro — Nudge Performance */}
              {isProUser && (
                <section id="section-nudges">
                  <SectionHeading
                    eyebrow="Nudges"
                    title="Performance"
                    pro
                  />
                  <NudgePerformance
                    apiBase={API_BASE}
                    shop={shop}
                    apiHeaders={apiHeaders}
                    displayCurrency={displayCurrency}
                  />
                </section>
              )}

              {/* Pro — Holdout Lift Report */}
              {isProUser && (
                <section id="section-lift">
                  <SectionHeading
                    eyebrow="Proof"
                    title="Did it actually work?"
                    pro
                  />
                  <LiftReport apiBase={API_BASE} shop={shop} apiHeaders={apiHeaders} displayCurrency={displayCurrency} />
                </section>
              )}

              {/* Pro — Scroll Intelligence + Cohort Retention side by side */}
              {isProUser && (
                <section id="section-scroll-cohorts">
                  <div className="grid gap-4 xl:grid-cols-2">
                    <div>
                      <SectionHeading
                        eyebrow="Scroll Depth"
                        title="Where visitors stop reading"
                        pro
                      />
                      <HeatmapCard apiBase={API_BASE} shop={shop} apiHeaders={apiHeaders} />
                    </div>
                    <div>
                      <SectionHeading
                        eyebrow="Retention"
                        title="Repeat purchase rates"
                      />
                      <CohortTable apiBase={API_BASE} shop={shop} apiHeaders={apiHeaders} displayCurrency={displayCurrency} />
                    </div>
                  </div>
                </section>
              )}

              {/* Pro — Revenue Forecast + Attribution + LTV Intelligence */}
              {isProUser && (
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
                              {forecastData.currency === "EUR" ? "€" : "$"}{(forecastData.forecast_7d?.revenue ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}
                            </div>
                            <div className="mt-0.5 text-[11px] text-slate-500">
                              range {forecastData.currency === "EUR" ? "€" : "$"}{(forecastData.forecast_7d?.revenue_low ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}
                              {" — "}
                              {forecastData.currency === "EUR" ? "€" : "$"}{(forecastData.forecast_7d?.revenue_high ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}
                            </div>
                          </div>
                          <div>
                            <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">30-day forecast</div>
                            <div className="mt-1 text-2xl font-bold text-white">
                              {forecastData.currency === "EUR" ? "€" : "$"}{(forecastData.forecast_30d?.revenue ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}
                            </div>
                            <div className="mt-0.5 text-[11px] text-slate-500">
                              range {forecastData.currency === "EUR" ? "€" : "$"}{(forecastData.forecast_30d?.revenue_low ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}
                              {" — "}
                              {forecastData.currency === "EUR" ? "€" : "$"}{(forecastData.forecast_30d?.revenue_high ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}
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

                    {/* Attribution Intelligence — killer cassettone restyled */}
                    <div>
                      <SectionHeading
                        eyebrow="Attribution"
                        title="Where revenue comes from"
                      />
                      {attrSummary ? (() => {
                        const ordersTotal = attrSummary.orders_total;
                        const ordersAttributed = attrSummary.orders_attributed;
                        const attrRate = attrSummary.attribution_rate;
                        const sources = attrSummary.top_sources_first_touch;
                        const matchRate = attrSummary.first_vs_last_match_rate;
                        // Normalize bars against the top source's revenue.
                        const maxRev = sources.length > 0
                          ? Math.max(...sources.map((s) => s.revenue), 1)
                          : 1;
                        // Source → brand accent color (repeatable for visual variety).
                        const sourcePalette = ["#c4b5fd", "#e8a04e", "#34d399", "#fb923c", "#d946ef"];

                        return (
                          <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
                            {/* Header eyebrow */}
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

                            {/* 3 big KPI tiles */}
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

                            {/* Top sources — bar chart style */}
                            {sources.length > 0 ? (
                              <div>
                                <div className="mb-2 flex items-center justify-between">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-500">
                                    Top sources (first touch)
                                  </div>
                                  <div className="text-[10px] text-slate-600">revenue per channel</div>
                                </div>
                                <div className="space-y-2">
                                  {sources.slice(0, 5).map((s, i) => {
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

                            {/* Journey insight — first vs last touch match rate */}
                            {matchRate != null && ordersAttributed > 0 && (
                              <div
                                className="mt-5 rounded-xl border px-4 py-3"
                                style={{
                                  borderColor: matchRate > 0.8
                                    ? "rgba(52, 211, 153, 0.18)"
                                    : "rgba(232, 160, 78, 0.18)",
                                  backgroundColor: matchRate > 0.8
                                    ? "rgba(52, 211, 153, 0.04)"
                                    : "rgba(232, 160, 78, 0.04)",
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

                            {/* Trust footer */}
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

                    {/* Customer Economics — killer cassettone, branded */}
                    <div>
                      <SectionHeading
                        eyebrow="Lifetime Value"
                        title="Customer economics"
                      />
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
                        const maxCohortRevenue = cohorts.length > 0
                          ? Math.max(...cohorts.map((c) => c.revenue_total), 1)
                          : 1;
                        return (
                          <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
                            {/* Header eyebrow + narrative headline */}
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

                            {/* 3 big KPIs */}
                            <div className="mb-6 grid grid-cols-3 gap-3">
                              <div
                                className="rounded-xl border px-4 py-3"
                                style={{ borderColor: "rgba(232, 160, 78, 0.18)", backgroundColor: "rgba(232, 160, 78, 0.04)" }}
                              >
                                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                                  Customers
                                </div>
                                <div className="mt-1 text-[26px] font-extrabold tabular-nums leading-none text-white">
                                  {totalCustomers.toLocaleString()}
                                </div>
                                <div className="mt-1 text-[10px] text-slate-500">identified</div>
                              </div>
                              <div
                                className="rounded-xl border px-4 py-3"
                                style={{ borderColor: `${repeatColor}40`, backgroundColor: `${repeatColor}0f` }}
                              >
                                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                                  Repeat rate
                                </div>
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
                                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                                  Avg customer value
                                </div>
                                <div className="mt-1 text-[26px] font-extrabold tabular-nums leading-none text-emerald-400">
                                  {formatDisplayMoney(avgRevenue, "USD", displayCurrency)}
                                </div>
                                <div className="mt-1 text-[10px] text-slate-500">lifetime revenue</div>
                              </div>
                            </div>

                            {/* Monthly cohorts — visualized as bar chart, not list */}
                            {cohorts.length > 0 ? (
                              <div>
                                <div className="mb-2 flex items-center justify-between">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-500">
                                    Monthly cohorts
                                  </div>
                                  <div className="text-[10px] text-slate-600">revenue by acquisition month</div>
                                </div>
                                <div className="space-y-2">
                                  {cohorts.slice(0, 6).map((c, i) => {
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
                                              boxShadow: isRecent
                                                ? `0 0 10px -2px ${barColor}88`
                                                : undefined,
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

                            {/* Coverage trust pill */}
                            {coverage.coverage_rate != null && totalCustomers > 0 && (
                              <div className="mt-5 inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3 py-1">
                                <span
                                  className="h-1.5 w-1.5 rounded-full"
                                  style={{
                                    backgroundColor: coverage.coverage_rate > 0.7 ? "#34d399" : "#fb923c",
                                  }}
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

                  {/* Profit Intelligence — the Sprint B killer cassettone that
                      closes the P&L gap vs Lifetimely + Triple Whale. Full-width
                      because the waterfall visualization needs the horizontal
                      real estate and Net Profit is the most important number a
                      merchant reads on this entire page. */}
                  <div className="mt-6">
                    <SectionHeading
                      eyebrow="Profit Intelligence"
                      title="What you actually keep"
                    />
                    <PnlReport
                      data={pnlData}
                      displayCurrency={displayCurrency}
                    />
                  </div>

                  {/* Gateway Products + Predicted LTV — the two killer cassettoni
                      that competitors structurally cannot match */}
                  <div className="mt-6 grid gap-4 xl:grid-cols-2">
                    <GatewayProducts
                      data={gatewayProductsData}
                      displayCurrency={displayCurrency}
                    />
                    <PredictedLtv
                      data={predictedLtvData}
                      displayCurrency={displayCurrency}
                    />
                  </div>

                  {/* Price + Market Intelligence — inside Pro Intelligence.
                      mt-10 breathing room so the Market Position heading
                      doesn't stick to the Gateway/Predicted cassettoni above. */}
                  <div className="mt-10">
                    <SectionHeading
                      eyebrow="Market Position"
                      title="Know where you stand"
                    />
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div id="section-price-intelligence">
                      <div className="h-full rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
                        <div className="mb-4 text-[16px] font-bold text-[#e8a04e]">Price Intelligence</div>
                        {priceIntel.length === 0 ? (
                          <p className="text-[14px] text-slate-500">No pricing data yet.</p>
                        ) : (
                          <div className="space-y-3">
                            {priceIntel.slice(0, 3).map((item, i) => (
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
                            {marketIntel.slice(0, 3).map((item, i) => (
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
              )}

              {/* Pro — Behavioral DNA (restyled killer section) */}
              {isProUser && behavioralData && (() => {
                const insights = behavioralData.insights;
                const byEngagement = behavioralData.segments.by_engagement;
                const byVisit = behavioralData.segments.by_visit_pattern;
                const bySource = behavioralData.segments.by_source;
                const coverage = behavioralData.data_coverage;

                // Helpers to find max avg_revenue per column so bars can be normalized
                type BehSegment = typeof byEngagement[number];
                const maxRev = (arr: readonly BehSegment[]) =>
                  arr.length > 0 ? Math.max(...arr.map((s) => s.avg_revenue), 1) : 1;
                const maxEng = maxRev(byEngagement);
                const maxVis = maxRev(byVisit);
                const maxSrc = maxRev(bySource);

                // Color logic per engagement tier
                const engagementColor = (level: string) => {
                  if (level === "HIGH")   return "#34d399"; // emerald
                  if (level === "MEDIUM") return "#e8a04e"; // amber
                  if (level === "LOW")    return "#f87171"; // red
                  return "#94a3b8";
                };
                const visitColor = (level: string) =>
                  level === "REPEAT_VISITOR" ? "#34d399" : "#e8a04e";

                // Pretty-label source codes
                const sourceLabel = (code: string) => {
                  const map: Record<string, string> = {
                    SEARCH: "Search",
                    SOCIAL: "Social",
                    DIRECT: "Direct",
                    EMAIL_SMS: "Email / SMS",
                    REFERRAL: "Referral",
                    PAID: "Paid ads",
                    ORGANIC: "Organic",
                    UNKNOWN: "Unknown",
                    OTHER: "Other",
                  };
                  return map[code] ?? code;
                };

                type SegmentRow = {
                  label: string;
                  customers: number;
                  avg_revenue: number;
                  repeat_rate: number;
                  color: string;
                };

                const renderSegmentCassettone = (
                  eyebrow: string,
                  headline: string,
                  rows: SegmentRow[],
                  maxR: number,
                  emptyMsg: string,
                ) => (
                  <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
                    <div className="mb-4">
                      <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-[#d946ef]">
                        {eyebrow}
                      </div>
                      <h4 className="mt-1 text-[13px] font-semibold text-white">{headline}</h4>
                    </div>
                    {rows.length === 0 ? (
                      <p className="text-[11px] text-slate-600">{emptyMsg}</p>
                    ) : (
                      <div className="space-y-3">
                        {rows.map((row, i) => {
                          const barWidth = Math.max(6, Math.round((row.avg_revenue / maxR) * 100));
                          return (
                            <div key={i}>
                              <div className="mb-1.5 flex items-center justify-between text-[11px]">
                                <div className="flex items-center gap-2">
                                  <span
                                    className="h-2 w-2 flex-shrink-0 rounded-full"
                                    style={{ backgroundColor: row.color, boxShadow: `0 0 6px ${row.color}66` }}
                                  />
                                  <span className="font-semibold" style={{ color: row.color }}>
                                    {row.label}
                                  </span>
                                  <span className="text-slate-600">{row.customers} cust</span>
                                </div>
                                <div className="tabular-nums">
                                  <span className="font-bold text-white">
                                    {formatDisplayMoney(row.avg_revenue, "USD", displayCurrency)}
                                  </span>
                                  <span className="ml-2 text-slate-500">
                                    {Math.round(row.repeat_rate * 100)}% repeat
                                  </span>
                                </div>
                              </div>
                              <div className="h-1 overflow-hidden rounded-full bg-white/[0.04]">
                                <div
                                  className="h-full rounded-full transition-all duration-500"
                                  style={{
                                    width: `${barWidth}%`,
                                    background: `linear-gradient(90deg, ${row.color} 0%, ${row.color}99 100%)`,
                                  }}
                                />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );

                const engagementRows: SegmentRow[] = byEngagement.map((s) => ({
                  label: s.segment === "HIGH" ? "High engagement" : s.segment === "MEDIUM" ? "Medium engagement" : "Low engagement",
                  customers: s.customers,
                  avg_revenue: s.avg_revenue,
                  repeat_rate: s.repeat_rate,
                  color: engagementColor(s.segment),
                }));
                const visitRows: SegmentRow[] = byVisit.map((s) => ({
                  label: s.segment === "REPEAT_VISITOR" ? "Repeat visitors" : "Single visit",
                  customers: s.customers,
                  avg_revenue: s.avg_revenue,
                  repeat_rate: s.repeat_rate,
                  color: visitColor(s.segment),
                }));
                const sourceRows: SegmentRow[] = bySource.map((s) => ({
                  label: sourceLabel(s.segment),
                  customers: s.customers,
                  avg_revenue: s.avg_revenue,
                  repeat_rate: s.repeat_rate,
                  color: "#c4b5fd", // lilac — consistent across sources (no hierarchy)
                }));

                // ─────────────────────────────────────────────────────────
                // Moat hero computation — the single killer number that
                // visualizes HedgeSpark's structural differentiator vs every
                // other Shopify analytics tool: linking pre-purchase
                // behavior to post-purchase revenue. Competitors can only
                // tell you WHAT customers bought; we can tell you HOW they
                // behaved before buying and how much more the high-engagement
                // ones are worth. No other tool has both the behavioral
                // tracking and the LTV attribution to compute this ratio.
                // ─────────────────────────────────────────────────────────
                const findTier = (name: string) =>
                  byEngagement.find((s) => s.segment === name);
                const highTier = findTier("HIGH");
                const lowTier  = findTier("LOW") || findTier("MEDIUM");

                // Moat is "live" only when we have both a HIGH tier and a
                // comparison tier with real revenue on both. Otherwise we
                // fall back to the original qualitative hero copy so the
                // cassettone still renders gracefully for new merchants.
                const moatIsLive = (
                  highTier != null
                  && lowTier != null
                  && highTier.segment !== lowTier.segment
                  && highTier.avg_revenue > 0
                  && lowTier.avg_revenue > 0
                  && highTier.customers >= 2
                  && lowTier.customers >= 2
                );
                const moatRatio = moatIsLive
                  ? highTier!.avg_revenue / lowTier!.avg_revenue
                  : 0;
                const moatTierLabel = lowTier
                  ? (lowTier.segment === "MEDIUM" ? "medium-engagement" : "low-engagement")
                  : "low-engagement";

                return (
                  <section id="section-behavioral-intelligence">
                    <SectionHeading
                      eyebrow="Behavioral DNA"
                      title="What separates your buyers from your browsers"
                    />

                    {/* Moat hero — live number when we have enough data to
                        compute it, qualitative copy otherwise. When live
                        this is the most important visualization on the
                        entire dashboard: it literally shows the moat
                        competitors cannot replicate. */}
                    <div
                      className="mb-5 overflow-hidden rounded-2xl border"
                      style={{
                        borderColor: "rgba(217, 70, 239, 0.22)",
                        background:
                          "linear-gradient(135deg, rgba(217, 70, 239, 0.08) 0%, rgba(124, 58, 237, 0.04) 45%, rgba(217, 70, 239, 0.02) 100%)",
                      }}
                    >
                      {moatIsLive ? (
                        <div className="p-6">
                          {/* Eyebrow */}
                          <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#d946ef]">
                            The HedgeSpark moat
                          </div>

                          {/* Killer headline — the ratio right in the face */}
                          <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                            <span className="text-[13px] font-medium text-slate-300">
                              High-engagement buyers are worth
                            </span>
                            <span
                              className="text-[44px] font-extrabold leading-none tabular-nums"
                              style={{
                                color: "#d946ef",
                                textShadow: "0 0 28px rgba(217, 70, 239, 0.4)",
                              }}
                            >
                              {moatRatio.toFixed(1)}×
                            </span>
                            <span className="text-[13px] font-medium text-slate-300">
                              more than {moatTierLabel} buyers.
                            </span>
                          </div>

                          {/* Double-bar visualization — dramatic, one image worth 1000 words */}
                          <div className="mt-5 space-y-3">
                            {/* HIGH bar */}
                            <div>
                              <div className="mb-1.5 flex items-center justify-between text-[11px]">
                                <div className="flex items-center gap-2">
                                  <span
                                    className="h-2 w-2 rounded-full"
                                    style={{
                                      backgroundColor: "#d946ef",
                                      boxShadow: "0 0 10px rgba(217, 70, 239, 0.7)",
                                    }}
                                  />
                                  <span className="font-bold uppercase tracking-[0.08em] text-[#d946ef]">
                                    High engagement
                                  </span>
                                  <span className="text-slate-500">
                                    {highTier!.customers} customers
                                  </span>
                                </div>
                                <span className="text-[16px] font-extrabold tabular-nums text-white">
                                  {formatDisplayMoney(highTier!.avg_revenue, "USD", displayCurrency)}
                                </span>
                              </div>
                              <div className="h-2.5 overflow-hidden rounded-full bg-white/[0.04]">
                                <div
                                  className="h-full rounded-full"
                                  style={{
                                    width: "100%",
                                    background: "linear-gradient(90deg, #d946ef 0%, #a855f7 100%)",
                                    boxShadow: "0 0 14px -2px rgba(217, 70, 239, 0.6)",
                                  }}
                                />
                              </div>
                            </div>

                            {/* LOW/MEDIUM bar — scaled against HIGH to make the ratio visceral */}
                            <div>
                              <div className="mb-1.5 flex items-center justify-between text-[11px]">
                                <div className="flex items-center gap-2">
                                  <span
                                    className="h-2 w-2 rounded-full bg-slate-500"
                                  />
                                  <span className="font-bold uppercase tracking-[0.08em] text-slate-400">
                                    {lowTier!.segment === "MEDIUM" ? "Medium engagement" : "Low engagement"}
                                  </span>
                                  <span className="text-slate-500">
                                    {lowTier!.customers} customers
                                  </span>
                                </div>
                                <span className="text-[16px] font-extrabold tabular-nums text-slate-400">
                                  {formatDisplayMoney(lowTier!.avg_revenue, "USD", displayCurrency)}
                                </span>
                              </div>
                              <div className="h-2.5 overflow-hidden rounded-full bg-white/[0.04]">
                                <div
                                  className="h-full rounded-full"
                                  style={{
                                    width: `${Math.max(6, Math.round((lowTier!.avg_revenue / highTier!.avg_revenue) * 100))}%`,
                                    background: "linear-gradient(90deg, #64748b 0%, #475569 100%)",
                                  }}
                                />
                              </div>
                            </div>
                          </div>

                          {/* Moat claim — the single sentence that no competitor can say */}
                          <div className="mt-5 border-t border-white/[0.07] pt-4">
                            <p className="text-[12px] leading-relaxed text-slate-300">
                              <strong className="text-white">This is the HedgeSpark moat.</strong>{" "}
                              Every other Shopify analytics tool segments customers by
                              <em> what</em> they bought. We segment them by
                              <strong className="text-white"> how they behaved before buying</strong> —
                              linking scroll depth, dwell time, and visit pattern to real
                              lifetime value. Structurally impossible to replicate without
                              first-party behavioral tracking joined to order attribution.
                            </p>
                          </div>

                          {/* Trust footer — every claim is measurable */}
                          <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[10px] text-slate-500">
                            <div className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.02] px-2.5 py-1">
                              <span className="h-1 w-1 rounded-full bg-[#d946ef]" />
                              <span>
                                Measured from {coverage.segmentable_customers} identified customers
                              </span>
                            </div>
                            <div className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.02] px-2.5 py-1">
                              <span className="h-1 w-1 rounded-full bg-[#d946ef]" />
                              <span>{behavioralData.window_days}-day window</span>
                            </div>
                            <div className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.02] px-2.5 py-1">
                              <span className="h-1 w-1 rounded-full bg-[#d946ef]" />
                              <span>First-party behavioral data, zero third-party cookies</span>
                            </div>
                          </div>

                          {/* Surface the generator-driven narrative insights below the
                              killer number — they add color without competing with
                              the ratio's impact. */}
                          {insights.length > 0 && (
                            <div className="mt-4 space-y-1.5 border-t border-white/[0.06] pt-4">
                              {insights.map((insight, i) => (
                                <p key={i} className="text-[12px] leading-relaxed text-slate-400">
                                  <span className="mr-2 text-[#d946ef]">›</span>
                                  {insight}
                                </p>
                              ))}
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="p-5">
                          <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#d946ef]">
                            The moat — what only HedgeSpark can show you
                          </div>
                          <p className="mt-1.5 text-[13px] leading-relaxed text-slate-300">
                            Competitors segment customers by <em>what</em> they bought.
                            HedgeSpark segments them by{" "}
                            <strong className="text-white">how they behaved before buying</strong> —
                            linking scroll depth, dwell time, and visit pattern to actual
                            revenue outcomes. The killer ratio becomes visible here once we
                            have 2+ customers in both a high-engagement and a low-engagement
                            tier with real revenue on both sides.
                          </p>
                          {insights.length > 0 && (
                            <div className="mt-4 space-y-1.5 border-t border-white/[0.06] pt-4">
                              {insights.map((insight, i) => (
                                <p key={i} className="text-[12px] leading-relaxed text-slate-400">
                                  <span className="mr-2 text-[#d946ef]">›</span>
                                  {insight}
                                </p>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>

                    {/* 3-column segment cassettoni */}
                    <div className="grid gap-4 xl:grid-cols-3">
                      {renderSegmentCassettone(
                        "By Engagement",
                        "Scroll + dwell + visits",
                        engagementRows,
                        maxEng,
                        "Needs visitor behavior data to segment.",
                      )}
                      {renderSegmentCassettone(
                        "By Visit Pattern",
                        "Browsing before purchase",
                        visitRows,
                        maxVis,
                        "Needs visitor session data.",
                      )}
                      {renderSegmentCassettone(
                        "By Traffic Source",
                        "Channel buyer quality",
                        sourceRows,
                        maxSrc,
                        "Needs attributed orders.",
                      )}
                    </div>

                    {/* Coverage indicator */}
                    {coverage.total_customers > 0 && (
                      <div className="mt-4 inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3 py-1">
                        <span
                          className="h-1.5 w-1.5 rounded-full"
                          style={{
                            backgroundColor: coverage.coverage_rate > 0.7 ? "#34d399" : "#fb923c",
                          }}
                        />
                        <span className="text-[10px] text-slate-400">
                          {coverage.segmentable_customers} of {coverage.total_customers} customers have behavioral data
                          ({Math.round(coverage.coverage_rate * 100)}% coverage)
                        </span>
                      </div>
                    )}
                  </section>
                );
              })()}

              {/* 11 — Settings / Integrations (all tiers) */}
              <section id="section-settings">
                  <SectionHeading
                    eyebrow="Settings"
                    title="Preferences &amp; integrations"
                  />

                  {/* Display currency card */}
                  <div className="mb-4 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-3">
                          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#e8a04e]/10 text-[#e8a04e]">
                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor" className="h-5 w-5">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m-3-2.818.879.659c1.171.879 3.07.879 4.242 0 1.172-.879 1.172-2.303 0-3.182C13.536 12.219 12.768 12 12 12c-2.25 0-3-1.125-3-2.25s.75-2.25 3-2.25c.768 0 1.536.219 2.121.659l.879.659" />
                            </svg>
                          </div>
                          <div>
                            <span className="block text-[13px] font-semibold text-white">Display currency</span>
                            <span className="block text-[11px] text-slate-500">
                              How amounts are shown across the dashboard.
                              {displayCurrency === "EUR" && " Values are converted from USD at a static rate of 0.92."}
                            </span>
                          </div>
                        </div>
                      </div>

                      {/* USD / EUR toggle */}
                      <div className="inline-flex flex-shrink-0 rounded-xl border border-white/[0.08] bg-white/[0.02] p-1" role="radiogroup" aria-label="Display currency">
                        {(["USD", "EUR"] as const).map((c) => {
                          const isActive = displayCurrency === c;
                          return (
                            <button
                              key={c}
                              type="button"
                              role="radio"
                              aria-checked={isActive}
                              onClick={() => setDisplayCurrency(c)}
                              className={`relative rounded-lg px-5 py-2 text-[13px] font-bold transition-all duration-200 ${
                                isActive
                                  ? "bg-[#e8a04e]/15 text-[#e8a04e] shadow-[0_0_12px_-2px_rgba(232,160,78,0.35)]"
                                  : "text-slate-500 hover:text-slate-300"
                              }`}
                            >
                              <span className="mr-1.5 tabular-nums">
                                {c === "USD" ? "$" : "€"}
                              </span>
                              {c}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  </div>

                  {/* Cost Configuration card — powers Profit Intelligence.
                      Every field is optional; empty = use module default. */}
                  <div className="mb-4 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
                    <div className="mb-4 flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-400">
                          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor" className="h-5 w-5">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 18L9 11.25l4.306 4.306a11.95 11.95 0 015.814-5.519l2.74-1.22m0 0l-5.94-2.28m5.94 2.28l-2.28 5.941" />
                          </svg>
                        </div>
                        <div>
                          <span className="block text-[13px] font-semibold text-white">Cost Configuration</span>
                          <span className="block text-[11px] text-slate-500">
                            Real costs per sale — powers Profit Intelligence precision.
                          </span>
                        </div>
                      </div>
                      {pnlData && (
                        <span
                          className={`rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] ring-1 ${
                            pnlData.precision === "exact"
                              ? "bg-emerald-500/15 text-emerald-400 ring-emerald-400/30"
                              : pnlData.precision === "refined"
                              ? "bg-amber-500/15 text-amber-400 ring-amber-400/30"
                              : "bg-white/5 text-slate-500 ring-white/10"
                          }`}
                        >
                          {pnlData.precision}
                        </span>
                      )}
                    </div>

                    <p className="mb-4 text-[11px] leading-relaxed text-slate-500">
                      Override the default cost assumptions with your real numbers. Leave any
                      field empty to keep the current default. Every saved field lifts your
                      Profit Intelligence precision from <em>rough</em> toward <em>exact</em>.
                    </p>

                    {/* 5-field form */}
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                      <label className="block">
                        <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                          COGS %
                        </span>
                        <div className="relative">
                          <input
                            type="number"
                            inputMode="decimal"
                            step="0.1"
                            min="0"
                            max="100"
                            value={costFormCogsPct}
                            onChange={(e) => setCostFormCogsPct(e.target.value)}
                            placeholder="40"
                            className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 pr-8 text-[13px] text-white tabular-nums outline-none transition-colors focus:border-emerald-400/40 focus:bg-white/[0.05]"
                          />
                          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[12px] text-slate-500">%</span>
                        </div>
                        <span className="mt-1 block text-[10px] text-slate-600">
                          Cost of goods as % of revenue
                        </span>
                      </label>

                      <label className="block">
                        <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                          Shipping per order
                        </span>
                        <div className="relative">
                          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[12px] text-slate-500">
                            {displayCurrency === "EUR" ? "€" : "$"}
                          </span>
                          <input
                            type="number"
                            inputMode="decimal"
                            step="0.01"
                            min="0"
                            value={costFormShipping}
                            onChange={(e) => setCostFormShipping(e.target.value)}
                            placeholder="5.00"
                            className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 pl-7 text-[13px] text-white tabular-nums outline-none transition-colors focus:border-emerald-400/40 focus:bg-white/[0.05]"
                          />
                        </div>
                        <span className="mt-1 block text-[10px] text-slate-600">
                          Fulfillment + carrier cost per order
                        </span>
                      </label>

                      <label className="block">
                        <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                          Ad spend / month
                        </span>
                        <div className="relative">
                          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[12px] text-slate-500">
                            {displayCurrency === "EUR" ? "€" : "$"}
                          </span>
                          <input
                            type="number"
                            inputMode="decimal"
                            step="1"
                            min="0"
                            value={costFormAdSpend}
                            onChange={(e) => setCostFormAdSpend(e.target.value)}
                            placeholder="0"
                            className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 pl-7 text-[13px] text-white tabular-nums outline-none transition-colors focus:border-emerald-400/40 focus:bg-white/[0.05]"
                          />
                        </div>
                        <span className="mt-1 block text-[10px] text-slate-600">
                          Bridge until Meta + Google Ads connect
                        </span>
                      </label>

                      <label className="block">
                        <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                          Payment %
                        </span>
                        <div className="relative">
                          <input
                            type="number"
                            inputMode="decimal"
                            step="0.01"
                            min="0"
                            max="100"
                            value={costFormPayPct}
                            onChange={(e) => setCostFormPayPct(e.target.value)}
                            placeholder="2.9"
                            className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 pr-8 text-[13px] text-white tabular-nums outline-none transition-colors focus:border-emerald-400/40 focus:bg-white/[0.05]"
                          />
                          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[12px] text-slate-500">%</span>
                        </div>
                        <span className="mt-1 block text-[10px] text-slate-600">
                          Payment processor rate (Shopify default 2.9%)
                        </span>
                      </label>

                      <label className="block">
                        <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                          Payment flat
                        </span>
                        <div className="relative">
                          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[12px] text-slate-500">
                            {displayCurrency === "EUR" ? "€" : "$"}
                          </span>
                          <input
                            type="number"
                            inputMode="decimal"
                            step="0.01"
                            min="0"
                            value={costFormPayFlat}
                            onChange={(e) => setCostFormPayFlat(e.target.value)}
                            placeholder="0.30"
                            className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 pl-7 text-[13px] text-white tabular-nums outline-none transition-colors focus:border-emerald-400/40 focus:bg-white/[0.05]"
                          />
                        </div>
                        <span className="mt-1 block text-[10px] text-slate-600">
                          Flat fee per order (Shopify default 0.30)
                        </span>
                      </label>
                    </div>

                    {/* Action row */}
                    <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div className="min-h-[1.25rem] text-[11px]">
                        {costSavedMsg && (
                          <span className={costSavedMsg.type === "ok" ? "text-emerald-400" : "text-rose-400"}>
                            {costSavedMsg.text}
                          </span>
                        )}
                        {!costSavedMsg && costSyncMsg && (
                          <span className={costSyncMsg.type === "ok" ? "text-emerald-400" : "text-rose-400"}>
                            {costSyncMsg.text}
                          </span>
                        )}
                        {!costSavedMsg && !costSyncMsg && costDefaults?.updated_at && (
                          <span className="text-slate-600">
                            Last updated {new Date(costDefaults.updated_at).toLocaleString()}
                          </span>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          onClick={handleShopifyCogsSync}
                          disabled={costSyncing}
                          title="Import real COGS from Shopify — reads inventory_items.cost for every product variant"
                          className="inline-flex items-center gap-2 rounded-lg bg-white/[0.04] px-4 py-2 text-[12px] font-semibold text-slate-300 ring-1 ring-white/10 transition-colors hover:bg-white/[0.07] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {costSyncing ? (
                            <>
                              <span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-400/40 border-t-slate-300" />
                              Importing from Shopify…
                            </>
                          ) : (
                            <>
                              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="h-3.5 w-3.5">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 13.5L12 21m0 0l-7.5-7.5M12 21V3" />
                              </svg>
                              Auto-import from Shopify
                            </>
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={handleCostDefaultsSave}
                          disabled={costSaving}
                          className="inline-flex items-center gap-2 rounded-lg bg-emerald-500/20 px-4 py-2 text-[12px] font-semibold text-emerald-300 ring-1 ring-emerald-400/30 transition-colors hover:bg-emerald-500/25 hover:text-emerald-200 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {costSaving ? (
                            <>
                              <span className="h-3 w-3 animate-spin rounded-full border-2 border-emerald-400/40 border-t-emerald-400" />
                              Saving…
                            </>
                          ) : (
                            "Save cost config"
                          )}
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* Klaviyo card */}
                  <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
                    {/* Header row: icon + name + status badge */}
                    <div className="mb-4 flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div className={`flex h-9 w-9 items-center justify-center rounded-lg ${
                          klaviyoIsConnected ? "bg-emerald-500/10 text-emerald-400" : "bg-white/5 text-slate-500"
                        }`}>
                          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-5 w-5"><path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" /></svg>
                        </div>
                        <div>
                          <span className="block text-[13px] font-semibold text-white">Klaviyo</span>
                          <span className="block text-[11px] text-slate-500">Email & SMS marketing automation</span>
                        </div>
                      </div>
                      {klaviyoStatus && (
                        <span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] ${
                          klaviyoStatus.status === "connected"
                            ? "bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-400/30"
                            : klaviyoStatus.status === "invalid_key"
                            ? "bg-red-500/15 text-red-400 ring-1 ring-red-400/30"
                            : klaviyoStatus.has_key
                            ? "bg-amber-500/15 text-amber-400 ring-1 ring-amber-400/30"
                            : "bg-white/5 text-slate-500 ring-1 ring-white/10"
                        }`}>
                          {klaviyoStatus.status === "connected" ? "Connected" :
                           klaviyoStatus.status === "invalid_key" ? "Invalid key" :
                           klaviyoStatus.status === "unverified" ? "Unverified" :
                           klaviyoStatus.status === "error" ? "Error" : "Not connected"}
                        </span>
                      )}
                    </div>

                    <div className="space-y-3">
                      {/* ---- Connected state: clean summary ---- */}
                      {klaviyoIsConnected && !klaviyoShowReplace && (
                        <div className="rounded-xl border border-emerald-400/10 bg-emerald-500/[0.04] p-4">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-3">
                              <div className="flex h-7 w-7 items-center justify-center rounded-full bg-emerald-500/15">
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="h-3.5 w-3.5 text-emerald-400"><path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" /></svg>
                              </div>
                              <div>
                                <span className="block text-[12px] font-medium text-white">Klaviyo is connected</span>
                                <span className="block text-[11px] text-slate-500">
                                  Key: <code className="font-mono text-slate-400">{klaviyoStatus?.key_hint}</code>
                                  {klaviyoStatus?.last_verified_at && (
                                    <span className="ml-1.5 text-slate-600">
                                      &middot; verified {new Date(klaviyoStatus.last_verified_at).toLocaleDateString()}
                                    </span>
                                  )}
                                </span>
                              </div>
                            </div>
                            <div className="flex items-center gap-1.5">
                              <button
                                onClick={() => setKlaviyoShowReplace(true)}
                                className="rounded-lg px-2.5 py-1.5 text-[10px] font-medium text-slate-500 transition-colors hover:bg-white/[0.05] hover:text-slate-300"
                              >
                                Replace key
                              </button>
                              <button
                                onClick={handleKlaviyoDisconnect}
                                className="rounded-lg px-2.5 py-1.5 text-[10px] font-medium text-red-400/50 transition-colors hover:bg-red-500/10 hover:text-red-400"
                              >
                                Disconnect
                              </button>
                            </div>
                          </div>

                          {/* Sync status line */}
                          {klaviyoStatus?.last_sync_at && (
                            <div className="mt-3 border-t border-emerald-400/10 pt-3">
                              <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
                                <span className="font-semibold uppercase tracking-[0.08em]">Last sync</span>
                                <span className="text-slate-400">{new Date(klaviyoStatus.last_sync_at).toLocaleString()}</span>
                                {klaviyoStatus.last_sync_error && (
                                  <span className="text-red-400/70">&middot; {klaviyoStatus.last_sync_error}</span>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      )}

                      {/* ---- Not connected / Replace key: input form ---- */}
                      {(!klaviyoIsConnected || klaviyoShowReplace) && (
                        <>
                          {/* Error/unverified states: show stored key info */}
                          {klaviyoStatus?.has_key && !klaviyoIsConnected && (
                            <div className="flex items-center gap-2 rounded-lg bg-red-500/[0.06] px-3 py-2 text-[11px] text-red-400/80">
                              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-3.5 w-3.5 flex-shrink-0"><path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" /></svg>
                              <span>{klaviyoStatus.last_error || "Key needs verification"}</span>
                            </div>
                          )}

                          <div className="flex gap-2">
                            <input
                              type="password"
                              value={klaviyoKeyInput}
                              onChange={(e) => setKlaviyoKeyInput(e.target.value)}
                              onKeyDown={(e) => { if (e.key === "Enter" && klaviyoKeyInput.trim()) handleKlaviyoConnect(); }}
                              placeholder="Paste your Klaviyo Private API Key"
                              autoComplete="off"
                              className="flex-1 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2.5 text-[12px] text-white placeholder-slate-600 outline-none transition-colors focus:border-violet-400/40 focus:ring-1 focus:ring-violet-400/20"
                            />
                            <button
                              onClick={handleKlaviyoConnect}
                              disabled={klaviyoConnecting || !klaviyoKeyInput.trim()}
                              className="rounded-lg bg-violet-500/20 px-5 py-2.5 text-[12px] font-semibold text-violet-300 transition-all hover:bg-violet-500/30 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                              {klaviyoConnecting ? "Connecting..." : "Connect"}
                            </button>
                            {klaviyoShowReplace && (
                              <button
                                onClick={() => { setKlaviyoShowReplace(false); setKlaviyoKeyInput(""); setKlaviyoMessage(null); }}
                                className="rounded-lg border border-white/[0.06] px-3 py-2.5 text-[11px] text-slate-500 transition-colors hover:bg-white/[0.03]"
                              >
                                Cancel
                              </button>
                            )}
                          </div>

                          <p className="text-[10px] leading-relaxed text-slate-600">
                            Find your Private API Key in Klaviyo: Account &rarr; Settings &rarr; API Keys.
                            Your key is encrypted at rest and never displayed after saving.
                          </p>
                        </>
                      )}

                      {/* ---- Feedback message (shared) ---- */}
                      {klaviyoMessage && (
                        <div className={`rounded-lg px-3 py-2 text-[11px] ${
                          klaviyoMessage.type === "ok"
                            ? "bg-emerald-500/10 text-emerald-400"
                            : "bg-red-500/10 text-red-400"
                        }`}>
                          {klaviyoMessage.text}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Lite tier: subtle upgrade hint (not a blocker) */}
                  {tier === "lite" && (
                    <div className="mt-4 flex items-center gap-2.5 rounded-xl border border-violet-400/10 bg-violet-500/[0.04] px-4 py-3">
                      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4 flex-shrink-0 text-violet-400/60"><path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" /></svg>
                      <span className="text-[11px] text-violet-300/70">
                        Connect now &mdash; upgrade to <button onClick={() => setUpgradeModalOpen(true)} className="font-semibold text-violet-300 underline decoration-violet-400/30 underline-offset-2 transition-colors hover:text-violet-200">Pro</button> to unlock automated flows and AI-driven actions.
                      </span>
                    </div>
                  )}

                  {/* Privacy — Art. 22 automated targeting opt-out */}
                  <div className="mb-4 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-3">
                          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-violet-500/10 text-violet-400">
                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor" className="h-5 w-5">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
                            </svg>
                          </div>
                          <div>
                            <span className="block text-[13px] font-semibold text-white">Automated targeting</span>
                            <span className="block text-[11px] text-slate-500">
                              {privacyOptedOut
                                ? "Opted out — AI scoring, nudge composition, and automated targeting are disabled for your store."
                                : "Enabled — HedgeSpark uses AI to score visitors, compose nudges, and target recommendations."}
                            </span>
                          </div>
                        </div>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={!privacyOptedOut}
                        disabled={privacyLoading}
                        onClick={handlePrivacyToggle}
                        className={`relative inline-flex h-7 w-12 flex-shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                          privacyOptedOut
                            ? "bg-white/[0.08]"
                            : "bg-violet-500/60"
                        } ${privacyLoading ? "opacity-50 cursor-wait" : ""}`}
                      >
                        <span
                          className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                            privacyOptedOut ? "translate-x-0.5" : "translate-x-[22px]"
                          }`}
                        />
                      </button>
                    </div>
                    <p className="mt-3 text-[10px] leading-relaxed text-slate-600">
                      GDPR Art. 22 &amp; CCPA §1798.120 — you can opt out of automated decision-making
                      and profiling at any time. This disables AI-powered features but does not affect
                      basic analytics. You can re-enable it whenever you want.
                    </p>
                  </div>

                  {/* Killer sprint settings: outbound webhooks + team collab (Pro only) */}
                  {isProUser && (
                    <div className="mt-4 space-y-4">
                      <ConnectToolsPanel apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                      <YourTeamPanel apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                    </div>
                  )}
              </section>

            </div>
          )}
        </main>
      </div>

      <UpgradeModal open={upgradeModalOpen} onClose={() => setUpgradeModalOpen(false)} shop={shop} trialDays={proTrialDays} price={proPrice} />

      {/* Spark toast notifications — top-right, auto-dismiss */}
      {activeToasts.length > 0 && (
        <div className="fixed right-5 top-16 z-40 flex w-80 flex-col gap-2">
          {activeToasts.map((t) => (
            <SparkToast
              key={t.id}
              notification={t}
              onDismiss={() => setActiveToasts((prev) => prev.filter((n) => n.id !== t.id))}
              onNavigate={handleNavigate}
            />
          ))}
        </div>
      )}

      {/* Trial expiry interstitial — focused conversion moment on last day */}
      {trialExpiryModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6">
          <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={() => setTrialExpiryModal(false)} />
          <div className="hs-fade-up relative z-10 w-full max-w-sm rounded-3xl border border-violet-400/20 bg-[#0d0d1e] p-8 shadow-[0_32px_80px_rgba(124,58,237,0.22)]" onClick={(e) => e.stopPropagation()}>

            {/* Close */}
            <button
              onClick={() => setTrialExpiryModal(false)}
              className="absolute right-5 top-5 rounded-lg p-1 text-slate-500 transition-colors hover:text-slate-300"
              aria-label="Close"
            >
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-5 w-5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>

            {/* Icon */}
            <div className="mb-5 flex justify-center">
              <div className="rounded-2xl bg-violet-500/15 p-3">
                <svg className="h-8 w-8 text-violet-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
            </div>

            {/* Copy */}
            <div className="mb-6 text-center">
              <h2 className="text-lg font-semibold text-white">
                {trialInfo.daysRemaining === 0
                  ? "Your Pro trial ends today"
                  : "Your Pro trial ends tomorrow"}
              </h2>
              <p className="mt-2 text-[13px] leading-relaxed text-slate-400">
                {strongSignals.length > 0
                  ? `HedgeSpark found ${strongSignals.length} revenue opportunity${strongSignals.length === 1 ? "" : "ies"} on your store. Pro automatically turns findings into fixes.`
                  : "Keep your AI actions, daily briefs, and market intelligence active."}
              </p>
            </div>

            {/* What they keep */}
            <div className="mb-6 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-600">
                What stays unlocked
              </div>
              <div className="space-y-2">
                {[
                  "AI action per finding",
                  "Full daily brief",
                  "Price & market intelligence",
                  "Revenue loss per product",
                ].map((f) => (
                  <div key={f} className="flex items-center gap-2">
                    <svg className="h-3 w-3 flex-shrink-0 text-violet-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                    </svg>
                    <span className="text-[12px] text-slate-300">{f}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* CTA */}
            <button
              onClick={handleTrialConvert}
              disabled={trialBillingLoading}
              className="w-full rounded-xl bg-violet-600 py-3 text-sm font-semibold text-white shadow-[0_0_20px_rgba(124,58,237,0.4)] transition-colors hover:bg-violet-500 active:bg-violet-700 disabled:opacity-60"
            >
              {trialBillingLoading ? "Opening Shopify billing\u2026" : `Continue with Pro \u2014 $${proPrice}/mo`}
            </button>

            {trialBillingError && (
              <p className="mt-2 text-center text-[12px] text-rose-400">{trialBillingError}</p>
            )}

            <button
              onClick={() => setTrialExpiryModal(false)}
              className="mt-3 w-full text-center text-[12px] text-slate-600 transition hover:text-slate-400"
            >
              Decide later
            </button>
          </div>
        </div>
      )}

      <KpiInsightModal activeKpi={activeKpi} summary={summary} onClose={() => setActiveKpi(null)} />
      <ProductInsightPanel
        product={activeTopProduct}
        mergedProducts={mergedProducts}
        isProUser={isProUser}
        onClose={() => setActiveTopProduct(null)}
        shopAov={data?.shop_aov ?? 50}
        shopCurrency={data?.shop_currency ?? "USD"}
        aovIsReal={data?.aov_is_real ?? false}
      />

      {/* Support chat — always visible, adapts to connection state */}
      <SupportChat
        connected={!!shop}
        onboardingHint={
          shop && data && (data.summary?.total_visitors ?? 0) > 0 && !heroRevenue
            ? "\ud83d\udca1 You\u2019re one step away from tracking revenue. Open the setup checklist above and complete the Purchase Tracking step."
            : undefined
        }
      />
    </div>
  );
}

export default function Page() {
  return (
    <DashboardErrorBoundary>
      <PageInner />
    </DashboardErrorBoundary>
  );
}
