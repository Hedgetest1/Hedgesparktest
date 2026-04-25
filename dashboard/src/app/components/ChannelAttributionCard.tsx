"use client";

/**
 * ChannelAttributionCard — UTM / channel attribution surface for Lite.
 *
 * Strada 3.2 (2026-04-20). Closes part of the "attribution" gap
 * without requiring ad-platform API integrations (Meta/Google/TikTok).
 * UTM attribution is deterministic — every conversion's first_source
 * and last_source are stored on visitor_purchase_sessions at purchase
 * time, so the numbers are evidence, not modeled guesses.
 *
 * What this surface does:
 *   - Headline attribution rate — "X of your Y orders are traceable
 *     to a channel. The rest hit your store directly or via untagged
 *     links."
 *   - Top sources by revenue (first-touch AND last-touch side by
 *     side) — spotlight which channel acquires and which closes.
 *   - Top campaigns (if the merchant uses utm_campaign tags).
 *   - First-vs-last match rate — "how often the same source both
 *     discovered AND closed the sale". High = simple funnels.
 *     Low = multi-touch journeys (the merchant should acquire via
 *     one channel and retarget via another).
 *
 * What this surface does NOT do (honesty):
 *   - Ad spend. We don't integrate Meta/Google/TikTok ad accounts,
 *     so we can't compute ROAS or cost-per-conversion. We track
 *     WHERE the traffic came from, not WHAT it cost the merchant.
 *     This is clearly stated in the methodology footer.
 *
 * Real-data contract: /attribution/summary is UTM-deterministic.
 * When no orders have been attributed yet (cold start), we render a
 * labeled preview with a "watching" pulse, matching the rest of the
 * Lite cold-start pattern.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "../app/_lib/formatters";
import type { components } from "../lib/api-types";

type AttributionSummary = components["schemas"]["AttributionSummaryResponse"];
type SourceRow = components["schemas"]["AttributionSourceRow"];
type CampaignRow = components["schemas"]["AttributionCampaignRow"];

const SOURCE_COLORS: Record<string, string> = {
  meta: "#60a5fa",
  facebook: "#60a5fa",
  instagram: "#f472b6",
  google: "#fbbf24",
  tiktok: "#f87171",
  email: "#34d399",
  direct: "#94a3b8",
  referral: "#a78bfa",
  organic: "#a78bfa",
};

function sourceColor(label: string): string {
  const key = label.toLowerCase();
  for (const [needle, color] of Object.entries(SOURCE_COLORS)) {
    if (key.includes(needle)) return color;
  }
  return "#94a3b8";
}

export function ChannelAttributionCard({
  apiBase,
  shop,
  displayCurrency = "USD",
}: {
  apiBase: string;
  shop: string;
  displayCurrency?: "USD" | "EUR";
}) {
  const [data, setData] = useState<AttributionSummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !shop) return;
    let active = true;
    apiClient
      .GET("/attribution/summary", { params: { query: { days: 30 } } })
      .then(({ data: raw }) => {
        if (!active) return;
        setData((raw as AttributionSummary) ?? null);
      })
      .catch(() => {
        if (active) setData(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [apiBase, shop]);

  const ordersTotal = data?.orders_total ?? 0;
  const ordersAttributed = data?.orders_attributed ?? 0;
  const attributionRate = data?.attribution_rate ?? 0;
  const firstTouch = data?.top_sources_first_touch ?? [];
  const lastTouch = data?.top_sources_last_touch ?? [];
  const campaigns = data?.top_campaigns ?? [];
  const matchRate = data?.first_vs_last_match_rate ?? 0;
  const hasData = !loading && ordersAttributed > 0;

  return (
    <div>
      {loading ? (
        <div className="rounded-2xl border border-white/[0.05] bg-[#0b0b14]/50 p-8">
          <div className="text-[13px] text-slate-400">Computing attribution…</div>
        </div>
      ) : hasData ? (
        <>
          {/* Hero row — attribution rate */}
          <div className="mb-6 grid gap-3 sm:grid-cols-3">
            <div className="rounded-2xl border border-white/[0.06] bg-[#0e0e1a]/70 p-5">
              <div className="text-[10.5px] font-bold uppercase tracking-[0.16em] text-slate-400">
                Orders (30 days)
              </div>
              <div className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums text-white">
                {ordersTotal.toLocaleString()}
              </div>
            </div>
            <div className="rounded-2xl border border-blue-400/[0.18] bg-blue-500/[0.05] p-5">
              <div className="text-[10.5px] font-bold uppercase tracking-[0.16em] text-blue-300">
                Attributed to a channel
              </div>
              <div className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums text-blue-300">
                {ordersAttributed.toLocaleString()}
              </div>
              <div className="mt-1.5 text-[12.5px] text-slate-400 tabular-nums">
                {(attributionRate * 100).toFixed(0)}% of orders
              </div>
            </div>
            <div className="rounded-2xl border border-violet-400/[0.18] bg-violet-500/[0.05] p-5">
              <div className="text-[10.5px] font-bold uppercase tracking-[0.16em] text-violet-300">
                Assisted conversions
              </div>
              <div className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums text-violet-300">
                {Math.round((1 - matchRate) * ordersAttributed).toLocaleString()}
              </div>
              <div className="mt-1.5 text-[12.5px] text-slate-400">
                {matchRate >= 0.7
                  ? `${Math.round((1 - matchRate) * 100)}% of orders — mostly single-channel funnels.`
                  : matchRate >= 0.4
                  ? `${Math.round((1 - matchRate) * 100)}% of orders — meaningful multi-touch paths.`
                  : `${Math.round((1 - matchRate) * 100)}% of orders — heavy multi-touch; first-touch channel earns credit it doesn't close.`}
              </div>
            </div>
          </div>

          {/* Channel split narrative — which channel acquires vs closes.
              Compares first-touch vs last-touch revenue shares so the
              merchant sees at-a-glance whether the same channel carries
              both ends. Strada 4 dominance: makes the Triple Whale
              "assisted" insight unambiguous on our card. */}
          {firstTouch.length > 0 && lastTouch.length > 0 && (
            <div className="mb-6 rounded-2xl border border-white/[0.05] bg-[#0b0b14]/60 p-5">
              <div className="mb-3 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
                Attribution flow · who acquires vs who closes
              </div>
              <p className="text-[13px] leading-relaxed text-slate-300">
                Your #1 acquirer is{" "}
                <span className="font-semibold text-emerald-300">
                  {firstTouch[0].label || firstTouch[0].source}
                </span>
                {firstTouch[0].source !== lastTouch[0].source ? (
                  <>
                    {" "}but your #1 closer is{" "}
                    <span className="font-semibold text-amber-300">
                      {lastTouch[0].label || lastTouch[0].source}
                    </span>
                    . The discovery channel and the conversion channel are
                    different — that&apos;s classic assisted behaviour.
                    Cutting the acquirer would starve the closer; cutting
                    the closer would orphan the discovery spend.
                  </>
                ) : (
                  <>
                    {" "}and it&apos;s also the #1 closer. Simple single-
                    channel funnel — most of your revenue travels through
                    a single touch.
                  </>
                )}
              </p>
            </div>
          )}

          {/* Top sources — side-by-side first-touch vs last-touch */}
          <div className="mb-6 grid gap-4 md:grid-cols-2">
            <div className="rounded-2xl border border-white/[0.06] bg-[#0e0e1a]/60 p-5">
              <div className="mb-4 flex items-center justify-between gap-2">
                <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-emerald-300">
                  First-touch — who acquires
                </div>
                <div className="text-[10px] text-slate-400">by revenue</div>
              </div>
              {firstTouch.length > 0 ? (
                <SourceList rows={firstTouch} ccy={displayCurrency} />
              ) : (
                <div className="text-[13px] text-slate-400">No attributed orders yet.</div>
              )}
            </div>
            <div className="rounded-2xl border border-white/[0.06] bg-[#0e0e1a]/60 p-5">
              <div className="mb-4 flex items-center justify-between gap-2">
                <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-amber-300">
                  Last-touch — who closes
                </div>
                <div className="text-[10px] text-slate-400">by revenue</div>
              </div>
              {lastTouch.length > 0 ? (
                <SourceList rows={lastTouch} ccy={displayCurrency} />
              ) : (
                <div className="text-[13px] text-slate-400">No attributed orders yet.</div>
              )}
            </div>
          </div>

          {/* Campaigns */}
          {campaigns.length > 0 && (
            <div className="rounded-2xl border border-white/[0.06] bg-[#0e0e1a]/60 p-5">
              <div className="mb-4 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
                Top campaigns · by revenue
              </div>
              <ul className="space-y-2">
                {campaigns.slice(0, 5).map((c, i) => (
                  <li
                    key={`${c.campaign}-${i}`}
                    className="flex items-center justify-between gap-4 rounded-xl border border-white/[0.05] bg-[#0b0b14]/60 px-4 py-3"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13.5px] font-semibold text-white">
                        {c.campaign || "(unnamed)"}
                      </div>
                      <div className="mt-0.5 text-[11.5px] text-slate-400 tabular-nums">
                        {c.orders} order{c.orders !== 1 ? "s" : ""}
                      </div>
                    </div>
                    <div className="flex-shrink-0 text-[15px] font-bold tabular-nums text-amber-300">
                      {formatMoneyCompact(c.revenue, displayCurrency)}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Methodology footer — the honest note about what we do NOT do */}
          <div className="mt-5 rounded-xl border border-white/[0.04] bg-[#0b0b14]/40 px-4 py-3">
            <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
              How this is measured
            </div>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-400">
              UTM-based attribution — the first and last source tagged on
              each converted visitor&apos;s session. No modeled attribution,
              no probabilistic guesses. Orders without UTM tags are
              correctly reported as unattributed rather than assigned to a
              default bucket.
            </p>
            <p className="mt-2 text-[11.5px] leading-relaxed italic text-slate-400">
              Ad spend and ROAS require Meta/Google/TikTok Ads API
              integrations we don&apos;t ship at the Lite tier. This
              surface tells you WHERE your converting traffic comes from,
              not what it cost you.
            </p>
          </div>
        </>
      ) : (
        // Cold-start — labeled preview + watching pulse (consistent
        // with LiteRarsHero / EmptyPreview aesthetic).
        <div className="rounded-2xl border border-dashed border-white/[0.12] bg-[#0b0b14]/40 p-6">
          <div className="mb-3 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
            <span
              className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-blue-300"
              aria-hidden="true"
            />
            Preview — what this surface will show
          </div>
          <p className="mb-5 text-[13px] leading-relaxed text-slate-400">
            Once your first UTM-tagged orders arrive, we&apos;ll break them
            down by traffic source (first-touch and last-touch), highlight
            your top campaigns by revenue, and show the match rate between
            who acquires vs who closes. Example with sample numbers below.
          </p>
          <div className="pointer-events-none mb-5 grid gap-3 opacity-50 sm:grid-cols-3">
            <div className="rounded-2xl border border-white/[0.06] bg-[#0e0e1a]/70 p-5">
              <div className="text-[10.5px] font-bold uppercase tracking-[0.16em] text-slate-400">Orders (30d)</div>
              <div className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums text-white">142</div>
            </div>
            <div className="rounded-2xl border border-blue-400/[0.18] bg-blue-500/[0.05] p-5">
              <div className="text-[10.5px] font-bold uppercase tracking-[0.16em] text-blue-300">Attributed</div>
              <div className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums text-blue-300">98</div>
              <div className="mt-1.5 text-[12.5px] text-slate-400 tabular-nums">69% of orders</div>
            </div>
            <div className="rounded-2xl border border-violet-400/[0.18] bg-violet-500/[0.05] p-5">
              <div className="text-[10.5px] font-bold uppercase tracking-[0.16em] text-violet-300">First = last</div>
              <div className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums text-violet-300">54%</div>
              <div className="mt-1.5 text-[12.5px] text-slate-400">Mixed funnels — some multi-touch journeys.</div>
            </div>
          </div>
          <div className="flex items-center gap-2 rounded-lg border border-emerald-400/15 bg-emerald-500/[0.05] px-3 py-2 text-[11.5px] font-semibold text-emerald-300">
            <span
              className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400"
              aria-hidden="true"
            />
            Watching your storefront — real numbers replace this preview after your first UTM-tagged orders.
          </div>
        </div>
      )}
    </div>
  );
}

