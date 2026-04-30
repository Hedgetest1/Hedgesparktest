"use client";

/**
 * LiveOpportunitiesCard — Phase 1.5 — Lite-accessible.
 *
 * Lists the store's pages that are leaking visitor intent RIGHT NOW:
 * pages with engaged traffic (scroll + dwell + clicks) but no or
 * under-converted activity. Every row is one page + one signal + one
 * suggested fix, sorted by priority. Data comes from
 * `/analytics/live-opportunities` which is Lite-accessible by design.
 *
 * Lite shows this card with the top 5 high-signal pages.
 * Pro shows the same card (no reduced-fidelity); the differentiation
 * on Pro lies in AUTO-DEPLOYING a fix (AI nudge composer, Pro feature)
 * vs Lite merchant who reads the recommendation and acts manually.
 *
 * Design intent: this is one of the six "right now" features that
 * make HedgeSpark Lite tell a story Lifetimely/BeProfit cannot
 * tell at all — they only show yesterday's data, never this-instant
 * friction.
 */

import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";

type Opportunity = {
  url: string;
  views: number;
  visitors: number;
  avg_dwell: number;
  avg_scroll: number;
  clicks: number;
  signal_type: "HIGH_INTENT_PAGE" | "ENGAGED_PAGE" | "LOW_SIGNAL";
  recommended_action: string;
  priority_score: number;
  explanation: string;
};

type LiveOpportunitiesData = {
  opportunities: Opportunity[];
};

// Drop LOW_SIGNAL rows — backend returns them as "Collect more data"
// which is noise at the card level (the merchant can't act on it and
// we don't want to pad the list with non-actionable rows). Top 5
// high-signal pages is the right density for a live-intelligence
// card; anything more becomes wall-of-text.
const MAX_ROWS = 5;

const SIGNAL_META: Record<
  Opportunity["signal_type"],
  { label: string; color: string; bg: string; border: string }
> = {
  HIGH_INTENT_PAGE: {
    label: "High intent",
    color: "#fbbf24",
    bg: "rgba(251,191,36,0.06)",
    border: "rgba(251,191,36,0.22)",
  },
  ENGAGED_PAGE: {
    label: "Engaged",
    color: "#a78bfa",
    bg: "rgba(167,139,250,0.05)",
    border: "rgba(167,139,250,0.2)",
  },
  LOW_SIGNAL: {
    label: "Low signal",
    color: "#94a3b8",
    bg: "rgba(100,116,139,0.03)",
    border: "rgba(100,116,139,0.12)",
  },
};

function shortUrl(url: string): string {
  // Drop protocol + host; keep pathname + query. "/products/silk-pillowcase"
  // is more useful than "https://shop.myshopify.com/products/silk-pillowcase"
  // in a narrow card layout.
  try {
    const parsed = new URL(url, "https://shop.myshopify.com");
    return parsed.pathname + (parsed.search || "");
  } catch {
    return url;
  }
}

function formatDwell(sec: number): string {
  if (!sec || sec < 1) return "—";
  if (sec < 60) return `${Math.round(sec)}s`;
  return `${Math.round(sec / 60)}m`;
}

