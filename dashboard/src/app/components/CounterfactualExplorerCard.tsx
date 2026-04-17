"use client";

/**
 * CounterfactualExplorerCard — Phase Ω⁷ killer #2.
 *
 * "If you'd acted N days ago, you'd have saved €X."
 *
 * Shows a one-sentence headline + per-scenario table: act now (0 days),
 * 7 days ago, 14 days ago, 30 days ago. Based on opportunity signal
 * detection timestamps + RARS loss rates — no fabrication.
 *
 * Source: GET /pro/counterfactual/signals
 */

import { useState } from "react";
import { CardError, CardSkeleton, useCardFetch } from "./_CardStates";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";

type Scenario = {
  days_ago: number;
  saved_eur: number;
  label: string;
};

type CfEntry = {
  signal_id: number;
  signal_type: string;
  product_url: string | null;
  detected_at: string | null;
  days_open: number;
  per_day_loss_eur: number;
  scenarios: Scenario[];
  max_save_eur: number;
  aov_used_eur: number;
  aov_is_real: boolean;
  headline: string;
};

type CfResponse = {
  shop_domain: string;
  aov_eur: number;
  aov_is_real: boolean;
  total_open_signals: number;
  total_max_save_eur: number;
  entries: CfEntry[];
  headline: string;
  // Shop's native currency (USD/EUR/GBP/…) — every `_eur` field above
  // is denominated in this currency.
  currency?: string;
  generated_at: string;
};

const fmtMoney = (n: number, currency?: string): string =>
  formatMoneyCompact(n, currency || "USD");

function prettyType(t: string): string {
  return t.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

export function CounterfactualExplorerCard({
  apiBase,
  isProUser,
}: {
  apiBase: string;
  isProUser: boolean;
}) {
  const { data, state, retry } = useCardFetch<CfResponse>({
    url: `${apiBase}/pro/counterfactual/signals`,
    enabled: isProUser && !!apiBase,
    isEmpty: (d) => !d.entries?.length,
  });
  const [expandedId, setExpandedId] = useState<number | null>(null);

  if (!isProUser) return null;
  if (state === "loading") return <CardSkeleton label="Loading counterfactual explorer" />;
  if (state === "error")
    return (
      <CardError
        label="Counterfactual explorer failed to load"
        message="Couldn't reach the signal loss projection — your revenue tracking is unaffected."
        onRetry={retry}
      />
    );
  if (!data || state === "empty") {
    return (
      <section className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
        <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
          Counterfactual Explorer
        </div>
        <h3 className="text-[15px] font-bold text-white">Nothing to simulate yet</h3>
        <p className="mt-2 text-[12px] leading-relaxed text-slate-400">
          This card will show how much revenue you&apos;d have recovered if
          you&apos;d acted N days ago on each open opportunity signal. Once
          your first signal fires, the math starts.
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-amber-400/15 bg-amber-500/[0.03] p-5" aria-labelledby="cf-heading">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
            Counterfactual Explorer
          </div>
          <h3 id="cf-heading" className="text-[15px] font-bold leading-snug text-white">
            If you&apos;d acted sooner
          </h3>
          <p className="mt-1 text-[11px] text-slate-500">
            Based on your real AOV ({fmtMoney(data.aov_eur, data.currency)}{!data.aov_is_real && " est."}) and the signal detection timeline.
          </p>
        </div>
        <div className="flex-shrink-0 rounded-xl border border-amber-400/25 bg-amber-500/[0.08] px-3 py-2 text-right">
          <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-amber-400">
            Max recoverable now
          </div>
          <div className="text-[22px] font-extrabold tabular-nums text-amber-300">
            {fmtMoney(data.total_max_save_eur, data.currency)}
          </div>
        </div>
      </div>

      <p className="mb-4 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3 text-[12px] leading-relaxed text-slate-300">
        {data.headline}
      </p>

      <ul className="space-y-2">
        {data.entries.slice(0, 5).map((entry) => {
          const isExpanded = expandedId === entry.signal_id;
          return (
            <li
              key={entry.signal_id}
              className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 transition-colors hover:border-amber-400/25"
            >
              <button
                type="button"
                onClick={() => setExpandedId(isExpanded ? null : entry.signal_id)}
                className="flex w-full items-start justify-between gap-3 text-left"
                aria-expanded={isExpanded}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="rounded-md bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-amber-300">
                      {entry.days_open}d open
                    </span>
                    <span className="truncate text-[12px] font-semibold text-slate-200">
                      {prettyType(entry.signal_type)}
                    </span>
                  </div>
                  {entry.product_url && (
                    <p className="mt-0.5 truncate text-[10px] text-slate-600" title={entry.product_url}>
                      {entry.product_url.replace(/^\/products\//, "")}
                    </p>
                  )}
                </div>
                <div className="flex-shrink-0 text-right">
                  <div className="text-[13px] font-bold tabular-nums text-amber-300">
                    {fmtMoney(entry.max_save_eur, data.currency)}
                  </div>
                  <div className="text-[9px] text-slate-600">
                    ~{fmtMoney(entry.per_day_loss_eur, data.currency)}/day
                  </div>
                </div>
              </button>

              {isExpanded && (
                <div className="mt-3 border-t border-white/[0.05] pt-3">
                  <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                    What-if scenarios
                  </div>
                  <div className="mt-2 grid grid-cols-4 gap-2">
                    {entry.scenarios.map((s) => (
                      <div
                        key={s.days_ago}
                        className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-2 py-1.5 text-center"
                      >
                        <div className="text-[9px] text-slate-600">{s.label}</div>
                        <div className="text-[12px] font-bold tabular-nums text-amber-300">
                          {fmtMoney(s.saved_eur, data.currency)}
                        </div>
                      </div>
                    ))}
                  </div>
                  <p className="mt-2 text-[10px] leading-relaxed text-slate-500">
                    {entry.headline}
                  </p>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
