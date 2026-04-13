"use client";

/**
 * NudgeDnaCard (δ5-UI) — Winning nudge copy patterns.
 *
 * Data: GET /pro/nudge-dna
 *
 * Shows:
 *   - Top winning patterns (feature + lift % + sample size)
 *   - Top 3 variants ranked by real conversion rate
 *   - Lessons for the composer (what to write more of)
 *
 * Copy idiot-proof: no "feature lift", no "uplift", just "these words work better".
 * Click a pattern → drawer with sample size and plain-language explanation.
 */

import { useCallback, useEffect, useState } from "react";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerKeyValueList,
  DrawerSectionHeading,
} from "./DetailDrawer";

type Feature = {
  feature: string;
  with_true_rate: number;
  with_false_rate: number;
  lift_pct: number;
  sample_with: number;
  sample_without: number;
  significance: "high" | "medium" | "low";
};

type Variant = {
  variant_key: string;
  copy_text: string;
  conversion_rate: number;
  impressions: number;
  conversions: number;
};

type DnaData = {
  shop_domain: string;
  window_days: number;
  total_impressions: number;
  total_conversions: number;
  overall_conversion_rate: number;
  features: Feature[];
  top_variants: Variant[];
  lessons_for_composer: string[];
  status?: string;
};

const FEATURE_HUMAN: Record<string, { label: string; emoji: string; explain: string }> = {
  length_short: {
    label: "Short copy (under 40 chars)",
    emoji: "✂️",
    explain: "Your shoppers prefer punchy messages — the fewer words the better.",
  },
  length_medium: {
    label: "Medium-length copy",
    emoji: "📏",
    explain: "A couple of sentences outperforms both one-liners and long blocks for this store.",
  },
  length_long: {
    label: "Longer copy",
    emoji: "📜",
    explain: "Your shoppers engage with detailed messages — they read the full pitch.",
  },
  contains_digits: {
    label: "Including a specific number",
    emoji: "🔢",
    explain: "Specific numbers (like \"3 left\" or \"27 people\") feel real and urgent.",
  },
  contains_emoji: {
    label: "Leading with an emoji",
    emoji: "🔥",
    explain: "An emoji at the start catches the eye before the words do.",
  },
  contains_percent: {
    label: "Showing a % discount",
    emoji: "💯",
    explain: "A visible percentage off converts better than vague savings language.",
  },
  has_urgency_word: {
    label: "Urgency words (now, hurry, last)",
    emoji: "⏰",
    explain: "Words that imply scarcity in time push shoppers to decide.",
  },
  has_social_proof_word: {
    label: "Social proof words",
    emoji: "👥",
    explain: "Words like \"people\", \"others\", \"trending\" tell shoppers they're not alone.",
  },
  has_cta_word: {
    label: "Clear call-to-action",
    emoji: "➡️",
    explain: "Verbs like \"buy\", \"grab\", \"claim\" work better than passive language.",
  },
  starts_with_number: {
    label: "Starting with a number",
    emoji: "1️⃣",
    explain: "A number at the very start anchors attention and feels quantified.",
  },
  has_exclamation: {
    label: "Exclamation marks",
    emoji: "❗",
    explain: "A single exclamation can inject energy — but beware of overuse.",
  },
};

const SIG_COLOR = {
  high: "#10b981",
  medium: "#e8a04e",
  low: "#64748b",
};

