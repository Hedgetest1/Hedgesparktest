"use client";

/**
 * ActionProof — compact proof-of-impact summary.
 *
 * NOTE (2026-04-14): this component is a duplicate of ProofHeroCard —
 * both read from GET /actions/proof and both are mounted on
 * app/page.tsx simultaneously. Flagged for founder decision:
 * ProofHeroCard covers the same data surface with a richer drawer, so
 * keeping both doubles the fetch and shows the merchant the same
 * result twice. Pending that decision this file is kept alive with
 * the Stage-1 minimum (useCardFetch + error/empty states + EN + EUR)
 * but NOT wired to a full drawer, since the hero version already has
 * one. If the founder approves removal, this file and its two imports
 * on app/page.tsx can be deleted in one commit.
 */

import type { paths } from "../lib/api-client";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";

type ProofData =
  paths["/actions/proof"]["get"]["responses"]["200"]["content"]["application/json"];

function fmtEur(n: number): string {
  if (n >= 1000) return `€${(n / 1000).toFixed(1)}k`;
  return `€${Math.round(n)}`;
}

export function ActionProof({
  apiBase,
  shop,
}: {
  apiBase: string;
  shop: string;
}) {
  const { data, state, retry } = useCardFetch<ProofData>({
    url: `${apiBase}/actions/proof`,
    enabled: !!shop && !!apiBase,
    isEmpty: (d) => d.actions_measured === 0,
  });

  if (state === "loading") {
    return <CardSkeleton label="Loading proof of impact" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Proof of impact unavailable"
        message="We couldn't load your before/after measurements. Your outcomes are safe — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="emerald"
        title="Proof of impact is warming up"
        body="When HedgeSpark takes an action on a signal we snapshot the baseline metrics and re-measure 7 days later. Your first before/after report will appear here."
        eta="First measurement in ~7 days"
      />
    );
  }

  const hasImprovements = data.improvements.length > 0;
  const revDelta = data.total_revenue_delta;

  return (
    <div className="rounded-2xl border border-emerald-500/25 bg-emerald-500/[0.04] p-5">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[11px] font-bold uppercase tracking-[0.16em] text-emerald-400">
            Proof of impact
          </div>
          <div className="mt-0.5 text-[11px] text-slate-500">
            {data.actions_measured} action{data.actions_measured === 1 ? "" : "s"} measured
          </div>
        </div>
        {revDelta > 0 && (
          <div className="text-right">
            <div className="text-[20px] font-extrabold tabular-nums text-emerald-300">
              +{fmtEur(revDelta)}
            </div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-emerald-400/60">
              Revenue delta
            </div>
          </div>
        )}
      </div>

      {hasImprovements && (
        <div className="mt-4 space-y-2">
          {data.improvements.slice(0, 3).map((imp, i) => (
            <div
              key={i}
              className="rounded-lg border border-emerald-500/15 bg-emerald-500/[0.03] px-3 py-2"
            >
              <div className="text-[12px] font-medium leading-relaxed text-slate-300">
                {imp.summary}
              </div>
              {imp.delta_revenue != null && imp.delta_revenue > 0 && (
                <div className="mt-1 text-[11px] font-semibold text-emerald-400/80">
                  +{fmtEur(imp.delta_revenue)} revenue
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {!hasImprovements && (
        <p className="mt-3 text-[12px] leading-relaxed text-slate-500">
          {data.actions_measured} action{data.actions_measured === 1 ? "" : "s"} measured so far —
          improvements will appear here when the before/after windows show a real delta.
        </p>
      )}
    </div>
  );
}
