"use client";

/**
 * KpiInsightModal — modal dialog opening when a KPI card is clicked.
 * Extracted from app/page.tsx (Phase Ω⁶ split).
 */

import { useEffect, useState } from "react";
import { formatDecimal, formatNumber, formatScore } from "../_lib/formatters";

export type SummaryShape = {
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

export function KpiInsightModal({
  activeKpi,
  summary,
  onClose,
}: {
  activeKpi: string | null;
  summary: SummaryShape;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!activeKpi) return;
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeKpi, onClose]);

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
      <div
        className="fixed inset-0 z-40 bg-black/30"
        onClick={onClose}
      />

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

        <div className="space-y-4 px-6 py-5">

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

          <div className="grid grid-cols-2 gap-2">
            {d.numbers.map((n) => (
              <div key={n.label} className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <div className="text-[10px] uppercase tracking-[0.1em] text-slate-600">{n.label}</div>
                <div className="mt-1 text-[15px] font-semibold tabular-nums text-white">{n.value}</div>
              </div>
            ))}
          </div>

          <div className="rounded-xl border border-violet-400/[0.12] bg-violet-500/[0.06] px-4 py-3.5">
            <p className="text-[12px] leading-[1.6] text-slate-300">{d.insight[0]}</p>
            <p className="mt-1.5 text-[11px] leading-[1.5] text-slate-500">{d.insight[1]}</p>
          </div>

        </div>
      </div>
    </>
  );
}