export function NudgeDnaCard({ apiBase, isProUser }: { apiBase: string; isProUser: boolean }) {
  const [data, setData] = useState<DnaData | null>(null);
  const [loading, setLoading] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${apiBase}/pro/nudge-dna`, { credentials: "include" });
      if (!r.ok) return;
      setData(await r.json());
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    if (!isProUser) {
      setLoading(false);
      return;
    }
    load();
  }, [isProUser, load]);

  if (!isProUser || loading || !data) return null;
  if (data.status === "insufficient_data" || data.total_impressions === 0) return null;

  // Keep only features with positive lift and decent sample
  const winners = data.features
    .filter((f) => f.lift_pct > 0 && f.significance !== "low")
    .slice(0, 6);

  if (winners.length === 0) return null;

  return (
    <>
      <div
        onClick={() => setDrawerOpen(true)}
        style={{
          marginBottom: "24px",
          padding: "24px 28px",
          borderRadius: "18px",
          background: "linear-gradient(135deg, #0b1220 0%, #141d33 100%)",
          border: "1px solid rgba(167,139,250,0.25)",
          cursor: "pointer",
          transition: "transform 0.2s ease",
        }}
        onMouseEnter={(e) => (e.currentTarget.style.transform = "translateY(-2px)")}
        onMouseLeave={(e) => (e.currentTarget.style.transform = "translateY(0)")}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "4px" }}>
          <span style={{ fontSize: "22px" }}>🧬</span>
          <div style={{ flex: 1 }}>
            <h3
              style={{
                color: "#c4b5fd",
                fontSize: "18px",
                fontWeight: 800,
                margin: 0,
                letterSpacing: "-0.01em",
              }}
            >
              What kind of words actually sell
            </h3>
            <div style={{ color: "#94a3b8", fontSize: "12px", marginTop: "2px" }}>
              Patterns from {data.total_impressions.toLocaleString("en")} real nudge impressions · click for details
            </div>
          </div>
        </div>

        {/* Top 4 winning patterns */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: "10px",
            marginTop: "14px",
          }}
        >
          {winners.slice(0, 4).map((f) => {
            const meta = FEATURE_HUMAN[f.feature] || {
              label: f.feature,
              emoji: "•",
              explain: "",
            };
            return (
              <div
                key={f.feature}
                style={{
                  padding: "12px 14px",
                  background: "rgba(15,23,42,0.6)",
                  borderRadius: "10px",
                  border: "1px solid rgba(167,139,250,0.15)",
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                }}
              >
                <span style={{ fontSize: "22px" }}>{meta.emoji}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      color: "#e2e8f0",
                      fontSize: "12px",
                      fontWeight: 600,
                      lineHeight: 1.3,
                    }}
                  >
                    {meta.label}
                  </div>
                  <div
                    style={{
                      color: "#10b981",
                      fontSize: "14px",
                      fontWeight: 800,
                      marginTop: "2px",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    +{f.lift_pct.toFixed(0)}% wins
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {data.lessons_for_composer.length > 0 && (
          <div
            style={{
              marginTop: "14px",
              paddingTop: "14px",
              borderTop: "1px solid rgba(148,163,184,0.08)",
              fontSize: "12px",
              color: "#94a3b8",
            }}
          >
            💡 <b style={{ color: "#fcd34d" }}>HedgeSpark is already using these patterns</b> in
            every new nudge it generates for your store.
          </div>
        )}
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="🧬"
        title="What kind of words actually sell"
        subtitle={`Based on ${data.total_impressions.toLocaleString("en")} real impressions over ${data.window_days} days`}
      >
        <DrawerExplainer
          body={
            "HedgeSpark writes nudges in several different styles and shows them to real visitors. " +
            "Every time someone buys after seeing one, we remember which words were in it. After " +
            "enough views, we know exactly what kind of copy converts in YOUR store — not on some " +
            "benchmark average. This is what's winning right now."
          }
          why={
            "Nobody can tell you what works for your specific audience except your own data. " +
            "Most tools send the same templates to every store. HedgeSpark learns your voice."
          }
        />

        <DrawerSectionHeading>Pattern leaderboard</DrawerSectionHeading>
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          {winners.map((f, i) => {
            const meta = FEATURE_HUMAN[f.feature] || {
              label: f.feature,
              emoji: "•",
              explain: "",
            };
            return (
              <div
                key={f.feature}
                style={{
                  padding: "14px 16px",
                  borderRadius: "12px",
                  background: "rgba(15,23,42,0.6)",
                  border: "1px solid rgba(148,163,184,0.12)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "10px",
                    marginBottom: "8px",
                  }}
                >
                  <span style={{ fontSize: "20px" }}>{meta.emoji}</span>
                  <div
                    style={{
                      flex: 1,
                      color: "#e2e8f0",
                      fontSize: "14px",
                      fontWeight: 700,
                    }}
                  >
                    #{i + 1} · {meta.label}
                  </div>
                  <div
                    style={{
                      color: "#10b981",
                      fontSize: "16px",
                      fontWeight: 800,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    +{f.lift_pct.toFixed(0)}%
                  </div>
                </div>
                <div
                  style={{
                    color: "#cbd5e1",
                    fontSize: "12px",
                    lineHeight: 1.5,
                    marginBottom: "8px",
                  }}
                >
                  {meta.explain}
                </div>
                <div
                  style={{
                    display: "flex",
                    gap: "14px",
                    fontSize: "11px",
                    color: "#64748b",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  <span>
                    With this:{" "}
                    <b style={{ color: "#cbd5e1" }}>
                      {(f.with_true_rate * 100).toFixed(1)}% buy
                    </b>
                  </span>
                  <span>
                    Without:{" "}
                    <b style={{ color: "#cbd5e1" }}>
                      {(f.with_false_rate * 100).toFixed(1)}% buy
                    </b>
                  </span>
                  <span style={{ marginLeft: "auto", color: SIG_COLOR[f.significance] }}>
                    ● {f.significance} confidence
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        {data.top_variants.length > 0 && (
          <>
            <DrawerSectionHeading>Top winning copies</DrawerSectionHeading>
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              {data.top_variants.slice(0, 3).map((v, i) => (
                <div
                  key={v.variant_key}
                  style={{
                    padding: "12px 14px",
                    borderRadius: "10px",
                    background: "rgba(16,185,129,0.08)",
                    border: "1px solid rgba(16,185,129,0.2)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      marginBottom: "4px",
                    }}
                  >
                    <div
                      style={{
                        color: "#10b981",
                        fontSize: "10px",
                        fontWeight: 700,
                        textTransform: "uppercase",
                        letterSpacing: "0.06em",
                      }}
                    >
                      #{i + 1} winner · {(v.conversion_rate * 100).toFixed(1)}% buy rate
                    </div>
                    <div style={{ color: "#64748b", fontSize: "10px" }}>
                      {v.impressions.toLocaleString("en")} views · {v.conversions} sales
                    </div>
                  </div>
                  <div
                    style={{
                      color: "#e2e8f0",
                      fontSize: "13px",
                      lineHeight: 1.5,
                      fontStyle: "italic",
                    }}
                  >
                    &ldquo;{v.copy_text}&rdquo;
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        <DrawerSectionHeading>What HedgeSpark is doing about it</DrawerSectionHeading>
        <div
          style={{
            padding: "14px 16px",
            borderRadius: "10px",
            background: "rgba(232,160,78,0.08)",
            border: "1px solid rgba(232,160,78,0.25)",
            color: "#fcd34d",
            fontSize: "13px",
            lineHeight: 1.6,
          }}
        >
          Every new nudge HedgeSpark writes for you uses these winning patterns as a starting
          point. The system feeds the top winners back into the copy generator so the next
          variant you see is even more tuned to YOUR audience. It's self-improving.
        </div>

        <DrawerSectionHeading>Overall performance</DrawerSectionHeading>
        <DrawerKeyValueList
          items={[
            { label: "Total impressions", value: data.total_impressions.toLocaleString("en") },
            { label: "Total conversions", value: data.total_conversions.toLocaleString("en") },
            {
              label: "Overall buy rate",
              value: `${(data.overall_conversion_rate * 100).toFixed(2)}%`,
            },
            { label: "Window", value: `${data.window_days} days` },
          ]}
        />
      </DetailDrawer>
    </>
  );
}
