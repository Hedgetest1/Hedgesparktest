"use client";

/**
 * MarginHealthCard — "Profit headroom" card.
 *
 * Merchant-speak for the margin_guard snapshot. Shows:
 *   - current gross margin %
 *   - how much discount room before it gets unsafe
 *   - precision level (rough / refined / exact)
 *
 * Click → drawer with what-if slider (drag to see projected margin).
 * The slider calls /pro/margin/check?discount_pct=-X live.
 *
 * Data: GET /pro/margin/snapshot
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
} from "./DetailDrawer";

type Snapshot = {
  shop_domain: string;
  window_days: number;
  revenue_eur: number;
  cogs_eur: number;
  gross_margin_eur: number;
  gross_margin_pct: number;
  cogs_pct_used: number;
  precision: string;
  min_required_margin_pct: number;
  computed_at: string;
};

type CheckResult = {
  allowed: boolean;
  reason: string;
  current_margin_pct: number;
  projected_margin_pct: number;
  min_required_pct: number;
  precision: string;
  total_revenue_30d: number;
  total_cogs_30d: number;
};

const PRECISION_META: Record<string, { label: string; note: string; color: string }> = {
  rough: {
    label: "Rough estimate",
    note: "Using industry default COGS. Add your real product costs for accuracy.",
    color: "#94a3b8",
  },
  refined: {
    label: "Refined",
    note: "Using your shop's COGS setting.",
    color: "#e8a04e",
  },
  exact: {
    label: "Exact",
    note: "Using per-product costs you configured.",
    color: "#10b981",
  },
};

const fmtEur = (n: number) => {
  if (n === 0) return "€0";
  if (n >= 10_000) return `€${Math.round(n / 1000)}k`;
  if (n >= 1000) return `€${(n / 1000).toFixed(1)}k`;
  return `€${Math.round(n).toLocaleString("en")}`;
};

export function MarginHealthCard({ apiBase, isProUser }: { apiBase: string; isProUser: boolean }) {
  const [data, setData] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [whatifPct, setWhatifPct] = useState(-10);
  const [whatif, setWhatif] = useState<CheckResult | null>(null);
  const [whatifLoading, setWhatifLoading] = useState(false);

  useEffect(() => {
    if (!isProUser) {
      setLoading(false);
      return;
    }
    apiClient
      .GET("/pro/margin/snapshot")
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      .then(({ data: j, error: err }) => { if (!err && j) setData(j as any); })
      .finally(() => setLoading(false));
  }, [apiBase, isProUser]);

  useEffect(() => {
    if (!drawerOpen || !isProUser) return;
    setWhatifLoading(true);
    const t = setTimeout(() => {
      apiClient
        .GET("/pro/margin/check", { params: { query: { discount_pct: whatifPct } } })
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        .then(({ data: j, error: err }) => { if (!err && j) setWhatif(j as any); })
        .finally(() => setWhatifLoading(false));
    }, 180);
    return () => clearTimeout(t);
  }, [drawerOpen, apiBase, whatifPct, isProUser]);

  if (!isProUser || loading || !data) return null;

  const precision = PRECISION_META[data.precision] || PRECISION_META.rough;
  const headroom = data.gross_margin_pct - data.min_required_margin_pct;
  const healthy = data.gross_margin_pct >= data.min_required_margin_pct + 10;
  const color = healthy ? "#10b981" : headroom > 0 ? "#e8a04e" : "#f43f5e";

  return (
    <>
      <div
        onClick={() => setDrawerOpen(true)}
        style={{
          padding: "22px 24px",
          borderRadius: "16px",
          background: "linear-gradient(135deg, #0b1220 0%, #141d33 100%)",
          border: `1px solid ${color}33`,
          cursor: "pointer",
          transition: "transform 0.2s ease",
          marginBottom: "16px",
          position: "relative",
          overflow: "hidden",
        }}
        onMouseEnter={(e) => (e.currentTarget.style.transform = "translateY(-2px)")}
        onMouseLeave={(e) => (e.currentTarget.style.transform = "translateY(0)")}
      >
        <div
          style={{
            position: "absolute",
            top: "-40%",
            right: "-10%",
            width: "320px",
            height: "320px",
            background: `radial-gradient(circle, ${color}12 0%, transparent 60%)`,
            pointerEvents: "none",
          }}
        />

        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "8px", position: "relative" }}>
          <span style={{ fontSize: "18px" }}>🛡️</span>
          <div
            style={{
              color: "#e8a04e",
              fontSize: "11px",
              fontWeight: 700,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}
          >
            Profit headroom
          </div>
          <span
            style={{
              marginLeft: "auto",
              padding: "3px 10px",
              borderRadius: "6px",
              background: `${precision.color}20`,
              color: precision.color,
              fontSize: "10px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            {precision.label}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "baseline", gap: "10px", position: "relative" }}>
          <div
            style={{
              color,
              fontSize: "38px",
              fontWeight: 900,
              fontVariantNumeric: "tabular-nums",
              letterSpacing: "-0.03em",
              lineHeight: 1,
            }}
          >
            {data.gross_margin_pct.toFixed(0)}%
          </div>
          <div style={{ color: "#94a3b8", fontSize: "14px" }}>kept after costs</div>
        </div>

        <div style={{ color: "#cbd5e1", fontSize: "13px", marginTop: "10px", position: "relative" }}>
          {headroom > 0
            ? `You've got ${headroom.toFixed(0)} points of room before a discount gets risky.`
            : `You're at the safety floor — discounts could push you into the red.`}
        </div>

        <div
          style={{
            marginTop: "12px",
            paddingTop: "12px",
            borderTop: "1px solid rgba(148,163,184,0.08)",
            display: "flex",
            gap: "16px",
            fontSize: "12px",
            color: "#64748b",
            position: "relative",
          }}
        >
          <span>
            Revenue <b style={{ color: "#cbd5e1" }}>{fmtEur(data.revenue_eur)}</b>
          </span>
          <span>·</span>
          <span>
            Costs <b style={{ color: "#cbd5e1" }}>{fmtEur(data.cogs_eur)}</b>
          </span>
          <span>·</span>
          <span>
            Kept <b style={{ color }}>{fmtEur(data.gross_margin_eur)}</b>
          </span>
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="🛡️"
        title="Profit headroom"
        subtitle={`${precision.label} — ${precision.note}`}
      >
        <DrawerExplainer
          body={
            "This is how much of every € you keep after paying for the product (what Shopify charges to " +
            "make + ship it). It's your safety cushion. When HedgeSpark runs a price test, it refuses any " +
            "discount that would drop this number below your safety floor — even if your trust contract " +
            "would otherwise allow it. Belt AND braces."
          }
          why={
            "Most SMB intelligence tools will happily recommend a 20% discount on a product with 22% margin. " +
            "That's a 2% profit margin after costs. We refuse to do that to you."
          }
        />

        <DrawerBigStat
          label="Current gross margin"
          value={`${data.gross_margin_pct.toFixed(0)}%`}
          sublabel={`${fmtEur(data.gross_margin_eur)} kept over the last 30 days`}
          color={color}
        />

        <DrawerKeyValueList
          items={[
            { label: "Revenue (30d)", value: fmtEur(data.revenue_eur) },
            { label: "Product costs", value: fmtEur(data.cogs_eur) },
            { label: "Kept after costs", value: fmtEur(data.gross_margin_eur), color },
            { label: "Safety floor", value: `${data.min_required_margin_pct.toFixed(0)}%` },
            { label: "Room to move", value: `${headroom.toFixed(0)} pts`, color },
          ]}
        />

        <DrawerSectionHeading>What-if: try a discount</DrawerSectionHeading>
        <div
          style={{
            padding: "16px 18px",
            borderRadius: "12px",
            background: "rgba(15,23,42,0.6)",
            border: "1px solid rgba(148,163,184,0.15)",
          }}
        >
          <div style={{ color: "#cbd5e1", fontSize: "13px", marginBottom: "12px" }}>
            Drag to see what would happen to your margin at different discount levels:
          </div>
          <input
            type="range"
            min={-50}
            max={0}
            value={whatifPct}
            onChange={(e) => setWhatifPct(Number(e.target.value))}
            style={{ width: "100%", accentColor: "#e8a04e" }}
          />
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              marginTop: "6px",
              fontSize: "11px",
              color: "#64748b",
            }}
          >
            <span>-50%</span>
            <span
              style={{
                color: "#e8a04e",
                fontWeight: 700,
                fontSize: "14px",
              }}
            >
              {whatifPct}% discount
            </span>
            <span>0%</span>
          </div>

          {whatif && !whatifLoading && (
            <div
              style={{
                marginTop: "14px",
                padding: "12px 14px",
                borderRadius: "10px",
                background: whatif.allowed ? "rgba(16,185,129,0.08)" : "rgba(244,63,94,0.08)",
                border: `1px solid ${whatif.allowed ? "rgba(16,185,129,0.25)" : "rgba(244,63,94,0.25)"}`,
              }}
            >
              <div
                style={{
                  color: whatif.allowed ? "#10b981" : "#f43f5e",
                  fontSize: "13px",
                  fontWeight: 700,
                }}
              >
                {whatif.allowed
                  ? `✓ Safe — margin drops to ${whatif.projected_margin_pct.toFixed(0)}%`
                  : `✗ Unsafe — margin would drop to ${whatif.projected_margin_pct.toFixed(0)}%`}
              </div>
              <div style={{ color: "#94a3b8", fontSize: "12px", marginTop: "4px" }}>
                {whatif.allowed
                  ? "HedgeSpark would allow this under a trust contract."
                  : `Below the ${whatif.min_required_pct.toFixed(0)}% floor — we'd refuse to run it even under a trust contract.`}
              </div>
            </div>
          )}
        </div>
      </DetailDrawer>
    </>
  );
}
