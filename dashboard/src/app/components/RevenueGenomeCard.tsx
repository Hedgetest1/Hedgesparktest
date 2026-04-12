"use client";

/**
 * RevenueGenomeCard — "Your Revenue DNA"
 *
 * THE unreachable feature. Shows the complete genetic profile of
 * the merchant's revenue across 6 gene clusters with an overall
 * health score and prescriptive priority actions.
 *
 * Visual: DNA-helix-inspired layout with gene score bars.
 *
 * Data source: GET /pro/revenue-genome
 */

import { useEffect, useState } from "react";

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
    case "Revenue Machine": return "from-emerald-500/20 to-emerald-400/5";
    case "Growth Ready": return "from-violet-500/20 to-violet-400/5";
    case "Emerging": return "from-amber-500/20 to-amber-400/5";
    default: return "from-slate-500/20 to-slate-400/5";
  }
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
  const [data, setData] = useState<GenomeData | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) { setLoading(false); return; }
    let active = true;
    fetch(`${apiBase}/pro/revenue-genome`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((j) => { if (active) setData(j); })
      .catch(() => {})
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiBase, shop, isProUser]);

  if (!isProUser) return null;

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-6">
        <div className="h-4 w-48 rounded bg-white/[0.06]" />
        <div className="mt-4 flex justify-center">
          <div className="h-28 w-28 rounded-full bg-white/[0.04]" />
        </div>
        <div className="mt-4 grid grid-cols-3 gap-2">
          {[0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="h-16 rounded bg-white/[0.04]" />)}
        </div>
      </div>
    );
  }

  if (!data) return null;

  const clusters = Object.values(data.gene_clusters);
  const sc = scoreColor(data.overall_score);

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
      {/* Header */}
      <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
        Revenue Genome
      </div>
      <h3 className="text-[17px] font-bold text-white">Your Revenue DNA</h3>

      {/* Overall score + archetype */}
      <div className={`mt-4 flex items-center gap-5 rounded-xl bg-gradient-to-r ${archetypeGradient(data.archetype)} border border-white/[0.06] p-4`}>
        {/* Score ring */}
        <div className="relative flex-shrink-0">
          <svg width="80" height="80" viewBox="0 0 80 80">
            <circle cx="40" cy="40" r="34" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="6" />
            <circle
              cx="40" cy="40" r="34"
              fill="none" stroke={sc} strokeWidth="6"
              strokeLinecap="round"
              strokeDasharray={`${data.overall_score * 2.14} 214`}
              transform="rotate(-90 40 40)"
              className="transition-all duration-1000"
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-[20px] font-extrabold tabular-nums" style={{ color: sc }}>
              {data.overall_score}
            </span>
            <span className="text-[8px] font-bold uppercase text-slate-500">/ 100</span>
          </div>
        </div>

        <div>
          <div className="text-[14px] font-bold text-white">{data.archetype}</div>
          <p className="mt-0.5 text-[11px] text-slate-400">{data.archetype_description}</p>
          <div className="mt-1.5 flex gap-3 text-[10px]">
            <span className="text-emerald-400">{data.strong_genes} strong</span>
            <span className="text-slate-500">{data.total_genes - data.strong_genes - data.weak_genes} moderate</span>
            <span className="text-red-400">{data.weak_genes} weak</span>
          </div>
        </div>
      </div>

      {/* Gene clusters */}
      <div className="mt-4 grid grid-cols-2 gap-2 lg:grid-cols-3">
        {clusters.map((cluster) => {
          const color = CLUSTER_COLORS[cluster.cluster] || "#94a3b8";
          const icon = CLUSTER_ICONS[cluster.cluster] || "?";
          const avgScore = cluster.genes.length > 0
            ? Math.round(cluster.genes.reduce((s, g) => s + g.score, 0) / cluster.genes.length)
            : 0;
          const isExpanded = expanded === cluster.cluster;

          return (
            <div
              key={cluster.cluster}
              className="cursor-pointer rounded-xl border border-white/[0.05] bg-white/[0.015] p-3 transition-all hover:border-white/[0.12]"
              onClick={() => setExpanded(isExpanded ? null : cluster.cluster)}
            >
              <div className="flex items-center gap-2">
                <div
                  className="flex h-7 w-7 items-center justify-center rounded-lg text-[11px] font-bold"
                  style={{ background: color + "25", color }}
                >
                  {icon}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[10px] font-semibold text-slate-300 truncate">
                    {cluster.cluster}
                  </div>
                  <div className="h-1.5 mt-1 overflow-hidden rounded-full bg-white/[0.05]">
                    <div
                      className="h-full rounded-full transition-all duration-700"
                      style={{ width: `${avgScore}%`, background: scoreColor(avgScore) }}
                    />
                  </div>
                </div>
                <span className="text-[13px] font-bold tabular-nums" style={{ color: scoreColor(avgScore) }}>
                  {avgScore}
                </span>
              </div>

              {/* Expanded genes */}
              {isExpanded && (
                <div className="mt-2.5 space-y-1.5 border-t border-white/[0.05] pt-2.5">
                  {cluster.genes.map((gene) => (
                    <div key={gene.name}>
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] text-slate-400">{gene.name}</span>
                        <span className="text-[10px] font-bold tabular-nums" style={{ color: scoreColor(gene.score) }}>
                          {gene.score}
                        </span>
                      </div>
                      <p className="text-[9px] text-slate-500">{gene.insight}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Priority actions */}
      {data.priority_actions.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.14em] text-amber-400">
            Priority actions
          </div>
          {data.priority_actions.map((a, i) => (
            <div key={i} className="mb-1.5 rounded-lg border border-amber-400/10 bg-amber-500/[0.03] px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-bold text-amber-300">{a.cluster}</span>
                <span className="text-[9px] text-slate-500">·</span>
                <span className="text-[10px] text-slate-400">{a.gene}</span>
                <span className="ml-auto text-[10px] font-bold tabular-nums text-red-400">{a.score}/100</span>
              </div>
              <p className="mt-0.5 text-[10px] text-amber-400/80">{a.action}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
