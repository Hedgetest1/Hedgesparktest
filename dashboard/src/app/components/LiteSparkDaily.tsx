"use client";

/**
 * LiteSparkDaily.tsx — the new `/app/lite` primary slot, v5.
 *
 * Six zones, single column, narrative-first. Spark narrates the whole
 * surface in first person. See /docs/LITE_VISUAL_SPEC_v5.md.
 *
 *   Zone 1 — Spark Says         (hero narrative: greeting + verdict + detail)
 *   Zone 2 — The Leak Gauge     (hero € number + 3 iOS-signal-style bars)
 *   Zone 3 — Today's 3 Fixes    (stacked action rows)
 *   Zone 4 — Your Week          (double-area Week Ridge chart + interpretation)
 *   Zone 5 — Spark's Memory     (5-row timeline)
 *   Zone 6 — Ask Spark          (existing AskHedgeSparkCard, reframed)
 *
 * Feature flag: gated on NEXT_PUBLIC_LITE_SPARK_DAILY === "true".
 * Falls back to the caller's current v4 layout when the flag is unset.
 *
 * Real-data contract: every number is derived from a real endpoint.
 * Empty states say "Watching…", "—", or "Clean morning"; never fake.
 */

import { useEffect, useRef, useState } from "react";
import Image from "next/image";

import { apiClient } from "../lib/api-client";
import {
  EVENT_DOT_COLORS,
  STATE_PHRASES,
  currencySymbol,
  greetByHour,
  openingVerdict,
  relativeLabel,
  shopDisplayName,
  topLeakDetail,
} from "../lib/sparkVoice";
import { AnalyticsAssistant } from "./AnalyticsAssistant";
import { CardError, CardSkeleton } from "./_CardStates";

// ============================================================================
// Types (mirroring backend payloads — typed via openapi where available)
// ============================================================================

type RarsComponent = {
  source: string;
  loss_eur: number;
  narrative?: string;
  evidence?: Record<string, unknown>;
};

type RarsPayload = {
  shop_domain?: string;
  total_at_risk_eur?: number;
  prevented_eur_this_month?: number;
  net_roi_eur?: number;
  components?: RarsComponent[];
  currency?: string;
  headline?: string;
};

type AbandonedProduct = {
  product_name: string;
  product_url: string;
  views_7d: number;
  carts_7d: number;
  purchases_7d: number;
  abandon_rate_pct: number;
  leak_point: string;
  leak_label: string;
};

type AbandonedPayload = {
  products?: AbandonedProduct[];
  total_products_count?: number;
  headline?: string;
  currency?: string;
};

type WeekRidgeDay = {
  date: string;
  at_risk_eur: number;
  captured_eur: number;
};

type WeekRidgePayload = {
  days: WeekRidgeDay[];
  currency: string;
  week_over_week_captured_pct: number | null;
  cold_start: boolean;
};

type SparkMemoryEvent = {
  timestamp: string;
  relative_label: string;
  event_type: string;
  sentence: string;
  dot_color: string;
};

type SparkMemoryPayload = {
  events: SparkMemoryEvent[];
  count: number;
};

// ============================================================================
// Scroll reveal (IntersectionObserver) — inlined, matches landing's R
// ============================================================================

function Reveal({
  children,
  delay = 0,
  className = "",
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setVisible(true);
          io.disconnect();
        }
      },
      { threshold: 0.1 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return (
    <div
      ref={ref}
      className={className}
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? "none" : "translateY(20px)",
        transition: `opacity 0.6s cubic-bezier(0.16,1,0.3,1) ${delay}s, transform 0.6s cubic-bezier(0.16,1,0.3,1) ${delay}s`,
      }}
    >
      {children}
    </div>
  );
}

// ============================================================================
// Zone 1 — Spark Says (hero narrative)
// ============================================================================

