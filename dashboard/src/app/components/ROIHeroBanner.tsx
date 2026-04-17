"use client";

/**
 * ROIHeroBanner — the single biggest-morale-boost element on the dashboard.
 *
 * Shows ONE number: holdout-proven savings in the last 30 days, with:
 *   - animated count-up on mount
 *   - trend arrow (7d vs prior 7d)
 *   - ROI ratio vs subscription cost
 *   - breakdown chips
 *   - top-win callout
 *
 * Placement: top of the Pro dashboard, above RevenueAtRiskHero.
 * Every login, the merchant sees this first. It is the retention weapon.
 *
 * Data source: GET /pro/roi-hero
 */

import { useEffect, useState } from "react";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
  DrawerBarChart,
} from "./DetailDrawer";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";

type BreakdownItem = {
  source: string;
  amount_eur: number;
  description: string;
  icon: string;
};

type TopWin = {
  title: string;
  amount_eur: number;
  narrative: string;
  when: string;
};

type ROIData = {
  shop_domain: string;
  total_saved_eur_30d: number;
  total_saved_eur_7d: number;
  total_saved_eur_all_time: number;
  delta_7d_vs_prior_pct: number | null;
  breakdown: BreakdownItem[];
  top_win: TopWin | null;
  plan_cost_eur_monthly: number;
  roi_ratio: number;
  headline_message: string;
  // Shop's native currency — `_eur` fields above are denominated here.
  currency?: string;
  generated_at: string;
};

// Compact money formatter routed through the shared helper so the
// symbol comes from the merchant's native currency (USD/EUR/GBP/…).
// The `currency` argument is the `data.currency` field from the
// /pro/roi-hero response; `formatMoneyCompact` falls back to "USD"
// safely when it's missing.
const makeFmtBig = (currency?: string) =>
  (n: number) => formatMoneyCompact(n, currency || "USD");

