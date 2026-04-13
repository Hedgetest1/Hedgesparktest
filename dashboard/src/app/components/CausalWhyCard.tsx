"use client";

/**
 * CausalWhyCard — Phase Ω killer.
 *
 * The "why" engine: when something looks off, this card surfaces the
 * leading causal hypothesis with confidence, supporting evidence, and
 * the next concrete action. Built on /pro/causal/explain.
 *
 * Storytelling > metrics: every other card says WHAT. This card says WHY.
 */

import { useEffect, useState } from "react";
import { t } from "../lib/i18n";

type Hypothesis = {
  label: string;
  confidence: number;
  score: number;
  prior: number;
  evidence: string[];
  suppressors: string[];
  narrative: string;
  recommended_action: string;
  rank: number;
};

type CausalResponse = {
  shop_domain: string;
  vertical?: string;
  vertical_display?: string;
  hypotheses: Hypothesis[];
  narrative: string;
  next_action?: string | null;
  fusion_alerts?: Array<{ pattern: string; severity: string; fusion_score: number }>;
  raw_signals?: Array<{ name: string; severity: number; delta_pct: number }>;
  generated_at: string;
};

function labelize(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function confidenceColor(c: number): string {
  if (c >= 0.7) return "#f87171"; // rose — high confidence high concern
  if (c >= 0.4) return "#fbbf24"; // amber
  return "#94a3b8";
}

export function CausalWhyCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [data, setData] = useState<CausalResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) { setLoading(false); return; }
    let active = true;
    setLoading(true);
    fetch(`${apiBase}/pro/causal/explain`, {
      credentials: "include",
      headers: { "Content-Type": "application/json" },
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((j: CausalResponse) => { if (active) setData(j); })
      .catch(() => { if (active) setData(null); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiBase, shop, isProUser]);

  if (!isProUser) return null;

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
        <div className="h-3 w-32 rounded bg-white/[0.06]" />
        <div className="mt-3 h-16 rounded bg-white/[0.04]" />
      </div>
    );
  }

  if (!data || !data.hypotheses || data.hypotheses.length === 0) {
    return (
      <div className="rounded-2xl border border-emerald-400/15 bg-emerald-500/[0.04] p-5">
        <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-400">
          {t("why.eyebrow")}
        </div>
        <h3 className="text-[15px] font-bold text-white">{t("why.healthy")}</h3>
        <p className="mt-2 text-[12px] leading-relaxed text-emerald-200/80">
          Your store reads as healthy. We're watching every signal in real time and will
          surface the *cause*, not just the metric, the moment something drifts.
        </p>
      </div>
    );
  }

  const top = data.hypotheses[0];
  const conf = Math.round((top.confidence || 0) * 100);
  const color = confidenceColor(top.confidence || 0);
  const others = data.hypotheses.slice(1, 4);

  return (
    <section
      className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5"
      aria-labelledby="why-engine-heading"
      role="region"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]" aria-hidden="true">
            {t("why.eyebrow")}
          </div>
          <h3 id="why-engine-heading" className="text-[15px] font-bold text-white">
            {t("why.title")}
          </h3>
          {data.vertical_display && (
            <p className="mt-1 text-[11px] text-slate-500">
              Tuned for <span className="font-semibold text-slate-300">{data.vertical_display}</span> stores
            </p>
          )}
        </div>
        <div
          className="flex-shrink-0 rounded-full px-3 py-1.5 text-[11px] font-bold tabular-nums"
          style={{ color, background: color + "20", border: `1px solid ${color}40` }}
        >
          {conf}% {t("common.confidence")}
        </div>
      </div>

      {/* Top hypothesis */}
      <div className="rounded-xl border border-white/[0.06] bg-white/[0.025] p-4">
        <div className="mb-2 flex items-center gap-2">
          <span
            className="rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide"
            style={{ color, background: color + "15", border: `1px solid ${color}30` }}
          >
            {labelize(top.label)}
          </span>
          {top.evidence?.length > 0 && (
            <span className="text-[10px] text-slate-500">
              {top.evidence.length} supporting signal{top.evidence.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
        <p className="text-[14px] leading-[1.6] text-slate-200">{top.narrative}</p>

        {top.recommended_action && (
          <div className="mt-3 rounded-lg border border-emerald-400/15 bg-emerald-500/[0.05] px-3 py-2.5">
            <div className="mb-1 flex items-center gap-2">
              <svg className="h-3.5 w-3.5 text-emerald-400/70" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
              </svg>
              <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-emerald-300/80">
                {t("why.next_step")}
              </span>
            </div>
            <p className="text-[13px] leading-relaxed text-slate-100">{top.recommended_action}</p>
          </div>
        )}
      </div>

      {/* Other hypotheses (collapsed) */}
      {others.length > 0 && (
        <div className="mt-3 space-y-1.5">
          <div className="text-[10px] font-medium uppercase tracking-wide text-slate-500" id="other-causes-label">
            Other possible causes
          </div>
          <ul className="space-y-1.5" aria-labelledby="other-causes-label">
            {others.map((h) => (
              <li
                key={h.label}
                className="flex items-center justify-between rounded-lg border border-white/[0.04] bg-white/[0.015] px-3 py-2"
              >
                <span className="text-[12px] text-slate-300">{labelize(h.label)}</span>
                <span className="text-[11px] tabular-nums text-slate-500" aria-label={`${Math.round((h.confidence || 0) * 100)} percent confidence`}>
                  {Math.round((h.confidence || 0) * 100)}%
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
