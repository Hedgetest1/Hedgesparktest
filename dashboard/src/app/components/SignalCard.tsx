"use client";

import { ProGate } from "./ProGate";

export type OpportunitySignal = {
  product_url?: string;
  signal_type?: string;
  signal_strength?: number;
  signal_confidence?: string;
  explanation?: string;
  detected_at?: string | null;
  human_label?: string;
  human_action?: string;
};

const SIGNAL_META: Record<
  string,
  { label: string; bg: string; text: string; ring: string; bar: string; glow: string }
> = {
  HIGH_TRAFFIC_NO_CART: {
    label: "High Traffic, No Cart",
    bg: "bg-amber-500/15",
    text: "text-amber-300",
    ring: "ring-amber-400/30",
    bar: "bg-amber-400",
    glow: "shadow-[0_0_8px_rgba(251,191,36,0.35)]",
  },
  LOW_CONVERSION_ATTENTION: {
    label: "Low Conversion",
    bg: "bg-rose-500/15",
    text: "text-rose-300",
    ring: "ring-rose-400/30",
    bar: "bg-rose-400",
    glow: "shadow-[0_0_8px_rgba(251,113,133,0.35)]",
  },
  RETURN_VISITOR_INTEREST: {
    label: "Return Visitor Interest",
    bg: "bg-cyan-500/15",
    text: "text-cyan-300",
    ring: "ring-cyan-400/30",
    bar: "bg-cyan-400",
    glow: "shadow-[0_0_8px_rgba(34,211,238,0.35)]",
  },
  TRAFFIC_SPIKE: {
    label: "Traffic Spike",
    bg: "bg-violet-500/15",
    text: "text-violet-300",
    ring: "ring-violet-400/30",
    bar: "bg-violet-400",
    glow: "shadow-[0_0_8px_rgba(167,139,250,0.35)]",
  },
  DEAD_TRAFFIC: {
    label: "Dead Traffic",
    bg: "bg-slate-500/15",
    text: "text-slate-300",
    ring: "ring-slate-400/30",
    bar: "bg-slate-400",
    glow: "shadow-[0_0_8px_rgba(148,163,184,0.25)]",
  },
  HIGH_ENGAGEMENT_NO_ACTION: {
    label: "High Engagement, No Action",
    bg: "bg-emerald-500/15",
    text: "text-emerald-300",
    ring: "ring-emerald-400/30",
    bar: "bg-emerald-400",
    glow: "shadow-[0_0_8px_rgba(52,211,153,0.35)]",
  },
  SCROLL_HIGH_NO_CLICK: {
    label: "Deep Scroll, No Click",
    bg: "bg-sky-500/15",
    text: "text-sky-300",
    ring: "ring-sky-400/30",
    bar: "bg-sky-400",
    glow: "shadow-[0_0_8px_rgba(56,189,248,0.35)]",
  },
  HIGH_RETURN_LOW_CONVERSION: {
    label: "Return Visitors Not Converting",
    bg: "bg-orange-500/15",
    text: "text-orange-300",
    ring: "ring-orange-400/30",
    bar: "bg-orange-400",
    glow: "shadow-[0_0_8px_rgba(251,146,60,0.35)]",
  },
  MOBILE_CONVERSION_GAP: {
    label: "Device Conversion Gap",
    bg: "bg-pink-500/15",
    text: "text-pink-300",
    ring: "ring-pink-400/30",
    bar: "bg-pink-400",
    glow: "shadow-[0_0_8px_rgba(244,114,182,0.35)]",
  },
  CART_RATE_DECLINING: {
    label: "Cart Rate Declining",
    bg: "bg-red-500/15",
    text: "text-red-300",
    ring: "ring-red-400/30",
    bar: "bg-red-400",
    glow: "shadow-[0_0_8px_rgba(248,113,113,0.35)]",
  },
  PAID_TRAFFIC_NOT_CONVERTING: {
    label: "Paid Traffic Not Converting",
    bg: "bg-yellow-500/15",
    text: "text-yellow-300",
    ring: "ring-yellow-400/30",
    bar: "bg-yellow-400",
    glow: "shadow-[0_0_8px_rgba(250,204,21,0.35)]",
  },
  DEVICE_PURCHASE_GAP: {
    label: "Device Purchase Gap",
    bg: "bg-fuchsia-500/15",
    text: "text-fuchsia-300",
    ring: "ring-fuchsia-400/30",
    bar: "bg-fuchsia-400",
    glow: "shadow-[0_0_8px_rgba(217,70,239,0.35)]",
  },
  SOURCE_REVENUE_GAP: {
    label: "Paid Traffic, No Revenue",
    bg: "bg-yellow-600/15",
    text: "text-yellow-200",
    ring: "ring-yellow-500/30",
    bar: "bg-yellow-500",
    glow: "shadow-[0_0_8px_rgba(234,179,8,0.35)]",
  },
  TIME_WINDOW_MISALIGNMENT: {
    label: "Timing Mismatch",
    bg: "bg-indigo-500/15",
    text: "text-indigo-300",
    ring: "ring-indigo-400/30",
    bar: "bg-indigo-400",
    glow: "shadow-[0_0_8px_rgba(129,140,248,0.35)]",
  },
  LANDING_PAGE_FAILURE: {
    label: "Landing Page Issue",
    bg: "bg-orange-600/15",
    text: "text-orange-200",
    ring: "ring-orange-500/30",
    bar: "bg-orange-500",
    glow: "shadow-[0_0_8px_rgba(249,115,22,0.35)]",
  },
  REVENUE_CONCENTRATION: {
    label: "Revenue Concentrated",
    bg: "bg-amber-600/15",
    text: "text-amber-200",
    ring: "ring-amber-500/30",
    bar: "bg-amber-500",
    glow: "shadow-[0_0_8px_rgba(217,119,6,0.35)]",
  },
  STORE_MOBILE_GAP: {
    label: "Store-Wide Mobile Gap",
    bg: "bg-pink-600/15",
    text: "text-pink-200",
    ring: "ring-pink-500/30",
    bar: "bg-pink-500",
    glow: "shadow-[0_0_8px_rgba(219,39,119,0.35)]",
  },
  STORE_PAID_GAP: {
    label: "Store-Wide Paid Gap",
    bg: "bg-yellow-600/20",
    text: "text-yellow-100",
    ring: "ring-yellow-400/30",
    bar: "bg-yellow-400",
    glow: "shadow-[0_0_8px_rgba(250,204,21,0.35)]",
  },
};

