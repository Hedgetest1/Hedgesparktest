"use client";

import Image from "next/image";
import { ProGate } from "./ProGate";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
export type BriefSnapshot = {
  product_url: string;
  product_label: string;
  signal_type: string;
  signal_strength: number;
  human_label: string;
  // PRO ONLY — present only when fetched from /brief/today/pro.
  // Absent from Lite responses; SnapshotRow renders it when present.
  human_action?: string;
};

export type DailyBrief = {
  brief_date?: string;
  generated_at?: string;
  headline?: string;
  signals_count?: number;
  top_product_url?: string | null;
  top_product_label?: string | null;
  top_signal_type?: string | null;
  top_action?: string | null;
  summary_text?: string | null;
  summary_generated?: boolean;
  metrics_snapshot?: BriefSnapshot[];
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------
type Props = {
  brief: DailyBrief | null;
  loading: boolean;
  tier: "lite" | "pro";
  onUpgradeClick: () => void;
  /** Optional contextual hint for the empty state — overrides generic "No signals yet" copy. */
  emptyHint?: string;
  /** Spark one-liner for the brief header — data-driven, contextual */
  sparkInsight?: string;
};

// ---------------------------------------------------------------------------
// Signal type badge colour — all 8 signals covered
// ---------------------------------------------------------------------------
function signalBadgeClass(signalType?: string | null): string {
  switch (signalType) {
    case "TRAFFIC_SPIKE":
      return "bg-rose-500/15 text-rose-300 ring-1 ring-rose-400/30";
    case "HIGH_TRAFFIC_NO_CART":
      return "bg-amber-500/15 text-amber-300 ring-1 ring-amber-400/30";
    case "LOW_CONVERSION_ATTENTION":
      return "bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-400/30";
    case "RETURN_VISITOR_INTEREST":
      return "bg-violet-500/15 text-violet-300 ring-1 ring-violet-400/30";
    case "DEAD_TRAFFIC":
      return "bg-slate-500/15 text-slate-300 ring-1 ring-slate-400/30";
    case "HIGH_ENGAGEMENT_NO_ACTION":
      return "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-400/30";
    case "SCROLL_HIGH_NO_CLICK":
      return "bg-sky-500/15 text-sky-300 ring-1 ring-sky-400/30";
    case "HIGH_RETURN_LOW_CONVERSION":
      return "bg-orange-500/15 text-orange-300 ring-1 ring-orange-400/30";
    case "MOBILE_CONVERSION_GAP":
      return "bg-pink-500/15 text-pink-300 ring-1 ring-pink-400/30";
    case "CART_RATE_DECLINING":
      return "bg-red-500/15 text-red-300 ring-1 ring-red-400/30";
    case "PAID_TRAFFIC_NOT_CONVERTING":
      return "bg-yellow-500/15 text-yellow-300 ring-1 ring-yellow-400/30";
    case "DEVICE_PURCHASE_GAP":
      return "bg-fuchsia-500/15 text-fuchsia-300 ring-1 ring-fuchsia-400/30";
    case "SOURCE_REVENUE_GAP":
      return "bg-yellow-600/15 text-yellow-200 ring-1 ring-yellow-500/30";
    case "TIME_WINDOW_MISALIGNMENT":
      return "bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-400/30";
    case "LANDING_PAGE_FAILURE":
      return "bg-orange-600/15 text-orange-200 ring-1 ring-orange-500/30";
    case "REVENUE_CONCENTRATION":
      return "bg-amber-600/15 text-amber-200 ring-1 ring-amber-500/30";
    case "STORE_MOBILE_GAP":
      return "bg-pink-600/15 text-pink-200 ring-1 ring-pink-500/30";
    case "STORE_PAID_GAP":
      return "bg-yellow-600/20 text-yellow-100 ring-1 ring-yellow-400/30";
    default:
      return "bg-white/5 text-slate-400 ring-1 ring-white/10";
  }
}

function prettySignal(s?: string | null): string {
  if (!s) return "—";
  return s
    .toLowerCase()
    .split("_")
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(" ");
}

// ---------------------------------------------------------------------------
// Format generated_at → "06:14 UTC"
// ---------------------------------------------------------------------------
function formatGeneratedAt(isoStr?: string): string {
  if (!isoStr) return "";
  try {
    const d = new Date(isoStr);
    return (
      d.toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
        timeZone: "UTC",
      }) + " UTC"
    );
  } catch {
    return "";
  }
}