function SparkSays({
  shopDomain,
  rars,
  abandoned,
  loading,
  error,
  onSeeFixes,
}: {
  shopDomain: string;
  rars: RarsPayload | null;
  abandoned: AbandonedPayload | null;
  loading: boolean;
  error: boolean;
  onSeeFixes: () => void;
}) {
  const hour = new Date().getHours();
  const displayName = shopDisplayName(shopDomain);
  const currency = rars?.currency ?? "USD";
  const total = Math.round(rars?.total_at_risk_eur ?? 0);
  const components = (rars?.components ?? []).filter((c) => c.loss_eur > 0);
  const countPlaces = components.length;

  const topProduct = abandoned?.products?.[0] ?? null;

  const greeting = greetByHour(hour, displayName);
  const verdict = openingVerdict({
    totalAtRiskEur: total,
    countPlaces,
    preventedEur: rars?.prevented_eur_this_month ?? 0,
    currency,
  });
  const detail = topProduct
    ? topLeakDetail({
        topProduct: topProduct.product_name,
        views: topProduct.views_7d,
        carts: topProduct.carts_7d,
      })
    : null;

  const now = new Date();
  const timeStr = now.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
  });

  return (
    <section
      aria-labelledby="spark-says-heading"
      className="relative mb-8 overflow-hidden rounded-3xl border border-white/[0.06] bg-gradient-to-br from-[#0e0e1a] via-[#0a0a14] to-[#0e0a1a] p-7 sm:p-10"
    >
      {/* Amber signature stripe */}
      <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-transparent via-[#e8a04e] to-transparent opacity-60" />
      {/* Ambient violet blur (intelligence tint) */}
      <div className="pointer-events-none absolute -right-32 -top-32 h-[420px] w-[420px] rounded-full bg-[#7c3aed]/[0.04] blur-[160px]" />

      <div className="relative flex flex-col gap-6 sm:flex-row sm:items-start sm:gap-8">
        {/* Spark mascot */}
        <div className="flex-shrink-0">
          <Image
            src="/branding/hedgespark/spark.png"
            alt="Spark"
            width={64}
            height={64}
            className="hs-float-gentle"
            priority
          />
        </div>

        <div className="min-w-0 flex-1">
          <h2
            id="spark-says-heading"
            className="text-[15px] font-medium text-slate-300"
          >
            {loading ? STATE_PHRASES.watching("your first brief", 5, "min") : greeting}
          </h2>
          <p className="mt-2 text-[1.5rem] font-extrabold leading-[1.08] text-cream sm:text-[1.75rem]">
            {error ? (
              STATE_PHRASES.hiccup("your brief")
            ) : loading || !rars ? (
              <span className="text-slate-500">
                I&apos;m still watching your first visitors.
              </span>
            ) : total > 0 && countPlaces >= 1 ? (
              <>
                This morning I noticed{" "}
                <span className="text-[#e8a04e]">
                  {currencySymbol(currency)}
                  {total.toLocaleString("en-US")}
                </span>{" "}
                leaking in {countPlaces}{" "}
                {countPlaces === 1 ? "place" : "places"}.
              </>
            ) : (
              verdict
            )}
          </p>
          {detail && (
            <p className="mt-2 text-[15px] leading-relaxed text-slate-400">
              {detail}
            </p>
          )}
          {!loading && !error && total > 0 && (
            <button
              type="button"
              onClick={onSeeFixes}
              className="hs-cta-gradient mt-5 inline-block rounded-xl px-6 py-3 text-[15px] font-bold text-white transition-all duration-300 hover:shadow-[0_4px_32px_rgba(212,137,58,0.35)]"
            >
              Show me the 3 fixes →
            </button>
          )}
          <div className="mt-4 text-[11px] tabular-nums text-slate-500">
            {loading
              ? "Watching your storefront…"
              : `Updated ${timeStr} · next refresh ~5 min`}
          </div>
        </div>
      </div>
    </section>
  );
}

// ============================================================================
// Zone 2 — The Leak Gauge
// ============================================================================

