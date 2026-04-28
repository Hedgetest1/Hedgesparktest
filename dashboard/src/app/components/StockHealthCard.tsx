"use client";

/**
 * StockHealthCard — Gap #4 Inventory KPIs (Lite full parity).
 *
 * Two-stat KPI summary + at-risk top-3 + "See full inventory →" CTA
 * that opens the Stock Health drawer with the paginated table.
 *
 * Voice: calm, merchant-friendly per founder direction 2026-04-28.
 *
 * Data: GET /merchant/inventory/kpis
 *       GET /merchant/inventory/details (drawer, on demand)
 */

import { useState } from "react";
import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";
import { apiClient } from "../lib/api-client";

const API_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "";

type AtRiskRow = {
  product_url: string;
  product_title: string;
  days_of_cover: number | null;
  inventory_quantity: number;
};

type Kpis = {
  shop_domain: string;
  products_tracked: number;
  out_of_stock_count: number;
  low_stock_count: number;
  days_of_cover_top: number | null;
  top_at_risk: AtRiskRow[];
  headline: string;
  lead_time_days: number;
  last_snapshot_at: string | null;
};

type DetailRow = {
  product_url: string;
  product_title: string;
  inventory_quantity: number;
  sales_rate_per_day: number;
  days_of_cover: number | null;
  sell_through_30d_pct: number;
  reorder_hint: string;
};

function fmtDays(d: number | null): string {
  if (d === null || d === undefined) return "—";
  return `${d.toFixed(0)} days`;
}

function reorderColor(hint: string): string {
  if (hint === "Reorder soon") return "text-rose-300";
  if (hint === "OK") return "text-emerald-300";
  return "text-slate-400";
}

