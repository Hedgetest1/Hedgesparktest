"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";

import { Sidebar } from "./components/Sidebar";
import { TopBar, type TrialInfo } from "./components/TopBar";
import { UpgradeModal } from "./components/UpgradeModal";
import { ProGate } from "./components/ProGate";
import { MascotLoader, MascotEmpty } from "./components/MascotLoader";
import { SignalCard, type OpportunitySignal } from "./components/SignalCard";
import { BriefHero, type DailyBrief } from "./components/BriefHero";
import { RevenueWindowPro, RevenueWindowLite } from "./components/RevenueWindowBanner";
import { LiftReport } from "./components/LiftReport";
import { HeatmapCard } from "./components/HeatmapCard";
import { CohortTable } from "./components/CohortTable";
import { SetupStatusPanel } from "./components/SetupStatusPanel";
import { OnboardingChecklist, type OnboardingData } from "./components/OnboardingChecklist";
import { NudgePerformance } from "./components/NudgePerformance";
import { AudienceSegments } from "./components/AudienceSegments";
import { OrdersSummary } from "./components/OrdersSummary";
import { ProductConversions } from "./components/ProductConversions";
import { ActionProof } from "./components/ActionProof";
import { RevenueHero } from "./components/RevenueHero";
import { TopSignalCard, loadRecentActions, type RecentAction } from "./components/TopSignalCard";
import { RecentActions } from "./components/RecentActions";
import { ProofHeroCard } from "./components/ProofHeroCard";
import { computeActions, type SparkAction } from "./lib/actionEngine";
import { updateReputation } from "./lib/sparkReputation";
import { generateNotifications, loadSettings, type SparkNotification } from "./lib/sparkNotifications";
import { SparkToast } from "./components/NotificationBell";
import { SparkInline } from "./components/SparkCompanion";
import { SupportChat } from "./components/SupportChat";

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
  // PRO ONLY — present only when fetched from /analytics/alerts/pro.
  // Absent from Lite responses; the alert card renders it when present.
  action?: string;
};

type TopPage = {
  url?: string;
  views?: number;
  visitors?: number;
  avg_dwell?: number;
};

type SessionRow = {
  visitor_id: string;
  pages_visited: string[];
  total_duration_seconds: number;
  last_page: string | null;
  event_count: number;
};

type FunnelStep = {
  step: string;
  label: string;
  count: number;
  pct: number | null;
  drop_off: number | null;
};

type ClickRow = {
  url: string;
  clicks: number;
};

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
// Inline sparkline — pixel heights, no CSS percentage ambiguity
// ---------------------------------------------------------------------------
const SPARK_H = 28; // container height in px