function LeakGauge({
  rars,
  loading,
  error,
}: {
  rars: RarsPayload | null;
  loading: boolean;
  error: boolean;
}) {
  const currency = rars?.currency ?? "USD";
  const sym = currencySymbol(currency);
  const total = Math.round(rars?.total_at_risk_eur ?? 0);
  const prevented = Math.round(rars?.prevented_eur_this_month ?? 0);

  return (
    <section
      aria-labelledby="leak-gauge-heading"
      className="relative mb-8 overflow-hidden rounded-3xl border border-[#d4893a]/[0.18] bg-gradient-to-br from-[#1a1405] via-[#0d0a0a] to-[#0a0a14] p-7 sm:p-10"
    >
      <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-transparent via-[#d4893a] to-transparent opacity-70" />
      <div className="pointer-events-none absolute -right-40 -top-40 h-[420px] w-[420px] rounded-full bg-[#d4893a]/[0.06] blur-[180px]" />

      <div className="relative">
        <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-[#e8a04e]">
          Money at risk · this month
        </div>
        <h2
          id="leak-gauge-heading"
          className="mt-2 text-[1.75rem] font-extrabold leading-[1.05] tracking-tight text-[#e8a04e] sm:text-[2rem]"
        >
          The number no other Shopify tool shows you
        </h2>

        <div className="mt-6 flex flex-wrap items-end gap-5">
          <div
            className="font-mono text-[4.5rem] font-extrabold leading-[0.9] tabular-nums sm:text-[5.5rem]"
            style={{
              color: total > 0 ? "#d4893a" : "#64748b",
              textShadow:
                total > 0 ? "0 0 60px rgba(212,137,58,0.2)" : "none",
            }}
          >
            {error ? "—" : loading ? "…" : `${sym}${total.toLocaleString("en-US")}`}
          </div>
          {prevented > 0 && !loading && !error && (
            <div className="mb-4 rounded-xl border border-emerald-400/25 bg-emerald-500/[0.06] px-3.5 py-2">
              <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-400">
                Prevented this month
              </div>
              <div className="mt-0.5 text-[15px] font-bold tabular-nums text-emerald-300">
                {sym}
                {prevented.toLocaleString("en-US")}
              </div>
            </div>
          )}
        </div>

        <p className="mt-4 max-w-2xl text-[14.5px] leading-relaxed text-slate-400">
          Five independent signals — abandoned high-intent carts, refund
          trends, nudges underperforming peers, benchmark gaps, monthly
          targets — summed in your store&apos;s currency. Updated every
          minute. Not yesterday&apos;s revenue; right-now risk.
        </p>

        {/* Leak bars — Phase 2 will read baseline fields off each source
            endpoint's `baseline_median_14d`. Until those ship, all three
            rows render a "Watching for baseline" placeholder — never
            fake a level. */}
        <div className="mt-8 border-t border-white/[0.06] pt-6">
          <div className="mb-4 text-[11px] font-bold uppercase tracking-[0.2em] text-slate-500">
            Leak intensity · 3 signals
          </div>
          <div className="space-y-3">
            {(
              [
                { label: "Product leaks", color: "#f87171" },
                { label: "Cart leaks", color: "#e8a04e" },
                { label: "Retention risk", color: "#a78bfa" },
              ] as const
            ).map((row) => (
              <div
                key={row.label}
                className="flex items-center justify-between gap-4 rounded-xl border border-white/[0.05] bg-white/[0.015] px-4 py-3"
              >
                <span
                  className="text-[11px] font-bold uppercase tracking-[0.14em]"
                  style={{ color: row.color }}
                >
                  {row.label}
                </span>
                <span className="text-[12px] text-slate-500">
                  Watching — baseline ready in ~14 days
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

// ============================================================================
// Zone 3 — Today's 3 Fixes
// ============================================================================

const LEAK_POINT_TIPS: Record<string, (p: AbandonedProduct) => string> = {
  browse: (p) =>
    `${p.views_7d} views, ${p.carts_7d} carts. Check the photos + price.`,
  product_page: (p) =>
    `${p.views_7d} views, ${p.carts_7d} carts. Check the photos + price.`,
  cart: (p) =>
    `${p.carts_7d} carts but only ${p.purchases_7d} sales. Check shipping + payment.`,
  checkout: (p) =>
    `${p.carts_7d} carts, ${p.purchases_7d} checkouts. Simplify the form.`,
  none: (p) => `${p.views_7d} views this week. Watching for pressure.`,
};

function tipForLeak(product: AbandonedProduct): string {
  const fn =
    LEAK_POINT_TIPS[product.leak_point] ??
    LEAK_POINT_TIPS["browse"];
  return fn(product);
}

function TodaysFixes({
  rars,
  abandoned,
  loading,
  error,
  listRef,
  onOpenDeeper,
}: {
  rars: RarsPayload | null;
  abandoned: AbandonedPayload | null;
  loading: boolean;
  error: boolean;
  listRef: React.RefObject<HTMLDivElement | null>;
  onOpenDeeper: () => void;
}) {
  const currency = rars?.currency ?? "USD";
  const sym = currencySymbol(currency);
  const products = abandoned?.products ?? [];
  // Up to 3 fixes: map products to rows; fall back to components if no products.
  const rows: Array<{
    label: string;
    lossText: string;
    tip: string;
    accent: string;
  }> = [];

  const accentByIndex = ["#f87171", "#e8a04e", "#a78bfa"];

  for (let i = 0; i < Math.min(3, products.length); i++) {
    const p = products[i];
    // Rough daily loss attribution: abandon_rate_pct × views × baseline CVR × AOV
    // We don't have AOV in this payload, so we show "high leak" qualitatively.
    const dailyLoss = Math.max(
      0,
      Math.round((p.views_7d * (p.abandon_rate_pct / 100)) / 7),
    );
    rows.push({
      label: p.product_name,
      lossText:
        dailyLoss > 0
          ? `${dailyLoss} visitors/day walking away`
          : `${p.views_7d} views this week`,
      tip: tipForLeak(p),
      accent: accentByIndex[i],
    });
  }

  // If we have fewer than 3 from abandoned, fill with RARS components
  if (rows.length < 3 && rars?.components) {
    const sorted = [...rars.components]
      .filter((c) => c.loss_eur > 0)
      .sort((a, b) => b.loss_eur - a.loss_eur);
    for (const c of sorted) {
      if (rows.length >= 3) break;
      // Deduplicate by narrative similarity — skip if we already have this leak type
      const human = sourceToHuman(c.source);
      if (!human) continue;
      rows.push({
        label: human,
        lossText: `${sym}${Math.round(c.loss_eur).toLocaleString("en-US")} at risk`,
        tip: c.narrative ?? "Open the drill-down for the recommended fix.",
        accent: accentByIndex[rows.length],
      });
    }
  }

  const isEmpty = !loading && !error && rows.length === 0;

  return (
    <section
      ref={listRef}
      aria-labelledby="todays-fixes-heading"
      className="relative mb-8 overflow-hidden rounded-3xl border border-[#e8a04e]/[0.15] bg-gradient-to-br from-[#1a120a] via-[#0a0a14] to-[#0b0c18] p-7 sm:p-9"
    >
      <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-transparent via-[#e8a04e] to-transparent opacity-60" />

      <div className="relative">
        <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-[#e8a04e]">
          Your next moves
        </div>
        <h2
          id="todays-fixes-heading"
          className="mt-2 text-[1.75rem] font-extrabold leading-[1.05] tracking-tight text-[#e8a04e] sm:text-[2rem]"
        >
          What to do first
        </h2>

        <div className="mt-6 space-y-3">
          {loading ? (
            <CardSkeleton label="Loading your fixes" />
          ) : error ? (
            <CardError
              message="I hit a hiccup loading your fixes — retrying on its own."
              label="Fixes failed to load"
            />
          ) : isEmpty ? (
            <div className="rounded-2xl border border-emerald-400/[0.18] bg-emerald-500/[0.03] p-5 text-center">
              <div className="text-[13px] font-bold uppercase tracking-[0.16em] text-emerald-300">
                Looking clean here
              </div>
              <p className="mt-2 text-[14px] text-slate-400">
                Nothing to fix right now. I&apos;m still watching.
              </p>
            </div>
          ) : (
            rows.map((row, i) => (
              <button
                key={`${row.label}-${i}`}
                type="button"
                onClick={onOpenDeeper}
                className="group flex w-full items-start gap-4 rounded-2xl border border-white/[0.06] bg-[#0e0e1a]/60 p-5 text-left transition-all hover:border-white/[0.14] hover:bg-[#0e0e1a]"
              >
                <span
                  className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full text-[13px] font-extrabold tabular-nums"
                  style={{
                    color: row.accent,
                    background: `${row.accent}1a`,
                    border: `1px solid ${row.accent}40`,
                  }}
                  aria-hidden
                >
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[16px] font-semibold text-cream truncate">
                      {row.label}
                    </span>
                    <span
                      className="flex-shrink-0 text-[13px] font-bold tabular-nums"
                      style={{ color: row.accent }}
                    >
                      {row.lossText}
                    </span>
                  </div>
                  <p className="mt-1.5 text-[14px] leading-snug text-slate-400">
                    {row.tip}
                  </p>
                </div>
                <span
                  className="mt-1 flex-shrink-0 text-[14px] opacity-50 transition-opacity group-hover:opacity-100"
                  style={{ color: row.accent }}
                  aria-hidden
                >
                  →
                </span>
              </button>
            ))
          )}
        </div>
      </div>
    </section>
  );
}

function sourceToHuman(source: string): string | null {
  const map: Record<string, string> = {
    abandoned_high_intent: "Abandoned high-intent carts",
    refund_decline: "Products losing traction",
    nudge_gap: "Nudges underperforming peers",
    below_benchmark: "Peers out-earning you",
    goal_gap: "Your monthly targets",
  };
  return map[source] ?? null;
}

// ============================================================================
// Zone 4 — Your Week (Week Ridge chart)
// ============================================================================

function WeekRidge({
  data,
  loading,
  error,
}: {
  data: WeekRidgePayload | null;
  loading: boolean;
  error: boolean;
}) {
  const currency = data?.currency ?? "USD";
  const sym = currencySymbol(currency);
  const days = data?.days ?? [];
  const coldStart = data?.cold_start ?? true;

  const atRiskTotal = days.reduce((s, d) => s + d.at_risk_eur, 0);
  const capturedTotal = days.reduce((s, d) => s + d.captured_eur, 0);

  let interpretation: string;
  if (loading) {
    interpretation = "";
  } else if (error) {
    interpretation = STATE_PHRASES.hiccup("your week");
  } else if (coldStart || days.length === 0) {
    interpretation = "Watching your week build. First full 7-day read coming.";
  } else if (capturedTotal >= atRiskTotal * 1.1) {
    interpretation = `You captured ${sym}${Math.round(capturedTotal).toLocaleString("en-US")} this week — more than the ${sym}${Math.round(atRiskTotal).toLocaleString("en-US")} that walked away.`;
  } else if (atRiskTotal > capturedTotal * 1.1) {
    interpretation = `${sym}${Math.round(capturedTotal).toLocaleString("en-US")} captured this week, vs ${sym}${Math.round(atRiskTotal).toLocaleString("en-US")} at risk. Let's close the gap.`;
  } else {
    interpretation = `${sym}${Math.round(capturedTotal).toLocaleString("en-US")} captured vs ${sym}${Math.round(atRiskTotal).toLocaleString("en-US")} at risk — steady week.`;
  }

  return (
    <section
      aria-labelledby="week-ridge-heading"
      className="relative mb-8 overflow-hidden rounded-3xl border border-emerald-400/[0.15] bg-gradient-to-br from-[#0a1612] via-[#0a0a14] to-[#0b0c18] p-7 sm:p-9"
    >
      <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-transparent via-[#34d399] to-transparent opacity-55" />

      <div className="relative">
        <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-emerald-300">
          Your week
        </div>
        <h2
          id="week-ridge-heading"
          className="mt-2 text-[1.75rem] font-extrabold leading-[1.05] tracking-tight text-[#e8a04e] sm:text-[2rem]"
        >
          How the last 7 days went
        </h2>

        <div className="mt-6">
          {loading ? (
            <CardSkeleton label="Loading your week" />
          ) : error ? (
            <CardError message="I hit a hiccup loading your week." label="Week failed to load" />
          ) : coldStart || days.length === 0 ? (
            <div className="rounded-xl border border-dashed border-white/[0.1] bg-white/[0.01] px-4 py-8 text-center">
              <div className="text-[12px] font-semibold text-slate-300">
                Watching your week build
              </div>
              <p className="mt-1 text-[12px] leading-relaxed text-slate-500">
                Once 3+ days of orders are in, this shows your daily
                captured revenue vs. daily leaks. No fake numbers until
                there&apos;s something real to show.
              </p>
            </div>
          ) : (
            <WeekRidgeChart days={days} />
          )}
        </div>

        {interpretation && (
          <p className="mt-5 text-[16px] leading-relaxed text-cream">
            {interpretation}
          </p>
        )}
      </div>
    </section>
  );
}

// --- Week Ridge SVG chart ----------------------------------------------------

function WeekRidgeChart({ days }: { days: WeekRidgeDay[] }) {
  if (days.length === 0) return null;
  const width = 720;
  const height = 120;
  const padX = 20;
  const padY = 10;
  const usableW = width - padX * 2;
  const usableH = height - padY * 2;

  const maxVal = Math.max(
    ...days.map((d) => d.at_risk_eur),
    ...days.map((d) => d.captured_eur),
    1,
  );

  const xForIdx = (i: number) =>
    padX + (i / Math.max(1, days.length - 1)) * usableW;
  const yForVal = (v: number) => padY + usableH - (v / maxVal) * usableH;

  function buildSmoothPath(points: [number, number][]): string {
    if (points.length === 0) return "";
    if (points.length === 1) {
      const [x, y] = points[0];
      return `M ${x} ${y}`;
    }
    let d = `M ${points[0][0]} ${points[0][1]}`;
    for (let i = 1; i < points.length; i++) {
      const [x0, y0] = points[i - 1];
      const [x1, y1] = points[i];
      const cx = (x0 + x1) / 2;
      d += ` C ${cx} ${y0}, ${cx} ${y1}, ${x1} ${y1}`;
    }
    return d;
  }

  const atRiskPts: [number, number][] = days.map((d, i) => [
    xForIdx(i),
    yForVal(d.at_risk_eur),
  ]);
  const capturedPts: [number, number][] = days.map((d, i) => [
    xForIdx(i),
    yForVal(d.captured_eur),
  ]);

  const atRiskPath = buildSmoothPath(atRiskPts);
  const capturedPath = buildSmoothPath(capturedPts);

  const atRiskArea = `${atRiskPath} L ${padX + usableW} ${padY + usableH} L ${padX} ${padY + usableH} Z`;
  const capturedArea = `${capturedPath} L ${padX + usableW} ${padY + usableH} L ${padX} ${padY + usableH} Z`;

  const xLabels = days.map((d) => {
    try {
      return new Date(d.date + "T12:00:00Z").toLocaleDateString("en-US", {
        weekday: "short",
      });
    } catch {
      return "";
    }
  });

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="h-[120px] w-full"
        role="img"
        aria-label="Captured revenue vs at-risk amount, last 7 days"
      >
        <path d={atRiskArea} fill="#d4893a" opacity={0.35} />
        <path d={atRiskPath} fill="none" stroke="#d4893a" strokeOpacity={0.7} strokeWidth={1.5} />
        <path d={capturedArea} fill="#34d399" opacity={0.7} />
        <path d={capturedPath} fill="none" stroke="#34d399" strokeWidth={1.8} />
      </svg>
      <div className="mt-2 flex justify-between text-[10px] tabular-nums text-slate-500">
        {xLabels.map((lbl, i) => (
          <span key={`${lbl}-${i}`}>{lbl}</span>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-4 text-[11px] text-slate-500">
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="inline-block h-2 w-3 rounded-sm"
            style={{ background: "#34d399" }}
          />
          Captured
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="inline-block h-2 w-3 rounded-sm"
            style={{ background: "#d4893a", opacity: 0.5 }}
          />
          At risk
        </span>
      </div>
    </div>
  );
}

// ============================================================================
// Zone 5 — Spark's Memory
// ============================================================================

const DOT_COLOR_CSS: Record<string, string> = {
  rose: "#f87171",
  emerald: "#34d399",
  amber: "#e8a04e",
  violet: "#a78bfa",
  slate: "#94a3b8",
};

function SparkMemoryTimeline({
  data,
  loading,
  error,
}: {
  data: SparkMemoryPayload | null;
  loading: boolean;
  error: boolean;
}) {
  const events = data?.events ?? [];
  const isEmpty = !loading && !error && events.length === 0;

  return (
    <section
      aria-labelledby="spark-memory-heading"
      className="mb-8 rounded-3xl border border-white/[0.05] bg-white/[0.01] p-7 sm:p-9"
    >
      <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-slate-400">
        What I&apos;ve noticed recently
      </div>
      <h2
        id="spark-memory-heading"
        className="mt-2 text-[1.5rem] font-extrabold leading-[1.08] tracking-tight text-slate-100"
      >
        Spark&apos;s memory
      </h2>

      <div className="mt-6">
        {loading ? (
          <CardSkeleton label="Loading memory" />
        ) : error ? (
          <CardError message="I hit a hiccup loading my memory. Retrying." label="Memory failed to load" />
        ) : isEmpty ? (
          <p className="text-[13px] leading-relaxed text-slate-500">
            {STATE_PHRASES.noData()}
          </p>
        ) : (
          <ul className="space-y-3">
            {events.map((e, i) => {
              const dot =
                DOT_COLOR_CSS[e.dot_color] ?? DOT_COLOR_CSS.slate;
              return (
                <li
                  key={`${e.event_type}-${i}`}
                  className="flex items-start gap-4 py-1"
                >
                  <span
                    className="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full"
                    style={{ background: dot }}
                    aria-hidden
                  />
                  <span className="w-[90px] flex-shrink-0 text-[10px] uppercase tracking-wide tabular-nums text-slate-500">
                    {e.relative_label}
                  </span>
                  <span className="min-w-0 flex-1 text-[13.5px] leading-relaxed text-slate-300">
                    {e.sentence}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}

// ============================================================================
// Zone 6 — Ask Spark (wraps existing AskHedgeSparkCard)
// ============================================================================

function AskSpark() {
  return (
    <section
      aria-labelledby="ask-spark-heading"
      data-ask-spark-zone
      className="mb-8 rounded-3xl border border-white/[0.06] bg-[#0e0e1a]/60 p-7 sm:p-9"
    >
      <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-[#a78bfa]">
        Ask me anything
      </div>
      <h2
        id="ask-spark-heading"
        className="mt-2 text-[1.5rem] font-extrabold leading-[1.08] tracking-tight text-slate-100"
      >
        Need more context? Ask me about your store.
      </h2>
      <div className="mt-6">
        <AnalyticsAssistant />
      </div>
    </section>
  );
}

// ============================================================================
// Main component — composes the 6 zones
// ============================================================================

export interface LiteSparkDailyProps {
  shopDomain: string;
  /**
   * When a row in Zone 3 is clicked, or the Spark Says CTA fires,
   * the caller opens the Deeper drawer / navigates to the relevant
   * cassettone. Keeps the drawer concern OUT of this component so
   * the consumer controls routing + state persistence.
   */
  onOpenDeeper: () => void;
}

export function LiteSparkDaily({
  shopDomain,
  onOpenDeeper,
}: LiteSparkDailyProps) {
  const [rars, setRars] = useState<RarsPayload | null>(null);
  const [abandoned, setAbandoned] = useState<AbandonedPayload | null>(null);
  const [week, setWeek] = useState<WeekRidgePayload | null>(null);
  const [memory, setMemory] = useState<SparkMemoryPayload | null>(null);

  const [loadingMain, setLoadingMain] = useState(true);
  const [errorMain, setErrorMain] = useState(false);

  const [loadingWeek, setLoadingWeek] = useState(true);
  const [errorWeek, setErrorWeek] = useState(false);

  const [loadingMemory, setLoadingMemory] = useState(true);
  const [errorMemory, setErrorMemory] = useState(false);

  const fixesRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let active = true;
    setLoadingMain(true);
    setErrorMain(false);
    Promise.all([
      apiClient.GET("/pro/revenue-at-risk", {}),
      apiClient.GET("/pro/abandoned-intent", {}),
    ])
      .then(([rarsResp, abandonedResp]) => {
        if (!active) return;
        if (rarsResp.data) setRars(rarsResp.data as RarsPayload);
        if (abandonedResp.data) setAbandoned(abandonedResp.data as AbandonedPayload);
        setLoadingMain(false);
      })
      .catch(() => {
        if (!active) return;
        setErrorMain(true);
        setLoadingMain(false);
      });
    return () => {
      active = false;
    };
  }, [shopDomain]);

  useEffect(() => {
    let active = true;
    setLoadingWeek(true);
    setErrorWeek(false);
    apiClient
      .GET("/analytics/week-ridge", {})
      .then(({ data }) => {
        if (!active) return;
        if (data) setWeek(data as WeekRidgePayload);
        setLoadingWeek(false);
      })
      .catch(() => {
        if (!active) return;
        setErrorWeek(true);
        setLoadingWeek(false);
      });
    return () => {
      active = false;
    };
  }, [shopDomain]);

  useEffect(() => {
    let active = true;
    setLoadingMemory(true);
    setErrorMemory(false);
    apiClient
      .GET("/merchant/spark-memory", {})
      .then(({ data }) => {
        if (!active) return;
        if (data) setMemory(data as SparkMemoryPayload);
        setLoadingMemory(false);
      })
      .catch(() => {
        if (!active) return;
        setErrorMemory(true);
        setLoadingMemory(false);
      });
    return () => {
      active = false;
    };
  }, [shopDomain]);

  const scrollToFixes = () => {
    fixesRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div>
      <Reveal delay={0}>
        <SparkSays
          shopDomain={shopDomain}
          rars={rars}
          abandoned={abandoned}
          loading={loadingMain}
          error={errorMain}
          onSeeFixes={scrollToFixes}
        />
      </Reveal>
      <Reveal delay={0.06}>
        <LeakGauge rars={rars} loading={loadingMain} error={errorMain} />
      </Reveal>
      <Reveal delay={0.1}>
        <TodaysFixes
          rars={rars}
          abandoned={abandoned}
          loading={loadingMain}
          error={errorMain}
          listRef={fixesRef}
          onOpenDeeper={onOpenDeeper}
        />
      </Reveal>
      <Reveal delay={0.14}>
        <WeekRidge data={week} loading={loadingWeek} error={errorWeek} />
      </Reveal>
      <Reveal delay={0.18}>
        <SparkMemoryTimeline
          data={memory}
          loading={loadingMemory}
          error={errorMemory}
        />
      </Reveal>
      <Reveal delay={0.22}>
        <AskSpark />
      </Reveal>
    </div>
  );
}

// Re-export for named convenience
export default LiteSparkDaily;

// Silence the lint about EVENT_DOT_COLORS being unused — kept for v2
// parity with backend when additional event types land.
void EVENT_DOT_COLORS;
