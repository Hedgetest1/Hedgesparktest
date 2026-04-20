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
  /** Suppress internal heading (used inside LiteCassettoniGrid). */
  hideHeading?: boolean;
  /** Spark-voiced empty state — overrides generic text. */
  emptyHint?: string;
  /** Spark one-liner insight (data-driven, contextual). */
  sparkInsight?: string;
  /** Optional second line — supporting data for sparkInsight. */
  sparkDetail?: string;
};

// ---------------------------------------------------------------------------
// Signal type badge colour
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
  if (!s) return "";
  return s
    .toLowerCase()
    .split("_")
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(" ");
}

// ---------------------------------------------------------------------------
// Skeleton
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
// Snapshot row — product signal with optional action
// ---------------------------------------------------------------------------
function SnapshotRow({ item }: { item: BriefSnapshot }) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3.5">
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 flex-shrink-0 rounded-lg px-2.5 py-1 text-[12px] font-bold uppercase tracking-wide ${signalBadgeClass(item.signal_type)}`}
        >
          {prettySignal(item.signal_type)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[15px] font-semibold text-white">
            {item.product_label || item.product_url || "—"}
          </div>
          {item.human_label && (
            <div className="mt-1 text-[14px] leading-[1.5] text-slate-400">
              {item.human_label}
            </div>
          )}
        </div>
        <div className="flex-shrink-0 text-[14px] font-bold tabular-nums text-slate-400">
          {Math.round((item.signal_strength ?? 0) * 100)}%
        </div>
      </div>
      {item.human_action && (
        <div className="mt-3 rounded-lg border border-emerald-400/15 bg-emerald-500/[0.05] px-3.5 py-2.5">
          <p className="text-[14px] leading-[1.5] text-slate-300">{item.human_action}</p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export function BriefHero({ brief, loading, tier, onUpgradeClick, emptyHint, sparkInsight, sparkDetail, hideHeading }: Props) {
  if (loading) return <BriefSkeleton />;

  const isEmpty =
    !brief ||
    (!brief.headline && !brief.signals_count && !brief.top_signal_type);

  // ── EMPTY STATE — Spark voice ──
  if (isEmpty) {
    return (
      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] px-7 py-8">
        <div className="flex items-start gap-4">
          <Image
            src="/branding/hedgespark/spark.png"
            alt=""
            width={28}
            height={28}
            className="mt-0.5 flex-shrink-0 opacity-60"
          />
          <div>
            <p className="text-[16px] leading-relaxed text-slate-400">
              {emptyHint || "Watching your store. Findings appear within minutes."}
            </p>
            <p className="mt-2 text-[13px] text-slate-600">— <span className="hs-brand-gradient font-semibold">Spark</span></p>
          </div>
        </div>
      </div>
    );
  }

  const snapshot = Array.isArray(brief.metrics_snapshot)
    ? brief.metrics_snapshot.slice(0, 3)
    : [];

  const signalCount = brief.signals_count ?? 0;

  return (
    <div className="rounded-3xl border border-[#d4893a]/15 bg-gradient-to-br from-[#d4893a]/[0.04] via-transparent to-[#7c3aed]/[0.03] shadow-[0_0_40px_rgba(212,137,58,0.06)]">
      {/* Top accent */}
      <div className="absolute inset-x-0 top-0 h-[2px] rounded-t-3xl bg-gradient-to-r from-[#d4893a] via-[#a855f7] to-[#7c3aed] opacity-40" />

      {/* ── Spark insight block ── */}
      <div className="px-7 pt-7 pb-5">
        {/* Header suppressed when rendered inside the cassettoni
            panel (panel supplies its own title/subtitle). */}
        <div className="mb-5">
          {!hideHeading && (
            <h2 className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight text-[#e8a04e] sm:text-[2rem]">
              Daily brief — today&apos;s headline
            </h2>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2.5 text-[13px]">
            {brief.brief_date && (
              <span className="text-slate-500">{brief.brief_date}</span>
            )}
            {signalCount > 0 && (
              <span className="rounded-lg bg-white/[0.04] px-2.5 py-1 font-semibold tabular-nums text-slate-400 ring-1 ring-white/[0.06]">
                {signalCount} finding{signalCount !== 1 ? "s" : ""}
              </span>
            )}
            {brief.top_signal_type && (
              <span className={`rounded-lg px-2.5 py-1 text-[12px] font-bold uppercase tracking-wide ${signalBadgeClass(brief.top_signal_type)}`}>
                {prettySignal(brief.top_signal_type)}
              </span>
            )}
          </div>
        </div>

        {/* Spark-voiced headline — the key insight */}
        {brief.headline && (
          <p className="text-[20px] font-bold leading-snug text-white">
            {brief.headline}
          </p>
        )}

        {/* Spark insight — data-first supporting line */}
        {sparkInsight && (
          <p className="mt-3 text-[16px] leading-relaxed text-slate-400">
            {sparkInsight}
          </p>
        )}

        {/* Spark detail — secondary data */}
        {sparkDetail && (
          <p className="mt-1.5 text-[14px] text-slate-500">
            {sparkDetail}
          </p>
        )}

        {/* Spark signature */}
        <p className="mt-4 text-[12px] text-slate-600">— <span className="hs-brand-gradient font-semibold">Spark</span></p>
      </div>

      {/* ── Action — Pro gated ── */}
      {brief.top_action && (
        <div className="border-t border-white/[0.05] px-7 py-5">
          <ProGate tier={tier} onUpgradeClick={onUpgradeClick} label="recommended action">
            <div className="rounded-xl border border-emerald-400/15 bg-emerald-500/[0.04] px-5 py-4">
              <div className="mb-1.5 flex items-center gap-2">
                <svg className="h-4 w-4 text-emerald-400/70" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
                </svg>
                <span className="text-[12px] font-bold uppercase tracking-[0.14em] text-emerald-300/80">Recommended fix</span>
              </div>
              <p className="text-[15px] leading-[1.6] text-slate-200">{brief.top_action}</p>
            </div>
          </ProGate>
        </div>
      )}

      {/* ── AI summary — Pro only ── */}
      {tier === "pro" && brief.summary_text ? (
        <div className="border-t border-white/[0.05] px-7 py-5">
          <div className="rounded-xl border border-[#d4893a]/10 bg-[#d4893a]/[0.03] px-5 py-4">
            <p className="text-[15px] leading-[1.6] text-slate-300">{brief.summary_text}</p>
          </div>
        </div>
      ) : (
        tier === "lite" && (
          <div className="border-t border-white/[0.05] px-7 py-5">
            <button
              onClick={onUpgradeClick}
              className="w-full rounded-xl border border-[#d4893a]/10 bg-[#d4893a]/[0.03] px-5 py-4 text-left transition-colors hover:border-[#d4893a]/20 hover:bg-[#d4893a]/[0.06]"
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-[14px] text-slate-400">
                  I can tell you more about this on Pro.
                </p>
                <span className="flex-shrink-0 rounded-lg border border-[#d4893a]/25 bg-[#d4893a]/15 px-3 py-1 text-[12px] font-bold text-[#e8a04e]">
                  Pro
                </span>
              </div>
            </button>
          </div>
        )
      )}

      {/* ── Product snapshot — Pro only ── */}
      {snapshot.length > 0 && (
        <div className="border-t border-white/[0.05] px-7 py-5">
          {tier === "pro" ? (
            <div className="space-y-3">
              {snapshot.map((item, i) => (
                <SnapshotRow key={`${item.product_url}-${i}`} item={item} />
              ))}
            </div>
          ) : (
            <div className="text-[15px] text-slate-500">
              {snapshot.length} product{snapshot.length !== 1 ? "s" : ""} flagged.{" "}
              <button className="font-semibold text-[#d4893a] transition hover:text-[#e8a04e]" onClick={onUpgradeClick}>
                View details &rarr;
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
