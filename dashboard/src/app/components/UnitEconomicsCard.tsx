"use client";

/**
 * UnitEconomicsCard — "Money in vs money back" card.
 *
 * Pure-merchant language: don't say "CAC:LTV ratio", say "for every €1
 * you spend to acquire a customer, you get €X back over time".
 *
 * Data: GET /pro/cac-ltv
 * Click → drawer with math, status colors, and the "fix it" guidance.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact, currencySymbol } from "@/app/app/_lib/formatters";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
} from "./DetailDrawer";

type CacLtvData = {
  shop_domain: string;
  window_days: number;
  customers_acquired: number;
  total_ad_spend_eur: number;
  cac_eur: number;
  avg_ltv_eur: number;
  predicted_12m_ltv_eur: number;
  ratio: number;
  status: string;
  headline: string;
  ad_spend_source: string;
  // Shop's native currency — `_eur` fields above are in this currency.
  currency?: string;
  generated_at: string;
};

// Compact native-currency formatter. Routed through the shared helper
// so the symbol table lives in one place (_lib/formatters.ts).
const makeFmt = (currency?: string) =>
  (n: number) => formatMoneyCompact(n, currency || "USD");

const STATUS_META: Record<string, { color: string; icon: string; label: string }> = {
  healthy: { color: "#10b981", icon: "🟢", label: "Healthy" },
  ok: { color: "#e8a04e", icon: "🟡", label: "Tight" },
  unprofitable: { color: "#f43f5e", icon: "🔴", label: "Losing money" },
  no_data: { color: "#64748b", icon: "⚪", label: "Not yet" },
};

export function UnitEconomicsCard({ apiBase, isProUser }: { apiBase: string; isProUser: boolean }) {
  const [data, setData] = useState<CacLtvData | null>(null);
  const [loading, setLoading] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    if (!isProUser) {
      setLoading(false);
      return;
    }
    apiClient
      .GET("/pro/cac-ltv", { params: { query: { window_days: 30 } } })
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      .then(({ data: j, error: err }) => { if (!err && j) setData(j as any); })
      .finally(() => setLoading(false));
  }, [apiBase, isProUser]);

  if (!isProUser || loading || !data) return null;

  const meta = STATUS_META[data.status] || STATUS_META.no_data;
  const hasData = data.status !== "no_data";
  const fmt = makeFmt(data.currency);
  // Native-currency unit prefix for prose ("for every $1 spent" / "for every €1 spent").
  const sym = currencySymbol(data.currency);

  return (
    <>
      <div
        onClick={() => setDrawerOpen(true)}
        style={{
          padding: "22px 24px",
          borderRadius: "16px",
          background: "linear-gradient(135deg, #0b1220 0%, #141d33 100%)",
          border: `1px solid ${meta.color}33`,
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
            background: `radial-gradient(circle, ${meta.color}15 0%, transparent 60%)`,
            pointerEvents: "none",
          }}
        />

        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "8px", position: "relative" }}>
          <span style={{ fontSize: "18px" }}>💶</span>
          <div
            style={{
              color: "#e8a04e",
              fontSize: "11px",
              fontWeight: 700,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}
          >
            Money in vs money back
          </div>
          <span
            style={{
              marginLeft: "auto",
              padding: "3px 10px",
              borderRadius: "6px",
              background: `${meta.color}20`,
              color: meta.color,
              fontSize: "10px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            {meta.label}
          </span>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: "10px",
            marginTop: "4px",
            position: "relative",
          }}
        >
          {hasData ? (
            <>
              <div
                style={{
                  color: meta.color,
                  fontSize: "38px",
                  fontWeight: 900,
                  fontVariantNumeric: "tabular-nums",
                  letterSpacing: "-0.03em",
                  lineHeight: 1,
                }}
              >
                {data.ratio.toFixed(1)}×
              </div>
              <div style={{ color: "#94a3b8", fontSize: "14px" }}>
                money back for every {sym}1 spent
              </div>
            </>
          ) : (
            <div style={{ color: "#94a3b8", fontSize: "14px" }}>
              Add your monthly ad spend in Settings → Costs
            </div>
          )}
        </div>

        <div style={{ color: "#cbd5e1", fontSize: "13px", marginTop: "10px", position: "relative" }}>
          {data.headline}
        </div>

        {hasData && (
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
              <b style={{ color: "#cbd5e1" }}>{data.customers_acquired}</b> new customers
            </span>
            <span>·</span>
            <span>
              <b style={{ color: "#cbd5e1" }}>{fmt(data.cac_eur)}</b> cost each
            </span>
            <span>·</span>
            <span>
              <b style={{ color: "#cbd5e1" }}>{fmt(data.predicted_12m_ltv_eur)}</b> back per customer
            </span>
          </div>
        )}
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="💶"
        title="Money in vs money back"
        subtitle={`Last ${data.window_days} days`}
      >
        <DrawerExplainer
          body={
            "This is the most important number for a growing store. It answers one question: " +
            `for every ${sym}1 you spend to bring in a new customer, how much do you get back from them ` +
            `over the next 12 months? If it's less than ${sym}1, you're bleeding cash. ` +
            `If it's more than ${sym}3, you're in a healthy spot and can press the accelerator on ads.`
          }
          why={
            "Almost every store that goes bankrupt had this number below 1 for months without knowing. " +
            "We calculate it from your real Shopify orders + the ad spend you enter in Settings."
          }
        />

        {hasData ? (
          <>
            <DrawerBigStat
              label={`For every ${sym}1 spent on ads`}
              value={`${sym}${data.ratio.toFixed(2)}`}
              sublabel="comes back over the next 12 months"
              color={meta.color}
            />

            <DrawerKeyValueList
              items={[
                {
                  label: "Ad spend (last 30d)",
                  value: fmt(data.total_ad_spend_eur),
                },
                {
                  label: "New customers acquired",
                  value: String(data.customers_acquired),
                },
                {
                  label: "Cost per new customer",
                  value: fmt(data.cac_eur),
                },
                {
                  label: "Average customer value so far",
                  value: fmt(data.avg_ltv_eur),
                },
                {
                  label: "Projected 12-month value",
                  value: fmt(data.predicted_12m_ltv_eur),
                  color: "#10b981",
                },
              ]}
            />

            <DrawerSectionHeading>How to read the number</DrawerSectionHeading>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "10px",
                fontSize: "13px",
                color: "#cbd5e1",
              }}
            >
              <div
                style={{
                  padding: "12px 14px",
                  borderRadius: "10px",
                  background: "rgba(16,185,129,0.08)",
                  border: "1px solid rgba(16,185,129,0.25)",
                }}
              >
                <b style={{ color: "#10b981" }}>3× or more — Healthy</b>. You can safely spend more on ads to grow faster. Every {sym}1 is working hard for you.
              </div>
              <div
                style={{
                  padding: "12px 14px",
                  borderRadius: "10px",
                  background: "rgba(232,160,78,0.08)",
                  border: "1px solid rgba(232,160,78,0.25)",
                }}
              >
                <b style={{ color: "#e8a04e" }}>1× to 3× — Tight</b>. You're breaking even but thin. Focus on getting customers to buy a second time.
              </div>
              <div
                style={{
                  padding: "12px 14px",
                  borderRadius: "10px",
                  background: "rgba(244,63,94,0.08)",
                  border: "1px solid rgba(244,63,94,0.25)",
                }}
              >
                <b style={{ color: "#f43f5e" }}>Below 1× — Losing money</b>. Every new customer costs more than you'll ever make back. Pause ads or raise prices immediately.
              </div>
            </div>
          </>
        ) : (
          <div
            style={{
              padding: "16px 18px",
              borderRadius: "10px",
              background: "rgba(15,23,42,0.6)",
              border: "1px solid rgba(148,163,184,0.15)",
              color: "#cbd5e1",
              fontSize: "14px",
              lineHeight: 1.6,
            }}
          >
            We need two things to compute this ratio:
            <ul style={{ marginTop: "10px", paddingLeft: "18px", lineHeight: 1.8 }}>
              <li>Your <b style={{ color: "#e8a04e" }}>monthly ad spend</b> (go to Settings → Costs and type it in — 30 seconds)</li>
              <li>At least <b style={{ color: "#e8a04e" }}>one new customer</b> in the last {data.window_days} days (you already have {data.customers_acquired})</li>
            </ul>
          </div>
        )}
      </DetailDrawer>
    </>
  );
}
