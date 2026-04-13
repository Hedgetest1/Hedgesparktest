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

function formatPct(value: unknown): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
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

/* ══════════════════════════════════════════════════════════════════════════════
   LIVE RADAR + WORLD MAP — the killer feature
   ══════════════════════════════════════════════════════════════════════════════ */

function geoToMapXY(lat: number, lon: number): { x: number; y: number } {
  const x = ((lon + 180) / 360) * 900;
  const y = ((90 - lat) / 180) * 450;
  return { x, y };
}

// Demo visitors for testing the map feature
const DEMO_VISITORS: LiveVisitor[] = [
  { visitor_id: "demo-1", url: "/products/silk-pillowcase", intent_level: "HOT", dwell_seconds: 45, country: "United States", country_code: "US", city: "New York", lat: 40.71, lon: -74.0 },
  { visitor_id: "demo-2", url: "/products/ceramic-mug", intent_level: "WARM", dwell_seconds: 22, country: "United Kingdom", country_code: "GB", city: "London", lat: 51.51, lon: -0.13 },
  { visitor_id: "demo-3", url: "/products/candle-trio", intent_level: "HOT", dwell_seconds: 38, country: "Germany", country_code: "DE", city: "Berlin", lat: 52.52, lon: 13.41 },
  { visitor_id: "demo-4", url: "/collections/home", intent_level: "COLD", dwell_seconds: 8, country: "Japan", country_code: "JP", city: "Tokyo", lat: 35.68, lon: 139.69 },
  { visitor_id: "demo-5", url: "/products/throw-blanket", intent_level: "WARM", dwell_seconds: 18, country: "Australia", country_code: "AU", city: "Sydney", lat: -33.87, lon: 151.21 },
  { visitor_id: "demo-6", url: "/products/linen-shirt", intent_level: "HOT", dwell_seconds: 52, country: "Italy", country_code: "IT", city: "Milan", lat: 45.46, lon: 9.19 },
];

