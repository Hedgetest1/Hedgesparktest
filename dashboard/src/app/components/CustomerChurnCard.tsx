"use client";

/**
 * CustomerChurnCard (δ4-UI) — Top at-risk customers with factor breakdown.
 *
 * Data: GET /pro/customer-churn?limit=50
 *
 * Shows:
 *   - risk band distribution (critical/high/medium/low)
 *   - top N most-at-risk customers in a table
 *   - click a row → drawer with the factors that pushed this customer's score up
 *
 * Copy: "Customers about to go silent" — idiot-proof. No "churn probability" jargon.
 *
 * Email is GDPR-hashed ("C-ABCD1234") — merchant sees handle, not email.
 */

import { useCallback, useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
} from "./DetailDrawer";

type Factors = {
  recency_days: number;
  frequency_90d: number;
  avg_order_value_eur: number;
  decay_ratio: number;
  tenure_days: number;
  z_score: number;
};

type Customer = {
  customer_email_hash: string;
  churn_probability: number;
  churn_score_100: number;
  risk_band: "critical" | "high" | "medium" | "low";
  factors: Factors;
  total_orders: number;
};

type Response = {
  shop_domain: string;
  total_customers_scored: number;
  by_risk_band: Record<string, number>;
  customers: Customer[];
  // Shop's native currency (USD/EUR/GBP/…) — `avg_order_value_eur`
  // is denominated in this currency.
  currency?: string;
};

const BAND_META: Record<
  string,
  { label: string; color: string; bg: string; note: string }
> = {
  critical: {
    label: "Critical",
    color: "#f43f5e",
    bg: "rgba(244,63,94,0.12)",
    note: "Almost certainly gone unless you act",
  },
  high: {
    label: "High",
    color: "#e8a04e",
    bg: "rgba(232,160,78,0.12)",
    note: "Drifting away — worth a win-back",
  },
  medium: {
    label: "Medium",
    color: "#a78bfa",
    bg: "rgba(167,139,250,0.12)",
    note: "Watch closely",
  },
  low: {
    label: "Low",
    color: "#10b981",
    bg: "rgba(16,185,129,0.12)",
    note: "Healthy",
  },
};

const fmtDays = (d: number): string => {
  if (d < 1) return "today";
  if (d < 2) return "1 day";
  if (d < 30) return `${Math.round(d)} days`;
  if (d < 60) return `${Math.round(d / 7)} weeks`;
  return `${Math.round(d / 30)} months`;
};

const fmtEur = (n: number, currency?: string): string =>
  formatMoneyCompact(n, currency || "USD");

