"use client";

/**
 * MarginDragCard — per-product margin-drag view.
 *
 * Strada 4 dominance move (2026-04-20). Closes the last PARI gap in
 * the P&L category: which specific products are eroding total margin.
 * No competitor at the €39 band surfaces this as a single-answer
 * ranking with a drag-in-euros call.
 *
 * Data: GET /analytics/pnl/margin-drag (new endpoint). Per product in
 * the last 30 days: revenue, COGS (exact if product_costs has it,
 * else 40% default flagged), margin%, units. Top-5 worst ranked.
 *
 * Drag-euros headline = "how much more monthly margin you'd make if
 * these 5 products matched your shop's average margin." That's the
 * single actionable number — repricing / renegotiating COGS on these
 * specific products lifts the whole P&L.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "../app/_lib/formatters";
import type { components } from "../lib/api-types";

type MarginDragData = components["schemas"]["MarginDragResponse"];

function marginColor(pct: number): string {
  if (pct >= 60) return "#34d399";
  if (pct >= 40) return "#e8a04e";
  if (pct >= 20) return "#fbbf24";
  return "#f87171";
}

export function MarginDragCard({
  apiBase,
  shop,
  displayCurrency = "USD",
}: {
  apiBase: string;
  shop: string;
  displayCurrency?: "USD" | "EUR";
}) {
  const [data, setData] = useState<MarginDragData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;
    apiClient
      .GET("/analytics/pnl/margin-drag", { params: { query: { window_days: 30, limit: 5 } } })
      .then(({ data: raw }) => {
        if (!active) return;
        setData((raw as MarginDragData) ?? null);
      })
      .catch(() => {
        if (active) setData(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [apiBase, shop]);

  const products = data?.products ?? [];
  const drag = data?.total_margin_drag_eur ?? 0;
  const avg = data?.avg_margin_pct ?? 0;
  const currency = data?.currency ?? displayCurrency;
  const anyDefaultCogs = products.some((p) => p.cogs_source === "default_40pct");

  if (loading) {
    return (
      <div className="rounded-2xl border border-white/[0.05] bg-[#0b0b14]/50 p-6">
        <div className="text-[13px] text-slate-400">Computing per-product margins…</div>
      </div>
    );
  }

  if (!products.length) {
    return (
      <div className="rounded-2xl border border-dashed border-white/[0.12] bg-[#0b0b14]/40 p-6">
        <div className="mb-3 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-300" aria-hidden="true" />
          Preview — product-level margin ranking
        </div>
        <p className="text-[13px] leading-relaxed text-slate-400">
          Once orders accumulate, the five products eroding your total
          margin the most rank here. Each with revenue, COGS (exact or
          40%-default-flagged), margin %, and the drag number — how
          much more margin you&apos;d make if these five matched your
          shop average.
        </p>
      </div>
    );
  }

  return (
    <div>
      {/* Drag hero */}
      <div className="mb-5 rounded-2xl border border-amber-400/[0.2] bg-amber-500/[0.04] p-5">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-[10.5px] font-bold uppercase tracking-[0.22em] text-amber-300">
              Margin drag · last 30 days
            </div>
            <div className="mt-2 text-[2rem] font-extrabold leading-none tabular-nums text-amber-300">
              {drag > 0 ? formatMoneyCompact(drag, currency) : "—"}
              <span className="ml-2 text-[12px] font-semibold tabular-nums text-slate-400">
                / mo recoverable
              </span>
            </div>
            <p className="mt-3 max-w-xl text-[12.5px] leading-relaxed text-slate-400">
              If your 5 worst-margin products matched your shop average{" "}
              <span className="font-semibold text-slate-300">({avg.toFixed(1)}%)</span>,
              you&apos;d keep this much more each month. Reprice, renegotiate
              COGS, or cut the drag — three concrete levers, one number.
            </p>
          </div>
        </div>
      </div>

      {/* Product ranking */}
      <div className="mb-4">
        <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Worst 5 by margin %
        </div>
        <ul className="space-y-2">
          {products.map((p, i) => {
            const color = marginColor(p.margin_pct);
            const isExact = p.cogs_source !== "default_40pct";
            return (
              <li
                key={p.product || `product-${i}`}
                className="flex flex-wrap items-center gap-4 rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 px-4 py-3"
              >
                <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.02] text-[11px] font-extrabold tabular-nums text-slate-400">
                  {i + 1}
                </span>
                <div className="min-w-[160px] flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[13.5px] font-semibold text-white" title={p.title}>
                      {p.title}
                    </span>
                    {!isExact && (
                      <span className="flex-shrink-0 rounded-md border border-amber-400/30 bg-amber-500/[0.08] px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-amber-300">
                        40% estimated
                      </span>
                    )}
                    {isExact && (
                      <span className="flex-shrink-0 rounded-md border border-emerald-400/30 bg-emerald-500/[0.08] px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-emerald-300">
                        Exact COGS
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-[11.5px] text-slate-400 tabular-nums">
                    {formatMoneyCompact(p.revenue, currency)} revenue · {p.units_sold} units · {formatMoneyCompact(p.cogs, currency)} cogs
                  </div>
                </div>
                <div className="flex flex-shrink-0 items-baseline gap-5 text-right">
                  <div>
                    <div className="text-[9.5px] font-semibold uppercase tracking-wider text-slate-500">Margin</div>
                    <div
                      className="text-[16px] font-extrabold tabular-nums leading-none"
                      style={{ color }}
                    >
                      {p.margin_pct.toFixed(1)}%
                    </div>
                  </div>
                  <div>
                    <div className="text-[9.5px] font-semibold uppercase tracking-wider text-slate-500">Kept</div>
                    <div className="text-[14px] font-bold tabular-nums text-slate-200">
                      {formatMoneyCompact(p.margin_eur, currency)}
                    </div>
                  </div>
                </div>
                {/* Margin bar */}
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.04]">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${Math.max(2, Math.min(100, p.margin_pct))}%`,
                      background: `linear-gradient(90deg, ${color} 0%, ${color}80 100%)`,
                    }}
                    aria-hidden="true"
                  />
                </div>
              </li>
            );
          })}
        </ul>
      </div>

      {/* Methodology + upgrade hint */}
      <div className="rounded-xl border border-white/[0.04] bg-[#0b0b14]/40 px-4 py-3">
        <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
          How this is measured
        </div>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-400">
          {data?.methodology ?? ""}
        </p>
        {anyDefaultCogs && (
          <p className="mt-2 text-[11.5px] leading-relaxed text-amber-300/80">
            Products flagged &quot;40% estimated&quot; use the default COGS
            assumption. Adding real costs per product (Settings → Cost
            inputs, or auto-sync from Shopify Admin) upgrades them to
            <span className="font-semibold"> exact</span> — and usually
            changes the ranking meaningfully.
          </p>
        )}
      </div>
    </div>
  );
}