// ---------------------------------------------------------------------------
// Skeleton shimmer while loading
// ---------------------------------------------------------------------------
function BriefSkeleton() {
  return (
    <div className="animate-pulse rounded-2xl border border-violet-400/10 bg-gradient-to-br from-violet-500/[0.05] to-transparent p-6">
      <div className="mb-4 h-3 w-32 rounded bg-white/[0.06]" />
      <div className="mb-3 h-5 w-3/4 rounded bg-white/[0.06]" />
      <div className="h-4 w-1/2 rounded bg-white/[0.04]" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------
function BriefEmpty() {
  return (
    <div className="rounded-2xl border border-dashed border-white/[0.08] bg-white/[0.02] px-6 py-8 text-center">
      <div className="text-sm text-slate-500">
        No signals yet — check back after your first day of traffic.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Snapshot row — product label · signal badge · human label · strength
// ---------------------------------------------------------------------------
function SnapshotRow({ item }: { item: BriefSnapshot }) {
  return (
    <div className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5">
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${signalBadgeClass(item.signal_type)}`}
        >
          {prettySignal(item.signal_type)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12px] font-medium text-slate-200">
            {item.product_label || item.product_url || "—"}
          </div>
          {item.human_label && (
            <div className="mt-0.5 text-[11px] leading-4 text-slate-500">
              {item.human_label}
            </div>
          )}
        </div>
        <div className="flex-shrink-0 text-[11px] tabular-nums text-slate-600">
          {Math.round((item.signal_strength ?? 0) * 100)}%
        </div>
      </div>
      {/* human_action — PRO ONLY. Present only when fetched via /brief/today/pro. */}
      {item.human_action && (
        <div className="mt-2 rounded-lg border border-emerald-400/15 bg-emerald-500/[0.05] px-2.5 py-1.5">
          <div className="mb-0.5 flex items-center gap-1.5">
            <span className="text-[9px] font-semibold uppercase tracking-[0.14em] text-emerald-300/70">Action</span>
            <span className="rounded border border-violet-400/20 px-1 py-px text-[9px] font-normal normal-case tracking-normal text-violet-400/70">PRO</span>
          </div>
          <p className="text-[11px] leading-[1.5] text-slate-300">{item.human_action}</p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export function BriefHero({ brief, loading, tier, onUpgradeClick, emptyHint, sparkInsight }: Props) {
  if (loading) return <BriefSkeleton />;

  const isEmpty =
    !brief ||
    (!brief.headline && !brief.signals_count && !brief.top_signal_type);

  if (isEmpty) {
    return (
      <div className="rounded-2xl border border-dashed border-white/[0.08] bg-white/[0.02] px-6 py-8 text-center">
        <div className="text-sm text-slate-500">
          {emptyHint || "No signals yet \u2014 check back after your first day of traffic."}
        </div>
      </div>
    );
  }

  const snapshot = Array.isArray(brief.metrics_snapshot)
    ? brief.metrics_snapshot.slice(0, 3)
    : [];

  const generatedAt = formatGeneratedAt(brief.generated_at ?? undefined);

  return (
    <div className="rounded-2xl border border-violet-400/[0.18] bg-gradient-to-br from-violet-500/[0.06] to-transparent p-6 shadow-[0_0_32px_rgba(124,58,237,0.06)]">
      {/* Eyebrow */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/80">
          Daily Brief
        </span>
        {brief.brief_date && (
          <span className="text-[11px] text-slate-600">
            {brief.brief_date}
            {generatedAt && (
              <span className="ml-1.5 text-slate-700">· {generatedAt}</span>
            )}
          </span>
        )}
        {brief.top_signal_type && (
          <span
            className={`rounded-full px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] ${signalBadgeClass(brief.top_signal_type)}`}
          >
            {prettySignal(brief.top_signal_type)}
          </span>
        )}
        {typeof brief.signals_count === "number" && brief.signals_count > 0 && (
          <span className="rounded-full bg-white/5 px-2.5 py-0.5 text-[10px] text-slate-400 ring-1 ring-white/10">
            {brief.signals_count} signal{brief.signals_count !== 1 ? "s" : ""} detected
          </span>
        )}
      </div>

      {/* Spark insight — contextual one-liner */}
      {sparkInsight && (
        <div className="mb-3 flex items-start gap-2">
          <Image
            src="/branding/hedgespark-mascot.png"
            alt=""
            width={18}
            height={18}
            className="mt-0.5 flex-shrink-0"
          />
          <p className="text-[12px] leading-[1.5] text-violet-300/70">{sparkInsight}</p>
        </div>
      )}

      {/* Headline — always visible */}
      {brief.headline && (
        <p className="mb-4 text-base font-semibold leading-snug text-white">
          {brief.headline}
        </p>
      )}

      {/* Top action — Pro gated */}
      {brief.top_action && (
        <div className="mb-4">
          <ProGate tier={tier} onUpgradeClick={onUpgradeClick} label="recommended action">
            <div className="rounded-xl border border-emerald-400/15 bg-emerald-500/5 px-4 py-3">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-emerald-300/80">
                Recommended action
              </div>
              <p className="text-[13px] leading-5 text-slate-200">{brief.top_action}</p>
            </div>
          </ProGate>
        </div>
      )}

      {/* AI summary — Pro only, or upgrade nudge for Lite */}
      {tier === "pro" && brief.summary_text ? (
        <div className="mb-4 rounded-xl border border-violet-400/15 bg-violet-500/5 px-4 py-3">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-violet-300/70">
            AI Summary
          </div>
          <p className="text-[13px] leading-5 text-slate-300">{brief.summary_text}</p>
        </div>
      ) : (
        tier === "lite" && (
          <button
            onClick={onUpgradeClick}
            className="mb-4 w-full rounded-xl border border-violet-400/15 bg-violet-500/5 px-4 py-3 text-left transition-colors hover:border-violet-400/30 hover:bg-violet-500/10"
          >
            <div className="flex items-center justify-between gap-2">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-violet-300/70">
                  AI Summary
                </div>
                <p className="mt-0.5 text-[12px] text-slate-500">
                  Get a daily AI-written narrative that explains what to do and why.
                </p>
              </div>
              <span className="flex-shrink-0 rounded-full border border-violet-400/30 bg-violet-500/20 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-violet-300">
                Pro →
              </span>
            </div>
          </button>
        )
      )}

      {/* Metrics snapshot — top 3 products, Pro only */}
      {snapshot.length > 0 && (
        tier === "pro" ? (
          <div className="space-y-2">
            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-600">
              Top products
              <span className="ml-2 text-[10px] text-violet-400/70 border border-violet-400/20 px-1.5 py-[1px] rounded align-middle">PRO</span>
            </div>
            {snapshot.map((item, i) => (
              <SnapshotRow key={`${item.product_url}-${i}`} item={item} />
            ))}
          </div>
        ) : (
          <div className="space-y-2">
            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-600">
              Top products
              <span className="ml-2 text-[10px] text-violet-400/70 border border-violet-400/20 px-1.5 py-[1px] rounded align-middle">PRO</span>
            </div>
            <div className="mt-1 text-sm text-slate-500">
              Full breakdown available
              <button
                className="ml-2 text-violet-400 transition hover:text-violet-300"
                onClick={onUpgradeClick}
              >
                View Pro insights →
              </button>
            </div>
          </div>
        )
      )}
    </div>
  );
}