// World map SVG paths — generated from Natural Earth 110m land polygons
// Equirectangular projection: x=(lon+180)/360*900, y=(90-lat)/180*450
// 87 polygons, simplified, no Antarctica
const LAND_PATHS: string[] = [
  "M301.1,425.1L300.3,426.4L299.6,427.5L294.4,427.2L288.8,427.3L285.6,426.5L285.6,426.4L284.3,425.6L289.9,425.7L295.3,426L297.2,425L298.5,424.1Z","M303.6,352.8L305.6,353.9L304.9,354.8L301.5,355.5L300.4,354.6L298.2,355.8L297,354.6L300,353.1L302.1,353.8Z","M813.5,327L815.9,327.8L817.3,327.5L819.2,327L820.7,327.2L820.9,330.2L820,331L819.8,333L818.9,332.3L817.2,334.1L816.7,334L815.1,333.9L813.6,331.7L813.2,330.1L811.8,327.9L811.9,326.8Z","M882.6,327.3L883.1,328.3L884.9,327.3L885.6,328.4L885.6,329.4L884.7,330.6L883.1,332.4L881.8,333.4L882.7,334.6L880.8,334.7L878.6,335.6L878,337.2L876.5,339.8L874.6,340.9L873.3,341.6L871,341.5L869.4,340.7L866.7,340.5L866.3,339.6L867.6,337.8L870.8,335.3L872.4,334.8L874.2,333.9L876.3,332.6L877.8,331.3L878.9,329.4L879.9,328.8L880.2,327.4L882,326.2Z",
  "M886.5,315.4L888.3,318L888.4,316.3L889.5,317L889.9,318.9L891.9,319.7L893.6,319.9L895,318.9L896.3,319.2L895.7,321.5L894.9,322.9L893,322.9L892.3,323.6L892.6,324.7L892.2,325.2L891.3,326.5L890,328.2L888.1,329.2L887.7,328.6L886.6,328.2L888.1,326.1L887.3,324.8L884.6,323.8L884.6,322.9L886.4,322L886.9,320.1L886.7,318.5L885.7,316.8L885.8,316.3L884.6,315.3L882.6,313.1L881.6,311.3L882.5,311.1L883.9,312.5L885.8,313.2Z","M867.8,280.4L866.9,281L865.5,280.3L863.7,279.2L862.1,277.9L860.4,276.1L860.1,275.3L861.1,275.3L862.6,276.1L863.7,277L864.4,277.7L866.5,279.3Z",
  "M575.1,258.9L575.5,261.9L576.2,263.1L575.9,264.3L575.5,265L574.7,263.5L574.2,264.3L574.7,266.1L574.4,267.2L573.7,267.8L573.6,269.9L572.6,272.8L571.4,276.2L569.8,281L568.9,284.5L567.7,287.4L565.7,287.9L563.5,289L562.1,288.4L560.1,287.5L559.4,286.2L559.2,283.9L558.4,281.9L558.1,280.1L558.6,278.3L559.7,277.9L559.7,277.1L560.9,275.2L561.2,273.6L560.6,272.4L560.1,270.8L559.9,268.5L560.8,267.1L561.1,265.5L562.4,265.4L563.8,264.9L564.7,264.5L565.8,264.5L567.2,263L569.3,261.5L570,260.2L569.7,259.2L570.7,259.5L572.1,257.7L572.2,256.2L573,255.1L573.9,256.2L574.5,257.2Z",
  "M808.9,259.4L811.4,260.4L813.4,262.5L813.7,265.7L814.7,267.3L815.2,270.7L818.7,273.7L822.1,276L823.2,278.2L825.2,280.3L826.8,281L829,285.2L832.1,288.2L832.9,291.6L833.9,295.3L833.3,298.6L832.7,302.3L831.1,306.4L828.4,309.5L826.8,312.9L825.2,316.1L825,318.6L820.8,319.5L817.3,321.5L813.7,321.5L812.6,319.7L809,322L805.4,321L801.6,320L799.5,316.6L797.7,314.3L796.1,312.8L794.3,312.7L793.4,311.8L794.7,309.1L792.5,309.4L790,312.2L788.1,309.9L785.2,307.1L782.5,305L778.3,303.7L770.6,304.9L765.4,305.5L760.6,307.4L759.1,309.7L755.5,310L751.5,309.8L748.2,311.3L746.3,311.9L743.2,312.6L738.9,311L737.6,309.1L739.3,308.1L739.5,305.5L737.9,301.5L737.6,298.7L736.5,296.3L735.1,293.3L733.3,290.3L733.6,289.1L735.6,290.7L734.3,287.5L733.5,286L734.3,283.9L734.3,281.2L735.6,281.3L738.7,278.7L741.8,276.8L743.6,276.9L747.1,275.7L748.1,274.9L752.1,274.2L754.1,271.8L755.7,269.5L757.5,266L759.6,267.7L759.5,265.3L760.9,263.9L762.9,261.7L764.2,260.6L765.4,260.2L767.7,259.5L770.9,262.2L774.1,262.4L774.7,259L775.5,257.8L778.1,255.5L781.4,255.3L779.6,253.2L782.5,253.4L786,255.1L788.2,255.6L790.6,255.1L792.4,255.9L790.8,258.2L790.2,259.3L788.6,261.8L790.7,263.9L794,265.5L796.5,267L798.2,268.4L802.2,268.4L803.2,266L804.3,262.6L804.1,260.7L804.1,257.4L804.2,256L805.3,253.3L806.3,251.7L807.2,254.5L807.9,255.8L809,258.5Z",
  "M761.1,250.4L758.9,250.9L758.6,250.6L758.9,249.8L760,248.2L762.4,247.2L762.7,246.6L764.9,246.1L766.6,246L767.4,245.7L768.3,246L767.4,246.7L764.8,247.8L762.7,248.5Z","M744.8,245.2L745.7,245.9L747.2,245.7L747.8,246.8L744.9,247.3L743.2,247.6L741.9,247.6L742.7,246.1L744.1,246.1Z","M757.3,245.2L756.9,246.6L753.1,247.3L749.8,247L749.8,246.1L751.8,245.6L753.4,246.3L755,246.2Z",
  "M721.6,241.9L726.3,242.2L726.9,241.2L731.5,242.4L732.4,244L736.2,244.4L739.3,245.9L736.4,246.9L733.7,245.9L731.4,245.9L728.8,245.8L726.5,245.3L723.6,244.4L721.7,244.1L720.7,244.4L716.1,243.4L715.7,242.3L713.4,242.1L715.1,239.7L718.2,239.9L720.2,240.9L721.2,241.1Z",
  "M830,238.7L828.6,238.9L828.3,239.6L826.9,240.2L825.6,240.8L824.3,240.8L822.2,240.1L820.8,239.4L821,238.6L823.2,239L824.6,238.8L825,237.6L825.3,237.5L825.6,238.8L827,238.6L827.7,237.8L829.1,236.9L828.8,235.4L830.3,235.4L830.8,235.8L830.8,237.2Z","M776.2,232.7L777.1,234.6L775,233.6L772.9,233.4L771.5,233.6L769.7,233.5L770.3,232.1L773.4,232Z","M832.9,236.2L832.1,236.9L831.6,235.4L831,234.5L829.9,233.7L828.5,232.6L826.7,231.9L827.3,231.3L828.7,231.9L829.6,232.5L830.6,233.1L831.6,234.1L832.5,235Z",
  "M785.4,227.9L786.1,231.9L788.6,233.4L790.7,230.8L793.6,229.3L795.8,229.3L798,230.1L799.8,231L802.5,231.5L806.8,233.2L811.5,234.7L813.2,235.9L814.6,237.2L815,238.7L819.1,240.2L819.7,241.5L817.4,241.8L818,243.5L820.2,245.1L821.8,247.8L823.3,247.7L823.2,248.8L825.1,249.2L824.3,249.7L827,250.7L826.7,251.5L825.1,251.6L824.5,251L822.3,250.7L819.8,250.3L817.8,248.7L816.4,247.4L815.1,245.2L811.9,244.1L809.7,244.8L808.2,245.6L808.5,247.5L806.6,248.3L805.2,247.9L802.6,247.8L800.4,245.7L797.8,245.2L797.2,246L794,246L795.1,244L796.7,243.3L796,240.6L794.8,238.5L790,236.4L787.9,236.2L784.2,233.8L783.4,235.1L782.5,235.3L781.9,234.4L781.9,233.3L780,232.1L782.7,231.2L784.5,231.2L784.2,230.5L780.6,230.5L779.6,229L777.4,228.6L776.3,227.3L779.7,226.7L781,225.9L785,227Z",
  "M763.1,221.5L761.1,223.9L759.2,224.4L756.8,223.9L752.6,224L750.5,224.4L750.1,226.3L752.3,228.5L753.7,227.4L758.4,226.5L758.1,227.7L757.1,227.3L756,228.8L753.8,229.8L756.1,233L755.7,233.8L757.9,236.7L757.9,238.4L756.6,239.1L755.6,238.2L756.8,236.2L754.3,237.1L753.7,236.4L754,235.5L752.2,234L752.4,231.6L750.8,232.3L751,235.2L751.1,238.8L749.5,239.2L748.4,238.4L749.1,236.1L748.7,233.7L747.7,233.7L746.9,232L748,230.4L748.3,228.4L749.6,224.6L750.1,223.6L752.2,221.7L754.2,222.5L757.3,222.8L760.2,222.7L762.7,220.9Z",
  "M771.7,222.2L771.6,224.4L770.3,224.1L769.9,225.6L770.9,227L770.3,227.2L769.2,225.7L768.5,222.5L769,220.5L769.8,219.6L770,220.9L771.5,221.1Z",
  "M714.5,239.6L711.8,239.7L709.7,237.6L706.5,235.6L705.4,234L703.5,232L702.3,230.1L700.4,226.6L698.2,224.5L697.4,222.4L696.5,220.4L694.2,218.9L692.9,216.7L691.1,215.3L688.5,212.6L688.2,211.3L689.8,211.4L693.7,211.9L695.9,214.3L697.9,216L699.2,217.1L701.6,219.8L704.1,219.8L706.2,221.5L707.7,223.6L709.6,224.7L708.6,226.8L710,227.6L710.9,227.7L711.3,229.5L712.2,230.9L714.1,231.1L715.3,232.7L714.6,235.8Z",
  "M744.7,220.4L747.5,222.7L744.5,223L743.7,224.7L743.8,227L741.4,228.7L741.3,231.2L740.4,235L740,234.1L737.2,235.3L736.2,233.7L734.4,233.6L733.1,232.8L730.2,233.7L729.3,232.5L727.6,232.6L725.6,232.3L725.2,229L723.9,228.3L722.7,226.1L722.4,224L722.7,221.6L724.2,220L726,220.8L727.9,220.4L728.4,218.3L729.5,217.8L732.5,217.2L734.3,215.3L735.5,213.7L736.5,212.7L738.6,211.4L740.6,209.6L741.8,207.7L742.8,207.7L744.1,208.9L744.2,210L745.9,210.7L748,211.5L747.8,212.5L746.1,212.6L746.5,213.8L744.7,214.7L743.3,216.9L745.1,219.3Z",
  "M765.9,204L766.2,205.6L766.3,207L765.5,209.3L764.6,206.8L763.4,208L764.2,209.9L763.5,211L760.5,209.6L759.8,207.8L760.6,206.6L759,205.4L758.2,206.5L757.1,206.4L755.2,207.8L754.8,207L755.8,204.9L757.4,204.2L758.7,203.3L759.6,204.4L761.5,203.7L761.9,202.6L763.7,202.5L763.5,200.6L765.6,201.8L765.8,203Z",
  "M653,209.5L650.9,210.1L649.7,208.1L649.2,204.5L650.4,200.4L652.1,201.8L653.3,203.6L654.5,206.2L654.1,208.8Z","M760,199.3L759.1,200.1L758.3,201.7L757.5,202.4L756,200.7L756.5,200L757.1,199.3L757.4,197.8L758.7,197.6L758.3,199.3L760.2,196.9Z","M746.3,201.7L742.9,204.1L744.2,202.3L746,200.8L747.5,199.1L748.8,196.6L749.2,198.6L747.6,200Z","M763.8,194.6L764.5,197.4L762.5,196.7L762.6,197.6L763.2,199.1L762,199.7L761.9,197.9L761.1,197.8L760.8,196.3L762.2,196.5L762.2,195.5L760.7,193.6L763.1,193.7Z",
  "M753.3,178.7L754.8,179.5L755.6,178.8L755.8,179.4L755.4,180.5L756.3,182.3L755.6,184.3L754.2,185.2L753.8,187.2L754.3,189.2L755.6,189.5L756.8,189.2L759.9,190.5L759.6,191.9L760.5,192.5L760.2,193.7L758.2,192.4L757.3,191.1L756.7,192L755.1,190.5L752.8,190.9L751.6,190.4L751.7,189.3L752.5,188.7L751.7,188.1L751.4,189L750.2,187.6L749.8,186.5L749.7,184.1L750.7,184.9L751,181L751.8,178.7Z",
  "M257.7,180.3L257,180.7L255.6,180.3L254.2,179.4L254.5,178.9L255.5,178.7L256.1,178.8L257.8,179L259.1,179.6L259.5,180.3Z",
  "M268.6,175.3L270.7,175.7L271,175.3L273,175.3L274.5,175.9L275.1,175.9L275.6,176.8L276.9,176.7L276.9,177.5L278,177.6L279.2,178.5L278.3,179.5L277.1,178.9L275.9,179L275.1,178.9L274.7,179.4L273.7,179.5L273.3,178.9L272.5,179.3L271.5,181L270.9,180.6L270.7,179.9L269.1,179.5L267.9,179.6L266.4,179.5L265.2,179.9L263.9,179.1L264.1,178.3L266.4,178.7L268.3,178.9L269.2,178.3L268,177.2L268,176.3L266.5,175.9L267,175.2Z",
  "M725.8,178.3L723.7,179.5L721.6,178.7L721.6,176.6L722.8,175.4L725.5,174.7L727,174.8L727.5,175.8L726.4,176.9Z",
  "M250.8,168.1L251.8,169L254.1,168.7L255,169.3L257.1,170.9L258.7,172L259.5,171.9L261,172.5L260.8,173.2L262.7,173.3L264.6,174.3L264.3,174.9L262.6,175.2L260.9,175.3L259.2,175.1L255.6,175.4L257.3,174L256.3,173.3L254.7,173.2L253.8,172.4L253.2,171L251.8,171.1L249.5,170.4L248.7,169.9L245.4,169.5L244.6,169L245.5,168.4L243.1,168.3L241.3,169.6L240.2,169.6L239.9,170.2L238.6,170.5L237.6,170.3L238.9,169.5L239.4,168.6L240.6,168L241.8,167.5L243.7,167.3L244.3,167L246.5,167.2L248.5,167.2Z",
  "M752.9,168L751.9,170.1L750.6,168L750.3,166.1L751.7,163.7L753.7,161.8L754.9,162.5L754.4,164Z","M786.6,139.6L786.9,140.5L785.5,142L784.5,141.2L783.2,141.8L782.5,143.2L780.9,142.5L780.9,141.3L782.3,139.8L783.7,140.1L784.8,139.1Z","M536.4,135.8L534.8,136.9L534.9,137.4L535,137.6L532.4,138.6L531.2,138.2L530.6,137.2L531.8,137.1L532,137.1L532.4,136.5L534.2,136.6Z",
  "M509.2,135.7L510.6,136.6L512.6,136.4L514.4,136.6L514.4,137.1L515.7,136.8L515.4,137.5L511.8,137.7L511.8,137.3L508.8,136.8Z","M488.8,129.4L487.9,131.4L488.3,132.2L487.7,133.5L485.8,132.5L484.6,132.2L481.1,131L481.4,129.7L484.4,129.9L486.9,129.6Z","M473,122L474.5,123.7L474.2,127.1L473,126.9L472,127.7L471.1,127.1L471,124.1L470.4,122.6L471.8,122.8Z",
  "M802.4,132.1L801.5,134.1L801.9,135.4L800.6,137.2L797.4,138.3L793,138.5L789.5,141.3L787.8,140.4L787.7,138.5L783.4,139.1L780.4,140.2L777.5,140.3L780,142.1L778.3,146.4L776.7,147.4L775.5,146.5L776.1,144.2L774.5,143.5L773.5,141.8L775.9,141L777.2,139.4L779.7,138.1L781.5,136.4L786.5,135.7L789.2,136.2L791.8,131.7L793.5,132.9L797.1,130.4L798.6,129.5L800.1,126.4L799.7,123.6L800.8,122L803.4,121.6L804.8,125L804.7,127L802.4,129.6Z",
  "M809.8,114.6L811.5,115.1L813.3,114L813.9,116.8L810.1,117.5L808,120L804,118.3L802.7,121L799.9,121.1L799.5,118.6L800.8,116.7L803.5,116.5L804.2,113.1L804.9,111.1L807.9,113.7Z","M290.8,108.6L292.7,109L295,108.9L293.7,109.9L292.8,110.1L289.6,109L289,108.2L290,107.4Z","M295.5,102.2L294.3,102.3L291,101.5L288.7,100.3L289.6,100.1L292.9,100.7L295.4,101.8Z",
  "M141.2,103.7L140,104.1L135.9,102.9L135.1,102.1L132.9,101.2L132.4,100.5L129.9,100L128.9,98.7L129.1,98.1L131.7,98.6L133.3,99L135.6,99.3L136.5,100.1L137.7,101.3L140.2,102.3Z",
  "M309.7,98.3L308,100.5L309.6,99.6L311.3,100.2L310.4,101L312.7,101.7L313.8,101.1L316.3,101.9L315.5,103.7L317.3,103.3L317.6,104.6L318.4,106.2L317.3,108.4L316.2,108.5L314.6,108L315.1,105.9L314.4,105.6L311.5,107.8L310,107.7L311.8,106.5L309.4,105.9L306.7,106.1L301.8,106L301.5,105.3L303,104.4L301.9,103.7L304,102.2L306.6,98.2L308.2,96.8L310.3,95.9L311.5,96L311,96.7Z",
  "M118.2,89.9L120.6,89.7L119.9,92.5L122.1,94.5L121.1,94.5L119.5,93.4L118.6,92.2L117.4,91.5L116.9,90.4L117,89.6Z",
  "M809.1,98.1L811.6,102.6L807.9,101.7L806.4,105.3L808.8,107.9L808.8,109.7L806.9,108.1L805.2,110.1L804.8,108L805,105.5L804.8,102.9L805.3,101L805.4,97.6L804,95.2L804.2,91.7L806.5,90.6L805.5,89.4L806.6,89.1L807.3,90.7L808.2,93.1L808.1,95.6Z",
  "M433,94.3L428.6,95.8L425.1,95.4L427.1,92.8L425.8,90.3L429.2,88.3L431.1,87.2L433.2,87.1L435.8,88.6L434.5,90.3L434.9,92.1Z","M67.5,82.2L65,83.2L63.7,82.5L63.3,81.3L65.6,80.5L66.9,80.1L68.6,80.2L69.6,81Z",
  "M442.5,78.4L439.8,81.1L442.4,80.8L445.1,80.8L444.5,82.8L442.2,85.1L444.8,85.2L447.2,88.4L448.9,88.8L450.5,91.7L451.2,92.7L454.2,93.2L453.9,94.8L452.6,95.5L453.6,96.8L451.4,98.1L448,98.1L443.8,98.7L442.6,98.3L441,99.4L438.6,99.1L436.9,100.1L435.6,99.6L439.2,97L441.5,96.4L437.5,96L436.8,95L439.4,94.2L438.1,92.9L438.6,91.3L442.3,91.5L442.6,90L440.9,88.5L437.9,88L437.3,87.3L438.2,86.2L437.4,85.5L436,86.7L435.9,84.3L434.6,83L435.5,80.5L437.5,78.4L439.5,78.6Z",
  "M245.3,68.2L242.3,69.6L240.6,69.5L240,68.9L241.9,67.7L245.3,67.7Z","M20.7,65.5L22.2,66L23.8,65.8L25.8,66.4L28.3,66.8L28.1,67L26.2,67.6L24.3,67L23.3,66.6L21.1,66.7L20.5,66.5Z",
  "M237.1,60.9L237.6,62L238.8,61.6L240.3,62.2L243,63.1L245.9,63.9L246.1,65.1L248,64.9L249.7,65.7L247.5,66.5L243.6,65.9L242.2,64.7L239.7,66.1L236.2,67.4L235.3,65.9L231.9,66.1L234.1,64.9L234.4,62.9L235.3,60.7Z","M413.7,58.9L413.2,60.5L416,62.2L412.7,64.1L405.5,65.8L403.4,66.3L400.1,65.9L393.1,65.1L395.6,64L390.1,62.8L394.5,62.3L394.4,61.6L389.2,61L390.9,59.3L394.7,59L398.6,60.7L402.4,59.3L405.5,60L409.6,58.7Z",
  "M260.3,57.1L257.5,57.3L256.9,56L258,54.6L260.3,54.3L262.2,55L262.2,56L262,56.4Z","M12.5,58.5L14.2,59.2L13.6,57.3L20.4,57.7L25.3,60.1L22.8,61.1L18.7,61.4L18.6,63.8L17.6,64.4L15.3,64.3L13.4,63.4L10,62.7L9.5,61.6L6.9,61.2L4.1,61.5L2.7,60.6L3.3,59.7L0.3,60.3L1.4,61.5L0,62.6L0,52.6L6.1,54.5L12.7,57Z",
  "M210.9,52.2L209.3,53.1L206,52.3L203.9,52.6L200.5,51.5L202.7,50.7L204.5,49.6L207.1,50.3L208.6,50.8L209.4,51.3Z","M3.3,47.8L0,47.9L0,46.2L0.3,46.1L2.4,46.1L6.1,46.8L5.8,47.2Z",
  "M223.6,51.3L246.8,52.1L228.8,64.8L219.3,82.3L246.5,94.6L253.7,78L276,72.3L295.5,84.2L303.1,97.3L278.4,104.2L300.5,110.2L279.9,114.2L273.4,121.3L264.4,123.8L260.1,132L260.3,133.6L246.3,148.2L246.7,160.9L235.6,149.6L224.6,152.2L206.5,158.3L207,173.4L223.7,175.3L231.4,176.3L229.4,182.4L231.2,185.5L239.1,185.4L241.3,192.8L244.5,202L253.7,201.4L264.5,196.7L270.9,198.9L277.8,196.4L293.2,198.9L306.1,209.2L322.3,215.9L339,230.3L363.2,243.4L351,270.7L328.8,289.7L315.5,311L308,317.3L287.2,327.7L281,340.8L276.3,355.7L261,346.7L265,329.5L271.3,297.2L260,261.6L246.5,236.8L249.9,224.1L256.3,214.8L252.2,202.5L247,205.9L240.9,202.4L235.5,198.9L231.7,192.5L220.8,189.7L200.8,183.2L186.2,173L176.8,158.9L165.3,146.1L167.6,153.9L176.5,166.6L166.3,158.1L160.3,149.5L148.4,138.5L138.7,118.1L142.9,102.5L122.3,87.1L90.1,75L74.1,72.4L53.9,85L48.6,85L50,78.6L34.7,71.2L48.1,63L40.9,58.6L42.7,50.4L73.2,48.9L110.9,51.7L139.3,51.5L173,55.5L196.4,55.9L208.8,49.8Z",
  "M164.6,42.2L163.3,43.4L168.9,42.6L172.4,43.9L175.2,42.6L177.5,43.4L179.5,45.9L180.8,44.8L179,42.3L181.2,41.9L183.7,42.3L186.5,43.3L188.1,45.8L188.8,47.5L193,48.8L197.5,49.9L197.3,51L193.2,51.2L194.8,52.2L193.9,53.1L189.4,52.7L185.1,52L182.2,52.2L177.5,53L170.1,53.5L166.7,53.7L165.4,52.5L161.9,51.8L159.7,52.1L156.7,50.1L158.3,49.8L162.2,49.4L165.7,49.5L169,49.1L164.1,48.5L158.8,48.7L155.2,48.6L153.9,47.7L159.7,46.7L155.9,46.8L151.5,46.1L153.6,44.2L155.3,43.2L162,41.7Z",
  "M188.8,41.4L186.6,43.1L182.7,41.4L183.5,41L186.8,40.9Z","M259.1,42.2L259.4,42.9L256.7,42.9L254,42.8L251.3,43.1L250.6,43L247.8,41.7L247.9,40.8L249.1,40.6L254.8,40.9Z",
  "M233.6,42.1L235.6,43.7L237.9,41.6L244.2,40.6L248.5,43.2L248.1,44.8L253.1,44.1L255.4,43.1L261,44.4L264.4,45.6L264.8,46.7L269.4,46.1L272,47.7L278,48.7L280.2,49.7L282.6,52L278,53.2L283.9,54.8L287.8,55.4L291.4,57.7L295.4,57.8L294.6,59.6L290.2,62.5L287.1,61.4L283.2,59L280,59.3L279.6,60.8L282.3,62.2L285.7,63.4L286.7,64L288.3,66.5L287.5,68.3L284.3,67.6L278,65.6L281.6,67.8L284.2,69.3L284.6,70.2L277.8,69.2L272.4,67.7L269.4,66.5L270.3,65.8L266.6,64.5L262.9,63.3L263,64L255.7,64.4L253.6,63.6L255.3,61.7L260,61.7L265.1,61.4L264.3,60.5L265.1,59.2L268.4,56.8L267.7,55.7L266.7,54.8L262.9,53.6L257.8,52.8L259.4,52.1L256.8,50.6L254.6,50.4L252.6,49.6L251.3,50.3L246.7,50.6L237.6,50.1L232.3,49.3L228.3,49L226.2,48.1L228.8,47L225.3,46.9L224.5,44.4L226.4,42.2L229,41.2L235.4,40.5Z",
  "M199.1,40.4L202.1,40.9L206.6,40.6L207.2,41.3L204.9,42.5L208.6,43.6L208.2,45.9L204.1,46.8L201.7,46.6L200,45.7L193.8,43.7L193.8,42.9L198.9,43.2L196.2,41.6Z","M809,42L805.2,42L800.1,41.7L799.7,41.6L802,40.6L805.2,40.4L808.7,41.3Z","M217,43.1L214.3,44.9L211.5,44.8L209.9,42.6L210,41.4L211.3,40.3L213.7,39.7L218.9,39.7L223.7,40.4L220,42.6Z",
  "M148.9,46.5L142.3,47.7L140.9,46.6L135.2,45.3L136,44.5L138,42.4L140.2,40.8L137.7,39.3L146.2,38.9L149.7,39.4L156.1,39.5L158.5,40.3L161.2,41.3L158.1,41.9L151.9,43.7L148.9,45.5Z","M826.8,37.3L823.9,38.3L819.9,38.1L815.3,37.1L815.9,36.3L820.6,36.6Z","M216,37.6L214.6,38.5L211,38.3L207.9,37.7L209.3,36.6L212.9,35.9L215.1,36.8Z",
  "M812.7,36.1L810.8,38L801.5,37.9L797.4,38.5L792.4,36.8L793.8,35.1L797.1,34.7L803.7,34.8Z","M203.8,33.2L205.7,34.4L205.7,35.6L204.6,37.5L200.5,37.8L197.8,37.4L197.8,35.9L193.7,36.1L193.6,34.2L196.3,34.2L200,33.4L203.6,33.5Z",
  "M179.5,34.5L180.5,35.4L182.7,35L185.3,35.1L185.7,36.3L184.2,37.5L175.8,37.9L169.4,39L165.6,39L165.3,38.2L170.5,37.1L159.2,37.4L155.7,36.9L159.1,34.5L161.5,33.8L168.5,34.6L173,36.1L177.3,36.3L173.8,33.9L176,33L178.6,33.3Z",
  "M593.8,48.2L592.4,48.4L584.2,48.1L583.5,47L579,46.3L578.6,45L581.2,44.4L581.1,43.1L586.1,40.9L583.8,40.6L589.8,38.4L589.1,37.3L594.7,36L602.9,34.4L611.2,33.9L615.5,33L620.4,32.7L622.1,33.6L620.5,34.4L611.6,35.7L604,36.8L596.2,39.2L592.5,41.7L588.5,44.1L589.1,46.1Z",
  "M213.3,32.3L216.1,33.1L221,33.1L223.1,33.9L222.6,34.8L225.4,35.4L227,36L230.4,36.1L234.1,36.3L238,35.8L243.1,35.5L247.2,35.7L249.9,36.7L250.4,37.7L248.9,38.4L245.1,38.9L241.9,38.6L234.8,39L229.6,39L225.6,38.7L218.9,37.9L218.1,36.5L217.8,35.3L215.3,34.2L210.1,33.9L207.2,33.1L208.1,32.1Z",
  "M159.5,30.9L159.2,32.8L157.2,33.7L154.9,33.8L150.3,34.9L146.3,35.2L142.9,34.7L147.1,32.8L152.2,31.2L156.1,31.3Z",
  "M717.4,32.6L746.9,41L799.7,46.3L877,52.5L893.4,68.7L855.3,84.7L854.7,74.1L805.5,77.4L788.8,115L768.8,126.7L764.2,130.1L754,126.6L756.3,132.7L751,157.4L720.1,171.1L710.8,198.8L704.1,208.1L701.7,215.2L696.3,192.2L680.1,170.7L659.9,179.2L644.9,204.4L622.9,169.8L586.8,158.8L575.4,158.3L581.4,164.6L598.6,168.3L589.2,180.3L566.8,191.5L557.1,185.2L542.9,162.9L534,155.1L542.8,175.5L558.7,196.8L577.6,197.1L551.6,231.2L551.2,251.9L538.4,279.6L530.5,296.9L506.4,309.7L492.7,299.7L479.4,264.5L480.6,240.7L472.4,215.2L447.3,212.5L418.9,206.8L408.3,194L407.4,171.4L424,152.3L449.7,135.3L476.5,135.1L498.9,148.7L524.2,147L539.9,138.5L517.6,130.9L554.3,120.1L539.6,108.4L526.9,108.5L515.1,123.5L507.8,130.2L498.9,120.7L484.3,111.2L496.2,124.6L486.8,123.5L457.5,118.8L435.3,134.9L427.5,121.1L441.8,102.7L471.4,89L474.8,87.5L502.6,84.9L520.2,73.7L503.4,64L475.9,76.3L515.9,47.5L537.4,64L565.6,54.4L602.7,52.6L631.5,43.1L634.6,52.3L655.6,40.4L715.2,31.6Z",
  "M215.4,31.2L214.3,31.3L209.6,31.1L208.9,30.4L213.9,30.4L215.7,30.9Z","M174.5,30.8L169.9,31.5L166.2,30.7L168.2,29.9L171.8,29.6L175.4,30Z","M511.8,30.4L506.2,31.4L501.8,30.8L503.5,30.2L502,29.4L507.2,28.9L508.2,29.8Z","M175.8,28.5L172.8,29L168.6,29L168.7,28.6L171.2,27.9L172.6,28Z",
  "M210.4,29.9L206.7,30.4L204.7,29.8L203.6,28.9L203.4,27.8L206.7,27.9L208.1,28.1L211.1,29Z","M199.8,29.2L200.8,30.2L196.7,30L192.6,29.1L187.1,29L189.5,28.3L186.5,27.7L186.3,26.7L191.2,27.1L197.9,28Z","M712.7,29.2L698.6,30.2L703.2,26.9L705.2,26.6L707.1,26.8L713.4,28.2Z",
  "M495.6,25.7L503.9,27.6L497.6,28.6L496.2,30.4L494,30.9L492.8,33L489.8,33.1L484.4,31.5L486.7,30.7L482.9,29.9L478.1,27.8L476.1,25.9L482.9,25L484.3,25.8L487.9,25.8L488.8,25L492.5,24.9Z","M513.6,24L518.5,24.9L514.8,26.2L507.6,26.5L500.2,26.1L499.7,25.4L496.2,25.4L493.4,24.2L501.1,23.5L504.8,24.1L507.3,23.4Z",
  "M577.8,23.6L574.5,24L572.2,24.2L571.9,24.6L569,25L566.3,24.4L567.7,23.6L562.1,23.5L567,23.1L570.8,23L571.3,23.7L572.7,23.1L575.1,22.7L578.8,23.3Z","M699.8,27.8L694.4,28.1L687.4,27.4L683.3,26.4L681.4,24.6L678,24.1L684.4,22.4L689.9,21.9L694.7,23.1L700.5,25.5Z",
  "M232.4,25.9L235.5,26.7L232,27.4L227.4,29.3L223,29.5L217.8,29.1L215.1,28.1L215.2,27.2L217.1,26.5L212.6,26.6L209.8,25.7L208.2,24.6L210,23.5L211.7,22.7L214.3,22.6L213.2,22L219,21.9L222.2,23.2L226.4,23.7L230.5,24.2Z",
  "M278.8,17.2L285.4,17.4L290.8,17.7L295.4,18.4L295.3,19.1L289.2,20.2L283.1,20.7L280.9,21.2L286.3,21.2L280.4,22.7L276.3,23.5L272,25.5L266.9,25.9L265.3,26.4L257.7,26.7L261.2,27L259.4,27.5L261.5,28.7L259.1,29.5L255.3,30.3L254.1,31.2L250.6,32L251,32.5L255.2,32.4L255.3,33.1L248.6,34.6L242.1,33.9L234.7,34.3L231,33.9L226.3,33.8L226,32.6L230.6,32.1L229.3,30.2L230.9,30.1L237.6,31.2L234.1,29.5L230.1,29.1L232.1,28.1L236.6,27.5L237.3,26.6L233.7,25.7L232.7,24.4L239.5,24.5L241.5,24.8L245.4,23.8L239.8,23.6L231,23.7L226.6,22.9L224.5,21.8L221.6,21.1L221,20.3L224.8,19.8L227.7,19.7L232.6,19.3L236.2,18.4L239.3,18.5L242,19.2L243.9,17.9L247.3,17.5L251.7,17.2L259.4,17.1L260.7,17.3L267.9,16.9L273.3,17.1Z",
  "M382.2,16.2L397.9,18.2L393.3,19.1L383.7,19.3L370.2,19.5L371.5,19.9L380.4,19.7L387.9,20.5L392.7,19.8L394.8,20.7L392.1,22.1L398.4,21.2L410.6,20.2L418.1,20.7L419.5,21.8L409.3,23.5L407.9,24.1L399.9,24.6L405.7,24.7L402.8,26.5L400.7,28.1L400.8,30.9L403.8,32.5L399.9,32.6L395.8,33.4L400.4,34.8L401,36.9L398.3,37.1L401.6,39.3L396,39.4L398.9,40.5L398.1,41.3L394.6,41.7L391.1,41.7L394.2,43.4L394.3,44.5L389.3,43.5L388,44.2L391.4,44.8L394.7,46.3L395.6,48.3L391.2,48.8L389.2,47.9L386.1,46.4L387,48.1L384.1,49.4L390.7,49.5L394.1,49.7L387.4,51.9L380.6,53.8L373.3,54.7L370.6,54.7L368,55.7L364.5,58.3L359.1,60.1L357.4,60.2L354.1,60.8L350.5,61.4L348.3,62.9L348.3,64.7L347,66.3L343,68.3L344,70.2L342.8,72.3L341.6,74.8L338,74.9L334.3,72.9L329.3,72.9L326.9,71.5L325.2,69L320.9,65.9L319.6,64.3L319.3,62.1L315.8,59.8L316.7,57.9L315.1,57L317.5,54.1L321.3,53.2L322.3,52.1L322.8,50.2L320,51.1L318.6,51.4L316.4,51.8L313.3,51L313.1,49.3L314.1,47.9L316.4,47.9L321.5,48.6L317.2,47L315,46.1L312.5,46.5L310.4,45.9L313.2,43.5L311.7,42.6L309.7,40.9L306.7,38.2L303.5,37.3L303.5,36.2L296.8,34.7L291.5,34.6L284.8,34.7L278.7,34.8L275.8,34.1L271.5,32.5L278.1,31.7L283.1,31.6L272.4,30.9L266.8,29.9L267.1,28.9L276.6,27.7L285.7,26.5L286.7,25.6L279.9,24.7L282.1,23.7L290.8,22L294.4,21.7L293.4,20.6L299.3,19.9L307,19.5L314.7,19.5L317.4,20.3L324,18.9L330,19.8L333.5,20L338.7,20.8L332.7,19.5L333.1,18.4L341.5,16.9L350.3,17L353.4,16.1L362.3,15.9Z",
];


