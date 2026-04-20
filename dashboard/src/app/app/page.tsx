"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";

import { Sidebar } from "../components/Sidebar";
import { TopBar, type TrialInfo } from "../components/TopBar";
import { UpgradeModal } from "../components/UpgradeModal";
import { MascotLoader } from "../components/MascotLoader";
import { CardEmpty, CardSkeleton } from "../components/_CardStates";
import { PreviewBanner } from "../components/PreviewBanner";
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
import { RevenueHero } from "../components/RevenueHero";
import { TopSignalCard, loadRecentActions, type RecentAction } from "../components/TopSignalCard";
import { RecentActions } from "../components/RecentActions";
import { ProofHeroCard } from "../components/ProofHeroCard";
import { SystemStatusBar } from "../components/SystemStatusBar";
import { computeActions, type SparkAction } from "../lib/actionEngine";
import { updateReputation } from "../lib/sparkReputation";
import { generateNotifications, loadSettings, type SparkNotification } from "../lib/sparkNotifications";
import { SparkToast } from "../components/NotificationBell";
import { SupportChat } from "../components/SupportChat";
import { IntelligenceHero } from "../components/IntelligenceHero";
// Killer feature components (2026-04-11 sprint) — loss-framed hero + drill-downs
import { RevenueAtRiskHero } from "../components/RevenueAtRiskHero";
import { PeerBenchmarksCard } from "../components/PeerBenchmarksCard";
import { CausalWhyCard } from "../components/CausalWhyCard";
import { NightShiftCard } from "../components/NightShiftCard";
import { NightShiftTimeline } from "../components/NightShiftTimeline";
import { KpiCard } from "./_components/KpiCard";
import { SectionHeading } from "./_components/SectionHeading";
import { FunnelVisualization } from "./_components/FunnelVisualization";
import { TrafficSourceBox } from "./_components/TrafficSourceBox";
import { LiveRadarMap } from "./_components/LiveRadarMap";
import { KpiInsightModal } from "./_components/KpiInsightModal";
import { ProductInsightPanel } from "./_components/ProductInsightPanel";
import { SettingsSection } from "./_sections/SettingsSection";
import { ProIntelligenceSection } from "./_sections/ProIntelligenceSection";
import { SectionErrorBoundary } from "../components/SectionErrorBoundary";
import { BehavioralIntelligenceSection } from "./_sections/BehavioralIntelligenceSection";
import { ProductPerformanceSection } from "./_sections/ProductPerformanceSection";
import { WhatNextSection } from "./_sections/WhatNextSection";
import { SessionsSection } from "./_sections/SessionsSection";
import { SignalsSection } from "./_sections/SignalsSection";
import {
  formatNumber,
  formatScore,
  formatDecimal,
  impactClass,
} from "./_lib/formatters";
import { reportFrontendError } from "../lib/error-reporter";
import { AnomalyFusionCard } from "../components/AnomalyFusionCard";
import { AnomalyReplayCard } from "../components/AnomalyReplayCard";
import { CounterfactualExplorerCard } from "../components/CounterfactualExplorerCard";
import { CompetitorPlaybookCard } from "../components/CompetitorPlaybookCard";
import { VerticalBenchmarksCard } from "../components/VerticalBenchmarksCard";
import { AskHedgeSparkCard } from "../components/AskHedgeSparkCard";
import { IntegrationsCard } from "../components/IntegrationsCard";
import { ProductsInDecline } from "../components/ProductsInDecline";
import { MonthlyTargetsCard } from "../components/MonthlyTargetsCard";
import { MonthlyROICard } from "../components/MonthlyROICard";
import { TimelineNotes } from "../components/TimelineNotes";
import { CompareProductsCard } from "../components/CompareProductsCard";