// Animated count-up hook
function useCountUp(target: number, durationMs = 1200): number {
  const [value, setValue] = useState(0);
  useEffect(() => {
    if (target === 0) {
      setValue(0);
      return;
    }
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const elapsed = now - start;
      const pct = Math.min(1, elapsed / durationMs);
      // Ease-out cubic for a satisfying snap
      const eased = 1 - Math.pow(1 - pct, 3);
      setValue(target * eased);
      if (pct < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs]);
  return value;
}

export function ROIHeroBanner({ apiBase, isProUser }: { apiBase: string; isProUser: boolean }) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<ROIData>({
    url: `${apiBase}/pro/roi-hero`,
    enabled: isProUser && !!apiBase,
    isEmpty: (d) =>
      (d.total_saved_eur_30d ?? 0) === 0 &&
      (d.total_saved_eur_all_time ?? 0) === 0 &&
      !d.top_win &&
      (d.breakdown?.length ?? 0) === 0,
  });

  const animated = useCountUp(data?.total_saved_eur_30d || 0);

  if (!isProUser) return null;

  if (state === "loading") {
    return (
      <div style={{ marginBottom: "24px" }}>
        <CardSkeleton label="Loading your proven savings" />
      </div>
    );
  }

  if (state === "error") {
    return (
      <div style={{ marginBottom: "24px" }}>
        <CardError
          label="Proven savings unavailable"
          message="We couldn't load your holdout-proven savings right now. Your core metrics are safe — this card will recover automatically. Retry to pull it now."
          onRetry={retry}
        />
      </div>
    );
  }

  if (state === "empty" || !data) {
    return (
      <div style={{ marginBottom: "24px" }}>
        <CardEmpty
          accent="emerald"
          title="Your proven-savings tracker is warming up"
          body="HedgeSpark runs real A/B tests against a control group before counting any saved euro. The first proven number appears once we've seen enough visitors — usually within 48 hours of going live."
          eta="First measurement in ~48h"
        />
      </div>
    );
  }

  const delta = data.delta_7d_vs_prior_pct;
  const deltaPositive = delta != null && delta > 0;
  const deltaNegative = delta != null && delta < 0;

  const isHero = data.total_saved_eur_30d > 0;
  const fmtEurBig = makeFmtBig(data.currency);

  return (
    <>
    <div
      role="button"
      tabIndex={0}
      aria-haspopup="dialog"
      aria-label={`Open proven savings details — ${fmtEurBig(data.total_saved_eur_30d)} saved in the last 30 days`}
      onClick={() => setDrawerOpen(true)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setDrawerOpen(true);
        }
      }}
      style={{
        marginBottom: "24px",
        padding: "28px 32px",
        borderRadius: "20px",
        background:
          "radial-gradient(circle at 20% 20%, rgba(16,185,129,0.12) 0%, transparent 55%), linear-gradient(135deg, #0b1220 0%, #111c2e 100%)",
        border: isHero ? "1px solid rgba(16,185,129,0.35)" : "1px solid rgba(232,160,78,0.25)",
        boxShadow: isHero
          ? "0 8px 32px rgba(16,185,129,0.18), inset 0 1px 0 rgba(255,255,255,0.06)"
          : "0 8px 32px rgba(0,0,0,0.4)",
        position: "relative",
        overflow: "hidden",
        cursor: "pointer",
        transition: "transform 0.2s ease, border-color 0.2s ease",
        outline: "none",
      }}
      onFocus={(e) => {
        e.currentTarget.style.boxShadow = isHero
          ? "0 8px 32px rgba(16,185,129,0.28), 0 0 0 3px rgba(232,160,78,0.45)"
          : "0 8px 32px rgba(0,0,0,0.4), 0 0 0 3px rgba(232,160,78,0.45)";
      }}
      onBlur={(e) => {
        e.currentTarget.style.boxShadow = isHero
          ? "0 8px 32px rgba(16,185,129,0.18), inset 0 1px 0 rgba(255,255,255,0.06)"
          : "0 8px 32px rgba(0,0,0,0.4)";
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = "translateY(-2px)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = "translateY(0)";
      }}
    >
      {/* Ambient glow */}
      <div
        style={{
          position: "absolute",
          top: "-60%",
          right: "-20%",
          width: "600px",
          height: "600px",
          background: isHero
            ? "radial-gradient(circle, rgba(16,185,129,0.1) 0%, transparent 60%)"
            : "radial-gradient(circle, rgba(232,160,78,0.06) 0%, transparent 60%)",
          pointerEvents: "none",
        }}
      />

      <div style={{ position: "relative", display: "flex", alignItems: "flex-start", gap: "32px", flexWrap: "wrap" }}>
        {/* ─── LEFT: Big Number ─── */}
        <div style={{ flex: "1 1 340px" }}>
          <div
            style={{
              color: "#64748b",
              fontSize: "11px",
              fontWeight: 700,
              letterSpacing: "0.1em",
              textTransform: "uppercase",
              marginBottom: "8px",
            }}
          >
            Holdout-proven savings · last 30 days
          </div>

          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: "16px",
              flexWrap: "wrap",
            }}
          >
            <div
              style={{
                fontSize: "clamp(42px, 6vw, 68px)",
                fontWeight: 900,
                color: isHero ? "#10b981" : "#e8a04e",
                fontVariantNumeric: "tabular-nums",
                letterSpacing: "-0.04em",
                lineHeight: 1,
                textShadow: isHero ? "0 0 40px rgba(16,185,129,0.3)" : "none",
              }}
            >
              {fmtEurBig(animated)}
            </div>

            {delta != null && isHero && (
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "4px",
                  padding: "6px 10px",
                  borderRadius: "8px",
                  background: deltaPositive
                    ? "rgba(16,185,129,0.15)"
                    : deltaNegative
                      ? "rgba(239,68,68,0.15)"
                      : "rgba(148,163,184,0.15)",
                  color: deltaPositive ? "#10b981" : deltaNegative ? "#ef4444" : "#94a3b8",
                  fontSize: "14px",
                  fontWeight: 700,
                }}
              >
                {deltaPositive ? "↑" : deltaNegative ? "↓" : "→"} {Math.abs(delta).toFixed(0)}%
                <span style={{ color: "inherit", opacity: 0.7, fontSize: "12px", marginLeft: "2px" }}>
                  vs prior 7d
                </span>
              </div>
            )}
          </div>

          <div
            style={{
              color: "#e2e8f0",
              fontSize: "15px",
              marginTop: "14px",
              maxWidth: "520px",
              lineHeight: 1.5,
              fontWeight: 500,
            }}
          >
            {data.headline_message}
          </div>

          {isHero && data.roi_ratio > 0 && (
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "8px",
                marginTop: "16px",
                padding: "8px 14px",
                borderRadius: "10px",
                background: "rgba(232,160,78,0.12)",
                border: "1px solid rgba(232,160,78,0.3)",
                color: "#e8a04e",
                fontSize: "13px",
                fontWeight: 700,
              }}
            >
              <span style={{ fontSize: "16px" }}>🎯</span>
              <span>
                ROI: {data.roi_ratio.toFixed(1)}× your {fmtEurBig(data.plan_cost_eur_monthly)}/mo subscription
              </span>
            </div>
          )}
        </div>

        {/* ─── RIGHT: Breakdown + Top Win ─── */}
        {(data.breakdown.length > 0 || data.top_win) && (
          <div style={{ flex: "1 1 280px", display: "flex", flexDirection: "column", gap: "12px" }}>
            {data.top_win && (
              <div
                style={{
                  padding: "14px 16px",
                  borderRadius: "12px",
                  background: "rgba(16,185,129,0.08)",
                  border: "1px solid rgba(16,185,129,0.25)",
                }}
              >
                <div
                  style={{
                    color: "#10b981",
                    fontSize: "10px",
                    fontWeight: 700,
                    letterSpacing: "0.08em",
                    textTransform: "uppercase",
                    marginBottom: "4px",
                  }}
                >
                  🏆 Top win
                </div>
                <div style={{ color: "#e2e8f0", fontSize: "14px", fontWeight: 600 }}>
                  {data.top_win.title}
                </div>
                <div style={{ color: "#10b981", fontSize: "18px", fontWeight: 800, marginTop: "2px" }}>
                  +{fmtEurBig(data.top_win.amount_eur)}
                </div>
              </div>
            )}

            {data.breakdown.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                {data.breakdown.map((b) => (
                  <div
                    key={b.source}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "10px",
                      padding: "8px 12px",
                      borderRadius: "8px",
                      background: "rgba(15,23,42,0.5)",
                      border: "1px solid rgba(148,163,184,0.1)",
                    }}
                  >
                    <span style={{ fontSize: "18px" }}>{b.icon}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ color: "#cbd5e1", fontSize: "12px", fontWeight: 600 }}>
                        {b.description}
                      </div>
                    </div>
                    <div
                      style={{
                        color: "#10b981",
                        fontSize: "14px",
                        fontWeight: 800,
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {fmtEurBig(b.amount_eur)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Footer badges */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "16px",
          marginTop: "20px",
          paddingTop: "16px",
          borderTop: "1px solid rgba(148,163,184,0.1)",
          fontSize: "12px",
          color: "#64748b",
          flexWrap: "wrap",
        }}
      >
        <span>
          7d: <b style={{ color: "#cbd5e1" }}>{fmtEurBig(data.total_saved_eur_7d)}</b>
        </span>
        <span>·</span>
        <span>
          All time: <b style={{ color: "#cbd5e1" }}>{fmtEurBig(data.total_saved_eur_all_time)}</b>
        </span>
        <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: "6px" }}>
          <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: "#10b981" }} />
          Proven by real tests · click for details
        </span>
      </div>
    </div>

    {/* Drawer — idiot-proof explainer + details */}
    <DetailDrawer
      open={drawerOpen}
      onClose={() => setDrawerOpen(false)}
      icon="💰"
      title="Money HedgeSpark saved you"
      subtitle="Proven against a real control group"
    >
      <DrawerExplainer
        body={
          "This is the money HedgeSpark actually kept in your pocket during the last 30 days. " +
          "Every number here was measured by comparing what happened WITH HedgeSpark's actions to " +
          "what would have happened WITHOUT them — using a small group of visitors we intentionally " +
          "left alone (the 'control group'). If the saved amount isn't clearly bigger than what " +
          "the control group produced, we don't count it."
        }
        why={
          "Most intelligence tools claim big numbers with no proof. Ours is the only saving you can " +
          "take to your accountant."
        }
      />

      <DrawerBigStat
        label="Saved in the last 30 days"
        value={fmtEurBig(data.total_saved_eur_30d)}
        sublabel={
          data.delta_7d_vs_prior_pct != null
            ? `${data.delta_7d_vs_prior_pct > 0 ? "+" : ""}${data.delta_7d_vs_prior_pct.toFixed(0)}% vs the week before`
            : undefined
        }
        color={isHero ? "#10b981" : "#e8a04e"}
      />

      <DrawerKeyValueList
        items={[
          { label: "Last 7 days", value: fmtEurBig(data.total_saved_eur_7d) },
          { label: "Last 30 days", value: fmtEurBig(data.total_saved_eur_30d) },
          { label: "All time", value: fmtEurBig(data.total_saved_eur_all_time) },
          {
            label: "Your subscription",
            value: `€${data.plan_cost_eur_monthly}/mo`,
          },
          {
            label: "Money back vs cost",
            value: `${data.roi_ratio.toFixed(1)}×`,
            color: data.roi_ratio >= 3 ? "#10b981" : data.roi_ratio >= 1 ? "#e8a04e" : "#f43f5e",
          },
        ]}
      />

      {data.top_win && (
        <>
          <DrawerSectionHeading>Your single biggest win</DrawerSectionHeading>
          <div
            style={{
              padding: "16px 18px",
              borderRadius: "12px",
              background: "rgba(16,185,129,0.08)",
              border: "1px solid rgba(16,185,129,0.25)",
            }}
          >
            <div style={{ color: "#e2e8f0", fontSize: "14px", fontWeight: 600, marginBottom: "4px" }}>
              {data.top_win.title}
            </div>
            <div
              style={{
                color: "#10b981",
                fontSize: "24px",
                fontWeight: 800,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              +{fmtEurBig(data.top_win.amount_eur)}
            </div>
            <div style={{ color: "#94a3b8", fontSize: "12px", marginTop: "6px" }}>
              {data.top_win.narrative}
            </div>
          </div>
        </>
      )}

      {data.breakdown.length > 0 && (
        <>
          <DrawerSectionHeading>Where the savings came from</DrawerSectionHeading>
          <DrawerBarChart
            points={data.breakdown.map((b) => ({
              label: `${b.icon} ${b.description}`,
              value: Math.round(b.amount_eur),
            }))}
            color="#10b981"
            unit="€"
          />
        </>
      )}

      <DrawerSectionHeading>How we prove it</DrawerSectionHeading>
      <div
        style={{
          padding: "14px 16px",
          borderRadius: "10px",
          background: "rgba(15,23,42,0.5)",
          border: "1px solid rgba(148,163,184,0.1)",
          color: "#cbd5e1",
          fontSize: "13px",
          lineHeight: 1.6,
        }}
      >
        For every action HedgeSpark takes, we leave <b style={{ color: "#e8a04e" }}>20% of eligible
        visitors untouched</b> as a control. Then we compare: did the 80% who got the action buy
        more than the 20% who didn't? If yes, we count the difference as real savings. If the two
        groups look the same, we count zero. No creative math. Just the gap between treated and
        untreated.
      </div>
    </DetailDrawer>
    </>
  );
}