function SourceList({ rows, ccy }: { rows: SourceRow[]; ccy: string }) {
  const total = rows.reduce((s, r) => s + (r.revenue ?? 0), 0);
  return (
    <ul className="space-y-2.5">
      {rows.slice(0, 5).map((r, i) => {
        const color = sourceColor(r.label);
        const pct = total > 0 ? Math.round(((r.revenue ?? 0) / total) * 100) : 0;
        return (
          <li
            key={`${r.source}-${i}`}
            className="rounded-xl border border-white/[0.04] bg-[#0b0b14]/60 px-3.5 py-3"
          >
            <div className="flex items-baseline justify-between gap-3">
              <span className="truncate text-[13px] font-semibold text-white">
                {r.label || r.source}
              </span>
              <span className="flex-shrink-0 text-[14px] font-bold tabular-nums" style={{ color }}>
                {formatMoneyCompact(r.revenue, ccy)}
              </span>
            </div>
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/[0.04]">
              <div
                className="h-full rounded-full transition-all"
                style={{
                  width: `${pct}%`,
                  background: `linear-gradient(90deg, ${color} 0%, ${color}80 100%)`,
                }}
                aria-hidden="true"
              />
            </div>
            <div className="mt-1.5 flex items-center justify-between gap-3 text-[11px]">
              <span className="text-slate-500 tabular-nums">
                {r.orders} order{r.orders !== 1 ? "s" : ""}
              </span>
              <span className="tabular-nums text-slate-500">{pct}% of revenue</span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