// R-series killer features (2026-04-12)
import { RevenueAutopsyCard } from "../components/RevenueAutopsyCard";
import { AbandonedIntentCard } from "../components/AbandonedIntentCard";
import { LiveOpportunitiesCard } from "../components/LiveOpportunitiesCard";
import { VisitorIntentCard } from "../components/VisitorIntentCard";
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
// Lite-floor — first-visit tour primer
import { LiteTourPrimer } from "../components/LiteTourPrimer";
// Pro-floor — 5 migrated Intelligence cards (previously on /app/intelligence)
import { RecommendationImpactCard } from "../components/RecommendationImpactCard";
import { ChurnForecastCard } from "../components/ChurnForecastCard";
import { RiskForecastCard } from "../components/RiskForecastCard";
import { CohortSummaryCard } from "../components/CohortSummaryCard";
import { NudgeActionQueueCard } from "../components/NudgeActionQueueCard";
import {
  type DisplayCurrency,
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
  // ── Tier-based floor partition (founder directive 2026-04-20) ──
  // When the dashboard is served at /app/lite it must show ONLY the
  // 7 Lite features + Live Radar — nothing else, regardless of the
  // merchant's actual plan. All other sections (Pro-only, Scale-
  // only, narrative / legacy blocks) render only when this page is
  // served at non-Lite routes (/app/pro, /app/scale, or during
  // development at /app). Rendered content is gated, data-fetching
  // is left untouched — the state hoists are still shared across
  // routes, so the non-Lite views keep their components wired to
  // the same backend endpoints without duplication.
  const pathname = usePathname();
  const isLiteFloor =
    pathname === "/app/lite" || pathname === "/app" || pathname === null;
  const isProFloor =
    pathname === "/app/pro" || pathname === "/app/intelligence";
  // The 5 migrated Intelligence cards (live on the Pro floor, originally
  // rendered by /app/intelligence/page.tsx) get mirrored at the top of
  // the Pro-floor render here so /app/pro has a single source of truth.
  // The re-export shim at /app/pro/page.tsx now points to THIS page so
  // Pro merchants see both the 5 cards AND the rich Pro sections below.

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

  // Preview mode — `?as=starter` (or `?as=lite`) downgrades tier for
  // per-plan verification. Downgrade-only by design: we never allow
  // `?as=pro` because that would fake Pro UI + trigger 403 on every
  // Pro endpoint call for non-Pro users (the very bug the `|| true`
  // hack used to mask). Anyone can preview the Lite experience —
  // it's strictly a subset of their real view, so zero risk.
  const [isPreviewing, setIsPreviewing] = useState(false);

  // Centralized tier setter that honors the preview override. Every
  // auth/billing code path goes through this instead of raw setTier,
  // so the preview stays sticky across session refresh, billing
  // callback, and OnboardingHub readiness updates.
  const applyTier = useCallback((real: "lite" | "pro") => {
    const asParam = new URLSearchParams(window.location.search).get("as");
    const preview = asParam === "starter" || asParam === "lite";
    setTier(preview ? "lite" : real);
    setIsPreviewing(preview);
  }, []);

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
  // Pro billing config — proTrialDays is still read by the trial-countdown
  // useMemo below. proPrice was dropped when pricing was hidden for the
  // beta phase (master plan §4.2) — the authoritative price now comes from
  // Shopify's billing confirmation screen, not from dashboard copy.
  const [proTrialDays, setProTrialDays] = useState(14);
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

  // Action candidates + tasks (Pro only): the candidates list itself
  // is no longer read anywhere in the page (it used to feed a sidebar
  // panel that has been replaced). The poll cycle only needs
  // hasExecutingRef to gate network calls, so the array state is dead.
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

    // Retry /merchant/me once with a short delay before falling back.
    // This handles the transient case where the backend is mid-restart
    // (PM2 reload, build, deploy) and the first request lands in the
    // ~2-5s window where Traefik returns 502 / the fetch rejects with
    // a network error. Without retry, every deploy cycle kicks returning
    // merchants to "No store connected" for no real reason — the cookie
    // is still valid, only the backend was briefly unreachable.
    type MePayload = {
      shop_domain?: string | null;
      plan?: string | null;
      billing_active?: boolean | null;
      pro_trial_days?: number | null;
      billing_confirmed_at?: string | null;
    } | null | undefined;
    const tryMe = async (): Promise<MePayload> => {
      try {
        const res = await apiClient.GET("/merchant/me", { headers: getHeaders(apiHeaders) });
        return res.data as MePayload;
      } catch {
        return null;
      }
    };

    const bootstrapWithShop = (target: string) => {
      // Full navigation — backend sets cookie and redirects us back.
      // No state set here; the page is unloading.
      window.location.href = `${API_BASE}/auth/session?shop=${encodeURIComponent(target)}`;
    };

    // THREE sources of "which shop is this browser" — try all, use
    // whichever survives. Any one alive → auto-recovery without
    // prompting the merchant. Order by reliability:
    //   1. URL ?shop= param (freshest, set by OAuth/launch redirect)
    //   2. Parent-domain hint cookie hs_shop (survives JWT expiry,
    //      survives subdomain-scoped localStorage clears, survives
    //      browser extensions that sandbox per-subdomain storage)
    //   3. localStorage hs_last_shop (set during every successful
    //      /merchant/me — the oldest fallback)
    // Reading hint cookie from document.cookie is best-effort; we
    // never trust its VALUE for auth, only as a signal for which
    // shop to re-bootstrap. The JWT handshake at /auth/session does
    // the real authentication.
    const readHintCookie = (): string => {
      if (typeof document === "undefined") return "";
      const pairs = document.cookie.split(";");
      for (const pair of pairs) {
        const idx = pair.indexOf("=");
        if (idx === -1) continue;
        const k = pair.slice(0, idx).trim();
        if (k === "hs_shop") {
          try {
            return decodeURIComponent(pair.slice(idx + 1).trim());
          } catch {
            return "";
          }
        }
      }
      return "";
    };
    const rememberedShop = (() => {
      try {
        return localStorage.getItem("hs_last_shop") || "";
      } catch {
        return "";
      }
    })();
    const hintShop = readHintCookie();
    const urlShop = params.get("shop") || "";
    const justInstalled = params.get("installed") === "1";

    (async () => {
      // Retry /merchant/me up to 3 times total (1 initial + 2 retries)
      // with increasing delays (1.5s, 3s). The cumulative 4.5s window
      // covers the typical PM2 restart duration (wishspark-backend
      // reloads in ~2-5s during a deploy). Without these retries, any
      // returning merchant who opens the dashboard during a deploy
      // blip sees "No store connected" even though their cookie is
      // valid — the backend was just briefly unreachable.
      const retryDelaysMs = [1500, 3000];
      let json = await tryMe();
      for (const delayMs of retryDelaysMs) {
        if (json != null) break;
        await new Promise((r) => setTimeout(r, delayMs));
        json = await tryMe();
      }

      if (json && json.shop_domain) {
        const shopDomain = json.shop_domain;
        setShop(shopDomain);
        try {
          localStorage.setItem("hs_last_shop", shopDomain);
        } catch {}
        const isPro = json.plan === "pro" && json.billing_active === true;
        applyTier(isPro ? "pro" : "lite");
        if (json.pro_trial_days != null) setProTrialDays(json.pro_trial_days);
        setBillingConfirmedAt(json.billing_confirmed_at ?? null);

        if (params.get("shop")) {
          params.delete("shop");
          params.delete("installed");
          params.delete("webhook");
          params.delete("tracker");
          const cleaned = params.toString();
          window.history.replaceState({}, "", `/app${cleaned ? `?${cleaned}` : ""}`);
        }
        setSessionResolved(true);
        return;
      }

      // No valid session after retry — attempt recovery in strict order:
      //   1. If a ?shop= query is present and the URL signals a fresh
      //      install, trust it as a display hint (the cookie was just
      //      set server-side; the /merchant/me call may race the
      //      browser cookie store).
      //   2. Otherwise, if we remember a shop from localStorage or the
      //      URL carries a shop param, bootstrap via /auth/session —
      //      this navigates away and returns with a fresh cookie.
      //   3. If none of the above, render the "reconnect" UI.

      if (urlShop && justInstalled) {
        setShop(urlShop);
        try {
          const planRes = await apiClient.GET("/merchant/plan", {
            headers: getHeaders(apiHeaders),
          });
          const planJson = planRes.data;
          if (planJson != null) {
            const isPro = planJson.plan === "pro" && planJson.billing_active === true;
            applyTier(isPro ? "pro" : "lite");
            if (planJson.pro_trial_days != null) setProTrialDays(planJson.pro_trial_days);
            setBillingConfirmedAt(planJson.billing_confirmed_at ?? null);
          }
        } catch { /* tier stays lite */ }
        setSessionResolved(true);
        return;
      }

      const bootstrapTarget = urlShop || hintShop || rememberedShop;
      if (bootstrapTarget) {
        bootstrapWithShop(bootstrapTarget);
        // Don't call setSessionResolved — page is unloading.
        return;
      }

      // 4th identity source: single-tenant auto-detect. When the DB has
      // exactly one active Pro merchant (founder dev env, early beta,
      // single-merchant on-prem), /auth/detect returns it so the dash
      // can self-bootstrap without the manual reconnect form. In
      // multi-tenant production this endpoint returns 404 and we fall
      // through to the form, which is the correct UX when the shop
      // genuinely cannot be inferred.
      try {
        const detectRes = await fetch(`${API_BASE}/auth/detect`, {
          credentials: "omit",
        });
        if (detectRes.ok) {
          const detectJson = (await detectRes.json()) as { shop_domain?: string };
          if (detectJson.shop_domain) {
            bootstrapWithShop(detectJson.shop_domain);
            return;
          }
        }
      } catch {
        // Network error — fall through to manual form
      }

      setSessionResolved(true);
    })();
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
        applyTier("pro");
      }
    },
    [applyTier]
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
      .catch((err: unknown) => {
        if (!active) return;
        setBrief(null);
        const e = err as { name?: string; message?: string } | null;
        reportFrontendError({
          component: "DailyBrief",
          error_type: (e && e.name) || "BriefFetchError",
          message: (e && e.message) || "daily brief fetch failed",
          severity: "warning",
          extra: { tier },
        });
      })
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
        console.warn("[HedgeSpark] loadProductData error:", err);
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
        // /actions/candidates/pro is still polled (side-effect warms a
        // backend cache) but the response body is not consumed — the
        // dashboard no longer renders a candidates list.
        const [, taskRes] = await Promise.all([
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

    loadProIntelligence().catch((err: unknown) => {
      // Pro intelligence bundle failure — the individual fulfilled slots
      // still render whatever they got, but a blanket failure before
      // Promise.allSettled completes is worth reporting so the self-
      // healing pipeline can pick it up.
      const e = err as { name?: string; message?: string } | null;
      reportFrontendError({
        component: "loadProIntelligence",
        error_type: e?.name ?? "FetchError",
        message: e?.message ?? "Pro intelligence bundle failed",
        severity: "warning",
      });
    });
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
    // (URL string was previously stored in alertsEndpoint but is now
    // built directly inside the typed apiClient call below.)

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
  // Feature gating — derived from plan tier (from /merchant/plan response,
  // lines 688 / 734 above). tier="pro" iff plan === "pro" AND billing_active.
  // Any other state (plan="starter", billing_active=false, or fetch failure)
  // → tier="lite" → non-Pro UI branches.
  // ---------------------------------------------------------------------------
  const isProUser = tier === "pro";

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
      .catch((err: unknown) => {
        const e = err as { name?: string; message?: string } | null;
        reportFrontendError({
          component: "CostConfig",
          error_type: (e && e.name) || "CostConfigFetchError",
          message: (e && e.message) || "cost config fetch failed",
          severity: "info",
        });
      });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shop]);

  // Fetch privacy preferences (Art. 22 opt-out state)
  useEffect(() => {
    if (!shop) return;
    let active = true;
    apiClient
      .GET("/merchant/privacy/preferences")
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      .then(({ data: d }) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const prefs = d as any;
        if (active && prefs && prefs.opt_out_automated_targeting != null) {
          setPrivacyOptedOut(!!prefs.opt_out_automated_targeting);
        }
      })
      .catch((err: unknown) => {
        // Privacy preferences are a first-class GDPR surface — we want
        // observability on failures so a broken consent endpoint gets
        // caught by the self-healing pipeline, not eaten silently.
        const e = err as { name?: string; message?: string } | null;
        reportFrontendError({
          component: "privacyPreferences",
          error_type: e?.name ?? "FetchError",
          message: e?.message ?? "Failed to fetch /merchant/privacy/preferences",
          severity: "warning",
        });
      });
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
      .catch((err: unknown) => {
        const e = err as { name?: string; message?: string } | null;
        reportFrontendError({
          component: "KlaviyoStatus",
          error_type: (e && e.name) || "IntegrationsStatusError",
          message: (e && e.message) || "klaviyo status fetch failed",
          severity: "info",
        });
      });
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
      const testData = testRes.ok ? await testRes.json().catch(() => ({})) : {};

      // Step 3: Refresh status
      const sRes = await apiFetch(`${API_BASE}/merchant/integrations`);
      const s = sRes.ok ? await sRes.json().catch(() => ({})) : {};
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
      const json = await res.json().catch(() => ({}));
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

  // taskKey + parseResultDetail were used by the deleted action
  // candidates panel and have no remaining callers. Removed along
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
    const notifs = generateNotifications(sparkActions, settings, resolvedCcy);
    if (notifs.length > 0) setActiveToasts(notifs);
  }, [sparkActions]);

  // sparkContext, executeCandidate, dismissTask were used by the
  // deleted action-candidates panel and the SparkInline companion
  // message. None of them are referenced by the current rendering
  // tree. Removed in cycle 13 cleanup along with their state.

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
      <PreviewBanner isPreviewing={isPreviewing} />
      <ProductTour isProUser={isProUser} />
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((c) => !c)}
        activeSection={activeSection}
        onNavigate={handleNavigate}
        tier={tier}
        currentFloor="pulse"
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
                {/* Diagnostic — only rendered when this banner is live.
                    Reveals exactly which of the three identity sources
                    was empty so the "why did this banner appear?"
                    question can be answered at a glance, not via
                    DevTools. */}
                <details className="mt-4 text-left text-[10px] font-mono text-amber-200/40">
                  <summary className="cursor-pointer select-none hover:text-amber-200/70">
                    Diagnostic (click to expand)
                  </summary>
                  <div className="mt-2 space-y-0.5 rounded border border-amber-400/10 bg-black/30 p-2">
                    <div>
                      localStorage.hs_last_shop:{" "}
                      <span className="text-amber-200/70">
                        {(() => {
                          try {
                            return localStorage.getItem("hs_last_shop") || "(empty)";
                          } catch {
                            return "(blocked)";
                          }
                        })()}
                      </span>
                    </div>
                    <div>
                      cookie hs_shop hint:{" "}
                      <span className="text-amber-200/70">
                        {(() => {
                          if (typeof document === "undefined") return "(n/a)";
                          const m = document.cookie.match(/(?:^|;\s*)hs_shop=([^;]+)/);
                          return m ? decodeURIComponent(m[1]) : "(empty)";
                        })()}
                      </span>
                    </div>
                    <div>
                      URL ?shop=:{" "}
                      <span className="text-amber-200/70">
                        {typeof window !== "undefined"
                          ? new URLSearchParams(window.location.search).get("shop") || "(empty)"
                          : "(n/a)"}
                      </span>
                    </div>
                    <div>
                      navigator.cookieEnabled:{" "}
                      <span className="text-amber-200/70">
                        {typeof navigator !== "undefined" && navigator.cookieEnabled ? "yes" : "NO"}
                      </span>
                    </div>
                    <div>
                      API_BASE:{" "}
                      <span className="text-amber-200/70">{API_BASE || "(empty)"}</span>
                    </div>
                    <div className="mt-1 text-amber-200/50">
                      If all three identity sources are empty your
                      browser has no memory of this store. Type the
                      domain above to reconnect — HedgeSpark will
                      remember it on future visits.
                    </div>
                  </div>
                </details>
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
            <div className="space-y-10 px-6 py-8 pb-[70vh]">

              {/* ═══ PRO FLOOR HEADER — 5 migrated Intelligence cards ═══
                  Only renders when this page is served at /app/pro. These
                  five cards were previously the entirety of the Intelligence
                  floor at /app/intelligence; they now live at the top of the
                  Pro floor so the rich Pro sections below (AudienceSegments,
                  NudgePerformance, LiftReport, ProIntelligenceSection, etc.)
                  sit under the same shell. */}
              {isProFloor && isProUser && shop && (
                <>
                  <div className="mb-6">
                    <div className="text-[11px] font-bold uppercase tracking-[0.2em] text-[#e8a04e]">
                      Floor · Pro
                    </div>
                    <h1 className="mt-3 text-[2rem] font-extrabold leading-[1.1] text-white sm:text-[2.5rem]">
                      Deep analytics. Every number defended.
                    </h1>
                  </div>
                  <RecommendationImpactCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <ChurnForecastCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <RiskForecastCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <CohortSummaryCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <NudgeActionQueueCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                </>
              )}

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

              {/* System health warning — shown when backend is degraded.
                  Softened visual per founder directive 2026-04-20: the
                  old amber "⚠" banner felt alarming; this one stays
                  honest (shows the issues) but reads as a status note,
                  not an emergency. */}
              {systemHealthIssues.length > 0 && (
                <div className="mb-3 flex items-start gap-3 rounded-2xl border border-amber-400/15 bg-gradient-to-br from-amber-500/[0.04] to-transparent px-5 py-3.5">
                  <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-amber-500/10 text-amber-300">
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={1.8}
                      className="h-4 w-4"
                      aria-hidden="true"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m0-10.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.75c0 5.592 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.75h-.152c-3.196 0-6.1-1.248-8.25-3.286zm0 13.036h.008v.008H12v-.008z" />
                    </svg>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-bold text-amber-200">
                        Some signals are catching up
                      </span>
                      <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.1em] text-amber-300">
                        Degraded
                      </span>
                    </div>
                    <div className="mt-0.5 text-[11.5px] leading-relaxed text-amber-200/70">
                      {systemHealthIssues.slice(0, 3).join(" · ")}. Your data
                      is safe — we&apos;re refreshing it in the background.
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
                totalVisitors={data ? (data.summary?.total_visitors ?? 0) : null}
                signalCount={strongSignals.length > 0 ? strongSignals.length : (data ? 0 : null)}
              />

              {/* ═══ TODAY — 3 THINGS TO DO ═══
                  Lite-floor killer strip per founder directive 2026-04-20.
                  Transforms /app/lite from diagnostic ("what's happening?")
                  to prescriptive ("what should I do?"). Pulls from the
                  existing sparkActions decision engine — the 3 highest-
                  priority actions the merchant can execute manually today.
                  Always renders on Lite; empty-state says "All clear —
                  Spark is watching" so the strip never disappears. */}
              {/* ═══ LITE TOUR — "What am I looking at?" primer ═══
                  Founder-flagged 2026-04-20: warm tone works, but a
                  first-time merchant stares at the dashboard and gets
                  "a couple of nice tables, zero comprehension". This
                  guide panel gives a 30-second map of the 7 Lite
                  sections with plain-language "what / why" per row.
                  Dismissed persistently via localStorage so repeat
                  visitors don't see it. Never auto-hide on scroll —
                  that's user-hostile. */}
              {isLiteFloor && shop && <LiteTourPrimer />}

              {isLiteFloor && (
                <section
                  aria-labelledby="today-actions-heading"
                  className="relative mb-8 overflow-hidden rounded-3xl border border-white/[0.06] bg-gradient-to-br from-[#0e0a1a] via-[#0a0a14] to-[#0b0c18] p-7 sm:p-9"
                >
                  {/* Brand gradient stripe top — the visual signature we
                      reserve for premium zones. Anchors "this is worth
                      your attention" before reading a single word. */}
                  <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-[#7c3aed] via-[#c026d3] to-[#e8a04e]" />
                  {/* Ambient corner glow — violet + amber wash, soft
                      enough to feel like depth, not decoration. */}
                  <div className="pointer-events-none absolute -right-32 -top-32 h-[380px] w-[380px] rounded-full bg-[#c026d3]/[0.05] blur-[160px]" />
                  <div className="pointer-events-none absolute -left-24 -bottom-32 h-[320px] w-[320px] rounded-full bg-[#e8a04e]/[0.04] blur-[140px]" />

                  <div className="relative">
                    {/* Unified heading: ONE big amber H2 (time-aware).
                        Live-dot indicator stays as a small meta in the
                        top-right — slate, not amber. No separate amber
                        eyebrow above the title. */}
                    <div className="mb-2 flex flex-wrap items-start justify-between gap-3">
                      <h1
                        id="today-actions-heading"
                        className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight text-[#e8a04e] sm:text-[2rem]"
                      >
                        {(() => {
                          const h = new Date().getHours();
                          const when =
                            h < 12 ? "this morning"
                              : h < 17 ? "this afternoon"
                                : h < 21 ? "this evening"
                                  : "right now";
                          if (sparkActions.length === 0) {
                            return "Nothing urgent — I'm watching.";
                          }
                          if (sparkActions.length === 1) {
                            return `I found 1 thing worth your time ${when}.`;
                          }
                          return `I found ${Math.min(3, sparkActions.length)} things worth your time ${when}.`;
                        })()}
                      </h1>
                      <span className="mt-1 inline-flex flex-shrink-0 items-center gap-1.5 text-[11px] font-semibold text-slate-500">
                        <span className="relative flex h-1.5 w-1.5" aria-hidden="true">
                          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/50" />
                          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
                        </span>
                        Ranked by impact · live
                      </span>
                    </div>
                    <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-slate-400">
                      {sparkActions.length === 0
                        ? "No urgent actions right now. Your store is running fine. New recommendations appear the moment a signal crosses threshold — no need to refresh."
                        : "Each card below is one signal + one action. Tackle them in order, skip what you can't do today — I'll re-rank tomorrow from your real data."}
                    </p>

                    {/* Action cards — or empty-state */}
                    {sparkActions.length === 0 ? (
                      <div className="mt-7 flex items-center gap-4 rounded-2xl border border-emerald-400/15 bg-emerald-500/[0.04] p-5">
                        <div className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-xl bg-emerald-500/10">
                          <svg
                            xmlns="http://www.w3.org/2000/svg"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth={1.8}
                            className="h-6 w-6 text-emerald-300"
                            aria-hidden="true"
                          >
                            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12c0 1.268-.63 2.39-1.593 3.068a3.745 3.745 0 01-1.043 3.296 3.745 3.745 0 01-3.296 1.043A3.745 3.745 0 0112 21c-1.268 0-2.39-.63-3.068-1.593a3.746 3.746 0 01-3.296-1.043 3.745 3.745 0 01-1.043-3.296A3.745 3.745 0 013 12c0-1.268.63-2.39 1.593-3.068a3.745 3.745 0 011.043-3.296 3.746 3.746 0 013.296-1.043A3.746 3.746 0 0112 3c1.268 0 2.39.63 3.068 1.593a3.746 3.746 0 013.296 1.043 3.746 3.746 0 011.043 3.296A3.745 3.745 0 0121 12z" />
                          </svg>
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="text-[13px] font-bold text-emerald-200">
                            Nothing needs you right now
                          </div>
                          <div className="mt-0.5 text-[12px] leading-relaxed text-slate-400">
                            I haven&apos;t seen any signal above threshold
                            yet. Either your store is quiet, or it&apos;s
                            warming up. I&apos;ll flag the first thing that
                            actually deserves your attention — no busy-work.
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="mt-7 grid gap-4 md:grid-cols-3">
                        {sparkActions.slice(0, 3).map((action, i) => {
                          const priorityTheme =
                            action.priority === "CRITICAL"
                              ? { color: "#f43f5e", label: "Now", bg: "rgba(244,63,94,0.08)" }
                              : action.priority === "HIGH"
                                ? { color: "#e8a04e", label: "Soon", bg: "rgba(232,160,78,0.08)" }
                                : { color: "#94a3b8", label: "When you can", bg: "rgba(148,163,184,0.05)" };
                          // Remap product-performance → section-hot (Lite-
                          // visible). Any other hidden targetSection falls
                          // back to section-hot since that's where the
                          // merchant sees the products the action refers to.
                          const liteVisibleSections = new Set([
                            "brief",
                            "hot",
                            "live",
                            "opportunities",
                            "visitors",
                            "abandonment",
                          ]);
                          const liteTarget = liteVisibleSections.has(action.targetSection)
                            ? action.targetSection
                            : "hot";
                          return (
                            <div
                              key={action.id}
                              className="group relative flex flex-col overflow-hidden rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.03] to-white/[0.01] p-5 transition-all hover:border-white/[0.16] hover:shadow-[0_8px_32px_rgba(232,160,78,0.06)]"
                            >
                              {/* Rank mark — dramatic tabular numeral in
                                  the corner, a visual signature. */}
                              <div
                                className="pointer-events-none absolute -right-2 -top-4 text-[84px] font-extrabold leading-none tabular-nums text-white/[0.03] sm:text-[96px]"
                                aria-hidden="true"
                              >
                                {i + 1}
                              </div>

                              {/* Priority + impact chip row */}
                              <div className="relative mb-3 flex items-center gap-2">
                                <span
                                  className="rounded-full px-2.5 py-1 text-[9.5px] font-bold uppercase tracking-[0.1em]"
                                  style={{
                                    color: priorityTheme.color,
                                    background: priorityTheme.bg,
                                    border: `1px solid ${priorityTheme.color}33`,
                                  }}
                                >
                                  {priorityTheme.label}
                                </span>
                                {action.impactValue > 0 && (
                                  <span className="text-[10.5px] font-bold text-emerald-300 tabular-nums">
                                    {action.impact}
                                  </span>
                                )}
                              </div>

                              {/* Title — the "what" */}
                              <div className="relative text-[15px] font-extrabold leading-snug text-white">
                                {action.title}
                              </div>

                              {/* Action body — the "how", plain language */}
                              <div className="relative mt-2 text-[12px] leading-relaxed text-slate-400">
                                {action.action}
                              </div>

                              {/* CTA — only show if we have a Lite-visible
                                  target. Uses brand gradient underline on
                                  hover for a touch of originality. */}
                              <button
                                type="button"
                                onClick={() => handleNavigate(liteTarget)}
                                className="relative mt-4 inline-flex items-center gap-1 self-start rounded-lg border border-[#e8a04e]/25 bg-[#e8a04e]/[0.06] px-3 py-1.5 text-[11.5px] font-bold text-[#e8a04e] transition-colors hover:bg-[#e8a04e]/[0.14]"
                              >
                                Show me
                                <span className="inline-block transition-transform group-hover:translate-x-0.5">
                                  →
                                </span>
                              </button>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </section>
              )}

              {/* ═══ INTELLIGENCE HERO — decision-first: what matters most right now ═══ */}
              {!isLiteFloor && (
                <IntelligenceHero
                  connected={!!shop}
                  isProUser={isProUser}
                  onUpgrade={() => setUpgradeModalOpen(true)}
                />
              )}

              {/* ═══ REVENUE HERO ═══ */}
              {!isLiteFloor && (
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
              )}

              {/* ═══ INSTANT INTELLIGENCE — 60s aha moment (α3) ═══ */}
              {isProUser && !isLiteFloor && <InstantIntelligenceCard apiBase={API_BASE} />}

              {/* ═══ ROI HERO BANNER — the retention weapon (α2) ═══ */}
              {!isLiteFloor && (
                <div data-tour="roi-hero">
                  <ROIHeroBanner apiBase={API_BASE} isProUser={isProUser} />
                </div>
              )}

              {/* ═══ DAILY NARRATIVE — storytelling block (α7) ═══ */}
              {!isLiteFloor && <DailyNarrativeBlock apiBase={API_BASE} isProUser={isProUser} />}

              {/* ═══ REVENUE AT RISK HERO — the new #1 headline ═══ */}
              <RevenueAtRiskHero
                apiBase={API_BASE}
                shop={shop}
                isProUser={isProUser}
                onUpgrade={() => setUpgradeModalOpen(true)}
              />

              {/* ═══ TRUST CONTROL CENTER — delegated autonomy (α1) ═══ */}
              {!isLiteFloor && (
                <div data-tour="trust-center">
                  <TrustControlCenter apiBase={API_BASE} isProUser={isProUser} />
                </div>
              )}

              {/* ═══ UNIT ECONOMICS + PROFIT HEADROOM + FORECAST (β1+β3+α6) ═══ */}
              {isProUser && !isLiteFloor && (
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
              {!isLiteFloor && <CustomerChurnCard apiBase={API_BASE} isProUser={isProUser} />}

              {/* ═══ NUDGE DNA — what words actually sell (δ5) ═══ */}
              {!isLiteFloor && <NudgeDnaCard apiBase={API_BASE} isProUser={isProUser} />}

              {/* ═══ WHAT BRINGS THE SALE — multi-touch attribution (β2) ═══ */}
              {!isLiteFloor && (
                <div data-tour="mta">
                  <MtaCompareCard apiBase={API_BASE} isProUser={isProUser} />
                </div>
              )}

              {/* ═══ AUTOMATION RULES — low-code (ζ2) ═══ */}
              {!isLiteFloor && <RuleBuilderCard apiBase={API_BASE} isProUser={isProUser} />}

              {/* ═══ REVENUE GENOME — the DNA of your revenue ═══ */}
              {isProUser && !isLiteFloor && (
                <RevenueGenomeCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
              )}

              {/* ═══ PHASE Ω⁵ — NIGHT SHIFT AGENT (morning reveal) ═══ */}
              {isProUser && !isLiteFloor && (
                <NightShiftCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
              )}

              {/* ═══ NIGHT SHIFT TIMELINE — proof-of-work for the autonomous loop ═══ */}
              {isProUser && !isLiteFloor && (
                <NightShiftTimeline apiBase={API_BASE} shop={shop} isProUser={isProUser} />
              )}

              {/* ═══ PHASE Ω — THE WHY ENGINE + ANOMALY RADAR (causal layer) ═══ */}
              {isProUser && !isLiteFloor && (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <CausalWhyCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <AnomalyFusionCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                </div>
              )}

              {/* ═══ PHASE Ω⁷ — THE UNREACHABLE THREE ═══ */}
              {isProUser && !isLiteFloor && (
                <AnomalyReplayCard apiBase={API_BASE} isProUser={isProUser} />
              )}
              {isProUser && !isLiteFloor && (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <CounterfactualExplorerCard apiBase={API_BASE} isProUser={isProUser} />
                  <CompetitorPlaybookCard apiBase={API_BASE} isProUser={isProUser} />
                </div>
              )}

              {/* ═══ PHASE Ω — ASK HEDGE SPARK (knowledge graph NL query) ═══ */}
              {isProUser && !isLiteFloor && (
                <AskHedgeSparkCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
              )}

              {/* ═══ KILLER FEATURE GRID — drill-downs from the RARS hero ═══ */}
              {isProUser && !isLiteFloor && (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <VerticalBenchmarksCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <PeerBenchmarksCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <ProductsInDecline apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <MonthlyTargetsCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <MonthlyROICard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <IntegrationsCard apiBase={API_BASE} isProUser={isProUser} />
                </div>
              )}

              {/* ═══ ABANDONED INTENT — Lite-accessible (Phase 1.4)
                  Backend returns reduced-fidelity for Lite (top 3
                  products + empty session_insights). Component handles
                  the bridge to Pro upgrade when isProUser=false. ═══ */}
              <AbandonedIntentCard
                apiBase={API_BASE}
                shop={shop}
                isProUser={isProUser}
                onUpgrade={() => setUpgradeModalOpen(true)}
              />

              {/* ═══ LIVE OPPORTUNITIES — Lite-accessible (Phase 1.5)
                  Backend `/analytics/live-opportunities` is Lite-accessible
                  by design (no plan gating). Same data to Pro and Lite —
                  the moat isn't the data, it's the AI nudge composer
                  that auto-deploys the recommended action (Pro moat).
                  Lite merchants read recommended_action and act manually. ═══ */}
              <SectionErrorBoundary name="Live Opportunities">
                <LiveOpportunitiesCard apiBase={API_BASE} shop={shop} />
              </SectionErrorBoundary>

              {/* ═══ VISITOR INTENT — Lite-accessible (Phase 1.6)
                  Three counts: Hot / Warm / Cold visitors classified
                  by conversion_score thresholds. Backend computes the
                  classification server-side with tenant isolation.
                  Lite sees the 3 counts; Pro unlocks the ranked per-
                  visitor drill-down via /visitor-scores. ═══ */}
              <SectionErrorBoundary name="Visitor Intent">
                <VisitorIntentCard
                  apiBase={API_BASE}
                  shop={shop}
                  isProUser={isProUser}
                  onUpgrade={() => setUpgradeModalOpen(true)}
                />
              </SectionErrorBoundary>

              {/* ═══ DEEP INTELLIGENCE GRID — Pro-only moat features ═══ */}
              {isProUser && !isLiteFloor && (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <RevenueAutopsyCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <PriceSensitivityCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                  <CausalLiftCard apiBase={API_BASE} shop={shop} isProUser={isProUser} />
                </div>
              )}

              {/* ═══ SECONDARY WIDGETS — smaller but useful ═══ */}
              {isProUser && !isLiteFloor && (
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
              <SectionErrorBoundary name="Daily Brief">
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
              </SectionErrorBoundary>

              {!isLiteFloor && (
                <>
              {/* ── Level 2 separator ── */}
              <div className="h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />

              {/* 2 — Store pulse */}
              <SectionErrorBoundary name="Store Pulse">
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
              </SectionErrorBoundary>

              {/* Real orders / revenue section */}
              <SectionErrorBoundary name="Revenue">
              <section id="section-revenue">
                <OrdersSummary apiBase={API_BASE} shop={shop} displayCurrency={displayCurrency} />
                <ProductConversions apiBase={API_BASE} shop={shop} />
              </section>
              </SectionErrorBoundary>

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

              {/* 3 — Signals/Findings (extracted to _sections/SignalsSection.tsx) */}
              <SectionErrorBoundary name="Signals">
                <SignalsSection
                  alerts={alerts}
                  strongSignals={strongSignals}
                  earlySignals={earlySignals}
                  isColdStart={isColdStart}
                />
              </SectionErrorBoundary>
                </>
              )}

              {/* ═══ HOT PRODUCTS — 3 cards per row ═══
                  Phase 1.3: always render the section header + CardEmpty
                  during cold start so Lite/Lite merchants see the
                  dashboard slot (not a silent gap). TrafficSourceBox
                  companion stays visible with its own empty-state
                  handling.
                  Phase 1.4-bis: gate the empty-state on `!loading` so
                  SSR/initial-render doesn't flash the "Warming up" copy
                  to Pro merchants with real data during the fetch
                  window. During load → CardSkeleton. After load → real
                  state (grid or CardEmpty). */}
              <SectionErrorBoundary name="Hot Products">
              <section className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-6 sm:p-8">
                <SectionHeading eyebrow="Hot Products — where buyers are active" title="" />

                {loading ? (
                  <CardSkeleton label="Loading hot products" />
                ) : topProducts.length > 0 ? (
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
                ) : (
                  <CardEmpty
                    title={coldStartPhase <= 1 ? "Warming up" : "No hot products yet this week"}
                    body={
                      coldStartPhase <= 1
                        ? "Your first visitors will populate this list. Each product here shows views, unique visitors, and the intent score HedgeSpark assigns to them."
                        : "No products have crossed the intent threshold in the last 7 days. This will update automatically as traffic patterns change."
                    }
                    eta={coldStartPhase <= 1 ? "Populates within ~5 minutes of your first visitor" : undefined}
                    accent="amber"
                  />
                )}

                {/* Traffic source companion — renders for both
                    populated + empty states so the "where traffic
                    comes from" narrative never disappears. */}
                <div className="mt-3">
                  <TrafficSourceBox
                    sourceQuality={sourceQuality}
                    isProUser={isProUser}
                    onUpgradeClick={() => setUpgradeModalOpen(true)}
                  />
                </div>
              </section>
              </SectionErrorBoundary>

              {!isLiteFloor && (
                <>
              {/* 4 — Product Performance (extracted to _sections/ProductPerformanceSection.tsx) */}
              {mergedProducts.length > 0 && (
                <SectionErrorBoundary name="Product Performance">
                  <ProductPerformanceSection
                    mergedProducts={mergedProducts}
                    isProUser={isProUser}
                    resolvedCcy={resolvedCcy}
                    resolvedAov={resolvedAov}
                    resolvedAovIsReal={resolvedAovIsReal}
                    shortUrl={shortUrl}
                    setUpgradeModalOpen={setUpgradeModalOpen}
                  />
                </SectionErrorBoundary>
              )}

              {/* 5 — WHAT TO DO NEXT (extracted to _sections/WhatNextSection.tsx) */}
              {sparkActions.length > 0 && (
                <SectionErrorBoundary name="What to do next">
                  <WhatNextSection
                    sparkActions={sparkActions}
                    isProUser={isProUser}
                    displayCurrency={displayCurrency}
                    setUpgradeModalOpen={setUpgradeModalOpen}
                  />
                </SectionErrorBoundary>
              )}

              {/* 6 — Weekly Trend */}
              <SectionErrorBoundary name="Weekly Trend">
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
              </SectionErrorBoundary>

              {/* 7 — Top Pages */}
              <SectionErrorBoundary name="Top Pages">
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
              </SectionErrorBoundary>

              {/* 8 — Conversion Funnel (Pro) */}
              <SectionErrorBoundary name="Funnel">
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
              </SectionErrorBoundary>

              {/* 9 — Sessions + Clicks (extracted to _sections/SessionsSection.tsx) */}
              <SectionErrorBoundary name="Sessions & Clicks">
                <SessionsSection
                  isProUser={isProUser}
                  sessions={sessions}
                  clicks={clicks}
                  formatDuration={formatDuration}
                  shortUrl={shortUrl}
                />
              </SectionErrorBoundary>
                </>
              )}

              {/* 10 — Live Radar + World Map */}
              <SectionErrorBoundary name="Live Radar">
              <section id="section-live" className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-6 sm:p-8">
                <SectionHeading
                  eyebrow="Live Radar — right now in your store"
                  title=""
                />
                <LiveRadarMap
                  visitors={liveVisitors}
                  radarPositions={RADAR_POSITIONS}
                  coldStartPhase={coldStartPhase}
                />
              </section>
              </SectionErrorBoundary>

              {/* ═══ SYSTEM STATUS BAR — intelligence progress + aliveness ═══
                  Moved here (bottom of the floor) per founder directive
                  2026-04-20: the "Running autonomously" heartbeat belongs
                  at the FOOT of the scroll as a proof-of-work signature,
                  not at the top competing with the Revenue-at-Risk hero.
                  Renders on both Lite and Pro floors — the merchant always
                  knows Spark is alive and working. */}
              <SystemStatusBar apiBase={API_BASE} shop={shop} />


              {/* ═══ PRO ZONE SEPARATOR ═══ */}
              {!isProUser && !isLiteFloor && (
                <div className="relative overflow-hidden rounded-3xl border border-[#d4893a]/15 bg-gradient-to-br from-[#d4893a]/[0.04] via-transparent to-[#7c3aed]/[0.03] p-8 sm:p-10">
                  <div className="absolute inset-x-0 top-0 h-[3px] bg-gradient-to-r from-[#7c3aed] via-[#c026d3] to-[#f97316]" />
                  <div className="pointer-events-none absolute -right-20 -top-20 h-[300px] w-[300px] rounded-full bg-[#d4893a]/[0.06] blur-[120px]" />
                  <div className="relative">
                    <h2 className="text-[1.75rem] font-extrabold text-white sm:text-[2rem]">
                      Unlock <span className="hs-brand-gradient">Deep analytics</span>
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
                      className="hs-cta-gradient mt-8 rounded-2xl px-8 py-3.5 text-[16px] font-bold text-white shadow-[0_0_30px_rgba(232,160,78,0.25)] transition-all hover:shadow-[0_0_40px_rgba(232,160,78,0.35)] focus:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220]"
                    >
                      Upgrade to Pro
                    </button>
                  </div>
                </div>
              )}

              {/* Deep analytics narrative hero — sets up the story the merchant
                  is about to read across the sections below. Designed to pass
                  the "stupid test": zero jargon, plain English, answers
                  "what am I looking at?" in one glance. */}
              {isProUser && !isLiteFloor && (
                <div className="relative overflow-hidden rounded-3xl border border-white/[0.06] bg-gradient-to-br from-white/[0.025] via-transparent to-white/[0.02] px-6 py-7 sm:px-10 sm:py-9">
                  {/* Ambient brand gradient stripe on top */}
                  <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-[#7c3aed] via-[#c026d3] to-[#f97316]" />
                  {/* Subtle glow */}
                  <div className="pointer-events-none absolute -right-24 -top-24 h-[320px] w-[320px] rounded-full bg-[#d946ef]/[0.04] blur-[140px]" />

                  <div className="relative">
                    <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.03] px-3 py-1">
                      <span className="h-1.5 w-1.5 rounded-full bg-[#e8a04e] shadow-[0_0_8px_rgba(232,160,78,0.6)]" />
                      <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-300">
                        Deep analytics
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
              {isProUser && !isLiteFloor && (
                <SectionErrorBoundary name="Audience Segments">
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
                </SectionErrorBoundary>
              )}

              {/* Pro — Nudge Performance */}
              {isProUser && !isLiteFloor && (
                <SectionErrorBoundary name="Nudge Performance">
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
                </SectionErrorBoundary>
              )}

              {/* Pro — Holdout Lift Report */}
              {isProUser && !isLiteFloor && (
                <SectionErrorBoundary name="Holdout Lift">
                <section id="section-lift">
                  <SectionHeading
                    eyebrow="Proof"
                    title="Did it actually work?"
                    pro
                  />
                  <LiftReport apiBase={API_BASE} shop={shop} apiHeaders={apiHeaders} displayCurrency={displayCurrency} />
                </section>
                </SectionErrorBoundary>
              )}

              {/* Pro — Scroll Intelligence + Cohort Retention side by side */}
              {isProUser && !isLiteFloor && (
                <SectionErrorBoundary name="Scroll & Cohorts">
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
                </SectionErrorBoundary>
              )}

              {/* Pro — Revenue Forecast + Attribution + LTV Intelligence */}
              {isProUser && !isLiteFloor && (
                <SectionErrorBoundary name="Deep analytics">
                  <ProIntelligenceSection
                    apiBase={API_BASE}
                    displayCurrency={displayCurrency}
                    forecastData={forecastData}
                    attrSummary={attrSummary}
                    ltvData={ltvData}
                    pnlData={pnlData}
                    gatewayProductsData={gatewayProductsData}
                    predictedLtvData={predictedLtvData}
                    priceIntel={priceIntel}
                    marketIntel={marketIntel}
                  />
                </SectionErrorBoundary>
              )}

              {/* Pro — Behavioral DNA (extracted to _sections/BehavioralIntelligenceSection.tsx) */}
              {isProUser && !isLiteFloor && behavioralData && (
                <SectionErrorBoundary name="Behavioral DNA">
                  <BehavioralIntelligenceSection
                    data={behavioralData}
                    displayCurrency={displayCurrency}
                  />
                </SectionErrorBoundary>
              )}

              {/* 11 — Settings / Integrations (all tiers) — extracted to _sections/SettingsSection.tsx */}
              {!isLiteFloor && (
                <SectionErrorBoundary name="Settings & Integrations">
                  <SettingsSection
                    apiBase={API_BASE}
                    shop={shop}
                    tier={tier}
                    isProUser={isProUser}
                    displayCurrency={displayCurrency}
                    setDisplayCurrency={setDisplayCurrency}
                    costDefaults={costDefaults}
                    costFormCogsPct={costFormCogsPct}
                    setCostFormCogsPct={setCostFormCogsPct}
                    costFormShipping={costFormShipping}
                    setCostFormShipping={setCostFormShipping}
                    costFormAdSpend={costFormAdSpend}
                    setCostFormAdSpend={setCostFormAdSpend}
                    costFormPayPct={costFormPayPct}
                    setCostFormPayPct={setCostFormPayPct}
                    costFormPayFlat={costFormPayFlat}
                    setCostFormPayFlat={setCostFormPayFlat}
                    costSaving={costSaving}
                    costSavedMsg={costSavedMsg}
                    costSyncing={costSyncing}
                    costSyncMsg={costSyncMsg}
                    pnlData={pnlData}
                    handleCostDefaultsSave={handleCostDefaultsSave}
                    handleShopifyCogsSync={handleShopifyCogsSync}
                    klaviyoStatus={klaviyoStatus}
                    klaviyoIsConnected={klaviyoIsConnected}
                    klaviyoKeyInput={klaviyoKeyInput}
                    setKlaviyoKeyInput={setKlaviyoKeyInput}
                    klaviyoConnecting={klaviyoConnecting}
                    klaviyoShowReplace={klaviyoShowReplace}
                    setKlaviyoShowReplace={setKlaviyoShowReplace}
                    klaviyoMessage={klaviyoMessage}
                    setKlaviyoMessage={setKlaviyoMessage}
                    handleKlaviyoConnect={handleKlaviyoConnect}
                    handleKlaviyoDisconnect={handleKlaviyoDisconnect}
                    privacyOptedOut={privacyOptedOut}
                    privacyLoading={privacyLoading}
                    handlePrivacyToggle={handlePrivacyToggle}
                    setUpgradeModalOpen={setUpgradeModalOpen}
                  />
                </SectionErrorBoundary>
              )}

            </div>
          )}
        </main>
      </div>

      <UpgradeModal open={upgradeModalOpen} onClose={() => setUpgradeModalOpen(false)} shop={shop} />

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
              {trialBillingLoading ? "Opening Shopify billing\u2026" : "Continue with Pro"}
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
