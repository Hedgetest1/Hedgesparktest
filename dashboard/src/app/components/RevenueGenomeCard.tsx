"use client";

/**
 * RevenueGenomeCard — "Your Revenue DNA"
 *
 * Shows the complete genetic profile of the merchant's revenue across
 * six gene clusters, each decomposed into individual genes with a score
 * out of 100. Summary view: score ring + archetype + cluster overview
 * + top priority actions. Drawer: full per-gene breakdown with insights.
 *
 * Data source: GET /pro/revenue-genome
 */

import { useState } from "react";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import {
  DetailDrawer,
  DrawerExplainer,
  DrawerBigStat,
  DrawerKeyValueList,
  DrawerSectionHeading,
  DrawerHowCalculated,
  DrawerNextAction,
} from "./DetailDrawer";

type Gene = {
  name: string;
  score: number;
  value: number | string;
  unit: string;
  status: string;
  insight: string;
  action: string;
};

type GeneCluster = {
  cluster: string;
  genes: Gene[];
  error?: string;
};

type PriorityAction = {
  gene: string;
  cluster: string;
  score: number;
  action: string;
};

type GenomeData = {
  overall_score: number;
  archetype: string;
  archetype_description: string;
  gene_clusters: Record<string, GeneCluster>;
  priority_actions: PriorityAction[];
  total_genes: number;
  strong_genes: number;
  weak_genes: number;
};

const CLUSTER_ICONS: Record<string, string> = {
  "Traffic DNA": "T",
  "Conversion DNA": "C",
  "Product DNA": "P",
  "Customer DNA": "U",
  "Intervention DNA": "I",
  "Risk DNA": "R",
};

const CLUSTER_COLORS: Record<string, string> = {
  "Traffic DNA": "#60a5fa",
  "Conversion DNA": "#f59e0b",
  "Product DNA": "#a78bfa",
  "Customer DNA": "#34d399",
  "Intervention DNA": "#f472b6",
  "Risk DNA": "#fb923c",
};

function scoreColor(score: number): string {
  if (score >= 70) return "#34d399";
  if (score >= 40) return "#fbbf24";
  return "#f87171";
}

function archetypeGradient(archetype: string): string {
  switch (archetype) {
    case "Revenue Machine":
      return "from-emerald-500/20 to-emerald-400/5";
    case "Growth Ready":
      return "from-violet-500/20 to-violet-400/5";
    case "Emerging":
      return "from-amber-500/20 to-amber-400/5";
    default:
      return "from-slate-500/20 to-slate-400/5";
  }
}

function clusterAvg(cluster: GeneCluster): number {
  if (!cluster.genes.length) return 0;
  return Math.round(cluster.genes.reduce((s, g) => s + g.score, 0) / cluster.genes.length);
}

