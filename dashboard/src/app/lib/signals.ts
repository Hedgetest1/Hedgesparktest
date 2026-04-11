/**
 * Centralized signal type metadata for HedgeSpark.
 *
 * Single source of truth for signal type → color / label mappings.
 * Used by SignalCard, TopSignalCard, BriefHero, and page.tsx.
 */

export type SignalMeta = {
  label: string;
  bg: string;
  text: string;
  ring: string;
  bar: string;
  glow: string;
  border: string;
};

export const SIGNAL_META: Record<string, SignalMeta> = {
  HIGH_TRAFFIC_NO_CART: {
    label: "High Traffic, No Cart",
    bg: "bg-amber-500/15",
    text: "text-amber-300",
    ring: "ring-amber-400/30",
    bar: "bg-amber-400",
    glow: "shadow-[0_0_8px_rgba(251,191,36,0.35)]",
    border: "border-amber-400/20",
  },
  LOW_CONVERSION_ATTENTION: {
    label: "Low Conversion",
    bg: "bg-rose-500/15",
    text: "text-rose-300",
    ring: "ring-rose-400/30",
    bar: "bg-rose-400",
    glow: "shadow-[0_0_8px_rgba(251,113,133,0.35)]",
    border: "border-rose-400/20",
  },
  RETURN_VISITOR_INTEREST: {
    label: "Return Visitor Interest",
    bg: "bg-cyan-500/15",
    text: "text-cyan-300",
    ring: "ring-cyan-400/30",
    bar: "bg-cyan-400",
    glow: "shadow-[0_0_8px_rgba(34,211,238,0.35)]",
    border: "border-cyan-400/20",
  },
  TRAFFIC_SPIKE: {
    label: "Traffic Spike",
    bg: "bg-violet-500/15",
    text: "text-violet-300",
    ring: "ring-violet-400/30",
    bar: "bg-violet-400",
    glow: "shadow-[0_0_8px_rgba(167,139,250,0.35)]",
    border: "border-violet-400/20",
  },
  DEAD_TRAFFIC: {
    label: "Dead Traffic",
    bg: "bg-slate-500/15",
    text: "text-slate-300",
    ring: "ring-slate-400/30",
    bar: "bg-slate-400",
    glow: "shadow-[0_0_8px_rgba(148,163,184,0.25)]",
    border: "border-slate-400/20",
  },
  HIGH_ENGAGEMENT_NO_ACTION: {
    label: "Engaged, Not Buying",
    bg: "bg-emerald-500/15",
    text: "text-emerald-300",
    ring: "ring-emerald-400/30",
    bar: "bg-emerald-400",
    glow: "shadow-[0_0_8px_rgba(52,211,153,0.35)]",
    border: "border-emerald-400/20",
  },
  SCROLL_HIGH_NO_CLICK: {
    label: "Deep Scroll, No Click",
    bg: "bg-sky-500/15",
    text: "text-sky-300",
    ring: "ring-sky-400/30",
    bar: "bg-sky-400",
    glow: "shadow-[0_0_8px_rgba(56,189,248,0.35)]",
    border: "border-sky-400/20",
  },
  HIGH_RETURN_LOW_CONVERSION: {
    label: "Returns Not Converting",
    bg: "bg-orange-500/15",
    text: "text-orange-300",
    ring: "ring-orange-400/30",
    bar: "bg-orange-400",
    glow: "shadow-[0_0_8px_rgba(251,146,60,0.35)]",
    border: "border-orange-400/20",
  },
};

const FALLBACK: SignalMeta = {
  label: "Signal",
  bg: "bg-white/5",
  text: "text-slate-400",
  ring: "ring-white/10",
  bar: "bg-slate-400",
  glow: "",
  border: "border-white/[0.08]",
};

export function getSignalMeta(signalType?: string | null): SignalMeta {
  if (!signalType) return FALLBACK;
  return SIGNAL_META[signalType] ?? FALLBACK;
}

/** Badge class string for signal type pills (used in BriefHero, cards) */
export function signalBadgeClass(signalType?: string | null): string {
  const m = getSignalMeta(signalType);
  return `${m.bg} ${m.text} ring-1 ${m.ring}`;
}