// Early signal visual meta — deliberately muted
const EARLY_SIGNAL_META: Record<
  string,
  { label: string; bg: string; text: string; ring: string; bar: string; glow: string }
> = {
  EARLY_BROWSING_NO_CART: {
    label: "Browsing, No Carts",
    bg: "bg-slate-500/10",
    text: "text-slate-400",
    ring: "ring-slate-400/20",
    bar: "bg-slate-500",
    glow: "",
  },
  FIRST_VISITOR_ENGAGEMENT: {
    label: "First Engagement",
    bg: "bg-violet-500/10",
    text: "text-violet-400/70",
    ring: "ring-violet-400/15",
    bar: "bg-violet-500/60",
    glow: "",
  },
  EARLY_DROP_OFF: {
    label: "Quick Exit",
    bg: "bg-amber-500/10",
    text: "text-amber-400/70",
    ring: "ring-amber-400/15",
    bar: "bg-amber-500/50",
    glow: "",
  },
  SINGLE_PRODUCT_FOCUS: {
    label: "All Eyes Here",
    bg: "bg-cyan-500/10",
    text: "text-cyan-400/70",
    ring: "ring-cyan-400/15",
    bar: "bg-cyan-500/60",
    glow: "",
  },
};

const FALLBACK_META = {
  label: "",
  bg: "bg-white/5",
  text: "text-slate-300",
  ring: "ring-white/10",
  bar: "bg-slate-500",
  glow: "",
};