export function RevenueGenomeCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data, state, retry } = useCardFetch<GenomeData>({
    url: `${apiBase}/pro/revenue-genome`,
    enabled: !!apiBase && !!shop && isProUser,
    isEmpty: (d) => !d.gene_clusters || Object.keys(d.gene_clusters).length === 0,
  });

  if (!isProUser) return null;

  if (state === "loading") {
    return <CardSkeleton label="Loading your revenue genome" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Revenue genome unavailable"
        message="We couldn't sequence your revenue genome right now. Your underlying metrics are safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="violet"
        title="Sequencing your revenue genome"
        body="The genome compares your store against six dimensions — traffic, conversion, product mix, customer behavior, interventions, and risk — and scores each one out of 100. We need enough visitor and order data across the board before the first reading is reliable."
        eta="Needs ~2 weeks of traffic and orders"
      />
    );
  }

  const clusters = Object.values(data.gene_clusters);
  const sc = scoreColor(data.overall_score);
  const moderateCount = data.total_genes - data.strong_genes - data.weak_genes;
  const topAction = data.priority_actions[0];

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label={`Open revenue genome details — overall score ${data.overall_score} out of 100, ${data.archetype}`}
        onClick={() => setDrawerOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setDrawerOpen(true);
          }
        }}
        className="group cursor-pointer rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6 transition-shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-[#e8a04e] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220] hover:border-white/[0.12]"
      >
        <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-[#e8a04e]">
          Revenue genome
        </div>
        <h3 className="text-[28px] font-extrabold leading-tight tracking-tight text-[#e8a04e]">
          Your store, sequenced
        </h3>
        <p className="mt-2 text-[14px] leading-relaxed text-slate-400">
          Six clusters, scored out of 100. A fast read on what your store is good at and what
          it&apos;s leaking.
        </p>

        {/* Overall score + archetype */}
        <div
          className={`mt-5 flex items-center gap-5 rounded-xl border border-white/[0.08] bg-gradient-to-r ${archetypeGradient(
            data.archetype,
          )} p-5`}
        >
          <div className="relative flex-shrink-0" aria-hidden="true">
            <svg width="96" height="96" viewBox="0 0 96 96">
              <circle cx="48" cy="48" r="40" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="7" />
              <circle
                cx="48"
                cy="48"
                r="40"
                fill="none"
                stroke={sc}
                strokeWidth="7"
                strokeLinecap="round"
                strokeDasharray={`${(data.overall_score * 251) / 100} 251`}
                transform="rotate(-90 48 48)"
                className="transition-all duration-1000"
              />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-[28px] font-extrabold tabular-nums" style={{ color: sc }}>
                {data.overall_score}
              </span>
              <span className="text-[9px] font-bold uppercase tracking-wider text-slate-500">/ 100</span>
            </div>
          </div>

          <div className="min-w-0">
            <div className="text-[18px] font-extrabold text-white">{data.archetype}</div>
            <p className="mt-1 text-[13px] leading-relaxed text-slate-400">{data.archetype_description}</p>
            <div className="mt-2 flex gap-4 text-[11px] font-semibold tabular-nums">
              <span className="text-emerald-400">{data.strong_genes} strong</span>
              <span className="text-slate-500">{moderateCount} moderate</span>
              <span className="text-rose-400">{data.weak_genes} weak</span>
            </div>
          </div>
        </div>

        {/* Gene clusters — summary view */}
        <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-3">
          {clusters.map((cluster) => {
            const color = CLUSTER_COLORS[cluster.cluster] || "#94a3b8";
            const icon = CLUSTER_ICONS[cluster.cluster] || "?";
            const avgScore = clusterAvg(cluster);
            return (
              <div
                key={cluster.cluster}
                className="rounded-xl border border-white/[0.05] bg-white/[0.015] p-4"
              >
                <div className="flex items-center gap-2.5">
                  <div
                    className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg text-[14px] font-extrabold"
                    style={{ background: color + "22", color }}
                    aria-hidden="true"
                  >
                    {icon}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[12px] font-bold text-slate-200">
                      {cluster.cluster}
                    </div>
                    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                      <div
                        className="h-full rounded-full transition-all duration-700"
                        style={{ width: `${avgScore}%`, background: scoreColor(avgScore) }}
                      />
                    </div>
                  </div>
                  <span
                    className="text-[16px] font-extrabold tabular-nums"
                    style={{ color: scoreColor(avgScore) }}
                  >
                    {avgScore}
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Top priority action — single highlight */}
        {topAction && (
          <div className="mt-5 rounded-xl border border-amber-400/20 bg-amber-500/[0.05] px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-bold uppercase tracking-wider text-amber-400">
                Top priority
              </span>
              <span className="text-[11px] text-slate-400">·</span>
              <span className="text-[11px] font-semibold text-slate-300">{topAction.cluster}</span>
              <span className="ml-auto text-[12px] font-extrabold tabular-nums text-rose-400">
                {topAction.score}/100
              </span>
            </div>
            <p className="mt-1.5 text-[13px] font-medium leading-relaxed text-amber-200/90">
              {topAction.action}
            </p>
          </div>
        )}

        <div className="mt-4 text-[11px] font-semibold text-slate-400">
          Click for the full gene-by-gene breakdown and all priority actions →
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        icon="🧬"
        title="Your revenue genome"
        subtitle={`${data.archetype} · ${data.overall_score}/100`}
        widthPx={640}
      >
        <DrawerExplainer
          body={
            "The genome takes everything we know about your store — traffic, conversion, product mix, " +
            "customers, interventions, risk — and grades each dimension against a healthy baseline. " +
            "Every gene gets a score from 0 to 100. Every cluster is an average of its genes. The " +
            "overall score is what your store looks like when you step back and squint."
          }
          why={
            "A single KPI tells you how the week went. The genome tells you where the structural " +
            "strengths and weaknesses are — the things that drive every week, not just this one. " +
            "Fixing a weak gene usually pays off for months; fixing a weak week usually doesn't."
          }
        />

        <DrawerBigStat
          label="Overall genome score"
          value={`${data.overall_score}`}
          sublabel={`${data.archetype} · ${data.strong_genes} strong · ${moderateCount} moderate · ${data.weak_genes} weak genes`}
          color={sc}
        />

        <DrawerKeyValueList
          items={[
            {
              label: "Archetype",
              value: data.archetype,
              color: sc,
            },
            {
              label: "Total genes",
              value: `${data.total_genes}`,
            },
            {
              label: "Strong (≥ 70)",
              value: `${data.strong_genes}`,
              color: "#10b981",
            },
            {
              label: "Moderate (40–69)",
              value: `${moderateCount}`,
              color: "#fbbf24",
            },
            {
              label: "Weak (< 40)",
              value: `${data.weak_genes}`,
              color: "#f43f5e",
            },
            {
              label: "Clusters",
              value: `${clusters.length}`,
            },
          ]}
        />

        <DrawerSectionHeading>Gene-by-gene breakdown</DrawerSectionHeading>
        <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
          {clusters.map((cluster) => {
            const color = CLUSTER_COLORS[cluster.cluster] || "#94a3b8";
            const avgScore = clusterAvg(cluster);
            return (
              <div
                key={cluster.cluster}
                style={{
                  padding: "14px 16px",
                  borderRadius: "12px",
                  background: "rgba(15,23,42,0.55)",
                  border: "1px solid rgba(148,163,184,0.12)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "10px",
                    marginBottom: "10px",
                  }}
                >
                  <div
                    style={{
                      width: "28px",
                      height: "28px",
                      borderRadius: "8px",
                      background: color + "22",
                      color,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: "13px",
                      fontWeight: 800,
                      flexShrink: 0,
                    }}
                  >
                    {CLUSTER_ICONS[cluster.cluster] || "?"}
                  </div>
                  <div
                    style={{
                      color: "#e2e8f0",
                      fontWeight: 700,
                      fontSize: "14px",
                      flex: 1,
                    }}
                  >
                    {cluster.cluster}
                  </div>
                  <div
                    style={{
                      color: scoreColor(avgScore),
                      fontWeight: 800,
                      fontSize: "18px",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {avgScore}
                  </div>
                </div>
                {cluster.error ? (
                  <div
                    style={{
                      color: "#fda4af",
                      fontSize: "12px",
                      fontStyle: "italic",
                    }}
                  >
                    {cluster.error}
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                    {cluster.genes.map((gene) => (
                      <div
                        key={gene.name}
                        style={{
                          padding: "8px 10px",
                          borderRadius: "8px",
                          background: "rgba(15,23,42,0.4)",
                          border: "1px solid rgba(148,163,184,0.08)",
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
                          <span style={{ color: "#cbd5e1", fontSize: "12px", fontWeight: 600 }}>
                            {gene.name}
                          </span>
                          <span
                            style={{
                              color: scoreColor(gene.score),
                              fontSize: "12px",
                              fontWeight: 800,
                              fontVariantNumeric: "tabular-nums",
                            }}
                          >
                            {gene.score}
                          </span>
                        </div>
                        <p
                          style={{
                            color: "#94a3b8",
                            fontSize: "11px",
                            lineHeight: 1.55,
                            margin: 0,
                          }}
                        >
                          {gene.insight}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {data.priority_actions.length > 0 && (
          <>
            <DrawerSectionHeading>All priority actions</DrawerSectionHeading>
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              {data.priority_actions.map((a, i) => (
                <div
                  key={i}
                  style={{
                    padding: "11px 14px",
                    borderRadius: "10px",
                    background: "rgba(245,158,11,0.05)",
                    border: "1px solid rgba(245,158,11,0.2)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      gap: "8px",
                      alignItems: "center",
                      marginBottom: "4px",
                    }}
                  >
                    <span
                      style={{
                        color: "#fbbf24",
                        fontSize: "10px",
                        fontWeight: 700,
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                      }}
                    >
                      {i + 1}. {a.cluster}
                    </span>
                    <span style={{ color: "#64748b", fontSize: "11px" }}>·</span>
                    <span style={{ color: "#cbd5e1", fontSize: "11px" }}>{a.gene}</span>
                    <span
                      style={{
                        marginLeft: "auto",
                        color: "#fb7185",
                        fontWeight: 800,
                        fontSize: "12px",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {a.score}/100
                    </span>
                  </div>
                  <p
                    style={{
                      color: "#fde68a",
                      fontSize: "12px",
                      lineHeight: 1.55,
                      margin: 0,
                    }}
                  >
                    {a.action}
                  </p>
                </div>
              ))}
            </div>
          </>
        )}

        <DrawerHowCalculated
          formula="Each gene is a single KPI compared to a healthy-baseline range. Score = clamp(0, 100, (actual − floor) ÷ (target − floor) × 100). Cluster score is the mean of its genes. Overall score is the weighted mean of the six clusters. Strong genes score ≥70, weak genes score <40, everything else is moderate."
          inputs={[
            { label: "Genes analyzed", value: `${data.total_genes}` },
            { label: "Clusters", value: `${clusters.length}` },
            {
              label: "Strong / moderate / weak",
              value: `${data.strong_genes} / ${moderateCount} / ${data.weak_genes}`,
            },
          ]}
          note="The baselines are calibrated from peer-anonymized benchmarks, not from guesswork. If your archetype shifts week-over-week, that's the structural profile of your store changing — something worth investigating."
        />

        {topAction && (
          <DrawerNextAction
            headline="Biggest lever right now"
            primary={{
              label: `Work on ${topAction.gene}`,
              description: `${topAction.cluster} — scoring ${topAction.score}/100. ${topAction.action}`,
              onClick: () => setDrawerOpen(false),
            }}
          />
        )}
      </DetailDrawer>
    </>
  );
}