function LiveRadarMap({
  visitors: realVisitors,
  radarPositions,
  coldStartPhase,
}: {
  visitors: LiveVisitor[];
  radarPositions: string[];
  coldStartPhase: number;
}) {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [demoMode, setDemoMode] = useState(false);

  const visitors = realVisitors.length > 0 ? realVisitors : demoMode ? DEMO_VISITORS : [];
  const isExpanded = selectedIdx !== null;
  const selectedVisitor = selectedIdx !== null ? visitors[selectedIdx] : null;

  function handleDotClick(i: number) {
    setSelectedIdx((prev) => (prev === i ? null : i));
  }

  return (
    <div className="relative overflow-hidden rounded-3xl border border-cyan-400/10 bg-[#08080f]" style={{ minHeight: 400 }}>
      <style>{`
        @keyframes radar-sweep { from { transform: translate(-50%,-50%) rotate(0deg); } to { transform: translate(-50%,-50%) rotate(360deg); } }
        @keyframes ripple-out { from { r: 5; opacity: 0.6; } to { r: 35; opacity: 0; } }
      `}</style>

      <div className="flex h-full" style={{ minHeight: 400 }}>

        {/* ── World Map — slides in from left on click ── */}
        <div
          className="relative overflow-hidden transition-all duration-700 ease-[cubic-bezier(0.16,1,0.3,1)]"
          style={{ width: isExpanded ? "60%" : "0%", opacity: isExpanded ? 1 : 0 }}
        >
          <svg viewBox="0 0 900 450" className="absolute inset-0 h-full w-full" preserveAspectRatio="xMidYMid meet">
            {/* Real land polygons from Natural Earth 110m */}
            {LAND_PATHS.map((d, i) => (
              <path key={`lp${i}`} d={d} fill="rgba(34,211,238,0.07)" stroke="rgba(34,211,238,0.2)" strokeWidth="0.8" strokeLinejoin="round" />
            ))}

            {/* ALL visitor dots (dimmed) */}
            {visitors.map((v, i) => {
              if (!v.lat || !v.lon) return null;
              const { x, y } = geoToMapXY(v.lat, v.lon);
              const isSel = selectedIdx === i;
              const color = v.intent_level === "HOT" ? "#fb7185" : v.intent_level === "WARM" ? "#fcd34d" : "#94a3b8";
              return (
                <g key={`mp-${i}`}>
                  {/* Ripple rings on selected */}
                  {isSel && (
                    <>
                      <circle cx={x} cy={y} fill="none" stroke={color} strokeWidth="1.5" r="5" opacity="0">
                        <animate attributeName="r" values="5;35" dur="2s" repeatCount="indefinite" />
                        <animate attributeName="opacity" values="0.7;0" dur="2s" repeatCount="indefinite" />
                      </circle>
                      <circle cx={x} cy={y} fill="none" stroke={color} strokeWidth="1" r="5" opacity="0">
                        <animate attributeName="r" values="5;35" dur="2s" begin="0.7s" repeatCount="indefinite" />
                        <animate attributeName="opacity" values="0.5;0" dur="2s" begin="0.7s" repeatCount="indefinite" />
                      </circle>
                    </>
                  )}
                  {/* Always-visible pulse for non-selected */}
                  {!isSel && (
                    <circle cx={x} cy={y} fill="none" stroke={color} strokeWidth="0.5" r="4" opacity="0">
                      <animate attributeName="r" values="4;15" dur="3s" repeatCount="indefinite" />
                      <animate attributeName="opacity" values="0.4;0" dur="3s" repeatCount="indefinite" />
                    </circle>
                  )}
                  {/* Dot */}
                  <circle
                    cx={x} cy={y}
                    r={isSel ? 8 : 4}
                    fill={color}
                    stroke={isSel ? "white" : "none"}
                    strokeWidth={isSel ? 3 : 0}
                    style={{ filter: `drop-shadow(0 0 ${isSel ? 20 : 8}px ${color})`, transition: "all 0.4s ease-out" }}
                    className="cursor-pointer"
                    onClick={() => handleDotClick(i)}
                  />
                  {/* City label for selected */}
                  {isSel && v.city && (() => {
                    const label = `${v.city}, ${v.country_code}`;
                    const labelW = label.length * 7.5 + 20;
                    return (
                      <g style={{ transition: "opacity 0.3s", opacity: 1 }}>
                        <rect x={x + 14} y={y - 16} width={labelW} height={28} rx="8" fill="rgba(0,0,0,0.85)" stroke={color} strokeWidth="1" />
                        <text x={x + 24} y={y + 2} fill="white" fontSize="13" fontWeight="700" fontFamily="system-ui,sans-serif">{label}</text>
                      </g>
                    );
                  })()}
                </g>
              );
            })}
          </svg>

          {/* Selected visitor detail */}
          {selectedVisitor && (
            <div className="absolute bottom-4 left-4 right-4 rounded-2xl border border-white/[0.08] bg-black/70 px-5 py-4 backdrop-blur-md">
              <div className="flex items-center gap-3">
                <span className={`h-4 w-4 flex-shrink-0 rounded-full ${intentDotClass(selectedVisitor.intent_level)}`} />
                <div className="min-w-0 flex-1">
                  <div className="text-[16px] font-bold text-white">
                    {selectedVisitor.city ? `${selectedVisitor.city}, ${selectedVisitor.country}` : "Unknown location"}
                  </div>
                  <div className="truncate text-[14px] text-slate-400">{selectedVisitor.url}</div>
                </div>
                <span className={`flex-shrink-0 rounded-lg px-3 py-1.5 text-[13px] font-bold uppercase ${
                  selectedVisitor.intent_level === "HOT" ? "bg-rose-500/20 text-rose-300" :
                  selectedVisitor.intent_level === "WARM" ? "bg-amber-500/20 text-amber-300" :
                  "bg-white/10 text-slate-400"
                }`}>{selectedVisitor.intent_level}</span>
              </div>
            </div>
          )}
        </div>

        {/* ── Radar — centered by default, slides right on click ── */}
        <div
          className="relative flex-1 transition-all duration-700 ease-[cubic-bezier(0.16,1,0.3,1)]"
          style={{ flexBasis: isExpanded ? "40%" : "100%" }}
        >
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(56,189,248,0.08),transparent_40%)]" />

          <div className="absolute inset-0 flex items-center justify-center">
            <div className={`relative rounded-full border border-cyan-400/12 transition-all duration-700 ${isExpanded ? "h-[180px] w-[180px]" : "h-[240px] w-[240px]"}`}>
              <div className="absolute inset-[22%] rounded-full border border-cyan-400/8" />
              <div className="absolute inset-[44%] rounded-full border border-cyan-400/5" />
              <div className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-cyan-400/[0.04]" />
              <div className="absolute left-0 top-1/2 h-px w-full -translate-y-1/2 bg-cyan-400/[0.04]" />

              {/* Sweep */}
              <div className="absolute left-1/2 top-1/2 h-1/2 w-px origin-bottom" style={{ animation: "radar-sweep 4s linear infinite", background: "linear-gradient(to top, transparent, rgba(34,211,238,0.35))" }} />

              {/* Center */}
              <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
                <div className="h-3 w-3 rounded-full bg-cyan-400/30" />
                <div className="absolute inset-0 animate-ping rounded-full bg-cyan-400/20" style={{ animationDuration: "3s" }} />
              </div>

              {/* Visitor dots — CLICK to select */}
              {visitors.slice(0, 8).map((v, i) => (
                <div
                  key={`rd-${v.visitor_id || i}`}
                  className={`absolute ${radarPositions[i % radarPositions.length]} -translate-x-1/2 -translate-y-1/2 cursor-pointer`}
                  onClick={() => handleDotClick(i)}
                >
                  <div className={`rounded-full transition-all duration-300 ${
                    selectedIdx === i ? "h-6 w-6 ring-[3px] ring-white/60" : "h-4 w-4"
                  } ${intentDotClass(v.intent_level)}`} />
                </div>
              ))}
            </div>
          </div>

          {/* Live badge */}
          <div className="absolute left-4 top-4 flex items-center gap-2 rounded-xl bg-black/40 px-4 py-2 backdrop-blur-sm">
            <div className="relative h-2.5 w-2.5">
              <div className="absolute inset-0 rounded-full bg-cyan-400" />
              <div className="absolute inset-0 animate-ping rounded-full bg-cyan-400/40" style={{ animationDuration: "2s" }} />
            </div>
            <span className="text-[16px] font-bold text-cyan-300">{visitors.length || 0}</span>
            <span className="text-[14px] text-slate-400">{visitors.length > 0 ? "live" : ""}</span>
            {demoMode && <span className="rounded bg-amber-500/20 px-2 py-0.5 text-[10px] font-bold text-amber-300">DEMO</span>}
          </div>

          {/* Legend */}
          <div className="absolute right-3 top-3 flex flex-col gap-1.5 rounded-lg bg-black/30 px-2.5 py-2 backdrop-blur-sm">
            <span className="flex items-center gap-1.5 text-[11px]"><span className="h-2.5 w-2.5 rounded-full bg-rose-400 shadow-[0_0_6px_rgba(251,113,133,0.5)]" /><span className="text-slate-400">Hot</span></span>
            <span className="flex items-center gap-1.5 text-[11px]"><span className="h-2.5 w-2.5 rounded-full bg-amber-300 shadow-[0_0_6px_rgba(252,211,77,0.5)]" /><span className="text-slate-400">Warm</span></span>
            <span className="flex items-center gap-1.5 text-[11px]"><span className="h-2.5 w-2.5 rounded-full bg-slate-400" /><span className="text-slate-400">Cold</span></span>
          </div>

          {/* Empty state + demo button */}
          {visitors.length === 0 && (
            <div className="absolute inset-x-0 bottom-5 flex flex-col items-center gap-3 text-center">
              <p className="text-[15px] font-semibold text-slate-400">
                {coldStartPhase <= 1 ? "Scanning..." : "No visitors right now"}
              </p>
              <button
                onClick={() => setDemoMode(true)}
                className="rounded-xl border border-cyan-400/20 bg-cyan-500/10 px-5 py-2 text-[14px] font-semibold text-cyan-300 transition-all hover:bg-cyan-500/20"
              >
                Preview with demo data
              </button>
            </div>
          )}

          {/* Instruction hint */}
          {visitors.length > 0 && selectedIdx === null && (
            <div className="absolute inset-x-0 bottom-4 text-center">
              <span className="rounded-lg bg-black/40 px-4 py-1.5 text-[13px] text-cyan-300/60 backdrop-blur-sm">
                Click a dot to locate on map
              </span>
            </div>
          )}

          {/* Close button when expanded */}
          {isExpanded && (
            <button
              onClick={() => setSelectedIdx(null)}
              className="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-lg bg-black/40 px-4 py-1.5 text-[13px] text-slate-400 backdrop-blur-sm transition hover:text-white"
            >
              Close map
            </button>
          )}
        </div>
      </div>

      {/* Visitor chips — bottom bar */}
      {visitors.length > 0 && (
        <div className="border-t border-white/[0.04] px-4 py-3">
          <div className="flex gap-2 overflow-x-auto pb-1">
            {visitors.slice(0, 8).map((v, i) => (
              <button
                key={`vl-${v.visitor_id || i}`}
                className={`flex flex-shrink-0 items-center gap-2 rounded-xl border px-3.5 py-2 text-[13px] transition-all duration-200 ${
                  selectedIdx === i
                    ? "border-cyan-400/30 bg-cyan-500/[0.1] shadow-[0_0_16px_rgba(34,211,238,0.12)]"
                    : "border-white/[0.05] bg-white/[0.02] hover:bg-white/[0.04]"
                }`}
                onClick={() => handleDotClick(i)}
              >
                <span className={`h-2.5 w-2.5 flex-shrink-0 rounded-full ${intentDotClass(v.intent_level)}`} />
                <span className="font-medium text-slate-200">{v.city || "Visitor"}</span>
                {v.country_code && <span className="text-slate-500">{v.country_code}</span>}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

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
// Count-up animation component
// ---------------------------------------------------------------------------
// CountUp extracted to _components/CountUp.tsx (Phase Ω⁶ split)

// ---------------------------------------------------------------------------
// Small UI atoms
// ---------------------------------------------------------------------------
function SectionHeading({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description?: string;
  /**
   * @deprecated The "PRO" badge next to section titles was visual noise:
   * Pro users already know they're Pro, and Lite users see the ProGate
   * overlay which already labels the section as Pro. The prop is still
   * accepted at callsites (no-op) to avoid churn — clean up later.
   */
  pro?: boolean;
}) {
  return (
    <div className="mb-6">
      <h2 className="text-[1.75rem] font-extrabold tracking-tight text-[#e8a04e] sm:text-[2rem]">
        {eyebrow}
      </h2>
      {title && (
        <p className="mt-1 text-[15px] text-slate-400">{title}</p>
      )}
      {description && (
        <p className="text-[14px] text-slate-500">{description}</p>
      )}
    </div>
  );
}

function KpiCard({
  label,
  value,
  hint,
  numeric,
  onClick,
  delta,
}: {
  label: string;
  value: string;
  hint: string;
  numeric?: number;
  onClick?: () => void;
  /** Trend percentage delta: positive = up, negative = down, undefined = hidden */
  delta?: number | null;
}) {
  return (
    <div
      className={`hs-fade-up group rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5 transition-all duration-200 hover:border-[#d4893a]/20 hover:bg-white/[0.05] hover:shadow-[0_4px_24px_rgba(212,137,58,0.06)]${onClick ? " cursor-pointer select-none" : ""}`}
      onClick={onClick}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="text-[14px] font-medium text-slate-400">{label}</div>
        {delta != null && Math.abs(delta) >= 1 && (
          <span className={`flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[12px] font-bold tabular-nums ${
            delta > 0
              ? "bg-emerald-500/15 text-emerald-300"
              : "bg-rose-500/15 text-rose-300"
          }`}>
            {delta > 0 ? "↑" : "↓"}{Math.abs(Math.round(delta))}%
          </span>
        )}
      </div>
      <div className="mt-2.5 text-[2rem] font-bold tabular-nums text-white">
        {numeric !== undefined ? (
          <CountUp value={numeric} />
        ) : (
          value
        )}
      </div>
      <div className="mt-1.5 text-[13px] leading-snug text-slate-500">{hint}</div>
    </div>
  );
}

function Divider() {
  return <div className="border-t border-white/[0.06]" />;
}

// ---------------------------------------------------------------------------
// Skeleton loading states — match final card layouts
// ---------------------------------------------------------------------------
function KpiSkeleton() {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="animate-pulse rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4">
          <div className="h-3 w-20 rounded bg-white/[0.06]" />
          <div className="mt-3 h-7 w-16 rounded bg-white/[0.06]" />
          <div className="mt-2 h-2.5 w-28 rounded bg-white/[0.04]" />
        </div>
      ))}
    </div>
  );
}

function TableSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="animate-pulse overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.02]">
      <div className="border-b border-white/[0.06] px-4 py-3">
        <div className="h-3 w-32 rounded bg-white/[0.06]" />
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 border-t border-white/[0.04] px-4 py-3">
          <div className="h-2 w-2 rounded-full bg-white/[0.06]" />
          <div className="h-3 w-40 rounded bg-white/[0.06]" />
          <div className="ml-auto h-3 w-12 rounded bg-white/[0.04]" />
          <div className="h-3 w-16 rounded bg-white/[0.04]" />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Funnel Visualization — tapered horizontal flow
// ---------------------------------------------------------------------------
const FUNNEL_COLORS = [
  { bar: "bg-violet-400/70", text: "text-violet-300", glow: "shadow-[0_0_12px_rgba(139,92,246,0.15)]" },
  { bar: "bg-cyan-400/60",   text: "text-cyan-300",   glow: "shadow-[0_0_12px_rgba(34,211,238,0.12)]" },
  { bar: "bg-amber-400/60",  text: "text-amber-300",  glow: "shadow-[0_0_12px_rgba(251,191,36,0.12)]" },
  { bar: "bg-emerald-400/60",text: "text-emerald-300", glow: "shadow-[0_0_12px_rgba(52,211,153,0.12)]" },
];

function FunnelVisualization({ steps }: { steps: FunnelStep[] }) {
  const topCount = steps[0]?.count ?? 1;

  // Find the biggest single drop-off
  let worstDropIdx = -1;
  let worstDrop = 0;
  steps.forEach((s, i) => {
    if (i > 0 && s.drop_off != null && s.drop_off > worstDrop) {
      worstDrop = s.drop_off;
      worstDropIdx = i;
    }
  });

  return (
    <div className="overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.02]">
      {/* Funnel bars */}
      <div className="space-y-0 px-5 pt-5 pb-3">
        {steps.map((step, i) => {
          const widthPct = topCount > 0 ? Math.max(8, (step.count / topCount) * 100) : 8;
          const colors = FUNNEL_COLORS[i % FUNNEL_COLORS.length];
          const isWorst = i === worstDropIdx;

          return (
            <div key={step.step}>
              {/* Drop-off connector between bars */}
              {i > 0 && step.drop_off != null && (
                <div className="flex items-center gap-2.5 py-1.5 pl-3">
                  <div className="flex flex-col items-center">
                    <div className="h-3 w-px bg-white/[0.08]" />
                    <svg className="h-2 w-2 text-slate-700" viewBox="0 0 8 8" fill="currentColor">
                      <path d="M4 8L0 2h8L4 8z" />
                    </svg>
                  </div>
                  <span className={`text-[11px] tabular-nums ${isWorst ? "font-semibold text-rose-400" : "text-rose-400/50"}`}>
                    ↓ {step.drop_off}% lost
                    {isWorst && (
                      <span className="ml-1.5 rounded bg-rose-500/15 px-1.5 py-px text-[9px] font-semibold uppercase tracking-[0.08em] text-rose-300">
                        biggest drop
                      </span>
                    )}
                  </span>
                </div>
              )}

              {/* Step bar */}
              <div className="flex items-center gap-4">
                {/* Tapered bar */}
                <div className="relative min-w-0 flex-1">
                  <div
                    className={`relative flex items-center rounded-lg px-4 py-3 transition-all duration-300 ${colors.glow}`}
                    style={{ width: `${widthPct}%`, minWidth: "140px" }}
                  >
                    {/* Background fill */}
                    <div className={`absolute inset-0 rounded-lg ${colors.bar}`} />
                    {/* Content */}
                    <div className="relative z-10 flex w-full items-center justify-between gap-3">
                      <span className="text-[12px] font-semibold uppercase tracking-[0.1em] text-white/90">
                        {step.label}
                      </span>
                      <span className="text-[15px] font-bold tabular-nums text-white">
                        {formatNumber(step.count)}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Percentage badge */}
                <div className="w-16 flex-shrink-0 text-right">
                  <span className={`text-[13px] font-semibold tabular-nums ${i === 0 ? "text-slate-400" : colors.text}`}>
                    {step.pct != null ? `${step.pct}%` : "—"}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Summary insight */}
      {worstDropIdx > 0 && (
        <div className="border-t border-white/[0.06] px-5 py-3">
          <div className="flex items-start gap-2.5">
            <Image src="/branding/hedgespark/spark.png" alt="" width={18} height={18} className="mt-0.5 flex-shrink-0 opacity-80" />
            <p className="text-[12px] leading-[1.55] text-slate-400">
              Your biggest leak is between{" "}
              <span className="font-medium text-slate-300">{steps[worstDropIdx - 1]?.label}</span>
              {" "}and{" "}
              <span className="font-medium text-slate-300">{steps[worstDropIdx]?.label}</span>
              {" "}— {worstDrop}% of visitors don&apos;t make it through.
              {worstDropIdx === 1 && " Reducing friction at add-to-cart would have the biggest impact."}
              {worstDropIdx === 2 && " Checkout friction or trust signals may need attention."}
              {worstDropIdx === 3 && " Payment or shipping costs may be causing abandonment."}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// KPI Insight Modal
// ---------------------------------------------------------------------------
function KpiInsightModal({
  activeKpi,
  summary,
  onClose,
}: {
  activeKpi: string | null;
  summary: Summary;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!activeKpi) return;
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeKpi, onClose]);

  // Slide-in entry animation — resets when panel closes so next open re-animates
  const [entered, setEntered] = useState(false);
  useEffect(() => {
    if (!activeKpi) { setEntered(false); return; }
    const id = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(id);
  }, [activeKpi]);

  if (!activeKpi) return null;

  const total    = summary.total_visitors ?? 0;
  const hot      = summary.hot_visitors ?? 0;
  const warm     = summary.warm_visitors ?? 0;
  const cold     = summary.cold_visitors ?? 0;
  const events   = summary.total_events ?? 0;
  const wishlist = summary.wishlist_adds ?? 0;
  const avgIntent = summary.avg_intent_score ?? 0;
  const allTimeVisitors = summary.total_visitors_all ?? 0;
  const convReady = summary.conversion_ready_products ?? 0;

  function pie(segs: { color: string; pct: number }[]): string {
    let gradient = "";
    let cum = 0;
    for (const s of segs) {
      gradient += `${s.color} ${cum}% ${cum + s.pct}%,`;
      cum += s.pct;
    }
    return `conic-gradient(${gradient.replace(/,$/, "")})`;
  }

  type KpiData = {
    title: string;
    segments: { color: string; label: string; pct: number }[];
    numbers: { label: string; value: string }[];
    insight: [string, string];
  };

  function getKpiData(): KpiData {
    if (activeKpi === "visitors") {
      const engaged = hot + warm;
      const engPct  = total > 0 ? Math.round((engaged / total) * 100) : 0;
      const coldPct = Math.max(0, 100 - engPct);
      return {
        title: "Total Visitors",
        segments: [
          { color: "#7c3aed", label: "Engaged (Hot + Warm)", pct: engPct },
          { color: "#1e293b", label: "Cold / New",           pct: coldPct },
        ],
        numbers: [
          { label: "Total",           value: formatNumber(total)   },
          { label: "Engaged",         value: formatNumber(engaged)  },
          { label: "Cold",            value: formatNumber(cold)     },
          { label: "Engagement Rate", value: `${engPct}%`           },
        ],
        insight: [
          engPct >= 30
            ? "Warm traffic is strong — focus on converting hot visitors now."
            : "Mostly cold traffic — build awareness before pushing conversions.",
          hot > 0
            ? `${formatNumber(hot)} visitors are at peak buying intent.`
            : "No hot visitors yet — monitor for intent-driving campaigns.",
        ],
      };
    }

    if (activeKpi === "events") {
      const wishPct  = events > 0 ? Math.round((wishlist / events) * 100) : 0;
      const browsePct = Math.max(0, 100 - wishPct);
      return {
        title: "Total Events",
        segments: [
          { color: "#f43f5e", label: "Wishlist / High-intent", pct: wishPct   },
          { color: "#1e293b", label: "Browse / Passive",       pct: browsePct },
        ],
        numbers: [
          { label: "Total Events",     value: formatNumber(events)                              },
          { label: "Wishlist Events",  value: formatNumber(wishlist)                            },
          { label: "Avg per Visitor",  value: total > 0 ? formatDecimal(events / total) : "—"  },
        ],
        insight: [
          wishPct >= 10
            ? "High interaction depth — visitors are engaging meaningfully."
            : "Mostly passive browsing — product CTAs may need attention.",
          `${wishPct}% of all events are high-intent wishlist actions.`,
        ],
      };
    }

    if (activeKpi === "hot") {
      const hotPct  = total > 0 ? Math.round((hot  / total) * 100) : 0;
      const warmPct = total > 0 ? Math.round((warm / total) * 100) : 0;
      const cldPct  = Math.max(0, 100 - hotPct - warmPct);
      return {
        title: "Hot Visitors",
        segments: [
          { color: "#f43f5e", label: "Hot",  pct: hotPct  },
          { color: "#fbbf24", label: "Warm", pct: warmPct },
          { color: "#1e293b", label: "Cold", pct: cldPct  },
        ],
        numbers: [
          { label: "Hot",      value: formatNumber(hot)  },
          { label: "Warm",     value: formatNumber(warm) },
          { label: "Cold",     value: formatNumber(cold) },
          { label: "Hot Rate", value: `${hotPct}%`       },
        ],
        insight: [
          hot > 0
            ? "These visitors are ready to buy — act within 24 hours."
            : "No hot visitors yet — monitor for signals throughout the day.",
          `Hot visitors are ${hotPct}% of total traffic.`,
        ],
      };
    }

    if (activeKpi === "intent") {
      const highPct = total > 0 ? Math.round((hot  / total) * 100) : 0;
      const medPct  = total > 0 ? Math.round((warm / total) * 100) : 0;
      const lowPct  = Math.max(0, 100 - highPct - medPct);
      return {
        title: "Average Intent Score",
        segments: [
          { color: "#7c3aed", label: "High Intent (≥70)", pct: highPct },
          { color: "#f59e0b", label: "Medium (40–70)",    pct: medPct  },
          { color: "#1e293b", label: "Low Intent (<40)",  pct: lowPct  },
        ],
        numbers: [
          { label: "Avg Score",     value: formatScore(avgIntent)  },
          { label: "Max Possible",  value: "100"                   },
          { label: "High Intent",   value: formatNumber(hot)       },
          { label: "Medium Intent", value: formatNumber(warm)      },
        ],
        insight: [
          avgIntent >= 65
            ? "Strong intent — your store is attracting ready-to-buy visitors."
            : avgIntent >= 40
            ? "Moderate intent — improve product pages to push visitors toward purchase."
            : "Low intent — focus on relevance and page engagement first.",
          `Score of ${formatScore(avgIntent)}/100 reflects collective purchase readiness.`,
        ],
      };
    }

    if (activeKpi === "distribution") {
      const t       = Math.max(hot + warm + cold, 1);
      const hotPct  = Math.round((hot  / t) * 100);
      const warmPct = Math.round((warm / t) * 100);
      const cldPct  = Math.max(0, 100 - hotPct - warmPct);
      return {
        title: "Intent Distribution",
        segments: [
          { color: "#f43f5e", label: "Hot",  pct: hotPct  },
          { color: "#fbbf24", label: "Warm", pct: warmPct },
          { color: "#334155", label: "Cold", pct: cldPct  },
        ],
        numbers: [
          { label: "Hot",  value: `${formatNumber(hot)}  (${hotPct}%)`  },
          { label: "Warm", value: `${formatNumber(warm)} (${warmPct}%)` },
          { label: "Cold", value: `${formatNumber(cold)} (${cldPct}%)`  },
        ],
        insight: [
          hotPct + warmPct >= 40
            ? "Healthy funnel — over 40% of visitors show purchase signals."
            : "Thin funnel — most visitors are cold. Improve top-of-funnel pages.",
          "Focus campaigns on converting warm visitors to hot.",
        ],
      };
    }

    if (activeKpi === "alltime") {
      const recent24h = summary.total_visitors_24h ?? 0;
      const recentPct = allTimeVisitors > 0 ? Math.round((recent24h / allTimeVisitors) * 100) : 0;
      const olderPct = Math.max(0, 100 - recentPct);
      return {
        title: "All-Time Visitors",
        segments: [
          { color: "#7c3aed", label: "Active (24h)", pct: recentPct },
          { color: "#1e293b", label: "Historical",   pct: olderPct  },
        ],
        numbers: [
          { label: "All-Time Total",  value: formatNumber(allTimeVisitors) },
          { label: "Active (24h)",    value: formatNumber(recent24h)       },
          { label: "Recency Rate",    value: `${recentPct}%`              },
        ],
        insight: [
          recentPct >= 10
            ? "Healthy visitor recency — recent traffic is contributing to your total."
            : "Most visitors are historical — focus on re-engagement campaigns.",
          `${formatNumber(recent24h)} visitors active in the last 24 hours out of ${formatNumber(allTimeVisitors)} total.`,
        ],
      };
    }

    if (activeKpi === "products") {
      const hotPool   = Math.max(hot + warm, 1);
      const readyPct  = Math.min(100, Math.round((convReady / hotPool) * 100));
      const notPct    = Math.max(0, 100 - readyPct);
      return {
        title: "Conversion-ready Products",
        segments: [
          { color: "#10b981", label: "Ready to Convert", pct: readyPct },
          { color: "#1e293b", label: "Not Yet Ready",    pct: notPct   },
        ],
        numbers: [
          { label: "Ready Products",   value: formatNumber(convReady)        },
          { label: "Hot + Warm Pool",  value: formatNumber(hot + warm)       },
          { label: "Opportunity Rate", value: `${readyPct}%`                 },
        ],
        insight: [
          convReady > 0
            ? "Act now — these products have live visitors at peak intent."
            : "No products at peak conversion readiness yet. Check back soon.",
          "Each ready product is a time-sensitive revenue opportunity.",
        ],
      };
    }

    if (activeKpi === "wishlist") {
      const wishlistRate = total > 0 ? Math.round((wishlist / total) * 100) : 0;
      const nonWishlist  = Math.max(0, total - wishlist);
      const wishPct      = Math.min(100, wishlistRate);
      const nonWishPct   = Math.max(0, 100 - wishPct);
      return {
        title: "Wishlist Adds",
        segments: [
          { color: "#f43f5e", label: "Added to Wishlist", pct: wishPct    },
          { color: "#1e293b", label: "Browsed Only",       pct: nonWishPct },
        ],
        numbers: [
          { label: "Wishlist Adds",  value: formatNumber(wishlist)     },
          { label: "Browsed Only",   value: formatNumber(nonWishlist)  },
          { label: "Wishlist Rate",  value: `${wishlistRate}%`         },
        ],
        insight: [
          wishlistRate >= 15
            ? "High wishlist rate — these visitors have strong product desire."
            : wishlist > 0
            ? "Some wishlist activity — target these visitors with follow-up campaigns."
            : "No wishlist adds yet — consider adding wishlist CTAs to product pages.",
          "Wishlist adds are your strongest intent signal before purchase.",
        ],
      };
    }

    return { title: "", segments: [], numbers: [], insight: ["", ""] };
  }

  const d = getKpiData();

  return (
    <>
      {/* Subtle backdrop — click outside to close; no blur so dashboard stays legible */}
      <div
        className="fixed inset-0 z-40 bg-black/30"
        onClick={onClose}
      />

      {/* Floating insight card */}
      <div
        className="fixed right-6 top-6 z-50 w-[440px] max-w-[calc(100vw-3rem)] overflow-y-auto rounded-3xl bg-[#09091a]"
        style={{
          maxHeight: "calc(100vh - 48px)",
          border: "1px solid rgba(124,58,237,0.16)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.65), 0 0 0 1px rgba(124,58,237,0.06)",
          transform: entered ? "translateY(0) scale(1)" : "translateY(-8px) scale(0.98)",
          opacity: entered ? 1 : 0,
          transition: "transform 220ms cubic-bezier(0.16,1,0.3,1), opacity 180ms ease",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between border-b border-white/[0.06] px-6 py-5">
          <div>
            <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-violet-400/50">
              KPI Insight
            </div>
            <h2 className="text-[15px] font-semibold text-white">{d.title}</h2>
          </div>
          <button
            onClick={onClose}
            className="ml-4 mt-0.5 flex-shrink-0 rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-white/[0.05] hover:text-slate-300"
            aria-label="Close"
          >
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="space-y-4 px-6 py-5">

          {/* Pie chart + legend */}
          {d.segments.length > 0 && (
            <div className="flex items-center gap-6 rounded-2xl border border-white/[0.06] bg-white/[0.025] p-5">
              <div
                className="relative flex-shrink-0 rounded-full"
                style={{ width: 88, height: 88, background: pie(d.segments) }}
              >
                <div
                  className="absolute rounded-full"
                  style={{ width: 50, height: 50, top: "50%", left: "50%", transform: "translate(-50%,-50%)", background: "#09091a" }}
                />
              </div>
              <div className="min-w-0 flex-1 space-y-2">
                {d.segments.map((s) => (
                  <div key={s.label} className="flex items-center gap-2.5">
                    <span className="h-2 w-2 flex-shrink-0 rounded-full" style={{ background: s.color }} />
                    <span className="truncate text-[11px] text-slate-400">{s.label}</span>
                    <span className="ml-auto flex-shrink-0 text-[12px] font-semibold tabular-nums text-white">{s.pct}%</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Numbers grid */}
          <div className="grid grid-cols-2 gap-2">
            {d.numbers.map((n) => (
              <div key={n.label} className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <div className="text-[10px] uppercase tracking-[0.1em] text-slate-600">{n.label}</div>
                <div className="mt-1 text-[15px] font-semibold tabular-nums text-white">{n.value}</div>
              </div>
            ))}
          </div>

          {/* Insight */}
          <div className="rounded-xl border border-violet-400/[0.12] bg-violet-500/[0.06] px-4 py-3.5">
            <p className="text-[12px] leading-[1.6] text-slate-300">{d.insight[0]}</p>
            <p className="mt-1.5 text-[11px] leading-[1.5] text-slate-500">{d.insight[1]}</p>
          </div>

        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Traffic Source Intelligence Box
// ---------------------------------------------------------------------------
function TrafficSourceBox({
  sourceQuality,
  isProUser,
  onUpgradeClick,
}: {
  sourceQuality: SourceQualityData | null;
  isProUser: boolean;
  onUpgradeClick: () => void;
}) {
  function qualityColor(label: string): string {
    if (label === "Strong intent") return "text-emerald-400";
    if (label === "Mixed intent")  return "text-amber-400";
    return "text-slate-500";
  }

  // Product label derived from the URL returned by the API
  let productLabel = "Top product";
  if (sourceQuality?.product_url) {
    try {
      const path = new URL(sourceQuality.product_url).pathname;
      const slug = path.split("/").filter(Boolean).pop() || "";
      if (slug) productLabel = slug.replace(/-/g, " ");
    } catch {
      productLabel = sourceQuality.product_url;
    }
  }

  // Human-readable names for all source_type values the tracker can emit.
  // Covers both legacy coarse buckets and the new specific-source values.
  const SOURCE_NAMES: Record<string, string> = {
    direct:     "Direct",
    referral:   "Referral",
    email:      "Email",
    unknown:    "Unattributed",
    // search engines
    google:     "Google",
    bing:       "Bing",
    yahoo:      "Yahoo",
    duckduckgo: "DuckDuckGo",
    baidu:      "Baidu",
    // social networks
    facebook:   "Facebook",
    instagram:  "Instagram",
    tiktok:     "TikTok",
    twitter:    "Twitter / X",
    pinterest:  "Pinterest",
    linkedin:   "LinkedIn",
    youtube:    "YouTube",
    reddit:     "Reddit",
    snapchat:   "Snapchat",
    // marketplaces
    amazon:     "Amazon",
    ebay:       "eBay",
    etsy:       "Etsy",
    // legacy coarse buckets (pre-tracker-upgrade rows)
    search:     "Organic / Search",
    social:     "Social",
  };

  const sources = sourceQuality?.sources ?? [];
  const totalVisitors = sources.reduce((s, r) => s + r.visitors, 0);

  return (
    <div className="hs-fade-up flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4">
      <div className="mb-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
          Where high-intent traffic comes from
        </div>
        <p className="mt-0.5 truncate text-[11px] text-slate-600" title={productLabel}>
          {productLabel}
        </p>
      </div>

      {sources.length === 0 ? (
        <p className="text-[12px] text-slate-600">
          Not enough traffic data yet to evaluate sources.
        </p>
      ) : (
        <>
          <div className="space-y-3">
            {sources.map((src) => {
              const name   = SOURCE_NAMES[src.source_type] ?? src.source_type;
              const color  = qualityColor(src.quality_label);
              const barPct = totalVisitors > 0
                ? Math.round((src.visitors / totalVisitors) * 100)
                : 0;

              // attention_label badge styling
              const attnStyle =
                src.attention_label === "Best source"
                  ? "bg-violet-500/20 text-violet-300"
                  : src.attention_label === "Low signal"
                  ? "bg-white/[0.04] text-slate-600"
                  : "bg-white/[0.04] text-slate-500";

              return (
                <div key={src.source_type}>
                  <div className="mb-1 flex items-center justify-between gap-2">
                    {/* Source name + status badge (attention_label values are self-describing) */}
                    <div className="flex min-w-0 items-center gap-1.5">
                      <span className="text-[12px] text-slate-300">{name}</span>
                      <span className={`rounded px-1 py-px text-[9px] font-semibold leading-none ${attnStyle}`}>
                        {src.attention_label}
                      </span>
                    </div>
                    {/* Score + quality label + share */}
                    <div className="flex flex-shrink-0 items-center gap-2">
                      <span className="text-[10px] tabular-nums text-slate-600">{src.quality_score}</span>
                      <span className={`text-[10px] font-medium ${color}`}>{src.quality_label}</span>
                      <span className="w-7 text-right text-[11px] tabular-nums text-slate-500">{barPct}%</span>
                    </div>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                    <div className="h-full rounded-full bg-violet-500/50" style={{ width: `${barPct}%` }} />
                  </div>

                  {/*
                    action_insight — prescriptive recommended action, Pro only.
                    Present only when fetched from /analytics/source-quality/pro.
                    Lite users never see this field; it is absent from their response.
                    Boundary: diagnostic (quality_label) = Lite, prescriptive (action_insight) = Pro.
                  */}
                  {isProUser && src.action_insight && (
                    <div className="mt-1.5 flex items-start gap-2 rounded-lg border border-emerald-400/15 bg-emerald-500/[0.05] px-2.5 py-1.5">
                      <span className="mt-1 h-1 w-1 flex-shrink-0 rounded-full bg-emerald-400/80" />
                      <p className="text-[11px] leading-[1.5] text-slate-300">{src.action_insight}</p>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Insight — visible to all tiers */}
          <p className="mt-auto border-t border-white/[0.05] pt-3 text-[11px] leading-[1.55] text-slate-500">
            {sourceQuality!.insight}
          </p>

          {/* Pro upsell — shown only to free users, below real data */}
          {!isProUser && (
            <div className="mt-2 flex items-center justify-between rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2">
              <span className="text-[11px] text-slate-600">
                Historical trends &amp; advanced recommendations in Pro
              </span>
              <button
                onClick={onUpgradeClick}
                className="ml-3 flex-shrink-0 rounded-md bg-violet-600/80 px-2.5 py-1 text-[10px] font-semibold text-white hover:bg-violet-500 transition-colors"
              >
                Upgrade
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Product Revenue Potential Panel
// ---------------------------------------------------------------------------

function ProductInsightPanel({
  product,
  mergedProducts,
  isProUser,
  onClose,
  shopAov,
  shopCurrency,
  aovIsReal,
}: {
  product: TopProduct | null;
  mergedProducts: MergedProductRow[];
  isProUser: boolean;
  onClose: () => void;
  shopAov: number;
  shopCurrency: string;
  aovIsReal: boolean;
}) {
  // ESC to close
  useEffect(() => {
    if (!product) return;
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [product, onClose]);

  // Slide-in animation — resets on close so next open re-animates
  const [entered, setEntered] = useState(false);
  useEffect(() => {
    if (!product) { setEntered(false); return; }
    const id = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(id);
  }, [product]);

  if (!product) return null;

  // Try to match a richer MergedProductRow by checking if the product_id
  // appears as a substring of the product_url (Shopify URL heuristic).
  const merged = mergedProducts.find((m) => {
    const pid = (product.product_id || "").toLowerCase().trim();
    const url = (m.product_url || "").toLowerCase();
    return pid.length > 0 && url.includes(pid);
  }) ?? null;

  // Views used for uplift formula: prefer 24h metric, fallback to total_views/7
  const views24h = merged?.views_24h ?? Math.round((product.total_views ?? 0) / 7);
  const aov = shopAov || 50;
  const ccy = shopCurrency || "USD";
  const uplift1  = Math.round(views24h * 0.01 * aov);
  const uplift2  = Math.round(views24h * 0.02 * aov);

  // Leverage badge from attention_score
  const attScore = merged?.attention_score ?? 0;
  const leverage =
    attScore >= 0.70
      ? { label: "High leverage",   cls: "bg-rose-500/15 text-rose-300 ring-1 ring-rose-400/30" }
      : attScore >= 0.40
      ? { label: "Worth testing",   cls: "bg-amber-500/15 text-amber-300 ring-1 ring-amber-400/30" }
      : { label: "Lower priority",  cls: "bg-white/5 text-slate-400 ring-1 ring-white/10" };

  // Block B — explanation sentence derived from row data
  const v    = merged?.views_24h ?? 0;
  const conv = merged?.cart_conversions_24h ?? 0;
  const eng  = merged?.engagement_score ?? 0;
  const scrl = merged?.avg_scroll_24h ?? 0;
  const explanation =
    merged === null
      ? "Based on visitor intent signals, this product has meaningful conversion potential."
      : eng > 0.8 && conv === 0
      ? "Visitors are showing strong interest but the product is not converting."
      : v > 20 && conv === 0
      ? "Visitors are interested, but the product is not converting."
      : scrl > 70 && conv === 0
      ? "Users are reaching the page content but not taking action."
      : eng > 0.6
      ? "This product is getting strong attention from visitors."
      : "Monitor this product — it is showing early engagement signals.";

  // Block C — suggested focus (Pro full / Lite teaser)
  const suggestion =
    merged?.action_suggestion
      ? merged.action_suggestion
      : conv === 0 && eng > 0.6
      ? "Review CTA placement and product page clarity."
      : conv === 0
      ? "Check pricing, urgency, or trust signals on the product page."
      : "Optimise the checkout flow to reduce drop-off.";

  const productLabel = product.product_name || product.product_id || "—";

  return (
    <>
      {/* Subtle backdrop — click outside to close */}
      <div className="fixed inset-0 z-40 bg-black/25" onClick={onClose} />

      {/* Floating insight card */}
      <div
        className="fixed right-6 top-6 z-50 w-[460px] max-w-[calc(100vw-3rem)] overflow-y-auto rounded-3xl bg-[#09091a]"
        style={{
          maxHeight: "calc(100vh - 48px)",
          border: "1px solid rgba(124,58,237,0.16)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.65), 0 0 0 1px rgba(124,58,237,0.06)",
          transform: entered ? "translateY(0) scale(1)" : "translateY(-8px) scale(0.98)",
          opacity: entered ? 1 : 0,
          transition: "transform 220ms cubic-bezier(0.16,1,0.3,1), opacity 180ms ease",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between border-b border-white/[0.06] px-6 py-5">
          <div className="min-w-0 flex-1">
            <div className="mb-1">
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${leverage.cls}`}>
                {leverage.label}
              </span>
            </div>
            <h2 className="text-[15px] font-semibold text-white">Revenue Potential</h2>
            <p className="mt-0.5 truncate text-[11px] text-slate-500" title={productLabel}>
              {productLabel}
            </p>
          </div>
          <button
            onClick={onClose}
            className="ml-4 mt-0.5 flex-shrink-0 rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-white/[0.05] hover:text-slate-300"
            aria-label="Close"
          >
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="space-y-5 px-6 py-5">

          {/* Block A — Potential Impact */}
          <div>
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
              Potential Impact
            </p>
            {isProUser ? (
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-xl border border-emerald-400/[0.15] bg-emerald-500/[0.06] px-4 py-3">
                  <div className="text-[10px] text-slate-500">+1% conversion / day</div>
                  <div className="mt-1 text-[20px] font-semibold tabular-nums text-emerald-300">{ccy === "EUR" ? "€" : ccy === "GBP" ? "£" : "$"}{uplift1}</div>
                  <div className="mt-0.5 text-[10px] text-slate-600">
                    {views24h} views × 1% × {ccy} {aov}{!aovIsReal && " (est.)"}
                  </div>
                </div>
                <div className="rounded-xl border border-emerald-400/[0.22] bg-emerald-500/[0.09] px-4 py-3">
                  <div className="text-[10px] text-slate-500">+2% conversion / day</div>
                  <div className="mt-1 text-[20px] font-semibold tabular-nums text-emerald-300">{ccy === "EUR" ? "€" : ccy === "GBP" ? "£" : "$"}{uplift2}</div>
                  <div className="mt-0.5 text-[10px] text-slate-600">
                    {views24h} views × 2% × {ccy} {aov}{!aovIsReal && " (est.)"}
                  </div>
                </div>
              </div>
            ) : (
              <div className="rounded-xl border border-violet-400/[0.10] bg-violet-500/[0.05] px-4 py-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-[12px] text-slate-300">This product may be generating untapped revenue.</p>
                    <p className="mt-1 text-[11px] text-slate-600">Upgrade to quantify the opportunity.</p>
                  </div>
                  <span className="flex-shrink-0 rounded-full border border-violet-400/25 bg-violet-500/10 px-2 py-0.5 text-[10px] font-semibold text-violet-400/70">
                    PRO
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Block B — Why this product matters */}
          <div>
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
              Why It Matters
            </p>
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <div className="text-[10px] uppercase tracking-[0.1em] text-slate-600">Views 24h</div>
                <div className="mt-1 text-[15px] font-semibold tabular-nums text-white">
                  {merged ? formatNumber(merged.views_24h) : formatNumber(product.total_views)}
                </div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <div className="text-[10px] uppercase tracking-[0.1em] text-slate-600">Avg Dwell</div>
                <div className="mt-1 text-[15px] font-semibold tabular-nums text-white">
                  {merged?.avg_dwell_24h != null ? `${Math.round(merged.avg_dwell_24h)}s` : "—"}
                </div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <div className="text-[10px] uppercase tracking-[0.1em] text-slate-600">Avg Scroll</div>
                <div className="mt-1 text-[15px] font-semibold tabular-nums text-white">
                  {merged?.avg_scroll_24h != null ? `${Math.round(merged.avg_scroll_24h)}%` : "—"}
                </div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <div className="text-[10px] uppercase tracking-[0.1em] text-slate-600">Cart Conv.</div>
                <div className="mt-1 text-[15px] font-semibold tabular-nums text-white">
                  {merged?.cart_conversions_24h != null ? formatNumber(merged.cart_conversions_24h) : "—"}
                </div>
              </div>
            </div>
            <p className="mt-2 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3 text-[12px] leading-[1.6] text-slate-300">
              {explanation}
            </p>
          </div>

          {/* Block C — Suggested Focus */}
          <div>
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
              Suggested Focus
            </p>
            {isProUser ? (
              <div className="rounded-xl border border-violet-400/[0.12] bg-violet-500/[0.06] px-4 py-3.5">
                <p className="text-[12px] leading-[1.6] text-slate-300">{suggestion}</p>
              </div>
            ) : (
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3.5">
                <p className="text-[12px] leading-[1.6] text-slate-400">
                  This product likely needs improvements in conversion elements.
                </p>
              </div>
            )}
          </div>

        </div>
      </div>
    </>
  );
}

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
  // Setup checks + readiness — populated by OnboardingHub callback
  const [setupChecks, setSetupChecks] = useState<{
    merchant_exists: boolean; install_active: boolean; token_ok: boolean;
    webhook_ok: boolean; tracker_ok: boolean;
    billing_active: boolean; billing_plan: string; billing_charge_pending: boolean;
  } | null>(null);
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
  const [candidates, setCandidates] = useState<ActionCandidate[]>([]);
  const [taskMap, setTaskMap] = useState<Map<string, ActionTask>>(new Map());
  const hasExecutingRef = useRef(false);
  const [expandedTaskKey, setExpandedTaskKey] = useState<string | null>(null);

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
      setSetupChecks(checks);
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
          setTaskMap(buildTaskMap(tasks));
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
        setTaskMap(buildTaskMap(tasks));
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

  // Build task map from a flat task list.
  // Tasks arrive newest-first (ORDER BY created_at DESC).
  // We keep only the FIRST task seen per key so newer tasks win.
  // Dismissed tasks are excluded so their candidates show Execute again.
  function buildTaskMap(tasks: ActionTask[]): Map<string, ActionTask> {
    const map = new Map<string, ActionTask>();
    for (const t of tasks) {
      if (t.status === "dismissed") continue;
      const key = taskKey(t.product_url, t.action_type);
      if (!map.has(key)) {
        map.set(key, t);
      }
    }
    return map;
  }

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
      const task: ActionTask | undefined = j.task;
      if (!task) return;
      setTaskMap((prev) => {
        const next = new Map(prev);
        next.set(taskKey(task.product_url, task.action_type), task);
        return next;
      });
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
      setTaskMap((prev) => {
        const next = new Map(prev);
        next.delete(key);
        return next;
      });
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