export function CustomerChurnCard({ apiBase, isProUser }: { apiBase: string; isProUser: boolean }) {
  const [data, setData] = useState<Response | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Customer | null>(null);

  const load = useCallback(async () => {
    try {
      const { data: j, error: err } = await apiClient.GET(
        "/pro/customer-churn",
        { params: { query: { limit: 50 } } },
      );
      if (err || !j) return;
      setData(j as unknown as Response);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isProUser) {
      setLoading(false);
      return;
    }
    load();
  }, [isProUser, load]);

  if (!isProUser || loading || !data) return null;
  if (data.total_customers_scored === 0) return null;

  const topCustomers = data.customers.slice(0, 8);
  const critCount = data.by_risk_band.critical || 0;
  const highCount = data.by_risk_band.high || 0;
  const medCount = data.by_risk_band.medium || 0;
  const lowCount = data.by_risk_band.low || 0;

  return (
    <>
      <div
        style={{
          marginBottom: "24px",
          padding: "24px 28px",
          borderRadius: "18px",
          background: "linear-gradient(135deg, #0b1220 0%, #141d33 100%)",
          border: "1px solid rgba(244,63,94,0.25)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "4px" }}>
          <span style={{ fontSize: "22px" }}>👥</span>
          <div style={{ flex: 1 }}>
            <h3
              style={{
                color: "#fca5a5",
                fontSize: "18px",
                fontWeight: 800,
                margin: 0,
                letterSpacing: "-0.01em",
              }}
            >
              Customers about to go silent
            </h3>
            <div style={{ color: "#94a3b8", fontSize: "12px", marginTop: "2px" }}>
              {data.total_customers_scored} customers scored · click any row to see why
            </div>
          </div>
        </div>

        {/* Risk band totals */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: "8px",
            marginTop: "16px",
            marginBottom: "16px",
          }}
        >
          {[
            { band: "critical", count: critCount },
            { band: "high", count: highCount },
            { band: "medium", count: medCount },
            { band: "low", count: lowCount },
          ].map(({ band, count }) => {
            const meta = BAND_META[band];
            return (
              <div
                key={band}
                style={{
                  padding: "10px 12px",
                  borderRadius: "10px",
                  background: meta.bg,
                  border: `1px solid ${meta.color}33`,
                }}
              >
                <div
                  style={{
                    color: meta.color,
                    fontSize: "10px",
                    fontWeight: 700,
                    textTransform: "uppercase",
                    letterSpacing: "0.06em",
                  }}
                >
                  {meta.label}
                </div>
                <div
                  style={{
                    color: meta.color,
                    fontSize: "24px",
                    fontWeight: 800,
                    fontVariantNumeric: "tabular-nums",
                    lineHeight: 1.1,
                  }}
                >
                  {count}
                </div>
              </div>
            );
          })}
        </div>

        {/* Top customers list */}
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          {topCustomers.map((c) => {
            const meta = BAND_META[c.risk_band];
            return (
              <div
                key={c.customer_email_hash}
                onClick={() => setSelected(c)}
                style={{
                  display: "grid",
                  gridTemplateColumns: "auto 1fr auto auto",
                  alignItems: "center",
                  gap: "14px",
                  padding: "10px 14px",
                  background: "rgba(15,23,42,0.5)",
                  borderRadius: "10px",
                  border: "1px solid rgba(148,163,184,0.08)",
                  cursor: "pointer",
                  transition: "background 0.15s ease",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "rgba(244,63,94,0.06)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "rgba(15,23,42,0.5)";
                }}
              >
                {/* Score circle */}
                <div
                  style={{
                    width: "40px",
                    height: "40px",
                    borderRadius: "50%",
                    background: `${meta.color}20`,
                    border: `2px solid ${meta.color}`,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: meta.color,
                    fontSize: "13px",
                    fontWeight: 800,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {c.churn_score_100}
                </div>

                {/* Handle + context */}
                <div>
                  <div style={{ color: "#e2e8f0", fontSize: "13px", fontWeight: 600 }}>
                    {c.customer_email_hash}
                  </div>
                  <div style={{ color: "#94a3b8", fontSize: "11px", marginTop: "2px" }}>
                    {c.total_orders} total orders · last seen {fmtDays(c.factors.recency_days)} ago
                  </div>
                </div>

                {/* AOV */}
                <div
                  style={{
                    color: "#cbd5e1",
                    fontSize: "12px",
                    fontVariantNumeric: "tabular-nums",
                    textAlign: "right",
                  }}
                >
                  <div style={{ color: "#94a3b8", fontSize: "10px" }}>Avg order</div>
                  <div style={{ fontWeight: 600 }}>{fmtEur(c.factors.avg_order_value_eur, data?.currency)}</div>
                </div>

                {/* Band badge */}
                <span
                  style={{
                    padding: "4px 10px",
                    borderRadius: "6px",
                    background: meta.bg,
                    color: meta.color,
                    fontSize: "10px",
                    fontWeight: 700,
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                  }}
                >
                  {meta.label}
                </span>
              </div>
            );
          })}
        </div>

        <div
          style={{
            marginTop: "14px",
            paddingTop: "12px",
            borderTop: "1px solid rgba(148,163,184,0.08)",
            fontSize: "11px",
            color: "#94a3b8",
            fontStyle: "italic",
          }}
        >
          Scores update every few minutes. Emails are hashed for privacy. Use the rule builder to
          auto-trigger a Klaviyo flow when a customer crosses into Critical.
        </div>
      </div>

      {/* Drawer */}
      {selected && (
        <DetailDrawer
          open={selected !== null}
          onClose={() => setSelected(null)}
          icon="👤"
          title={`Customer ${selected.customer_email_hash}`}
          subtitle={`${BAND_META[selected.risk_band].label} risk — ${BAND_META[selected.risk_band].note}`}
        >
          <DrawerExplainer
            title="Why this customer is scored"
            body={
              "We look at five things: how long since their last order, how often they order, " +
              "how much they spend, whether their activity is fading compared to earlier, and " +
              "how long they've been a customer. Each factor pushes the score up or down."
            }
            why={
              selected.risk_band === "critical" || selected.risk_band === "high"
                ? "Act fast: a win-back email or a small discount now is much cheaper than acquiring a new customer. Most replenishment stores lose these within two more weeks."
                : "This customer is in okay shape. Keep them warm with normal marketing."
            }
          />

          <DrawerBigStat
            label="Risk score"
            value={`${selected.churn_score_100}/100`}
            sublabel={BAND_META[selected.risk_band].note}
            color={BAND_META[selected.risk_band].color}
          />

          <DrawerKeyValueList
            items={[
              {
                label: "Last order",
                value: `${fmtDays(selected.factors.recency_days)} ago`,
                color:
                  selected.factors.recency_days > 45
                    ? "#f43f5e"
                    : selected.factors.recency_days > 21
                      ? "#e8a04e"
                      : "#10b981",
              },
              {
                label: "Orders in last 90 days",
                value: String(selected.factors.frequency_90d),
              },
              {
                label: "Total orders ever",
                value: String(selected.total_orders),
              },
              {
                label: "Average order value",
                value: fmtEur(selected.factors.avg_order_value_eur, data?.currency),
              },
              {
                label: "Activity trend",
                value:
                  selected.factors.decay_ratio >= 1
                    ? "Steady or growing"
                    : selected.factors.decay_ratio >= 0.5
                      ? "Slowing down"
                      : "Fading fast",
                color:
                  selected.factors.decay_ratio >= 1
                    ? "#10b981"
                    : selected.factors.decay_ratio >= 0.5
                      ? "#e8a04e"
                      : "#f43f5e",
              },
              {
                label: "Customer since",
                value: `${fmtDays(selected.factors.tenure_days)} ago`,
              },
            ]}
          />

          <DrawerSectionHeading>What to do</DrawerSectionHeading>
          <div
            style={{
              padding: "14px 16px",
              borderRadius: "10px",
              background: "rgba(15,23,42,0.6)",
              border: "1px solid rgba(148,163,184,0.15)",
              color: "#cbd5e1",
              fontSize: "13px",
              lineHeight: 1.6,
            }}
          >
            {selected.risk_band === "critical" || selected.risk_band === "high" ? (
              <>
                <b style={{ color: "#e8a04e" }}>Send a targeted win-back</b> within 48 hours.
                Highlight a product similar to their previous purchase. Offer something small
                (free shipping, a bundle, a loyalty nudge) — not a deep discount.
                <br />
                <br />
                Even better: use the <b style={{ color: "#e8a04e" }}>rule builder</b> to create
                "When customer churn score &gt; 75 → send Klaviyo event" and let the flow run
                automatically.
              </>
            ) : (
              <>
                This customer is healthy. Keep normal marketing cadence. Watch for a sudden jump
                in score — that's the moment to act.
              </>
            )}
          </div>
        </DetailDrawer>
      )}
    </>
  );
}
