"use client";

import { ProGate } from "./ProGate";

export type OpportunitySignal = {
  product_url?: string;
  signal_type?: string;
  signal_strength?: number;
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
  const meta = SIGNAL_META[signal.signal_type || ""] ?? {
    ...FALLBACK_META,
    label: signal.signal_type ?? "Unknown Signal",
  };
  const strength = signal.signal_strength ?? 0;
  const strengthPct = Math.round(strength * 100);

  return (
    <div className="hs-fade-up group rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4 transition-all duration-150 hover:border-violet-400/25 hover:bg-white/[0.05] hover:shadow-[0_4px_20px_rgba(124,58,237,0.07)]">
      {/* Header row — signal type badge + relative time */}
      <div className="mb-3 flex items-start justify-between gap-2">
        <span
          className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ring-1 ${meta.bg} ${meta.text} ${meta.ring}`}
        >
          {meta.label}
        </span>
        <span className="flex-shrink-0 text-[11px] text-slate-600">
          {relativeTime(signal.detected_at)}
        </span>
      </div>

      {/* Primary text — human_label if available, else product_url */}
      <div className="mb-2 text-sm font-medium leading-snug text-slate-100">
        {signal.human_label || signal.product_url || "—"}
      </div>

      {/* Product URL as secondary meta when human_label is present */}
      {signal.human_label && signal.product_url && (
        <div className="mb-2 truncate text-[11px] text-slate-600">
          {signal.product_url}
        </div>
      )}

      {/*
        Explanation — diagnostic text, visible in full for all tiers (Lite and Pro).

        Lite boundary: explanation describes what is happening (diagnostic).
        Pro boundary:  human_action below describes what to do (prescriptive).

        Do not truncate or gate this field — it belongs in Lite.
      */}
      {!signal.human_label && signal.explanation && (
        <p className="mb-3 text-[12px] leading-5 text-slate-500">
          {signal.explanation}
        </p>
      )}

      {/* Signal strength bar */}
      <div className="mb-3 space-y-1.5">
        <div className="flex items-center justify-between text-[11px] text-slate-600">
          <span>Signal strength</span>
          <span className={`tabular-nums ${strengthPct >= 70 ? meta.text : ""}`}>
            {strengthPct}%
          </span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.07]">
          <div
            className={`h-full rounded-full transition-all duration-500 ${meta.bar} ${strengthPct >= 50 ? meta.glow : ""}`}
            style={{ width: `${strengthPct}%` }}
          />
        </div>
      </div>

      {/* human_action — prescriptive content, Pro only */}
      {signal.human_action && (
        <ProGate tier={tier} onUpgradeClick={onUpgradeClick} label="recommended action">
          <div className="rounded-xl border border-emerald-400/15 bg-emerald-500/5 px-3 py-2.5">
            <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-emerald-300/80">
              Action
              <span className="ml-2 text-[10px] text-violet-400/70 border border-violet-400/20 px-1.5 py-[1px] rounded align-middle normal-case tracking-normal">PRO</span>
            </div>
            <p className="text-[12px] leading-4 text-slate-200">{signal.human_action}</p>
          </div>
        </ProGate>
      )}
    </div>
  );
}
