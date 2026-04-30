"use client";

/**
 * HowCustomersFindYouCard — Gap #7 of brutal $0-70 audit.
 *
 * "How customers find you" — last-30d distribution from the
 * post-purchase survey deployed via Shopify Checkout UI Extension.
 * Shown to Lite + Pro alike (parity with Pathlight/Zigpoll free tier).
 *
 * Data source: GET /merchant/survey/aggregate?range=last_30_days
 *
 * Loss-prevention framing: "X% of N shoppers found you via Y" lets the
 * merchant double down on the channel that's actually working AND
 * notice when a paid channel disappears from the top.
 */

import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";

type DistributionEntry = {
  choice: string;
  count: number;
  pct: number;
};

type SurveyAggregate = {
  shop_domain: string;
  range: string;
  total: number;
  distribution: DistributionEntry[];
  top_choice: DistributionEntry | null;
};

// Map raw choice values back to display labels. Falls back to
// title-cased value when an unrecognised choice arrives (Pro merchants
// can introduce custom values via /pro/settings/surveys).
const KNOWN_LABELS: Record<string, string> = {
  instagram: "Instagram",
  tiktok: "TikTok",
  google: "Google",
  friend: "Friend",
  email: "Email",
  other: "Other",
};

function labelFor(choice: string): string {
  if (KNOWN_LABELS[choice]) return KNOWN_LABELS[choice];
  return choice.charAt(0).toUpperCase() + choice.slice(1).replace(/_/g, " ");
}

// Color sequence for the distribution bars — top bar amber, rest slate.
// Mirrors palette from CLAUDE.md §4 (amber = warm/highlight, slate =
// neutral). Top option pops; the rest stay quiet.
function barColor(index: number): string {
  return index === 0 ? "#e8a04e" : "rgba(148,163,184,0.55)";
}

export function HowCustomersFindYouCard({
  apiBase,
  shop,
}: {
  apiBase: string;
  shop: string;
}) {
  const { data, state, retry } = useCardFetch<SurveyAggregate>({
    url: `${apiBase}/merchant/survey/aggregate?range=last_30_days`,
    enabled: !!apiBase && !!shop,
    isEmpty: (d) => !d.distribution || d.distribution.length === 0,
    component: "HowCustomersFindYouCard",
  });

  if (state === "loading") {
    return <CardSkeleton label="Loading attribution survey results" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Attribution survey unavailable"
        message="We couldn't load this week's attribution survey. Your survey data is safe — this card will recover automatically."
        onRetry={retry}
      />
    );
  }

  if (state === "empty" || !data) {
    return (
      <div className="rounded-2xl border border-dashed border-white/[0.10] bg-white/[0.015] p-6">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h3 className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight text-[#e8a04e] sm:text-[2rem]">
              How customers find you
            </h3>
            <p className="mt-2 max-w-2xl text-[13px] leading-relaxed text-slate-400">
              The post-purchase survey is live on your Thank-You page. The first
              response will land here within 24h of your next order — preview
              below shows a typical SMB Shopify mix.
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

        <div className="space-y-2 opacity-50">
          {[
            { choice: "instagram", count: 8, pct: 42 },
            { choice: "tiktok", count: 5, pct: 26 },
            { choice: "friend", count: 3, pct: 16 },
            { choice: "google", count: 3, pct: 16 },
          ].map((entry, i) => (
            <div
              key={entry.choice}
              className="flex items-center gap-3 rounded-xl border border-white/[0.05] bg-white/[0.015] px-4 py-3"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-3">
                  <span className="truncate text-[13px] font-semibold text-slate-200">
                    {labelFor(entry.choice)}
                  </span>
                  <span className="flex-shrink-0 text-[12px] tabular-nums text-slate-400">
                    {entry.count} ({entry.pct}%)
                  </span>
                </div>
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/[0.04]">
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${entry.pct}%`, background: barColor(i) }}
                  />
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="mt-4 inline-block rounded-full bg-white/[0.04] px-2.5 py-1 text-[10px] font-semibold text-slate-400">
          Real responses replace this preview after ~1 order
        </div>
      </div>
    );
  }

  const top = data.top_choice;
  const total = data.total;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
      <h3 className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight text-[#e8a04e] sm:text-[2rem]">
        How customers find you
      </h3>
      {top && (
        <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-slate-400">
          Last 30 days: <span className="font-bold text-slate-200">{top.pct.toFixed(0)}%</span>{" "}
          of <span className="font-bold text-slate-200">{total}</span>{" "}
          {total === 1 ? "shopper" : "shoppers"} heard about you via{" "}
          <span className="font-bold text-amber-300">{labelFor(top.choice)}</span>.
        </p>
      )}

      {/* Horizontal bar chart — top option amber, rest slate.
          Bars use percentage width; max 100% for the leader. */}
      <div className="mt-5 space-y-2">
        {data.distribution.map((entry, i) => (
          <div
            key={entry.choice}
            className="flex items-center gap-3 rounded-xl border border-white/[0.05] bg-white/[0.015] px-4 py-3"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate text-[13px] font-semibold text-slate-200">
                  {labelFor(entry.choice)}
                </span>
                <span className="flex-shrink-0 text-[12px] tabular-nums text-slate-400">
                  {entry.count} ({entry.pct.toFixed(0)}%)
                </span>
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/[0.04]">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${Math.min(100, entry.pct)}%`,
                    background: barColor(i),
                  }}
                />
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-4 text-[11px] text-slate-400">
        Surveys captured on the Thank-You page. PII-free; merchants can disable on Pro Settings.
      </div>
    </div>
  );
}
