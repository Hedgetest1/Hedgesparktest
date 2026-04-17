"use client";

/**
 * MtaCompareCard — Multi-Touch Attribution side-by-side.
 *
 * Shows every revenue source across all 5 attribution models
 * (first-touch / last-touch / linear / time-decay / position-based)
 * in a single matrix, so merchants can see how dramatically their
 * ROAS numbers change based on the model — and pick the one that
 * reflects reality.
 *
 * Data: GET /pro/mta/compare?window_days=30
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
  DrawerBarChart,
} from "./DetailDrawer";
import { VisitorJourneyTimeline } from "./VisitorJourneyTimeline";

type MatrixRow = {
  source: string;
  first_touch: number;
  last_touch: number;
  linear: number;
  time_decay: number;
  position_based: number;
  max: number;
  min: number;
  swing_pct: number;
};

type CompareData = {
  shop_domain: string;
  window_days: number;
  matrix: MatrixRow[];
  total_revenue_eur: number;
  total_orders: number;
  headline: string | null;
  generated_at: string;
};

const MODELS: { key: keyof MatrixRow; label: string; color: string; plain: string }[] = [
  { key: "first_touch", label: "Discovery", color: "#8b5cf6", plain: "First thing they saw" },
  { key: "last_touch", label: "Closer", color: "#06b6d4", plain: "Last thing before buying" },
  { key: "linear", label: "Equal", color: "#10b981", plain: "Everything equal" },
  { key: "time_decay", label: "Recent", color: "#e8a04e", plain: "Recent weighs more" },
  { key: "position_based", label: "Balanced", color: "#f43f5e", plain: "Discovery + closer + middle" },
];

const SOURCE_ICONS: Record<string, string> = {
  direct: "🔗",
  google: "🔍",
  meta: "📘",
  facebook: "📘",
  instagram: "📸",
  tiktok: "🎵",
  email: "📧",
  klaviyo: "📧",
  newsletter: "📧",
  organic: "🌱",
  referral: "🤝",
};

// MTA revenue figures are EUR-normalized by the backend
// (order_ingestion._normalize_to_eur) — all SUM(*_eur) columns are
// safe to render in EUR. Routing through the shared helper means
// the symbol table lives in one place (_lib/formatters.ts).
const fmt = (n: number) => formatMoneyCompact(n, "EUR");

export function MtaCompareCard({ apiBase, isProUser }: { apiBase: string; isProUser: boolean }) {
  const [data, setData] = useState<CompareData | null>(null);
  const [loading, setLoading] = useState(true);
  const [windowDays, setWindowDays] = useState(30);
  const [drawerSource, setDrawerSource] = useState<string | null>(null);
  const [drawerExplainerOpen, setDrawerExplainerOpen] = useState(false);

  useEffect(() => {
    if (!isProUser) {
      setLoading(false);
      return;
    }
    setLoading(true);
    apiClient
      .GET("/pro/mta/compare", { params: { query: { window_days: windowDays } } })
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      .then(({ data: j, error: err }) => { if (!err && j) setData(j as any); })
      .finally(() => setLoading(false));
  }, [apiBase, isProUser, windowDays]);

  if (!isProUser || loading || !data) return null;
  if (data.total_orders === 0) return null;

  const topRows = data.matrix.slice(0, 8);
  const activeRow = drawerSource ? data.matrix.find((r) => r.source === drawerSource) : null;

  return (
    <>
    <div
      style={{
        marginBottom: "24px",
        padding: "24px 28px",
        borderRadius: "18px",
        background: "linear-gradient(135deg, #0b1220 0%, #141d33 100%)",
        border: "1px solid rgba(139,92,246,0.25)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "8px", flexWrap: "wrap" }}>
        <div
          onClick={() => setDrawerExplainerOpen(true)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "12px",
            flex: 1,
            cursor: "pointer",
          }}
        >
          <div style={{ fontSize: "22px" }}>🧭</div>
          <div>
            <h3
              style={{
                color: "#a78bfa",
                fontSize: "18px",
                fontWeight: 800,
                margin: 0,
                letterSpacing: "-0.01em",
              }}
            >
              What brings the sale
            </h3>
            <div style={{ color: "#94a3b8", fontSize: "12px", marginTop: "2px" }}>
              {data.total_orders} orders · €{data.total_revenue_eur.toLocaleString("en")} · last {data.window_days} days · click for details
            </div>
          </div>
        </div>
        <div style={{ display: "flex", gap: "4px" }}>
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setWindowDays(d)}
              style={{
                padding: "4px 10px",
                borderRadius: "6px",
                fontSize: "11px",
                fontWeight: 700,
                background: d === windowDays ? "rgba(139,92,246,0.2)" : "transparent",
                border: `1px solid ${d === windowDays ? "#a78bfa" : "rgba(148,163,184,0.2)"}`,
                color: d === windowDays ? "#a78bfa" : "#94a3b8",
                cursor: "pointer",
              }}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {data.headline && (
        <div
          style={{
            padding: "10px 14px",
            borderRadius: "10px",
            background: "rgba(232,160,78,0.08)",
            border: "1px solid rgba(232,160,78,0.25)",
            color: "#fcd34d",
            fontSize: "13px",
            fontWeight: 600,
            marginTop: "10px",
            marginBottom: "14px",
            lineHeight: 1.5,
          }}
        >
          💡 {data.headline}
        </div>
      )}

      {/* Matrix table */}
      <div style={{ overflowX: "auto", marginTop: "12px" }}>
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: "13px",
            color: "#cbd5e1",
          }}
        >
          <thead>
            <tr style={{ textAlign: "left", color: "#64748b", fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em" }}>
              <th style={{ padding: "8px 10px" }}>Source</th>
              {MODELS.map((m) => (
                <th key={m.key} style={{ padding: "8px 10px", textAlign: "right", color: m.color }}>
                  {m.label}
                </th>
              ))}
              <th style={{ padding: "8px 10px", textAlign: "right" }}>Swing</th>
            </tr>
          </thead>
          <tbody>
            {topRows.map((row) => (
              <tr
                key={row.source}
                onClick={() => setDrawerSource(row.source)}
                style={{
                  borderTop: "1px solid rgba(148,163,184,0.08)",
                  cursor: "pointer",
                  transition: "background 0.15s ease",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "rgba(139,92,246,0.06)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                }}
              >
                <td style={{ padding: "10px 10px", fontWeight: 600 }}>
                  <span style={{ marginRight: "6px" }}>{SOURCE_ICONS[row.source] || "•"}</span>
                  {row.source}
                </td>
                {MODELS.map((m) => {
                  const v = row[m.key] as number;
                  const isMax = v === row.max && row.max > 0;
                  return (
                    <td
                      key={m.key}
                      style={{
                        padding: "10px 10px",
                        textAlign: "right",
                        fontVariantNumeric: "tabular-nums",
                        color: isMax ? m.color : "#cbd5e1",
                        fontWeight: isMax ? 700 : 500,
                      }}
                    >
                      {fmt(v)}
                    </td>
                  );
                })}
                <td
                  style={{
                    padding: "10px 10px",
                    textAlign: "right",
                    color: row.swing_pct > 50 ? "#f43f5e" : row.swing_pct > 25 ? "#e8a04e" : "#64748b",
                    fontSize: "12px",
                    fontWeight: 700,
                  }}
                >
                  {row.swing_pct.toFixed(0)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div
        style={{
          marginTop: "12px",
          paddingTop: "12px",
          borderTop: "1px solid rgba(148,163,184,0.08)",
          fontSize: "11px",
          color: "#64748b",
          fontStyle: "italic",
        }}
      >
        <b style={{ color: "#94a3b8" }}>Swing</b> = how much credit a source gets under the most vs least generous rule.
        A 70%+ swing means you might be sending money to the wrong channel. Click any row to see why.
      </div>
    </div>

    {/* Explainer drawer — header click */}
    <DetailDrawer
      open={drawerExplainerOpen}
      onClose={() => setDrawerExplainerOpen(false)}
      icon="🧭"
      title="What brings the sale"
      subtitle="5 different ways of answering the same question"
    >
      <DrawerExplainer
        body={
          "When someone buys from you, multiple things usually played a role. They saw your Meta ad on Tuesday, " +
          "clicked an email on Friday, then searched Google on Sunday and bought. Which one gets credit? " +
          "There's no single right answer — so HedgeSpark shows you ALL five common ways of splitting the credit, " +
          "side by side, for every source. You pick the one that matches how you think about your marketing."
        }
        why={
          "If you decide Meta is worth the money using 'Last thing before buying', you'll probably cut the budget. " +
          "But under 'First thing they saw', Meta is often the star — because it started the journey. " +
          "Seeing both numbers protects you from cutting a channel that's secretly doing all the heavy lifting."
        }
      />

      <DrawerSectionHeading>The 5 ways, in plain words</DrawerSectionHeading>
      <DrawerKeyValueList
        items={MODELS.map((m) => ({
          label: m.label,
          value: m.plain,
          color: m.color,
        }))}
      />

      <DrawerSectionHeading>How paths look in your store</DrawerSectionHeading>
      <div
        style={{
          padding: "14px 16px",
          borderRadius: "10px",
          background: "rgba(15,23,42,0.5)",
          border: "1px solid rgba(148,163,184,0.1)",
          fontSize: "13px",
          color: "#cbd5e1",
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
          lineHeight: 1.9,
        }}
      >
        {data.matrix.length > 0 ? (
          <div>Sample journeys extracted from your real orders:</div>
        ) : (
          <div style={{ color: "#64748b" }}>No journeys yet — come back after a few orders.</div>
        )}
      </div>
    </DetailDrawer>

    {/* Source drill-down drawer — row click */}
    <DetailDrawer
      open={drawerSource !== null}
      onClose={() => setDrawerSource(null)}
      icon={drawerSource ? SOURCE_ICONS[drawerSource] || "•" : "🧭"}
      title={drawerSource ? `Source: ${drawerSource}` : ""}
      subtitle={
        activeRow
          ? `${activeRow.swing_pct.toFixed(0)}% swing between the best and worst way of measuring it`
          : ""
      }
    >
      {activeRow && (
        <>
          <DrawerExplainer
            title={`What ${drawerSource} is really worth`}
            body={
              `The table below shows exactly how much revenue ${drawerSource} gets credited under each ` +
              "of the five measurement rules. The bigger the gap, the more your decision depends on which " +
              "rule you pick. Pay the most attention to the rule that matches how YOU think about the " +
              "sale: is this source a discovery channel or a closer?"
            }
            why={
              activeRow.swing_pct > 50
                ? "This source has a wide swing — don't trust a single number, use the comparison."
                : "This source is stable across rules, which means your view of its value is solid."
            }
          />

          <DrawerBigStat
            label="Biggest number across all rules"
            value={fmt(activeRow.max)}
            sublabel={`Smallest: ${fmt(activeRow.min)}`}
            color="#a78bfa"
          />

          <DrawerSectionHeading>Credit under each rule</DrawerSectionHeading>
          <DrawerBarChart
            points={MODELS.map((m) => ({
              label: m.label,
              subLabel: m.plain,
              value: Math.round(activeRow[m.key] as number),
            }))}
            color="#a78bfa"
            unit="€"
          />

          <DrawerSectionHeading>What this means</DrawerSectionHeading>
          <div
            style={{
              padding: "14px 16px",
              borderRadius: "10px",
              background: "rgba(15,23,42,0.5)",
              border: "1px solid rgba(148,163,184,0.1)",
              color: "#cbd5e1",
              fontSize: "13px",
              lineHeight: 1.6,
              marginBottom: "8px",
            }}
          >
            {activeRow.swing_pct > 50 ? (
              <>
                <b style={{ color: "#f43f5e" }}>High disagreement ({activeRow.swing_pct.toFixed(0)}%).</b>{" "}
                This source looks very different depending on how you measure it — which usually means it's
                playing a <b>supporting role</b>, not a closing one. If you're using only "last thing before buying"
                for your ROAS, you're probably underpaying {drawerSource}.
              </>
            ) : (
              <>
                <b style={{ color: "#10b981" }}>Stable measurement.</b> {drawerSource} gets similar credit no
                matter which rule you apply, which means its real value is solid. You can trust the numbers
                from this source when you allocate budget.
              </>
            )}
          </div>

          {/* π6 KILLER: real customer journey playback */}
          <DrawerSectionHeading>Real customer journeys through {drawerSource}</DrawerSectionHeading>
          <VisitorJourneyTimeline apiBase={apiBase} source={drawerSource!} />
        </>
      )}
    </DetailDrawer>
    </>
  );
}