function InlineSparkline({ values }: { values: number[] }) {
  if (!Array.isArray(values) || values.length === 0) return null;
  const clean = values.map((v) => (typeof v === "number" && !Number.isNaN(v) ? v : 0));
  const max = Math.max(...clean, 1);
  return (
    <div
      className="flex items-end gap-px"
      style={{ height: SPARK_H, width: 64 }}
      aria-hidden="true"
    >
      {clean.map((v, i) => {
        const px = Math.max(2, Math.round((v / max) * SPARK_H));
        return (
          <div
            key={i}
            className="flex-1 rounded-[2px] bg-violet-400/60"
            style={{ height: px }}
            title={String(v)}
          />
        );
      })}
    </div>
  );
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
        <p className="mt-1 max-w-2xl text-[13px] text-slate-400">{description}</p>
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
      className={`hs-fade-up rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4 transition-all duration-150 hover:border-violet-400/20 hover:bg-white/[0.05] hover:shadow-[0_0_20px_rgba(124,58,237,0.06)]${onClick ? " cursor-pointer select-none" : ""}`}
      onClick={onClick}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="text-[12px] text-slate-500">{label}</div>
        {delta != null && Math.abs(delta) >= 1 && (
          <span className={`flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums ${
            delta > 0
              ? "bg-emerald-500/15 text-emerald-300"
              : "bg-rose-500/15 text-rose-300"
          }`}>
            {delta > 0 ? "↑" : "↓"}{Math.abs(Math.round(delta))}%
          </span>
        )}
      </div>
      <div className="mt-2 text-2xl font-semibold tabular-nums text-white">
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
            <Image src="/branding/hedgespark-mascot.png" alt="" width={18} height={18} className="mt-0.5 flex-shrink-0 opacity-80" />
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
                    <div className="mt-1.5 rounded-lg border border-emerald-400/15 bg-emerald-500/[0.05] px-2.5 py-1.5">
                      <div className="mb-0.5 flex items-center gap-1.5">
                        <span className="text-[9px] font-semibold uppercase tracking-[0.14em] text-emerald-300/70">
                          Action
                        </span>
                        <span className="rounded border border-violet-400/20 px-1 py-px text-[9px] font-normal normal-case tracking-normal text-violet-400/70">
                          PRO
                        </span>
                      </div>
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
// Main page
// ---------------------------------------------------------------------------
export default function Page() {
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
  // True when the page loaded with ?billing=activated — triggers deep re-check in SetupStatusPanel
  const [billingJustActivated, setBillingJustActivated] = useState(false);
  // Setup checks + readiness — populated by SetupStatusPanel callback, consumed by OnboardingChecklist
  const [setupChecks, setSetupChecks] = useState<OnboardingData["setupChecks"]>(null);
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
  const [attrSummary, setAttrSummary] = useState<Record<string, unknown> | null>(null);
  const [ltvData, setLtvData] = useState<Record<string, unknown> | null>(null);
  const [forecastData, setForecastData] = useState<Record<string, unknown> | null>(null);
  const [behavioralData, setBehavioralData] = useState<Record<string, unknown> | null>(null);

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
      window.history.replaceState({}, "", `/${clean ? `?${clean}` : ""}`);
      setTimeout(() => setBillingToast((prev) => prev ? { ...prev, visible: false } : null), 8000);
    }

    // 2. Resolve shop from session cookie via /merchant/me
    if (!API_BASE) {
      setSessionResolved(true);
      return;
    }

    fetch(`${API_BASE}/merchant/me`, {
      headers: apiHeaders(),
      credentials: "include",
      cache: "no-store",
    })
      .then(async (res) => {
        if (res.ok) {
          const json = await res.json();
          const shopDomain = json.shop_domain;
          if (shopDomain) {
            setShop(shopDomain);
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
              window.history.replaceState({}, "", `/${cleaned ? `?${cleaned}` : ""}`);
            }
            return;
          }
        }

        // 3. No valid session — fall back to ?shop= from URL
        // This covers the OAuth redirect landing and dev usage
        const urlShop = params.get("shop") || "";
        if (urlShop) {
          setShop(urlShop);
          // Try to fetch plan for this shop (will work if cookie exists for it)
          try {
            const planRes = await fetch(
              `${API_BASE}/merchant/plan?shop=${encodeURIComponent(urlShop)}`,
              { headers: apiHeaders(), credentials: "include", cache: "no-store" }
            );
            if (planRes.ok) {
              const planJson = await planRes.json();
              const isPro = planJson.plan === "pro" && planJson.billing_active === true;
              setTier(isPro ? "pro" : "lite");
              if (planJson.pro_trial_days != null) setProTrialDays(planJson.pro_trial_days);
              if (planJson.pro_price != null) setProPrice(planJson.pro_price);
              setBillingConfirmedAt(planJson.billing_confirmed_at ?? null);
            }
          } catch { /* tier stays lite */ }
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
    window.history.replaceState({}, "", `/${clean ? `?${clean}` : ""}`);

    // Load recent actions from localStorage
    setRecentActions(loadRecentActions());
  }, [sessionResolved, tier]);

  // ---------------------------------------------------------------------------
  // Setup status → tier sync callback
  // When SetupStatusPanel detects pro_active, upgrade tier immediately
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
        const res = await fetch(`${API_BASE}/system/health`, { cache: "no-store" });
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
        const [candRes, taskRes] = await Promise.all([
          fetch(
            `${API_BASE}/actions/candidates/pro?shop=${encodeURIComponent(shop)}`,
            { headers: apiHeaders(), credentials: "include", cache: "no-store" }
          ),
          fetch(
            `${API_BASE}/actions/tasks?limit=50&shop=${encodeURIComponent(shop)}`,
            { headers: apiHeaders(), credentials: "include", cache: "no-store" }
          ),
        ]);
        if (!active) return;
        if (candRes.ok) {
          const j = await candRes.json();
          setCandidates(Array.isArray(j.candidates) ? j.candidates : []);
        }
        if (taskRes.ok) {
          const j = await taskRes.json();
          const tasks: ActionTask[] = Array.isArray(j.tasks) ? j.tasks : [];
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
        const res = await fetch(
          `${API_BASE}/actions/tasks?limit=50&shop=${encodeURIComponent(shop)}`,
          { headers: apiHeaders(), credentials: "include", cache: "no-store" }
        );
        if (!active || !res.ok) return;
        const j = await res.json();
        const tasks: ActionTask[] = Array.isArray(j.tasks) ? j.tasks : [];
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
      const enc = encodeURIComponent(shop);
      const opts = { headers: apiHeaders(), credentials: "include" as const, cache: "no-store" as const };

      const [attrRes, ltvRes, fcRes, behRes] = await Promise.allSettled([
        fetch(`${API_BASE}/attribution/summary/pro?shop=${enc}&days=30`, opts),
        fetch(`${API_BASE}/pro/cohorts/monthly?shop=${enc}&months=6`, opts),
        fetch(`${API_BASE}/orders/forecast/pro?shop=${enc}`, opts),
        fetch(`${API_BASE}/pro/cohorts/behavioral?shop=${enc}&days=90`, opts),
      ]);

      if (!active) return;

      if (attrRes.status === "fulfilled" && attrRes.value.ok) {
        try { setAttrSummary(await attrRes.value.json()); } catch {}
      }
      if (ltvRes.status === "fulfilled" && ltvRes.value.ok) {
        try { setLtvData(await ltvRes.value.json()); } catch {}
      }
      if (fcRes.status === "fulfilled" && fcRes.value.ok) {
        try { setForecastData(await fcRes.value.json()); } catch {}
      }
      if (behRes.status === "fulfilled" && behRes.value.ok) {
        try { setBehavioralData(await behRes.value.json()); } catch {}
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
        // Core analytics: alerts + weekly trend (all tiers)
        const [alertsRes, trendRes] = await Promise.all([
          fetch(alertsEndpoint, { headers: apiHeaders(), credentials: "include", cache: "no-store" }),
          fetch(`${API_BASE}/analytics/weekly-trend?shop=${encodeURIComponent(shop)}`, { headers: apiHeaders(), credentials: "include", cache: "no-store" }),
        ]);

        // Detect session expiry on core analytics (most reliable signal)
        if ([alertsRes.status, trendRes.status].some((s) => s === 401 || s === 403)) {
          dispatchSessionExpired();
          return;
        }

        const alertsJson = alertsRes.ok ? await alertsRes.json() : { alerts: [] };
        const trendJson  = trendRes.ok  ? await trendRes.json()  : { trend: [] };

        if (!active) return;
        setAlerts(Array.isArray(alertsJson.alerts) ? alertsJson.alerts : []);
        setTrend(Array.isArray(trendJson.trend) ? trendJson.trend : []);
        setAnalyticsError("");
        setLastUpdated(new Date());

        // Extended analytics: sessions, funnel, clicks, top pages — Pro only.
        // Lite users see empty/placeholder UI for these sections, so fetching
        // them wastes 4 network requests + server queries per load cycle.
        // Top pages — available to all tiers
        try {
          const pagesRes = await fetch(`${API_BASE}/analytics/top-pages?shop=${encodeURIComponent(shop)}`, { headers: apiHeaders(), credentials: "include", cache: "no-store" });
          const pagesJson = pagesRes.ok ? await pagesRes.json() : { pages: [] };
          if (active) setTopPages(Array.isArray(pagesJson.pages) ? pagesJson.pages : []);
        } catch { /* top pages is supplementary — degrade silently */ }

        // Funnel, sessions, clicks — Pro only
        if (tier === "pro") {
          const [sessionsRes, funnelRes, clicksRes] = await Promise.all([
            fetch(`${API_BASE}/analytics/sessions?shop=${encodeURIComponent(shop)}`,  { headers: apiHeaders(), credentials: "include", cache: "no-store" }),
            fetch(`${API_BASE}/analytics/funnel?shop=${encodeURIComponent(shop)}`,    { headers: apiHeaders(), credentials: "include", cache: "no-store" }),
            fetch(`${API_BASE}/analytics/clicks?shop=${encodeURIComponent(shop)}`,    { headers: apiHeaders(), credentials: "include", cache: "no-store" }),
          ]);

          const sessionsJson = sessionsRes.ok ? await sessionsRes.json() : { sessions: [] };
          const funnelJson   = funnelRes.ok   ? await funnelRes.json()   : { steps: [] };
          const clicksJson   = clicksRes.ok   ? await clicksRes.json()   : { clicks: [] };

          if (!active) return;
          setSessions(Array.isArray(sessionsJson.sessions) ? sessionsJson.sessions : []);
          setFunnelSteps(Array.isArray(funnelJson.steps) ? funnelJson.steps : []);
          setClicks(Array.isArray(clicksJson.clicks) ? clicksJson.clicks : []);
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
    const headline = topSig
      ? `I'd start with ${topSig.human_label || shortUrl(topSig.product_url || "this product")} \u2014 it's showing ${prettyText(topSig.signal_type).toLowerCase()} and deserves attention`
      : earlySignals.length > 0
      ? "We're seeing early visitor activity — signals will sharpen as traffic grows"
      : `${formatNumber(totalViews)} views tracked today — check the product table to see what's ready to convert`;
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
    <div className="flex h-screen overflow-hidden bg-[#080811] text-white">
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((c) => !c)}
        activeSection={activeSection}
        onNavigate={handleNavigate}
        tier={tier}
        sparkContext={sparkContext}
      />

      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar shop={shop} tier={tier} onTierToggle={handleTierToggle} trial={trialInfo} notifications={activeToasts} reputation={sparkReputation} />

        <main ref={mainRef} className="flex-1 overflow-y-auto hs-scroll-smooth">
          {!sessionResolved ? (
            <MascotLoader caption="Connecting to your store…" />
          ) : !shop ? (
            <div className="flex flex-col items-center justify-center gap-6 py-24 px-6">
              <MascotLoader caption="Waiting for a shop connection…" />
              <div className="max-w-sm rounded-2xl border border-amber-400/20 bg-amber-500/10 p-6 text-center">
                <div className="text-sm font-semibold text-amber-200">No shop connected</div>
                <div className="mt-2 text-sm text-amber-200/70">
                  Install Hedge Spark from the Shopify App Store to connect your store.
                  If you&apos;ve already installed, try refreshing this page.
                </div>
              </div>
            </div>
          ) : loading ? (
            <MascotLoader caption="Reading the signals…" />
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
            <div className="space-y-8 px-6 py-5 pb-[70vh]">

              {/* Session expired banner — shown when any fetch returns 401/403 */}
              {sessionExpired && (
                <div className="rounded-2xl border border-amber-400/20 bg-amber-500/[0.07] px-4 py-3">
                  <div className="flex items-center gap-2 text-sm font-medium text-amber-200">
                    <span>Your session has expired.</span>
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
                      "Pro activated — welcome to Hedge Spark Pro. Your dashboard is upgrading now."}
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
                    <span className="text-[13px] font-medium text-emerald-200">
                      Your first insight just landed
                    </span>
                    <span className="ml-1.5 text-[12px] text-emerald-300/60">
                      — Hedge Spark found something worth looking at.
                    </span>
                  </div>
                  <button
                    onClick={() => { setFirstSignalToast(false); setActiveSection("brief"); }}
                    className="flex-shrink-0 rounded-lg bg-emerald-500/15 px-3 py-1.5 text-[12px] font-semibold text-emerald-300 ring-1 ring-emerald-400/20 transition hover:bg-emerald-500/25"
                  >
                    View insight
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

              {/* Setup status + onboarding panel — always the first thing rendered */}
              <SetupStatusPanel
                shop={shop}
                apiBase={API_BASE}
                apiHeaders={apiHeaders}
                onReadinessChange={handleReadinessChange}
                billingJustActivated={billingJustActivated}
                trialDays={proTrialDays}
                price={proPrice}
              />

              {/* Onboarding checklist — visible during cold-start, auto-hides when activated */}
              <OnboardingChecklist
                data={{
                  setupChecks,
                  readiness: setupReadiness,
                  totalVisitors: data ? (data.summary?.total_visitors ?? 0) : null,
                  signalCount: strongSignals.length > 0 ? strongSignals.length : (data ? 0 : null),
                  overviewLoading: loading,
                }}
              />

              {/* ═══ REVENUE HERO — the first thing the merchant sees ═══ */}
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
                apiBase={API_BASE}
                shop={shop}
              />

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
                      ? "Your daily brief will appear here once setup is complete and visitor data starts flowing."
                      : coldStartPhase === 1
                      ? "Your first daily brief is waiting for visitor data. Once traffic arrives, you\u2019ll get a ranked summary here every day."
                      : coldStartPhase === 2
                      ? "Visitors are being tracked \u2014 your first brief will generate once enough behavior is collected to produce insights."
                      : undefined
                  }
                  sparkInsight={
                    strongSignals.length > 0 && strongSignals[0]
                      ? `Your top opportunity today: ${strongSignals[0].human_label || prettyText(strongSignals[0].signal_type)}${strongSignals[0].product_url ? ` on ${shortUrl(strongSignals[0].product_url)}` : ""}.`
                      : earlySignals.length > 0
                      ? "We're starting to see activity in your store."
                      : undefined
                  }
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

                {/* Cold-start guidance for KPI grid */}
                {isColdStart && (
                  <div className="mb-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
                    <div className="text-[12px] leading-[1.6] text-slate-500">
                      {coldStartPhase === 0 ? (
                        "These metrics will populate once your app installation is complete and the tracker is running on your storefront."
                      ) : coldStartPhase === 1 ? (
                        "Your tracker is active \u2014 these numbers will start moving as soon as your first visitor lands on your store. This usually happens within minutes."
                      ) : (
                        "Visitors are being tracked. As more behavior accumulates, you\u2019ll see intent scores, hot visitors, and conversion-ready products appear here."
                      )}
                    </div>
                  </div>
                )}

                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <KpiCard label="Visitors (24h)" value={formatNumber(summary.total_visitors)} hint="Unique visitors in last 24 hours" numeric={summary.total_visitors} onClick={() => setActiveKpi("visitors")} />
                  <KpiCard label="Events (24h)" value={formatNumber(summary.total_events)} hint="Behavioral signals in last 24 hours" numeric={summary.total_events} onClick={() => setActiveKpi("events")} />
                  <KpiCard label="Hot Visitors" value={formatNumber(summary.hot_visitors)} hint="Unique visitors with high intent" numeric={summary.hot_visitors} onClick={() => setActiveKpi("hot")} />
                  <KpiCard label="Wishlist Adds" value={formatNumber(summary.wishlist_adds)} hint="Strong product desire signals" numeric={summary.wishlist_adds} onClick={() => setActiveKpi("wishlist")} />
                  <KpiCard label="Average Intent" value={formatScore(summary.avg_intent_score)} hint="Average signal strength — click for breakdown" numeric={summary.avg_intent_score} onClick={() => setActiveKpi("intent")} />
                  <KpiCard label="Intent Distribution" value={`${formatNumber(summary.hot_visitors)} / ${formatNumber(summary.warm_visitors)} / ${formatNumber(summary.cold_visitors)}`} hint="Hot / Warm / Cold — click for breakdown" onClick={() => setActiveKpi("distribution")} />
                  <KpiCard label="All-Time Visitors" value={formatNumber(summary.total_visitors_all)} hint="Total unique visitors ever tracked" numeric={summary.total_visitors_all} onClick={() => setActiveKpi("visitors")} />
                  <KpiCard label="Conversion-ready Products" value={formatNumber(summary.conversion_ready_products)} hint="Products with action potential" numeric={summary.conversion_ready_products} onClick={() => setActiveKpi("products")} />
                </div>
              </section>

              {/* Real orders / revenue section */}
              <section id="section-revenue">
                <OrdersSummary apiBase={API_BASE} shop={shop} />
                <ProductConversions apiBase={API_BASE} shop={shop} />
              </section>

              {/* Revenue at risk banner — below KPI grid, above signals */}
              {isProUser
                ? (revenueWindows && (revenueWindows.total_revenue_at_risk ?? 0) > 0) && (
                    <RevenueWindowPro data={revenueWindows} />
                  )
                : (revenueWindowTease && (revenueWindowTease.estimated_revenue_at_risk ?? 0) > 0) && (
                    <RevenueWindowLite
                      data={revenueWindowTease}
                      onUpgradeClick={() => setUpgradeModalOpen(true)}
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
                      <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/50">Early Activity</span>
                      <span className="rounded-full bg-white/[0.04] px-2 py-0.5 text-[9px] font-medium uppercase tracking-[0.08em] text-slate-500 ring-1 ring-white/[0.06]">Live</span>
                    </div>
                    <h3 className="text-[14px] font-medium text-slate-300">We're starting to see activity in your store</h3>
                    <p className="mt-0.5 text-[11px] text-slate-600">These early signals will sharpen as more visitors arrive.</p>
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
                    <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-600">Early signals</span>
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
              <section id="section-signals">
                {/* Dual heading row — mirrors the 2+2 card columns below */}
                <div className="mb-3 grid gap-4 xl:grid-cols-2">
                  <SectionHeading eyebrow={strongSignals.length > 0 ? "Attention" : "Attention"} title={strongSignals.length > 0 ? "Top opportunities to improve your store" : "What needs fixing"} />
                  {topProducts.length > 0 && (
                    <SectionHeading eyebrow="Hot Products" title="Where buyers are active" />
                  )}
                </div>

                {/* Unified 4-column card grid — all cards share one grid context
                    so CSS auto-rows enforces equal height per row */}
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">

                  {/* Alert cards — occupy first 2 columns */}
                  {alerts.length === 0 ? (
                    <p className="sm:col-span-2 rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-600">
                      {isColdStart && earlySignals.length === 0
                        ? "Waiting for your first visitors..."
                        : isColdStart
                        ? "Building stronger signals as traffic grows..."
                        : "No alerts right now \u2014 your store looks healthy."}
                    </p>
                  ) : (
                    alerts.slice(0, 4).map((alert, i) => (
                      <div
                        key={`${alert.type || "alert"}-${i}`}
                        className="flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4"
                      >
                        <div className="mb-2 flex items-center gap-2">
                          <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${impactClass(alert.priority)}`}>
                            {alert.priority || "Info"}
                          </span>
                          <span className="text-[11px] text-slate-500">{prettyText(alert.type)}</span>
                        </div>
                        {/*
                          message is diagnostic (what is happening) — shown in full
                          for all tiers. It is a count sentence, not prescriptive content.
                          action is prescriptive (what to do) — present only in Pro
                          API response; rendered below when available.
                        */}
                        <p className="flex-1 text-[13px] leading-5 text-slate-300">{alert.message || "—"}</p>
                        {alert.action && (
                          <div className="mt-2 rounded-lg border border-emerald-400/15 bg-emerald-500/[0.05] px-2.5 py-1.5">
                            <div className="mb-0.5 flex items-center gap-1.5">
                              <span className="text-[9px] font-semibold uppercase tracking-[0.14em] text-emerald-300/70">Action</span>
                              <span className="rounded border border-violet-400/20 px-1 py-px text-[9px] font-normal normal-case tracking-normal text-violet-400/70">PRO</span>
                            </div>
                            <p className="text-[11px] leading-[1.5] text-slate-300">{alert.action}</p>
                          </div>
                        )}
                      </div>
                    ))
                  )}

                  {/* Product cards — occupy last 2 columns */}
                  {topProducts.slice(0, 4).map((product, i) => (
                    <div
                      key={`${product.product_id || "prod"}-${i}`}
                      className="hs-fade-up flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4 cursor-pointer select-none transition-colors hover:border-violet-400/25 hover:bg-white/[0.05]"
                      onClick={() => setActiveTopProduct(product)}
                    >
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <span className="truncate text-[13px] font-medium text-white">
                          {product.product_name || product.product_id || "—"}
                        </span>
                        {product.intent_level && (
                          <span className={`flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ${impactClass(product.intent_level === "HOT" ? "HIGH" : product.intent_level === "WARM" ? "MEDIUM" : "LOW")}`}>
                            {product.intent_level}
                          </span>
                        )}
                      </div>
                      <div className="mt-auto grid grid-cols-3 gap-1 border-t border-white/[0.05] pt-2">
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

                {/* Traffic source companion — aligned under Highest-intent products */}
                {topProducts.length > 0 && (
                  <div className="mt-3 grid gap-3 xl:grid-cols-2">
                    <div className="hidden xl:block" />
                    <TrafficSourceBox
                      sourceQuality={sourceQuality}
                      isProUser={isProUser}
                      onUpgradeClick={() => setUpgradeModalOpen(true)}
                    />
                  </div>
                )}
              </section>

              {/* 4 — Product Performance */}
              {mergedProducts.length > 0 && (
                <section id="section-product-performance">
                  <SectionHeading
                    eyebrow="Products"
                    title="Where your traffic goes"
                    description="24h metrics per product — sorted by what needs attention first."
                  />

                  {/* Urgency signal — Lite only, when high-priority rows exist */}
                  {!isProUser && (() => {
                    const highCount = mergedProducts.filter((r) => r.priority === "HIGH").length;
                    if (!highCount) return null;
                    return (
                      <div className="mb-3 flex items-center rounded-xl border border-violet-400/15 bg-violet-500/[0.06] px-4 py-2.5">
                        <span className="text-[12px] text-slate-400">
                          You have {highCount} high-priority {highCount === 1 ? "opportunity" : "opportunities"} waiting
                        </span>
                        <span
                          className="ml-2 cursor-pointer text-[12px] text-violet-400 transition hover:text-violet-300"
                          role="button"
                          onClick={() => setUpgradeModalOpen(true)}
                        >
                          Unlock now →
                        </span>
                      </div>
                    );
                  })()}

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
                      <table className="min-w-full text-left text-[13px]">
                        <thead>
                          <tr className="border-b border-white/[0.06] text-[11px] uppercase tracking-wide text-slate-600">
                            <th className="px-4 py-3 font-medium">Product</th>
                            <th className="px-4 py-3 font-medium">Views 24h</th>
                            <th className="px-4 py-3 font-medium">7d Trend</th>
                            <th className="px-4 py-3 font-medium">Cart Abandon</th>
                            <th className="px-4 py-3 font-medium">Avg Dwell</th>
                            <th className="px-4 py-3 font-medium">Avg Scroll</th>
                            <th className="px-4 py-3 font-medium">Engagement</th>
                            <th className="px-4 py-3 font-medium" title="Weighted priority score: views · engagement · cart abandonment">Priority</th>
                            <th className="px-4 py-3 font-medium">
                              Est. Loss / Action
                              <span className="ml-2 text-[10px] text-violet-400/70 border border-violet-400/20 px-1.5 py-[1px] rounded align-middle">PRO</span>
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
                              <td className="max-w-[260px] px-4 py-2.5">
                                <div className="flex items-start gap-2">
                                  <span
                                    className={`mt-[3px] h-2 w-2 flex-shrink-0 rounded-full ${
                                      row.priority === "HIGH"
                                        ? "bg-rose-400 shadow-[0_0_6px_rgba(251,113,133,0.7)]"
                                        : row.priority === "MED"
                                        ? "bg-amber-300 shadow-[0_0_6px_rgba(252,211,77,0.6)]"
                                        : "bg-slate-600"
                                    }`}
                                    title={`${row.priority} priority`}
                                  />
                                  <div className="min-w-0">
                                    <span className="block truncate text-[12px] text-slate-300" title={row.product_url}>
                                      {shortUrl(row.product_url)}
                                    </span>
                                    {row.insight && (
                                      <span className="mt-0.5 block text-[10px] leading-3 text-slate-500">
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
                              <td className="px-4 py-2.5 tabular-nums text-slate-200">{formatNumber(row.views_24h)}</td>
                              <td className="px-4 py-2.5">
                                {!row.trend_is_synthetic && row.last_7_days_views.length > 0 ? (
                                  <InlineSparkline values={row.last_7_days_views} />
                                ) : (
                                  <span className="text-[11px] text-slate-700">—</span>
                                )}
                              </td>
                              <td className="px-4 py-2.5 tabular-nums">
                                {(row.cart_conversions_24h ?? 0) === 0 ? (
                                  <span className="text-slate-600">No conversions</span>
                                ) : row.cart_abandonment_rate != null ? (
                                  <span className={row.cart_abandonment_rate >= 0.8 ? "text-rose-400" : row.cart_abandonment_rate >= 0.5 ? "text-amber-400" : "text-slate-400"}>
                                    {formatPct(row.cart_abandonment_rate)}
                                  </span>
                                ) : (
                                  <span className="text-slate-700">—</span>
                                )}
                              </td>
                              <td className="px-4 py-2.5 tabular-nums text-slate-400">
                                {row.avg_dwell_24h != null ? `${formatDecimal(row.avg_dwell_24h, 1)}s` : <span className="text-slate-700">—</span>}
                              </td>
                              <td className="px-4 py-2.5 tabular-nums text-slate-400">
                                {row.avg_scroll_24h != null ? `${formatDecimal(row.avg_scroll_24h, 0)}%` : <span className="text-slate-700">—</span>}
                              </td>
                              <td className="px-4 py-2.5">
                                {row.engagement_score != null ? (
                                  <span className="inline-flex items-center gap-1.5 tabular-nums" title="Engagement score 0–100% · composite of dwell time and scroll depth">
                                    <span className={row.engagement_score >= 0.7 ? "font-semibold text-emerald-400" : row.engagement_score >= 0.4 ? "text-amber-300" : "text-slate-500"}>
                                      {Math.round(row.engagement_score * 100)}%
                                    </span>
                                    <span className={`text-[10px] font-medium ${row.engagement_score > 0.8 ? "text-emerald-500" : row.engagement_score >= 0.5 ? "text-amber-400/80" : "text-slate-600"}`}>
                                      {row.engagement_score > 0.8 ? "High" : row.engagement_score >= 0.5 ? "Med" : "Low"}
                                    </span>
                                  </span>
                                ) : (
                                  <span className="text-[11px] text-slate-600" title="Engagement score 0–100% · composite of dwell time and scroll depth">No data</span>
                                )}
                              </td>
                              <td className="px-4 py-2.5">
                                <div className="flex items-center gap-1.5">
                                  <div className="h-1 w-16 overflow-hidden rounded-full bg-white/[0.07]">
                                    <div
                                      className={`h-full rounded-full ${row.priority === "HIGH" ? "bg-rose-400/70" : row.priority === "MED" ? "bg-amber-300/70" : "bg-slate-600/70"}`}
                                      style={{ width: `${Math.round(row.attention_score * 100)}%` }}
                                    />
                                  </div>
                                  <span className="text-[11px] tabular-nums text-slate-600">{Math.round(row.attention_score * 100)}</span>
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
                                ~{fmtLoss(act.impactValue)}/wk
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
                            <Image src="/branding/hedgespark-mascot.png" alt="" width={16} height={16} className="mt-0.5 flex-shrink-0" />
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
                  <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-600">
                    {coldStartPhase <= 1
                      ? "Your weekly traffic chart will build up here once visitors start arriving. Each day adds a new bar."
                      : "No trend data yet \u2014 check back once a full day of traffic has been recorded."}
                  </p>
                ) : (
                  <div className="grid gap-2 sm:grid-cols-4 md:grid-cols-7">
                    {trend.map((point, i) => {
                      const val = point.visitors || 0;
                      const barH = Math.max(8, Math.round((val / maxTrend) * 80));
                      return (
                        <div
                          key={`${point.day || "day"}-${i}`}
                          className="hs-fade-up rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2.5"
                          style={{ animationDelay: `${i * 40}ms` }}
                        >
                          <div className="mb-1.5 text-[11px] text-slate-500">{point.day || `Day ${i + 1}`}</div>
                          <div className="flex h-20 items-end">
                            <div className="w-full rounded-md bg-gradient-to-t from-violet-500/80 to-cyan-400/60" style={{ height: barH }} />
                          </div>
                          <div className="mt-1.5 text-sm font-semibold tabular-nums text-white">{formatNumber(val)}</div>
                          <div className="text-[10px] text-slate-600">Visitors</div>
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
                  <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-600">
                    {coldStartPhase <= 1
                      ? "The pages your visitors browse most will appear here, ranked by views and dwell time."
                      : "No page data available yet \u2014 this populates as visitors browse your store."}
                  </p>
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
                            <tr key={`${page.url || "page"}-${i}`} className="border-t border-white/[0.04] transition-colors hover:bg-white/[0.02]">
                              <td className="max-w-[380px] truncate px-4 py-2.5 text-slate-300">{page.url || "—"}</td>
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

              {/* 8 — Conversion Funnel (Pro) */}
              <section id="section-funnel">
                <SectionHeading
                  eyebrow="Funnel"
                  title="Where you lose buyers"
                  description="Drop-off at each stage from view to purchase."
                />
                {!isProUser ? (
                  <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] px-5 py-4">
                    <p className="text-[12px] leading-relaxed text-slate-500">
                      See exactly where visitors drop off on the path from view to purchase.
                    </p>
                    <button
                      onClick={() => setUpgradeModalOpen(true)}
                      className="mt-3 rounded-lg bg-violet-500/15 px-3 py-1.5 text-[11px] font-semibold text-violet-300 transition hover:bg-violet-500/25"
                    >
                      See where you lose buyers →
                    </button>
                  </div>
                ) : funnelSteps.length === 0 ? (
                  <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-600">
                    No funnel data yet — events will appear once your tracker is active.
                  </p>
                ) : (
                  <FunnelVisualization steps={funnelSteps} />
                )}
              </section>

              {/* 9 — Session Timeline + Click Insights (Pro) */}
              <section id="section-sessions">
                {!isProUser ? (
                  <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] px-5 py-4">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-600">Sessions &amp; Clicks</div>
                    <p className="mt-2 text-[12px] leading-relaxed text-slate-500">
                      Watch individual visitor journeys and see which elements get the most clicks.
                    </p>
                    <button
                      onClick={() => setUpgradeModalOpen(true)}
                      className="mt-3 rounded-lg bg-violet-500/15 px-3 py-1.5 text-[11px] font-semibold text-violet-300 transition hover:bg-violet-500/25"
                    >
                      Explore visitor behavior →
                    </button>
                  </div>
                ) : (
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

              {/* 10 — Live Radar */}
              <section id="section-live">
                <SectionHeading
                  eyebrow="Live"
                  title="Who's in your store right now"
                />
                <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
                  {liveVisitors.length === 0 ? (
                    <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-600">
                      {coldStartPhase <= 1
                        ? "Live visitor dots will appear here in real time as people browse your store."
                        : "No live visitors right now \u2014 this updates in real time when visitors are active."}
                    </p>
                  ) : (
                    <div className="relative min-h-[220px] overflow-hidden rounded-2xl border border-white/[0.07] bg-[radial-gradient(circle_at_center,rgba(56,189,248,0.12),transparent_22%)]">
                      <div className="absolute inset-0 flex items-center justify-center">
                        <div className="relative h-[190px] w-[190px] rounded-full border border-cyan-400/15">
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
                      <div className="absolute bottom-3 left-4 right-4">
                        <div className="flex items-center gap-4 text-[11px] text-slate-500">
                          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-rose-400" />Hot</span>
                          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-amber-300" />Warm</span>
                          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-slate-400" />Cold</span>
                          <span className="ml-auto font-medium text-slate-400">{liveVisitors.length} live</span>
                        </div>
                      </div>
                    </div>
                  )}
                  <div className="flex flex-col gap-2">
                    {liveVisitors.length > 0 && liveVisitors.slice(0, 6).map((v, i) => (
                      <div key={`${v.visitor_id || "lv"}-${i}`} className="flex items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
                        <span className={`h-2 w-2 flex-shrink-0 rounded-full ${intentDotClass(v.intent_level)}`} />
                        <span className="min-w-0 flex-1 truncate text-[12px] text-slate-300">{v.url || "—"}</span>
                        <span className="flex-shrink-0 rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-slate-500">{v.intent_level || "—"}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </section>


              {/* PRO separator */}
              <div className="flex items-center gap-4 py-1">
                <div className="h-px flex-1 bg-white/[0.06]" />
                <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-violet-300/40">
                  <span className="rounded-full border border-violet-400/20 bg-violet-500/10 px-2 py-0.5">Pro</span>
                  Unlock more intelligence
                </span>
                <div className="h-px flex-1 bg-white/[0.06]" />
              </div>

              {/* Pro — Audience Segments */}
              {isProUser && (
                <section id="section-audience">
                  <SectionHeading
                    eyebrow="Audience"
                    title="Who's ready to buy"
                    description="Visitors classified by real buying behavior — calibrated on your store's actual buyers."
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
                    title="How your nudges are performing"
                    description="Impressions, conversions, and controlled lift measurement."
                  />
                  <NudgePerformance
                    apiBase={API_BASE}
                    shop={shop}
                    apiHeaders={apiHeaders}
                  />
                </section>
              )}

              {/* Pro — Holdout Lift Report */}
              {isProUser && (
                <section id="section-lift">
                  <SectionHeading
                    eyebrow="Proof"
                    title="Did your nudges actually work?"
                    description="Measured with a real control group — exposed visitors vs visitors who saw nothing."
                  />
                  <LiftReport apiBase={API_BASE} shop={shop} apiHeaders={apiHeaders} />
                </section>
              )}

              {/* Pro — Scroll Intelligence + Cohort Retention side by side */}
              {isProUser && (
                <section id="section-scroll-cohorts">
                  <div className="grid gap-4 xl:grid-cols-2">
                    <div>
                      <SectionHeading
                        eyebrow="Behavioral"
                        title="Scroll intelligence"
                        description="Where visitors stop reading on each product page."
                      />
                      <HeatmapCard apiBase={API_BASE} shop={shop} apiHeaders={apiHeaders} />
                    </div>
                    <div>
                      <SectionHeading
                        eyebrow="Retention"
                        title="Cohort retention"
                        description="Weekly repeat purchase rates by first-purchase cohort."
                      />
                      <CohortTable apiBase={API_BASE} shop={shop} apiHeaders={apiHeaders} />
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
                      {(forecastData as any).confidence ? (
                        <div className="grid gap-4 sm:grid-cols-3">
                          <div>
                            <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">7-day forecast</div>
                            <div className="mt-1 text-2xl font-bold text-white">
                              {(forecastData as any).currency === "EUR" ? "€" : "$"}{((forecastData as any).forecast_7d?.revenue ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}
                            </div>
                            <div className="mt-0.5 text-[11px] text-slate-500">
                              range {(forecastData as any).currency === "EUR" ? "€" : "$"}{((forecastData as any).forecast_7d?.revenue_low ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}
                              {" — "}
                              {(forecastData as any).currency === "EUR" ? "€" : "$"}{((forecastData as any).forecast_7d?.revenue_high ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}
                            </div>
                          </div>
                          <div>
                            <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">30-day forecast</div>
                            <div className="mt-1 text-2xl font-bold text-white">
                              {(forecastData as any).currency === "EUR" ? "€" : "$"}{((forecastData as any).forecast_30d?.revenue ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}
                            </div>
                            <div className="mt-0.5 text-[11px] text-slate-500">
                              range {(forecastData as any).currency === "EUR" ? "€" : "$"}{((forecastData as any).forecast_30d?.revenue_low ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}
                              {" — "}
                              {(forecastData as any).currency === "EUR" ? "€" : "$"}{((forecastData as any).forecast_30d?.revenue_high ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}
                            </div>
                          </div>
                          <div>
                            <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Trend</div>
                            <div className={`mt-1 text-2xl font-bold ${
                              (forecastData as any).trend?.direction === "up" ? "text-emerald-400" :
                              (forecastData as any).trend?.direction === "down" ? "text-rose-400" :
                              "text-slate-300"
                            }`}>
                              {(forecastData as any).trend?.direction === "up" ? "↑" :
                               (forecastData as any).trend?.direction === "down" ? "↓" : "→"}{" "}
                              {Math.abs((forecastData as any).trend?.weekly_change_pct ?? 0).toFixed(1)}% / week
                            </div>
                            <div className="mt-0.5 text-[11px] text-slate-500">
                              Confidence: {(forecastData as any).confidence}
                              {(forecastData as any).seasonality_available ? " · seasonality detected" : ""}
                            </div>
                          </div>
                        </div>
                      ) : (
                        <p className="text-[12px] text-slate-500">
                          {(forecastData as any).confidence_reason === "no_order_history"
                            ? "Revenue forecasting activates once you have order history. Keep selling — your forecast will build automatically."
                            : `Building forecast — need more order data. ${(forecastData as any).confidence_reason || ""}`}
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
                      <SectionHeading
                        eyebrow="Attribution"
                        title="Where revenue comes from"
                        description="First-touch and last-touch source attribution on real orders."
                        pro
                      />
                      {attrSummary ? (
                        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
                          <div className="grid grid-cols-3 gap-3 mb-4">
                            <div>
                              <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Orders tracked</div>
                              <div className="mt-0.5 text-xl font-bold text-white">{(attrSummary as any).orders_total ?? 0}</div>
                            </div>
                            <div>
                              <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Attributed</div>
                              <div className="mt-0.5 text-xl font-bold text-emerald-400">{(attrSummary as any).orders_attributed ?? 0}</div>
                            </div>
                            <div>
                              <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Attribution rate</div>
                              <div className="mt-0.5 text-xl font-bold text-white">{((attrSummary as any).attribution_rate * 100).toFixed(0)}%</div>
                            </div>
                          </div>
                          {((attrSummary as any).top_sources_first_touch?.length > 0) ? (
                            <div>
                              <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Top sources (first touch)</div>
                              {(attrSummary as any).top_sources_first_touch.slice(0, 5).map((s: any, i: number) => (
                                <div key={i} className="flex items-center justify-between border-t border-white/[0.04] py-1.5 text-[12px]">
                                  <span className="text-slate-300">{s.label || s.source}</span>
                                  <span className="text-white font-medium">{s.orders} orders · ${s.revenue?.toLocaleString(undefined, {maximumFractionDigits: 0})}</span>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <p className="text-[12px] text-slate-500">Attribution data builds as visitors convert. Keep the tracker active.</p>
                          )}
                          {(attrSummary as any).first_vs_last_match_rate != null && (attrSummary as any).orders_attributed > 0 && (
                            <div className="mt-3 rounded-lg bg-white/[0.03] px-3 py-2 text-[11px] text-slate-400">
                              {((attrSummary as any).first_vs_last_match_rate * 100).toFixed(0)}% of conversions had the same first and last touch source —{" "}
                              {(attrSummary as any).first_vs_last_match_rate > 0.8
                                ? "most customers convert from the channel that brought them."
                                : "customers are discovering you on one channel and converting on another."}
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
                          <div className="h-4 w-40 animate-pulse rounded bg-white/[0.05]" />
                        </div>
                      )}
                    </div>

                    {/* Customer LTV */}
                    <div>
                      <SectionHeading
                        eyebrow="Lifetime Value"
                        title="Customer economics"
                        description="Monthly cohort analysis — how much each acquisition group is worth."
                        pro
                      />
                      {ltvData ? (
                        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
                          <div className="grid grid-cols-3 gap-3 mb-4">
                            <div>
                              <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Customers</div>
                              <div className="mt-0.5 text-xl font-bold text-white">{(ltvData as any).overall?.total_customers ?? 0}</div>
                            </div>
                            <div>
                              <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Repeat rate</div>
                              <div className={`mt-0.5 text-xl font-bold ${
                                ((ltvData as any).overall?.repeat_rate ?? 0) > 0.2 ? "text-emerald-400" :
                                ((ltvData as any).overall?.repeat_rate ?? 0) > 0 ? "text-amber-400" :
                                "text-slate-400"
                              }`}>
                                {(((ltvData as any).overall?.repeat_rate ?? 0) * 100).toFixed(1)}%
                              </div>
                            </div>
                            <div>
                              <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Revenue / customer</div>
                              <div className="mt-0.5 text-xl font-bold text-white">
                                ${((ltvData as any).overall?.avg_revenue_per_customer ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}
                              </div>
                            </div>
                          </div>
                          {((ltvData as any).cohorts?.length > 0) ? (
                            <div>
                              <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Monthly cohorts</div>
                              {(ltvData as any).cohorts.slice(0, 4).map((c: any, i: number) => (
                                <div key={i} className="flex items-center justify-between border-t border-white/[0.04] py-1.5 text-[12px]">
                                  <span className="text-slate-400">{c.cohort_month}</span>
                                  <span className="text-slate-300">{c.size} customers</span>
                                  <span className="text-white font-medium">${c.revenue_total?.toLocaleString(undefined, {maximumFractionDigits: 0})}</span>
                                  <span className="text-slate-500">{c.orders_per_customer?.toFixed(1)} orders/cust</span>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <p className="text-[12px] text-slate-500">Cohort data builds from real orders with customer identifiers.</p>
                          )}
                          {(ltvData as any).customer_coverage?.coverage_rate != null && (ltvData as any).overall?.total_customers > 0 && (
                            <div className="mt-3 rounded-lg bg-white/[0.03] px-3 py-2 text-[11px] text-slate-400">
                              {((ltvData as any).customer_coverage.coverage_rate * 100).toFixed(0)}% of orders have customer identity —{" "}
                              {(ltvData as any).customer_coverage.coverage_rate > 0.7
                                ? "strong coverage for LTV analysis."
                                : "connect Shopify webhooks to improve coverage."}
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
                          <div className="h-4 w-40 animate-pulse rounded bg-white/[0.05]" />
                        </div>
                      )}
                    </div>

                  </div>
                </section>
              )}

              {/* Pro — Behavioral Customer Intelligence */}
              {isProUser && behavioralData && (
                <section id="section-behavioral-intelligence">
                  <SectionHeading
                    eyebrow="Behavioral Intelligence"
                    title="Which visitors become your best customers?"
                    description="Segments customers by pre-purchase behavior — scroll depth, dwell time, visit frequency, and traffic source."
                    pro
                  />
                  <div className="space-y-4">
                    {/* Insights banner */}
                    {((behavioralData as any).insights?.length > 0) && (
                      <div className="rounded-2xl border border-violet-400/10 bg-violet-500/[0.04] p-4">
                        <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300/70">Key Insights</div>
                        {(behavioralData as any).insights.map((insight: string, i: number) => (
                          <p key={i} className="text-[12px] leading-relaxed text-slate-300 mb-1.5 last:mb-0">
                            {insight}
                          </p>
                        ))}
                      </div>
                    )}

                    {/* Segment tables */}
                    <div className="grid gap-4 xl:grid-cols-3">
                      {/* By Engagement */}
                      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
                        <div className="mb-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">By Engagement Level</div>
                        {((behavioralData as any).segments?.by_engagement?.length > 0) ? (
                          (behavioralData as any).segments.by_engagement.map((s: any, i: number) => (
                            <div key={i} className="flex items-center justify-between border-t border-white/[0.04] py-2 text-[12px]">
                              <div>
                                <span className={`font-medium ${
                                  s.segment === "HIGH" ? "text-emerald-400" :
                                  s.segment === "MEDIUM" ? "text-amber-400" :
                                  s.segment === "LOW" ? "text-rose-400" : "text-slate-500"
                                }`}>{s.segment}</span>
                                <span className="ml-2 text-slate-600">{s.customers} cust</span>
                              </div>
                              <div className="text-right">
                                <span className="text-white font-medium">${s.avg_revenue?.toFixed(0)}/cust</span>
                                <span className="ml-2 text-slate-500">{(s.repeat_rate * 100).toFixed(0)}% repeat</span>
                              </div>
                            </div>
                          ))
                        ) : (
                          <p className="text-[11px] text-slate-600">Needs visitor behavior data</p>
                        )}
                      </div>

                      {/* By Visit Pattern */}
                      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
                        <div className="mb-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">By Visit Pattern</div>
                        {((behavioralData as any).segments?.by_visit_pattern?.length > 0) ? (
                          (behavioralData as any).segments.by_visit_pattern.map((s: any, i: number) => (
                            <div key={i} className="flex items-center justify-between border-t border-white/[0.04] py-2 text-[12px]">
                              <div>
                                <span className={`font-medium ${
                                  s.segment === "REPEAT_VISITOR" ? "text-emerald-400" : "text-amber-400"
                                }`}>{s.segment === "REPEAT_VISITOR" ? "Repeat visitors" : "Single visit"}</span>
                                <span className="ml-2 text-slate-600">{s.customers} cust</span>
                              </div>
                              <div className="text-right">
                                <span className="text-white font-medium">${s.avg_revenue?.toFixed(0)}/cust</span>
                                <span className="ml-2 text-slate-500">{(s.repeat_rate * 100).toFixed(0)}% repeat</span>
                              </div>
                            </div>
                          ))
                        ) : (
                          <p className="text-[11px] text-slate-600">Needs visitor behavior data</p>
                        )}
                      </div>

                      {/* By Source */}
                      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
                        <div className="mb-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">By Traffic Source</div>
                        {((behavioralData as any).segments?.by_source?.length > 0) ? (
                          (behavioralData as any).segments.by_source.map((s: any, i: number) => (
                            <div key={i} className="flex items-center justify-between border-t border-white/[0.04] py-2 text-[12px]">
                              <div>
                                <span className="font-medium text-slate-300">{s.segment}</span>
                                <span className="ml-2 text-slate-600">{s.customers} cust</span>
                              </div>
                              <div className="text-right">
                                <span className="text-white font-medium">${s.avg_revenue?.toFixed(0)}/cust</span>
                                <span className="ml-2 text-slate-500">{(s.repeat_rate * 100).toFixed(0)}% repeat</span>
                              </div>
                            </div>
                          ))
                        ) : (
                          <p className="text-[11px] text-slate-600">Needs order attribution data</p>
                        )}
                      </div>
                    </div>

                    {/* Coverage indicator */}
                    {(behavioralData as any).data_coverage?.total_customers > 0 && (
                      <div className="text-[10px] text-slate-600">
                        {(behavioralData as any).data_coverage.segmentable_customers} of{" "}
                        {(behavioralData as any).data_coverage.total_customers} customers have behavioral data
                        ({((behavioralData as any).data_coverage.coverage_rate * 100).toFixed(0)}% coverage)
                      </div>
                    )}
                  </div>
                </section>
              )}

              {/* 10 — Price + Market Intelligence (compact 2-col) */}
              <section>
                <div className="mb-4">
                  <div className="mb-1.5 flex items-center gap-2">
                    <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/70">Intelligence</span>
                    <span className="rounded-full border border-violet-400/30 bg-violet-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-violet-300">Pro</span>
                  </div>
                  <h2 className="mt-1 text-lg font-semibold text-white">Know your market position</h2>
                  <p className="mt-1 max-w-lg text-[13px] text-slate-400">Competitive pricing analysis and market positioning per product — see exactly where you stand and what to change.</p>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">

                  {/* Price Intelligence */}
                  <div id="section-price-intelligence">
                    <ProGate tier={tier} onUpgradeClick={() => setUpgradeModalOpen(true)} label="Price Intelligence" teaser="See how your pricing compares to competitors — get specific reposition recommendations per product.">
                      <div className="h-full rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4">
                        <div className="mb-3 flex items-center justify-between">
                          <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">Price Intelligence</span>
                          <span className="rounded-full border border-violet-400/30 bg-violet-500/15 px-2 py-0.5 text-[10px] font-semibold text-violet-300">Pro</span>
                        </div>
                        {priceIntel.length === 0 ? (
                          <p className="text-[12px] text-slate-600">No pricing data yet.</p>
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
                                <div className="truncate text-[12px] font-medium text-white">{item.product_name || "—"}</div>
                                {item.recommended_price_action && (
                                  <div className="mt-0.5 text-[11px] text-slate-500">{String(item.recommended_price_action)}</div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </ProGate>
                  </div>

                  {/* Market Intelligence */}
                  <div id="section-market-intelligence">
                    <ProGate tier={tier} onUpgradeClick={() => setUpgradeModalOpen(true)} label="Market Intelligence" teaser="Understand which products are unique, which face heavy competition, and where to focus your strategy.">
                      <div className="h-full rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4">
                        <div className="mb-3 flex items-center justify-between">
                          <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">Market Intelligence</span>
                          <span className="rounded-full border border-violet-400/30 bg-violet-500/15 px-2 py-0.5 text-[10px] font-semibold text-violet-300">Pro</span>
                        </div>
                        {marketIntel.length === 0 ? (
                          <p className="text-[12px] text-slate-600">No market data yet.</p>
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
                                <div className="truncate text-[12px] font-medium text-white">{item.product_name || "—"}</div>
                                {item.recommended_next_step && (
                                  <div className="mt-0.5 text-[11px] text-slate-500">{prettyText(String(item.recommended_next_step))}</div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </ProGate>
                  </div>

                </div>
              </section>

              {/* 11 — Settings / Integrations (all tiers) */}
              <section id="section-settings">
                  <div className="mb-4">
                    <div className="mb-1.5 flex items-center gap-2">
                      <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/70">Settings</span>
                    </div>
                    <h2 className="mt-1 text-lg font-semibold text-white">Integrations</h2>
                    <p className="mt-1 max-w-lg text-[13px] text-slate-400">Connect external platforms to power automated execution flows.</p>
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
                  ? `Hedge Spark has found ${strongSignals.length} actionable insight${strongSignals.length === 1 ? "" : "s"} for your store. Keep your AI actions, daily briefs, and market intelligence.`
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
                  "AI action per signal",
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

      {/* Support chat — floating, with onboarding context */}
      {shop && <SupportChat onboardingHint={
        // Contextual hint based on data pipeline state
        data && (data.summary?.total_visitors ?? 0) > 0 && !heroRevenue
          ? "\ud83d\udca1 You\u2019re one step away from tracking revenue. Open the setup checklist above and complete the Purchase Tracking step."
          : undefined
      } />}
    </div>
  );
}