export function LiveOpportunitiesCard({
  apiBase,
  shop,
}: {
  apiBase: string;
  shop: string;
}) {
  const { data, state, retry } = useCardFetch<LiveOpportunitiesData>({
    url: `${apiBase}/analytics/live-opportunities`,
    enabled: !!apiBase && !!shop,
    isEmpty: (d) => {
      const visible = (d?.opportunities || []).filter(
        (o) => o.signal_type !== "LOW_SIGNAL",
      );
      return visible.length === 0;
    },
    component: "LiveOpportunitiesCard",
  });

  if (state === "loading") {
    return <CardSkeleton label="Loading live opportunities" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Live opportunities unavailable"
        message="We couldn't load this card right now. Your traffic data is still being captured; this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    const sampleRows = [
      {
        url: "/products/silk-pillowcase",
        signal: "HIGH_INTENT",
        meta: { color: "#fb7185", border: "rgba(251,113,133,0.30)", bg: "rgba(251,113,133,0.05)", label: "High intent" },
        explanation: "47 visitors scrolled past the fold and dwelled 35s+ but only 1 added to cart.",
        action: "Add a social-proof badge to convert hesitant buyers.",
        score: 88,
        views: 124,
        visitors: 47,
        scroll: 78,
        dwell: 41,
      },
      {
        url: "/products/ceramic-mug",
        signal: "MEDIUM_INTENT",
        meta: { color: "#fbbf24", border: "rgba(251,191,36,0.25)", bg: "rgba(251,191,36,0.04)", label: "Medium intent" },
        explanation: "Steady traffic, scroll OK but cart abandonment 3× the page average.",
        action: "Surface free-shipping threshold above the fold.",
        score: 64,
        views: 89,
        visitors: 32,
        scroll: 64,
        dwell: 28,
      },
      {
        url: "/collections/winter",
        signal: "HIGH_INTENT",
        meta: { color: "#fb7185", border: "rgba(251,113,133,0.30)", bg: "rgba(251,113,133,0.05)", label: "High intent" },
        explanation: "Top entry page this week, but bounce rate climbing on mobile.",
        action: "Tighten hero copy and shorten the first product grid row.",
        score: 81,
        views: 213,
        visitors: 96,
        scroll: 52,
        dwell: 33,
      },
    ];
    return (
      <section>
        <div className="mb-6">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight text-[#e8a04e] sm:text-[2rem]">
                Live opportunities — pages leaking intent right now
              </h3>
              <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-slate-400">
                Once visitors start scrolling, dwelling, and clicking around your
                store, we surface the pages with the highest conversion potential
                here — each with one recommended next action. Preview below
                shows what it looks like in flight.
              </p>
            </div>
            <div className="flex flex-shrink-0 items-center gap-2 rounded-full bg-amber-500/[0.08] px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-amber-300">
              <span className="relative inline-flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400/60" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-amber-400" />
              </span>
              Sample
            </div>
          </div>
        </div>

        <ul className="space-y-2 opacity-50">
          {sampleRows.map((op) => (
            <li
              key={op.url}
              className="rounded-xl border p-4"
              style={{ borderColor: op.meta.border, background: op.meta.bg }}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate font-mono text-[13px] font-semibold text-slate-200">
                      {op.url}
                    </span>
                    <span
                      className="inline-flex items-center rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.1em]"
                      style={{ color: op.meta.color, background: `${op.meta.color}1A` }}
                    >
                      {op.meta.label}
                    </span>
                  </div>
                  <p className="mt-2 text-[12.5px] leading-[1.55] text-slate-400">
                    {op.explanation}
                  </p>
                  <p className="mt-2 text-[12px] font-semibold text-slate-300">
                    <span className="text-slate-500">Next action:</span> {op.action}
                  </p>
                </div>
                <div className="flex flex-shrink-0 flex-col items-end gap-0.5 text-right">
                  <div
                    className="text-[20px] font-extrabold tabular-nums"
                    style={{ color: op.meta.color }}
                  >
                    {op.score}
                  </div>
                  <div className="text-[10px] uppercase tracking-[0.12em] text-slate-400">
                    priority
                  </div>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-4 gap-2 border-t border-white/[0.04] pt-3 text-center">
                <div>
                  <div className="text-[14px] font-bold tabular-nums text-slate-200">{op.views}</div>
                  <div className="text-[10px] uppercase tracking-wider text-slate-400">Views</div>
                </div>
                <div>
                  <div className="text-[14px] font-bold tabular-nums text-slate-200">{op.visitors}</div>
                  <div className="text-[10px] uppercase tracking-wider text-slate-400">Visitors</div>
                </div>
                <div>
                  <div className="text-[14px] font-bold tabular-nums text-slate-200">{op.scroll}%</div>
                  <div className="text-[10px] uppercase tracking-wider text-slate-400">Scroll</div>
                </div>
                <div>
                  <div className="text-[14px] font-bold tabular-nums text-slate-200">{op.dwell}s</div>
                  <div className="text-[10px] uppercase tracking-wider text-slate-400">Dwell</div>
                </div>
              </div>
            </li>
          ))}
        </ul>

        <div className="mt-4 inline-block rounded-full bg-white/[0.04] px-2.5 py-1 text-[10px] font-semibold text-slate-400">
          Real opportunities replace this preview after ~10-20 engaged sessions
        </div>
      </section>
    );
  }

  // Filter to high-signal rows only + cap at MAX_ROWS.
  const visible = data.opportunities
    .filter((o) => o.signal_type !== "LOW_SIGNAL")
    .slice(0, MAX_ROWS);

  return (
    <section>
      {/* Unified section heading: ONE big amber H2 + slate subtitle. */}
      <div className="mb-6">
        <h3 className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight text-[#e8a04e] sm:text-[2rem]">
          Live opportunities — pages leaking intent right now
        </h3>
        <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-slate-400">
          Visitors engaged with these pages — scrolled, clicked, lingered —
          but conversion is under-delivering. One recommended next action
          per page.
        </p>
      </div>

      <ul className="space-y-2">
        {visible.map((op) => {
          const meta = SIGNAL_META[op.signal_type];
          return (
            <li
              key={op.url}
              className="rounded-xl border p-4 transition-colors"
              style={{ borderColor: meta.border, background: meta.bg }}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className="truncate font-mono text-[13px] font-semibold text-slate-200"
                      title={op.url}
                    >
                      {shortUrl(op.url)}
                    </span>
                    <span
                      className="inline-flex items-center rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.1em]"
                      style={{ color: meta.color, background: `${meta.color}1A` }}
                    >
                      {meta.label}
                    </span>
                  </div>
                  <p className="mt-2 text-[12.5px] leading-[1.55] text-slate-400">
                    {op.explanation}
                  </p>
                  <p className="mt-2 text-[12px] font-semibold text-slate-300">
                    <span className="text-slate-500">Next action:</span>{" "}
                    {op.recommended_action}
                  </p>
                </div>
                <div
                  className="flex flex-shrink-0 flex-col items-end gap-0.5 text-right"
                  aria-label="Priority score"
                >
                  <div
                    className="text-[20px] font-extrabold tabular-nums"
                    style={{ color: meta.color }}
                  >
                    {op.priority_score}
                  </div>
                  <div className="text-[10px] uppercase tracking-[0.12em] text-slate-400">
                    priority
                  </div>
                </div>
              </div>

              <div className="mt-3 grid grid-cols-4 gap-2 border-t border-white/[0.04] pt-3 text-center">
                <Stat label="Views" value={op.views.toLocaleString()} />
                <Stat label="Visitors" value={op.visitors.toLocaleString()} />
                <Stat
                  label="Scroll"
                  value={op.avg_scroll > 0 ? `${Math.round(op.avg_scroll)}%` : "—"}
                />
                <Stat label="Dwell" value={formatDwell(op.avg_dwell)} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[14px] font-bold tabular-nums text-slate-200">
        {value}
      </div>
      <div className="text-[10px] uppercase tracking-[0.1em] text-slate-400">
        {label}
      </div>
    </div>
  );
}
