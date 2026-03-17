"use client";

export type OpportunitySignal = {
  product_url?: string;
  signal_type?: string;
  signal_strength?: number;
  explanation?: string;
  detected_at?: string | null;
};

const SIGNAL_META: Record<
  string,
  { label: string; bg: string; text: string; ring: string; bar: string }
> = {
  HIGH_TRAFFIC_NO_CART: {
    label: "High Traffic, No Cart",
    bg: "bg-amber-500/15",
    text: "text-amber-300",
    ring: "ring-amber-400/30",
    bar: "bg-amber-400",
  },
  LOW_CONVERSION_ATTENTION: {
    label: "Low Conversion",
    bg: "bg-rose-500/15",
    text: "text-rose-300",
    ring: "ring-rose-400/30",
    bar: "bg-rose-400",
  },
  RETURN_VISITOR_INTEREST: {
    label: "Return Visitor Interest",
    bg: "bg-cyan-500/15",
    text: "text-cyan-300",
    ring: "ring-cyan-400/30",
    bar: "bg-cyan-400",
  },
  TRAFFIC_SPIKE: {
    label: "Traffic Spike",
    bg: "bg-violet-500/15",
    text: "text-violet-300",
    ring: "ring-violet-400/30",
    bar: "bg-violet-400",
  },
};

function relativeTime(isoStr?: string | null): string {
  if (!isoStr) return "—";
  const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function SignalCard({ signal }: { signal: OpportunitySignal }) {
  const meta = SIGNAL_META[signal.signal_type || ""] ?? {
    label: signal.signal_type ?? "Unknown Signal",
    bg: "bg-white/5",
    text: "text-slate-300",
    ring: "ring-white/10",
    bar: "bg-slate-500",
  };
  const strength = signal.signal_strength ?? 0;

  return (
    <div className="hs-fade-up group rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4 transition-all duration-150 hover:border-violet-400/25 hover:bg-white/[0.05] hover:shadow-[0_4px_20px_rgba(124,58,237,0.07)]">
      {/* Header row */}
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

      {/* Product URL */}
      <div className="mb-2 truncate text-sm font-medium text-slate-100">
        {signal.product_url || "—"}
      </div>

      {/* Explanation */}
      {signal.explanation && (
        <p className="mb-3 text-[12px] leading-5 text-slate-500">
          {signal.explanation}
        </p>
      )}

      {/* Signal strength bar */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between text-[11px] text-slate-600">
          <span>Signal strength</span>
          <span>{Math.round(strength * 100)}%</span>
        </div>
        <div className="h-1 w-full overflow-hidden rounded-full bg-white/[0.07]">
          <div
            className={`h-full rounded-full ${meta.bar}`}
            style={{ width: `${Math.round(strength * 100)}%` }}
          />
        </div>
      </div>
    </div>
  );
}