function relativeTime(isoStr?: string | null): string {
  if (!isoStr) return "—";
  const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

type Props = {
  signal: OpportunitySignal;
  tier: "lite" | "pro";
  onUpgradeClick: () => void;
};

export function SignalCard({ signal, tier, onUpgradeClick }: Props) {
  const isEarly = signal.signal_confidence === "low";
  const meta = isEarly
    ? (EARLY_SIGNAL_META[signal.signal_type || ""] ?? { ...FALLBACK_META, label: signal.signal_type ?? "Signal" })
    : (SIGNAL_META[signal.signal_type || ""] ?? { ...FALLBACK_META, label: signal.signal_type ?? "Unknown Signal" });
  const strength = signal.signal_strength ?? 0;
  const strengthPct = Math.round(strength * 100);

  return (
    <div className={`hs-fade-up group rounded-2xl border p-5 transition-all duration-200 ${
      isEarly
        ? "border-white/[0.05] bg-white/[0.015] opacity-85"
        : "border-white/[0.07] bg-white/[0.03] hover:border-[#d4893a]/20 hover:bg-white/[0.05] hover:shadow-[0_4px_24px_rgba(212,137,58,0.06)]"
    }`}>
      {/* Header row — signal type badge + early tag + relative time */}
      <div className="mb-4 flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className={`rounded-lg px-3 py-1.5 text-[13px] font-bold uppercase tracking-wide ring-1 ${meta.bg} ${meta.text} ${meta.ring}`}
          >
            {meta.label}
          </span>
          {isEarly && (
            <span className="rounded-lg bg-white/[0.04] px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.08em] text-slate-400 ring-1 ring-white/[0.06]">
              Early
            </span>
          )}
        </div>
        <span className="flex-shrink-0 text-[13px] text-slate-400">
          {relativeTime(signal.detected_at)}
        </span>
      </div>

      {/* Primary text — human_label if available, else product_url */}
      <div className={`mb-1.5 text-[16px] font-semibold leading-snug ${isEarly ? "text-slate-300" : "text-white"}`}>
        {signal.human_label || signal.product_url || "—"}
      </div>
      {isEarly && (
        <div className="mb-2 text-[13px] text-slate-400">Based on limited data</div>
      )}

      {/* Product URL as secondary meta when human_label is present */}
      {signal.human_label && signal.product_url && (
        <div className="mb-2.5 truncate text-[13px] text-slate-400">
          {signal.product_url}
        </div>
      )}

      {/* Explanation — diagnostic text, visible for all tiers */}
      {!signal.human_label && signal.explanation && (
        <p className="mb-4 text-[14px] leading-[1.6] text-slate-400">
          {signal.explanation}
        </p>
      )}

      {/* Signal strength bar */}
      <div className="mb-4 space-y-2">
        <div className="flex items-center justify-between text-[13px]">
          <span className="font-medium text-slate-400">Signal strength</span>
          <span className={`font-bold tabular-nums ${strengthPct >= 70 ? meta.text : "text-slate-300"}`}>
            {strengthPct}%
          </span>
        </div>
        <div className="h-2.5 w-full overflow-hidden rounded-full bg-white/[0.07]">
          <div
            className={`h-full rounded-full transition-all duration-500 ${meta.bar} ${strengthPct >= 50 ? meta.glow : ""}`}
            style={{ width: `${strengthPct}%` }}
          />
        </div>
      </div>

      {/* human_action — prescriptive content, Pro only */}
      {signal.human_action && (
        <ProGate tier={tier} onUpgradeClick={onUpgradeClick} label="recommended action">
          <div className="rounded-xl border border-emerald-400/15 bg-emerald-500/[0.05] px-4 py-3.5">
            <div className="mb-1 flex items-center gap-2">
              <svg className="h-4 w-4 text-emerald-400/70" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
              </svg>
              <span className="text-[12px] font-bold uppercase tracking-[0.14em] text-emerald-300/80">
                Recommended fix
              </span>
              <span className="rounded border border-[#d4893a]/25 bg-[#d4893a]/10 px-1.5 py-[2px] text-[10px] font-bold text-[#d4893a]/70">PRO</span>
            </div>
            <p className="text-[14px] leading-[1.6] text-slate-200">{signal.human_action}</p>
          </div>
        </ProGate>
      )}
    </div>
  );
}
