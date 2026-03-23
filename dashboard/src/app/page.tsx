"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
// UpgradeModal defined inline below
import { ProGate } from "./components/ProGate";
import { MascotLoader, MascotEmpty } from "./components/MascotLoader";
import { type OpportunitySignal } from "./components/SignalCard";
import { BriefHero, type DailyBrief } from "./components/BriefHero";
import { RevenueWindowPro, RevenueWindowLite } from "./components/RevenueWindowBanner";
import { LiftReport } from "./components/LiftReport";
import { HeatmapCard } from "./components/HeatmapCard";
import { CohortTable } from "./components/CohortTable";

// ---------------------------------------------------------------------------
// Environment
// ---------------------------------------------------------------------------
const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "";
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

type OverviewResponse = {
  summary?: Summary;
  top_products?: TopProduct[];
  price_intelligence?: PriceIntelligence[];
  market_lookup?: MarketLookup[];
  revenue_window_tease?: RevenueWindowTease;
  revenue_windows?: RevenueWindows;
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
  avg_dwell_24h?: number | null;
  avg_scroll_24h?: number | null;
  cart_abandonment_rate?: number | null;
  return_visitor_rate?: number | null;
  engagement_score?: number | null;
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
  onClick,
}: {
  label: string;
  value: string;
  hint: string;
  numeric?: number;
  onClick?: () => void;
}) {
  return (
    <div
      className={`hs-fade-up rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4 transition-all duration-150 hover:border-violet-400/20 hover:bg-white/[0.05]${onClick ? " cursor-pointer select-none" : ""}`}
      onClick={onClick}
    >
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
// Inline Upgrade Modal
function UpgradeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" />
      <div
        className="hs-fade-up relative z-10 w-full max-w-sm rounded-3xl border border-violet-400/20 bg-[#0d0d1e] p-8 shadow-[0_32px_80px_rgba(124,58,237,0.22)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Close */}
        <button
          onClick={onClose}
          className="absolute right-5 top-5 rounded-lg p-1 text-slate-500 transition-colors hover:text-slate-300"
          aria-label="Close"
        >
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-5 w-5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        {/* Heading */}
        <div className="mb-6 text-center">
          <h2 className="text-xl font-semibold leading-snug text-white">
            Unlock full store intelligence
          </h2>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            See exactly what to fix and how much revenue you&apos;re losing
          </p>
        </div>

        {/* Bullets */}
        <ul className="mb-7 space-y-3">
          {[
            "Full product insights and AI actions",
            "Revenue loss per product",
            "Complete daily brief breakdown",
          ].map((item) => (
            <li key={item} className="flex items-start gap-3">
              <span className="mt-0.5 flex-shrink-0 rounded-full bg-violet-500/20 p-0.5">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor" className="h-3 w-3 text-violet-400">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                </svg>
              </span>
              <span className="text-[13px] text-slate-300">{item}</span>
            </li>
          ))}
        </ul>

        {/* CTA — navigate to in-app pricing page */}
        <button
          onClick={() => {
            const shop = new URLSearchParams(window.location.search).get("shop") || "";
            window.location.href = `/pricing${shop ? `?shop=${encodeURIComponent(shop)}` : ""}`;
          }}
          className="w-full rounded-xl bg-violet-600 py-3 text-sm font-semibold text-white shadow-[0_0_20px_rgba(124,58,237,0.4)] transition-colors hover:bg-violet-500 active:bg-violet-700"
        >
          Upgrade to Pro — €49/mo
        </button>

        {/* Secondary */}
        <button
          onClick={onClose}
          className="mt-3 w-full text-center text-[12px] text-slate-600 transition hover:text-slate-400"
        >
          Continue with Lite
        </button>
      </div>
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
  const sessions  = summary.total_sessions ?? 0;
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

    if (activeKpi === "sessions") {
      const sessPerVisitor = total > 0 ? sessions / total : 0;
      const deepPct   = Math.min(100, Math.round(sessPerVisitor >= 1.3 ? 55 : sessPerVisitor >= 1.1 ? 35 : 15));
      const shallowPct = Math.max(0, 100 - deepPct);
      return {
        title: "Sessions",
        segments: [
          { color: "#7c3aed", label: "Multi-page (Deep)",     pct: deepPct    },
          { color: "#1e293b", label: "Single-page (Shallow)", pct: shallowPct },
        ],
        numbers: [
          { label: "Total Sessions",      value: formatNumber(sessions)               },
          { label: "Total Visitors",       value: formatNumber(total)                  },
          { label: "Sessions / Visitor",   value: formatDecimal(sessPerVisitor)        },
        ],
        insight: [
          sessPerVisitor >= 1.3
            ? "Visitors are returning and browsing multiple pages — strong engagement."
            : "Most sessions are single-visit — improve internal linking and CTAs.",
          `On average, each visitor starts ${formatDecimal(sessPerVisitor)} session(s).`,
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
const PRODUCT_AOV = 50;

function ProductInsightPanel({
  product,
  mergedProducts,
  isProUser,
  onClose,
}: {
  product: TopProduct | null;
  mergedProducts: MergedProductRow[];
  isProUser: boolean;
  onClose: () => void;
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
  const uplift1  = Math.round(views24h * 0.01 * PRODUCT_AOV);
  const uplift2  = Math.round(views24h * 0.02 * PRODUCT_AOV);

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
                  <div className="mt-1 text-[20px] font-semibold tabular-nums text-emerald-300">€{uplift1}</div>
                  <div className="mt-0.5 text-[10px] text-slate-600">
                    {views24h} views × 1% × €{PRODUCT_AOV}
                  </div>
                </div>
                <div className="rounded-xl border border-emerald-400/[0.22] bg-emerald-500/[0.09] px-4 py-3">
                  <div className="text-[10px] text-slate-500">+2% conversion / day</div>
                  <div className="mt-1 text-[20px] font-semibold tabular-nums text-emerald-300">€{uplift2}</div>
                  <div className="mt-0.5 text-[10px] text-slate-600">
                    {views24h} views × 2% × €{PRODUCT_AOV}
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

  // Tier state
  const [tier, setTier] = useState<"lite" | "pro">("lite");
  const [upgradeModalOpen, setUpgradeModalOpen] = useState(false);
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

  // ---------------------------------------------------------------------------
  // Read ?shop= from URL on mount
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setShop(params.get("shop") || "");
  }, []);

  // ---------------------------------------------------------------------------
  // Fetch plan
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!shop) return;
    fetch(
      `${API_BASE}/merchant/plan?shop=${encodeURIComponent(shop)}`,
      { headers: apiHeaders() }
    )
      .then((res) => (res.ok ? res.json() : { plan: "lite", billing_active: false }))
      .then((json) => {
        const isPro = json.plan === "pro" && json.billing_active === true;
        setTier(isPro ? "pro" : "lite");
      })
      .catch(() => setTier("lite"));
  }, [shop]);

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

    fetch(endpoint, { headers: apiHeaders(), cache: "no-store" })
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
          { method: "GET", headers: apiHeaders(), cache: "no-store" }
        );

        if (!res.ok) throw new Error(`Overview failed: ${res.status}`);
        const json = (await res.json()) as OverviewResponse;

        if (mounted) {
          setData({
            summary:              json.summary || {},
            top_products:         Array.isArray(json.top_products) ? json.top_products : [],
            price_intelligence:   Array.isArray(json.price_intelligence) ? json.price_intelligence : [],
            market_lookup:        Array.isArray(json.market_lookup) ? json.market_lookup : [],
            revenue_window_tease: json.revenue_window_tease ?? null,
            revenue_windows:      json.revenue_windows ?? null,
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
            { headers: apiHeaders(), cache: "no-store" }
          ),
          fetch(
            `${API_BASE}/products/trend?shop=${encodeURIComponent(shop)}`,
            { headers: apiHeaders(), cache: "no-store" }
          ),
        ]);

        const metricsJson = metricsRes.ok ? await metricsRes.json() : { products: [] };
        const trendJson = trendRes.ok ? await trendRes.json() : { products: [] };

        console.log("[WishSpark] /products/metrics →", metricsJson);
        console.log("[WishSpark] /products/trend →", trendJson);

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

        console.log("[WishSpark] trend rows normalised:", trendRows.length, "rows");
        if (trendRows.length > 0) {
          console.log("[WishSpark] first trend row sample:", trendRows[0]);
        }

        // Diagnostic: compare raw product_url values to catch mismatch
        if (metrics.length > 0 && trendRows.length > 0) {
          console.log(
            "[WishSpark] metrics URLs (first 3):",
            metrics.slice(0, 3).map((m) => JSON.stringify(m.product_url))
          );
          console.log(
            "[WishSpark] trend URLs (first 3):",
            trendRows.slice(0, 3).map((t) => JSON.stringify(t.product_url))
          );
        }

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
        const res = await fetch(endpoint, { headers: apiHeaders(), cache: "no-store" });
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
        const res = await fetch(endpoint, { method: "GET", headers: apiHeaders(), cache: "no-store" });
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
            { headers: apiHeaders(), cache: "no-store" }
          ),
          fetch(
            `${API_BASE}/actions/tasks?limit=50&shop=${encodeURIComponent(shop)}`,
            { headers: apiHeaders(), cache: "no-store" }
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
          { headers: apiHeaders(), cache: "no-store" }
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
        const [alertsRes, trendRes, pagesRes, sessionsRes, funnelRes, clicksRes] = await Promise.all([
          fetch(alertsEndpoint,                                                                       { headers: apiHeaders(), cache: "no-store" }),
          fetch(`${API_BASE}/analytics/weekly-trend?shop=${encodeURIComponent(shop)}`, { headers: apiHeaders(), cache: "no-store" }),
          fetch(`${API_BASE}/analytics/top-pages?shop=${encodeURIComponent(shop)}`,    { headers: apiHeaders(), cache: "no-store" }),
          fetch(`${API_BASE}/analytics/sessions?shop=${encodeURIComponent(shop)}`,     { headers: apiHeaders(), cache: "no-store" }),
          fetch(`${API_BASE}/analytics/funnel?shop=${encodeURIComponent(shop)}`,       { headers: apiHeaders(), cache: "no-store" }),
          fetch(`${API_BASE}/analytics/clicks?shop=${encodeURIComponent(shop)}`,       { headers: apiHeaders(), cache: "no-store" }),
        ]);

        const alertsJson   = alertsRes.ok   ? await alertsRes.json()   : { alerts: [] };
        const trendJson    = trendRes.ok    ? await trendRes.json()    : { trend: [] };
        const pagesJson    = pagesRes.ok    ? await pagesRes.json()    : { pages: [] };
        const sessionsJson = sessionsRes.ok ? await sessionsRes.json() : { sessions: [] };
        const funnelJson   = funnelRes.ok   ? await funnelRes.json()   : { steps: [] };
        const clicksJson   = clicksRes.ok   ? await clicksRes.json()   : { clicks: [] };

        if (!active) return;
        setAlerts(Array.isArray(alertsJson.alerts)     ? alertsJson.alerts     : []);
        setTrend(Array.isArray(trendJson.trend)        ? trendJson.trend       : []);
        setTopPages(Array.isArray(pagesJson.pages)     ? pagesJson.pages       : []);
        setSessions(Array.isArray(sessionsJson.sessions) ? sessionsJson.sessions : []);
        setFunnelSteps(Array.isArray(funnelJson.steps) ? funnelJson.steps      : []);
        setClicks(Array.isArray(clicksJson.clicks)     ? clicksJson.clicks     : []);
      } catch { /* silent */ }
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
    const topSig = signals[0];
    const totalViews = productMetrics.reduce((s, m) => s + (m.views_24h ?? 0), 0);
    if (!topSig && totalViews === 0) return null;
    const today = new Date().toISOString().split("T")[0];
    const headline = topSig
      ? `🦔 I'd start with ${topSig.human_label || shortUrl(topSig.product_url || "this product")} — it's showing ${prettyText(topSig.signal_type).toLowerCase()} and deserves attention`
      : `${formatNumber(totalViews)} views tracked today — check the product table to see what's ready to convert`;
    return {
      brief_date: today,
      headline,
      signals_count: signals.length || undefined,
      top_signal_type: topSig?.signal_type ?? null,
      top_product_url: topSig?.product_url ?? null,
      top_product_label: topSig?.human_label ?? topSig?.product_url ?? null,
      summary_generated: false,
    };
  }, [brief, briefLoading, signals, productMetrics]);

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
  // Only applies when there are zero cart conversions — we assume a 2% baseline
  // conversion rate and a €50 average order value.
  const ASSUMED_AOV = 50;
  const BASELINE_CVR = 0.02;

  function computeEstimatedLoss(m: ProductMetricsRow): number | null {
    if ((m.cart_conversions_24h ?? 0) !== 0) return null;
    const views = m.views_24h ?? 0;
    if (views === 0) return null;
    return Math.round(views * BASELINE_CVR * ASSUMED_AOV);
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
  // UI handlers
  // ---------------------------------------------------------------------------
  function handleTierToggle() {
    if (tier === "lite") setUpgradeModalOpen(true);
  }

  function handleNavigate(id: string) {
    setActiveSection(id);
    document
      .getElementById(`section-${id}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function executeCandidate(candidate: ActionCandidate) {
    try {
      const res = await fetch(
        `${API_BASE}/actions/execute?shop=${encodeURIComponent(shop)}`,
        {
          method: "POST",
          headers: apiHeaders(),
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
      />

      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar shop={shop} tier={tier} onTierToggle={handleTierToggle} />

        <main className="flex-1 overflow-y-auto">
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
            <div className="space-y-6 px-6 py-5 pb-16">

              {/* Greeting */}
              <p className="text-[13px] text-slate-500">
                Hey 👋 Here&apos;s what&apos;s happening in your store today
              </p>

              {/* 1 — Daily Brief */}
              <section id="section-brief">
                <BriefHero
                  brief={effectiveBrief}
                  loading={briefLoading}
                  tier={tier}
                  onUpgradeClick={() => setUpgradeModalOpen(true)}
                />
              </section>

              {/* 2 — Merchant snapshot */}
              <section id="section-overview">
                <SectionHeading
                  eyebrow="Overview"
                  title="Merchant snapshot"
                  description="Core metrics at a glance: traffic, intent, and conversion-ready signals."
                />
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <KpiCard label="Total Visitors" value={formatNumber(summary.total_visitors)} hint="Unique tracked visitors" numeric={summary.total_visitors} onClick={() => setActiveKpi("visitors")} />
                  <KpiCard label="Total Events" value={formatNumber(summary.total_events)} hint="Behavioral signals collected" numeric={summary.total_events} onClick={() => setActiveKpi("events")} />
                  <KpiCard label="Hot Visitors" value={formatNumber(summary.hot_visitors)} hint="High purchase intent" numeric={summary.hot_visitors} onClick={() => setActiveKpi("hot")} />
                  <KpiCard label="Wishlist Adds" value={formatNumber(summary.wishlist_adds)} hint="Strong product desire signals" numeric={summary.wishlist_adds} onClick={() => setActiveKpi("wishlist")} />
                  <KpiCard label="Average Intent" value={formatScore(summary.avg_intent_score)} hint="Average signal strength — click for breakdown" numeric={summary.avg_intent_score} onClick={() => setActiveKpi("intent")} />
                  <KpiCard label="Intent Distribution" value={`${formatNumber(summary.hot_visitors)} / ${formatNumber(summary.warm_visitors)} / ${formatNumber(summary.cold_visitors)}`} hint="Hot / Warm / Cold — click for breakdown" onClick={() => setActiveKpi("distribution")} />
                  <KpiCard label="Sessions" value={formatNumber(summary.total_sessions)} hint="Tracked browsing sessions" numeric={summary.total_sessions} onClick={() => setActiveKpi("sessions")} />
                  <KpiCard label="Conversion-ready Products" value={formatNumber(summary.conversion_ready_products)} hint="Products with action potential" numeric={summary.conversion_ready_products} onClick={() => setActiveKpi("products")} />
                </div>
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

              {/* 3 — Signals requiring attention + Highest-intent products */}
              <section id="section-signals">
                {/* Dual heading row — mirrors the 2+2 card columns below */}
                <div className="mb-3 grid gap-4 xl:grid-cols-2">
                  <SectionHeading eyebrow="Live Alerts" title="Signals requiring attention" />
                  {topProducts.length > 0 && (
                    <SectionHeading eyebrow="Top Products" title="Highest-intent products" />
                  )}
                </div>

                {/* Unified 4-column card grid — all cards share one grid context
                    so CSS auto-rows enforces equal height per row */}
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">

                  {/* Alert cards — occupy first 2 columns */}
                  {alerts.length === 0 ? (
                    <p className="sm:col-span-2 rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-600">
                      No alerts right now — your store looks healthy.
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
                    eyebrow="Product Performance"
                    title="Engagement &amp; conversion by product"
                    description="Pre-aggregated 24h metrics — views, cart abandonment, dwell, scroll, and engagement score."
                  />
                  <p className="-mt-3 mb-5 text-[11px] text-slate-600">
                    Focus on high priority products first
                  </p>

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
                              <span className="ml-1 text-[11px] text-rose-200/50">(est. 2% CVR × €50 AOV)</span>
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
                              className={`border-t border-white/[0.04] transition-colors hover:bg-white/[0.02] ${
                                i === 0 ? "bg-violet-500/[0.04] shadow-[inset_0_0_0_1px_rgba(167,139,250,0.08)]" : ""
                              }`}
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
                                      <span className="block text-[10px] text-slate-500">based on 2% conversion · €50 AOV</span>
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
                                    Unlock full list →
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

              {/* 5 — What to do next */}
              {(() => {
                const productItems = mergedProducts.filter((r) => r.insight && r.action_suggestion).slice(0, 3);
                const sigItems = signals.filter((s) => s.human_action || s.explanation).slice(0, Math.max(0, 3 - productItems.length));
                if (productItems.length === 0 && sigItems.length === 0) return null;

                const topRow = productItems[0];
                // Hedgehog tip is diagnostic for Lite (what is happening) and
                // prescriptive for Pro (what to do). Keep the distinction here
                // so the Lite/Pro boundary is explicit and auditable.
                const hedgehogTip = isProUser
                  ? topRow
                    ? `🦔 I'd start with ${shortUrl(topRow.product_url)} — ${(topRow.insight || "").replace(/^[^\w]*/, "").toLowerCase()}`
                    : "🦔 Focus on products with returning visitors first — they're already interested, a small nudge can convert them."
                  : topRow
                    ? `🦔 ${shortUrl(topRow.product_url)} is showing ${(topRow.insight || "").replace(/^[^\w]*/, "").toLowerCase()}.`
                    : "🦔 Products with returning visitors often carry the strongest conversion signal.";

                return (
                  <section id="section-what-next">
                    <div className="mb-3">
                      {/* Heading is diagnostic for Lite, prescriptive for Pro — matches what each tier can act on */}
                      <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/70">
                        {isProUser ? "What to do next" : "Active signals"}
                      </span>
                    </div>
                    <div className="divide-y divide-white/[0.05] overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.02]">
                      {productItems.map((row, i) => (
                        <div key={`wtn-prod-${row.product_url}-${i}`} className="flex items-start gap-3 px-4 py-3 transition-colors hover:bg-white/[0.02]">
                          <span className={`mt-[3px] h-2 w-2 flex-shrink-0 rounded-full ${
                            row.priority === "HIGH" ? "bg-rose-400 shadow-[0_0_5px_rgba(251,113,133,0.6)]"
                            : row.priority === "MED" ? "bg-amber-300 shadow-[0_0_5px_rgba(252,211,77,0.5)]"
                            : "bg-slate-600"
                          }`} />
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                              <span className="text-[12px] font-medium text-slate-200">{shortUrl(row.product_url)}</span>
                              <span className="text-[11px] text-slate-500">{row.insight}</span>
                            </div>
                            {row.action_suggestion && (
                              isProUser ? (
                                <button
                                  className="mt-0.5 text-[11px] text-violet-400/80 underline-offset-2 hover:text-violet-300 hover:underline"
                                  onClick={() => row.product_url && window.open(row.product_url, "_blank", "noopener,noreferrer")}
                                >
                                  → {row.action_suggestion}
                                </button>
                              ) : (
                                <span
                                  role="button"
                                  className="mt-0.5 block cursor-pointer text-[11px] text-slate-500 transition hover:text-slate-400"
                                  onClick={() => setUpgradeModalOpen(true)}
                                >
                                  This product needs attention<span className="ml-1.5 text-violet-400/70">Unlock in Pro</span>
                                </span>
                              )
                            )}
                          </div>
                          {row.estimated_loss != null && (
                            isProUser ? (
                              <span className="flex-shrink-0 text-[11px] tabular-nums text-amber-400/70">€{row.estimated_loss}</span>
                            ) : (
                              <span
                                role="button"
                                className="flex-shrink-0 cursor-pointer text-[11px] text-slate-500 transition hover:text-slate-400"
                                onClick={() => setUpgradeModalOpen(true)}
                              >
                                Revenue at risk<span className="ml-2 text-[10px] text-slate-600">(visible in Pro)</span>
                              </span>
                            )
                          )}
                        </div>
                      ))}
                      {sigItems.map((sig, i) => (
                        <div key={`wtn-sig-${sig.product_url}-${i}`} className="flex items-start gap-3 px-4 py-3 transition-colors hover:bg-white/[0.02]">
                          <span className="mt-[3px] h-2 w-2 flex-shrink-0 rounded-full bg-cyan-400/60" />
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                              <span className="text-[12px] font-medium text-slate-200">{sig.human_label || shortUrl(sig.product_url || "")}</span>
                              <span className="text-[11px] text-slate-500">{prettyText(sig.signal_type)}</span>
                            </div>
                            {sig.human_action && (
                              isProUser ? (
                                <button
                                  className="mt-0.5 text-[11px] text-violet-400/80 underline-offset-2 hover:text-violet-300 hover:underline"
                                  onClick={() => sig.product_url && window.open(sig.product_url, "_blank", "noopener,noreferrer")}
                                >
                                  → {sig.human_action}
                                </button>
                              ) : (
                                <span
                                  role="button"
                                  className="mt-0.5 block cursor-pointer text-[11px] text-slate-500 transition hover:text-slate-400"
                                  onClick={() => setUpgradeModalOpen(true)}
                                >
                                  Action available<span className="ml-1.5 text-violet-400/70">Unlock in Pro</span>
                                </span>
                              )
                            )}
                          </div>
                        </div>
                      ))}
                      {/* Pro-only: Action candidates with live task status */}
                      {isProUser && candidates.length > 0 && (
                        <>
                          <div className="flex items-center gap-2 bg-violet-500/[0.04] px-4 py-2">
                            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-violet-300/60">AI Actions</span>
                            <span className="rounded-full bg-violet-500/20 px-1.5 py-px text-[9px] font-semibold text-violet-300/70">{candidates.length}</span>
                          </div>
                          {candidates.slice(0, 5).map((c, i) => {
                            const key = taskKey(c.product_url, c.action_type);
                            const task = taskMap.get(key);
                            const taskStatus = task?.status;
                            const isExpandable = taskStatus === "done" || taskStatus === "failed";
                            const isExpanded = expandedTaskKey === key;
                            const detail = isExpandable ? parseResultDetail(task?.result_detail) : {};
                            return (
                              <div key={`cand-${key}-${i}`} className="transition-colors hover:bg-white/[0.02]">
                                {/* Main row */}
                                <div
                                  className={`flex items-start gap-3 px-4 py-3${isExpandable ? " cursor-pointer select-none" : ""}`}
                                  onClick={isExpandable ? () => setExpandedTaskKey((prev) => (prev === key ? null : key)) : undefined}
                                >
                                  {/* Priority dot */}
                                  <span className={`mt-[3px] h-2 w-2 flex-shrink-0 rounded-full ${
                                    taskStatus === "executing" ? "bg-blue-400 shadow-[0_0_5px_rgba(96,165,250,0.6)]"
                                    : taskStatus === "done" ? "bg-emerald-400"
                                    : taskStatus === "failed" ? "bg-rose-400"
                                    : taskStatus === "pending" ? "bg-slate-400"
                                    : c.ready_now ? "bg-violet-400 shadow-[0_0_5px_rgba(167,139,250,0.5)]"
                                    : "bg-violet-400/40"
                                  }`} />
                                  {/* Content */}
                                  <div className="min-w-0 flex-1">
                                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                                      <span className="text-[10px] font-semibold uppercase tracking-[0.1em] text-violet-300/70">{prettyText(c.action_type)}</span>
                                      <span className="text-[12px] font-medium text-slate-200">{shortUrl(c.product_url)}</span>
                                    </div>
                                    <div className="mt-0.5 text-[11px] text-slate-500">{c.action_hint}</div>
                                    {taskStatus === "executing" && task?.claimed_by && (
                                      <div className="mt-1 inline-flex items-center gap-1.5 rounded border border-blue-400/20 bg-blue-500/[0.07] px-1.5 py-px">
                                        <span className="h-1 w-1 rounded-full bg-blue-400 opacity-80" />
                                        <span className="text-[10px] font-medium text-blue-400/80">{task.claimed_by}</span>
                                      </div>
                                    )}
                                  </div>
                                  {/* Right side */}
                                  <div className="flex flex-shrink-0 items-center gap-2">
                                    {c.expected_loss > 0 && (
                                      <span className="text-[11px] tabular-nums text-amber-400/70">€{Math.round(c.expected_loss)}</span>
                                    )}
                                    {taskStatus === "pending" ? (
                                      <>
                                        <span className="rounded-full bg-slate-500/20 px-2 py-0.5 text-[10px] text-slate-400">Pending</span>
                                        <button
                                          onClick={(e) => { e.stopPropagation(); dismissTask(task!.id, key); }}
                                          className="text-[10px] text-slate-600 transition hover:text-rose-400"
                                        >
                                          Dismiss
                                        </button>
                                      </>
                                    ) : taskStatus === "executing" ? (
                                      <span className="rounded-full bg-blue-500/20 px-2 py-0.5 text-[10px] text-blue-400">Executing</span>
                                    ) : taskStatus === "done" ? (
                                      <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] text-emerald-400">
                                        Done {isExpanded ? "▴" : "▾"}
                                      </span>
                                    ) : taskStatus === "failed" ? (
                                      <span className="rounded-full bg-rose-500/20 px-2 py-0.5 text-[10px] text-rose-400">
                                        Failed {isExpanded ? "▴" : "▾"}
                                      </span>
                                    ) : (
                                      <button
                                        onClick={() => executeCandidate(c)}
                                        className="rounded-lg bg-violet-600/80 px-2.5 py-1 text-[10px] font-semibold text-white transition hover:bg-violet-500 active:bg-violet-700"
                                      >
                                        Execute
                                      </button>
                                    )}
                                  </div>
                                </div>
                                {/* Inline expansion panel — done/failed only */}
                                {isExpanded && (
                                  <div className="mx-4 mb-3 rounded-lg border border-white/[0.06] bg-white/[0.03] px-3 py-2.5">
                                    {detail.outcome && (
                                      <div className="mb-1.5 flex items-center gap-2">
                                        <span className={`rounded-full px-2 py-px text-[9px] font-semibold uppercase tracking-[0.1em] ${
                                          detail.outcome === "PASS" ? "bg-emerald-500/20 text-emerald-300"
                                          : detail.outcome === "PARTIAL" ? "bg-amber-500/20 text-amber-300"
                                          : "bg-rose-500/20 text-rose-300"
                                        }`}>{detail.outcome}</span>
                                        {detail.agent_id && (
                                          <span className="text-[10px] text-slate-600">{detail.agent_id}</span>
                                        )}
                                      </div>
                                    )}
                                    {detail.summary ? (
                                      <p className="text-[11px] leading-relaxed text-slate-400">{detail.summary}</p>
                                    ) : !detail.outcome && !detail.agent_id ? (
                                      <p className="text-[11px] text-slate-600">No detail recorded.</p>
                                    ) : null}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </>
                      )}
                      <div className="flex items-center justify-between gap-2.5 bg-violet-500/[0.03] px-4 py-3">
                        <div className="flex items-start gap-2.5">
                          <span className="mt-[1px] flex-shrink-0 text-[13px]">🦔</span>
                          <p className="text-[11px] leading-[1.5] text-slate-500">{hedgehogTip}</p>
                        </div>
                        {!isProUser && (
                          <button
                            className="flex-shrink-0 text-[11px] text-violet-400 transition hover:text-violet-300"
                            onClick={() => setUpgradeModalOpen(true)}
                          >
                            Upgrade to unlock →
                          </button>
                        )}
                      </div>
                    </div>
                  </section>
                );
              })()}

              {/* 6 — Weekly Trend */}
              <section>
                <SectionHeading
                  eyebrow="Weekly Trend"
                  title="Traffic across the week"
                  description="Visitor volume over the last tracked days."
                />
                {trend.length === 0 ? (
                  <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-600">
                    No trend data yet — check back once traffic is flowing.
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
                <SectionHeading eyebrow="Top Pages" title="Where visitors spend time" />
                {topPages.length === 0 ? (
                  <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-600">
                    No page data available yet.
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

              {/* 8 — Conversion Funnel */}
              <section id="section-funnel">
                <SectionHeading
                  eyebrow="Funnel"
                  title="Conversion funnel"
                  description="Unique visitors at each stage of the purchase path."
                />
                {funnelSteps.length === 0 ? (
                  <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-600">
                    No funnel data yet — events will appear once your tracker is active.
                  </p>
                ) : (
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    {funnelSteps.map((step, i) => {
                      const barWidth = step.pct != null ? step.pct : 0;
                      const isLast = i === funnelSteps.length - 1;
                      return (
                        <div key={step.step} className="relative">
                          <div className="flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4">
                            {/* Step label */}
                            <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-600">
                              {step.label}
                            </div>
                            {/* Count */}
                            <div className="text-2xl font-semibold tabular-nums text-white">
                              {formatNumber(step.count)}
                            </div>
                            {/* Conversion bar */}
                            <div className="mt-2.5 h-1 w-full overflow-hidden rounded-full bg-white/[0.07]">
                              <div
                                className="h-full rounded-full bg-violet-400/60"
                                style={{ width: `${barWidth}%` }}
                              />
                            </div>
                            {/* % of views */}
                            <div className="mt-1 text-[11px] tabular-nums text-slate-500">
                              {step.pct != null ? `${step.pct}% of views` : "baseline"}
                            </div>
                            {/* Drop-off — shown on steps 2–4 */}
                            {step.drop_off != null && i > 0 && (
                              <div className="mt-1 text-[11px] tabular-nums text-rose-400/60">
                                ↓ {step.drop_off}% drop-off
                              </div>
                            )}
                          </div>
                          {/* Connector arrow */}
                          {!isLast && (
                            <div className="absolute -right-[7px] top-1/2 z-10 -translate-y-1/2 text-[10px] text-slate-700">
                              ›
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>

              {/* 9 — Session Timeline + Click Insights (side by side) */}
              <section id="section-sessions">
                <div className="grid gap-4 xl:grid-cols-2">

                  {/* Left — Session Replay Lite */}
                  <div>
                    <SectionHeading
                      eyebrow="Sessions"
                      title="Recent session timeline"
                      description="Last 10 visitor sessions — pages visited, duration, exit page."
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
                      title="Click insights"
                      description="Top pages by click interactions — no heatmap, ranked by volume."
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
              </section>

              {/* 10 — Live Radar */}
              <section id="section-live">
                <SectionHeading
                  eyebrow="Live"
                  title="Live visitor radar"
                  description="Real-time visitor positions by page and intent level."
                />
                <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
                  {liveVisitors.length === 0 ? (
                    <p className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3 text-[12px] text-slate-600">
                      No live visitors right now.
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

              {/* Pro — Holdout Lift Report */}
              {isProUser && (
                <section id="section-lift">
                  <SectionHeading
                    eyebrow="Proof of Value"
                    title="Holdout-controlled lift report"
                    description="How much more revenue did nudges drive vs visitors who saw nothing? Measured with a real control group."
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

              {/* 10 — Price + Market Intelligence (compact 2-col) */}
              <section>
                <div className="mb-4">
                  <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/70">Intelligence</span>
                  <h2 className="mt-1 text-lg font-semibold text-white">Unlock more intelligence</h2>
                  <p className="mt-1 text-sm text-slate-500">Competitive pricing and market positioning — available on Pro.</p>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">

                  {/* Price Intelligence */}
                  <div id="section-price-intelligence">
                    <ProGate tier={tier} onUpgradeClick={() => setUpgradeModalOpen(true)} label="Price Intelligence">
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
                    <ProGate tier={tier} onUpgradeClick={() => setUpgradeModalOpen(true)} label="Market Intelligence">
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

            </div>
          )}
        </main>
      </div>

      <UpgradeModal open={upgradeModalOpen} onClose={() => setUpgradeModalOpen(false)} />
      <KpiInsightModal activeKpi={activeKpi} summary={summary} onClose={() => setActiveKpi(null)} />
      <ProductInsightPanel
        product={activeTopProduct}
        mergedProducts={mergedProducts}
        isProUser={isProUser}
        onClose={() => setActiveTopProduct(null)}
      />
    </div>
  );
}