export function StockHealthCard({
  apiBase,
  shop,
}: {
  apiBase: string;
  shop: string;
}) {
  const { data, state, retry } = useCardFetch<Kpis>({
    url: `${apiBase}/merchant/inventory/kpis`,
    enabled: !!apiBase && !!shop,
    isEmpty: (d) => d.products_tracked === 0,
    component: "StockHealthCard",
  });
  const [drawerOpen, setDrawerOpen] = useState(false);

  if (state === "loading") {
    return <CardSkeleton label="Loading your stock health" />;
  }
  if (state === "error") {
    return (
      <CardError
        label="Stock health unavailable"
        message="We couldn't load your inventory snapshot. Your stock data is safe — this card will recover automatically."
        onRetry={retry}
      />
    );
  }
  if (state === "empty" || !data) {
    return (
      <CardEmpty
        accent="amber"
        title="We're listening"
        body="Your first inventory snapshot will land here within 24h of install."
        eta="Daily snapshot via Shopify"
      />
    );
  }

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="dialog"
        aria-label="Open stock health drawer"
        onClick={() => setDrawerOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setDrawerOpen(true);
          }
        }}
        className="group cursor-pointer rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6 transition-shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-[#e8a04e] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1220] hover:border-white/[0.12]"
      >
        <h3 className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight text-[#e8a04e] sm:text-[2rem]">
          Stock health
        </h3>
        <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-slate-400">
          Where your stock is heading.
        </p>

        <div className="mt-5 grid grid-cols-2 gap-3">
          <div className="rounded-xl border border-rose-400/15 bg-rose-500/[0.05] px-4 py-3">
            <div className="text-[10px] font-bold uppercase tracking-wider text-rose-300">
              Out of stock
            </div>
            <div className="mt-1 text-[22px] font-extrabold tabular-nums text-rose-200">
              {data.out_of_stock_count}
            </div>
          </div>
          <div className="rounded-xl border border-amber-400/15 bg-amber-500/[0.05] px-4 py-3">
            <div className="text-[10px] font-bold uppercase tracking-wider text-amber-300">
              Low stock
            </div>
            <div className="mt-1 text-[22px] font-extrabold tabular-nums text-amber-200">
              {data.low_stock_count}
            </div>
          </div>
        </div>

        {data.top_at_risk.length > 0 ? (
          <div className="mt-5 space-y-2">
            {data.top_at_risk.map((r) => (
              <div
                key={r.product_url}
                className="flex items-center justify-between rounded-xl border border-white/[0.05] bg-white/[0.015] px-4 py-3"
              >
                <span className="truncate text-[13px] font-semibold text-slate-200">
                  {r.product_title}
                </span>
                <span className="flex-shrink-0 text-[12px] tabular-nums text-amber-300">
                  {fmtDays(r.days_of_cover)}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="mt-5 rounded-xl border border-emerald-400/15 bg-emerald-500/[0.04] px-4 py-3 text-[12px] text-emerald-200/80">
            All products have healthy stock right now.
          </div>
        )}

        <div className="mt-4 text-[11px] font-semibold text-slate-400">
          {data.headline} See full inventory →
        </div>
      </div>

      {drawerOpen && (
        <StockHealthDrawer
          shop={shop}
          apiBase={apiBase}
          leadTimeDays={data.lead_time_days}
          onClose={() => setDrawerOpen(false)}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Drawer with paginated full table + CSV export
// ---------------------------------------------------------------------------

function StockHealthDrawer({
  shop,
  apiBase,
  leadTimeDays,
  onClose,
}: {
  shop: string;
  apiBase: string;
  leadTimeDays: number;
  onClose: () => void;
}) {
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 25;

  const { data, state, retry } = useCardFetch<{
    rows: DetailRow[];
    total: number;
    page: number;
    page_size: number;
  }>({
    url: `${apiBase}/merchant/inventory/details?page=${page}&page_size=${PAGE_SIZE}`,
    enabled: !!apiBase && !!shop,
    isEmpty: (d) => !d.rows || d.rows.length === 0,
    component: "StockHealthDrawer",
  });

  function downloadCsv() {
    window.open(`${API_BASE}/analytics/export?surface=inventory&format=csv`, "_blank");
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Stock health detail"
      className="fixed inset-0 z-50 flex items-stretch justify-end bg-black/60"
      onClick={onClose}
    >
      <div
        className="flex h-full w-full max-w-[900px] flex-col overflow-y-auto border-l border-white/[0.08] bg-[#07070f] p-8"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-6 flex items-start justify-between">
          <div>
            <h2 className="text-[24px] font-extrabold tracking-tight text-[#e8a04e]">
              Stock health
            </h2>
            <p className="mt-1 text-[13px] text-slate-400">
              Lead time used for reorder hint: {leadTimeDays} days. Override per shop in Settings (coming soon).
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close stock health drawer"
            className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-1.5 text-[12px] font-semibold text-slate-200 hover:border-[#e8a04e]/40 hover:text-[#e8a04e]"
          >
            Close
          </button>
        </div>

        {state === "loading" && <CardSkeleton label="Loading inventory" />}
        {state === "error" && (
          <CardError
            label="Inventory unavailable"
            message="We couldn't load the full inventory. Try again in a moment."
            onRetry={retry}
          />
        )}
        {(state === "empty" || (state === "ready" && data && data.rows.length === 0)) && (
          <CardEmpty
            accent="amber"
            title="No inventory yet"
            body="Once your first snapshot lands, you'll see every product here."
          />
        )}
        {state === "ready" && data && data.rows.length > 0 && (
          <>
            <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
              <table className="w-full text-[13px]">
                <thead className="bg-white/[0.03] text-[11px] font-bold uppercase tracking-wider text-slate-400">
                  <tr>
                    <th className="px-4 py-2 text-left">Product</th>
                    <th className="px-4 py-2 text-right">Qty</th>
                    <th className="px-4 py-2 text-right">Sales / day</th>
                    <th className="px-4 py-2 text-right">Days of cover</th>
                    <th className="px-4 py-2 text-right">Sell-through 30d</th>
                    <th className="px-4 py-2 text-right">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr
                      key={r.product_url}
                      className="border-t border-white/[0.05]"
                    >
                      <td className="px-4 py-2 text-slate-200">
                        {r.product_title}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-slate-300">
                        {r.inventory_quantity}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-slate-300">
                        {r.sales_rate_per_day.toFixed(2)}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-slate-300">
                        {fmtDays(r.days_of_cover)}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-slate-300">
                        {r.sell_through_30d_pct.toFixed(0)}%
                      </td>
                      <td
                        className={`px-4 py-2 text-right text-[12px] font-semibold ${reorderColor(r.reorder_hint)}`}
                      >
                        {r.reorder_hint}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <span className="text-[12px] text-slate-400">
                {data.total} products · page {data.page} of {totalPages}
              </span>
              <div className="ml-auto flex items-center gap-2">
                <button
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-1.5 text-[12px] font-semibold text-slate-200 hover:border-[#e8a04e]/40 hover:text-[#e8a04e] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-1.5 text-[12px] font-semibold text-slate-200 hover:border-[#e8a04e]/40 hover:text-[#e8a04e] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Next
                </button>
                <button
                  onClick={downloadCsv}
                  className="rounded-lg bg-[#e8a04e] px-3 py-1.5 text-[12px] font-bold uppercase tracking-[0.1em] text-white hover:bg-[#fbbf24]"
                >
                  Export CSV
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
