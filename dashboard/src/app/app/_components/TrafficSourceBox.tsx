/**
 * TrafficSourceBox — traffic source intelligence card.
 * Extracted from app/page.tsx (Phase Ω⁶ split).
 *
 * Shows per-source visitor quality + share for the top product.
 * Pro users also see `action_insight` strings. Lite users see the
 * upgrade CTA below the real data (strictly additive).
 */

export type SourceRowShape = {
  source_type: string;
  visitors: number;
  views: number;
  avg_dwell: number | null;
  avg_scroll: number | null;
  cart_conversions: number;
  hot_visitors: number;
  quality_label: "Strong intent" | "Mixed intent" | "Low quality";
  quality_score: number;
  attention_label: "Best source" | "Needs work" | "Low signal";
  action_insight?: string;
};

export type SourceQualityDataShape = {
  product_url: string | null;
  sources: SourceRowShape[];
  insight: string;
};

const SOURCE_NAMES: Record<string, string> = {
  direct:     "Direct",
  referral:   "Referral",
  email:      "Email",
  unknown:    "Unattributed",
  google:     "Google",
  bing:       "Bing",
  yahoo:      "Yahoo",
  duckduckgo: "DuckDuckGo",
  baidu:      "Baidu",
  facebook:   "Facebook",
  instagram:  "Instagram",
  tiktok:     "TikTok",
  twitter:    "Twitter / X",
  pinterest:  "Pinterest",
  linkedin:   "LinkedIn",
  youtube:    "YouTube",
  reddit:     "Reddit",
  snapchat:   "Snapchat",
  amazon:     "Amazon",
  ebay:       "eBay",
  etsy:       "Etsy",
  search:     "Organic / Search",
  social:     "Social",
};

function qualityColor(label: string): string {
  if (label === "Strong intent") return "text-emerald-400";
  if (label === "Mixed intent")  return "text-amber-400";
  return "text-slate-500";
}

export function TrafficSourceBox({
  sourceQuality,
  isProUser,
  onUpgradeClick,
}: {
  sourceQuality: SourceQualityDataShape | null;
  isProUser: boolean;
  onUpgradeClick: () => void;
}) {
  let productLabel = "Top product";
  if (sourceQuality?.product_url) {
    try {
      const path = new URL(sourceQuality.product_url).pathname;
      const slug = path.split("/").filter(Boolean).pop() || "";
      if (slug) productLabel = slug.replace(/-/g, " ");
    } catch {
      productLabel = sourceQuality.product_url;
    }
  }

  const sources = sourceQuality?.sources ?? [];
  const totalVisitors = sources.reduce((s, r) => s + r.visitors, 0);

  return (
    <div className="hs-fade-up flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4">
      <div className="mb-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
          Where high-intent traffic comes from
        </div>
        <p className="mt-0.5 truncate text-[11px] text-slate-400" title={productLabel}>
          {productLabel}
        </p>
      </div>

      {sources.length === 0 ? (
        <p className="text-[12px] text-slate-400">
          Not enough traffic data yet to evaluate sources.
        </p>
      ) : (
        <>
          <div className="space-y-3">
            {sources.map((src) => {
              const name   = SOURCE_NAMES[src.source_type] ?? src.source_type;
              const color  = qualityColor(src.quality_label);
              const barPct = totalVisitors > 0
                ? Math.round((src.visitors / totalVisitors) * 100)
                : 0;

              const attnStyle =
                src.attention_label === "Best source"
                  ? "bg-violet-500/20 text-violet-300"
                  : src.attention_label === "Low signal"
                  ? "bg-white/[0.04] text-slate-600"
                  : "bg-white/[0.04] text-slate-500";

              return (
                <div key={src.source_type}>
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <span className="text-[12px] text-slate-300">{name}</span>
                      <span className={`rounded px-1 py-px text-[9px] font-semibold leading-none ${attnStyle}`}>
                        {src.attention_label}
                      </span>
                    </div>
                    <div className="flex flex-shrink-0 items-center gap-2">
                      <span className="text-[10px] tabular-nums text-slate-400">{src.quality_score}</span>
                      <span className={`text-[10px] font-medium ${color}`}>{src.quality_label}</span>
                      <span className="w-7 text-right text-[11px] tabular-nums text-slate-400">{barPct}%</span>
                    </div>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                    <div className="h-full rounded-full bg-violet-500/50" style={{ width: `${barPct}%` }} />
                  </div>

                  {isProUser && src.action_insight && (
                    <div className="mt-1.5 flex items-start gap-2 rounded-lg border border-emerald-400/15 bg-emerald-500/[0.05] px-2.5 py-1.5">
                      <span className="mt-1 h-1 w-1 flex-shrink-0 rounded-full bg-emerald-400/80" />
                      <p className="text-[11px] leading-[1.5] text-slate-300">{src.action_insight}</p>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <p className="mt-auto border-t border-white/[0.05] pt-3 text-[11px] leading-[1.55] text-slate-400">
            {sourceQuality!.insight}
          </p>

          {!isProUser && (
            <div className="mt-2 flex items-center justify-between rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2">
              <span className="text-[11px] text-slate-400">
                Historical trends &amp; advanced recommendations in Pro
              </span>
              <button
                onClick={onUpgradeClick}
                className="ml-3 flex-shrink-0 rounded-md bg-violet-600/80 px-2.5 py-1 text-[10px] font-semibold text-white hover:bg-violet-500 transition-colors"
              >
                Upgrade
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
