"use client";

import Image from "next/image";

// ---------------------------------------------------------------------------
// Context passed from the dashboard — all optional, degrade gracefully
// ---------------------------------------------------------------------------
export type SparkContext = {
  signalCount?: number;
  highPriorityCount?: number;
  topSignalLabel?: string;
  topSignalProduct?: string;
  topActionImpact?: string;
  topActionIsPattern?: boolean;
  /** Proof loop: has any action shown improvement? */
  hasImproving?: boolean;
  improvingDetail?: string;
  revenue7d?: number;
  orders7d?: number;
  liveVisitorCount?: number;
  hotVisitorCount?: number;
  coldStartPhase?: number;
  isProUser?: boolean;
  hasProof?: boolean;
};

// ---------------------------------------------------------------------------
// Message + target section for navigation
// ---------------------------------------------------------------------------
type SparkMessage = {
  text: string;
  /** Section id to scroll to when clicked (optional) */
  target?: string;
};

// ---------------------------------------------------------------------------
// Spark state derived from context — drives visual indicator
// ---------------------------------------------------------------------------
type SparkState = "loading" | "waiting" | "active" | "idle";

function deriveState(ctx: SparkContext): SparkState {
  if (ctx.coldStartPhase !== undefined && ctx.coldStartPhase === 0) return "loading";
  if (ctx.coldStartPhase !== undefined && ctx.coldStartPhase < 3) return "waiting";
  if ((ctx.signalCount ?? 0) > 0 || (ctx.highPriorityCount ?? 0) > 0) return "active";
  return "idle";
}

const STATE_DOT: Record<SparkState, string> = {
  loading: "bg-indigo-400",
  waiting: "bg-amber-400",
  active:  "bg-emerald-400",
  idle:    "bg-slate-500",
};

const STATE_DOT_GLOW: Record<SparkState, string> = {
  loading: "shadow-[0_0_6px_rgba(99,102,241,0.6)]",
  waiting: "shadow-[0_0_6px_rgba(251,191,36,0.5)]",
  active:  "shadow-[0_0_6px_rgba(52,211,153,0.5)]",
  idle:    "",
};

const STATE_ANIMATE: Record<SparkState, boolean> = {
  loading: true,
  waiting: true,
  active:  false,
  idle:    false,
};

function pickMessage(ctx: SparkContext): SparkMessage {
  const {
    signalCount = 0,
    highPriorityCount = 0,
    topSignalLabel,
    topSignalProduct,
    revenue7d = 0,
    orders7d = 0,
    liveVisitorCount = 0,
    hotVisitorCount = 0,
    coldStartPhase,
    hasProof,
  } = ctx;

  // 1. Cold start
  if (coldStartPhase !== undefined && coldStartPhase < 3) {
    if (coldStartPhase === 0) return { text: "Connecting to your store\u2026" };
    if (coldStartPhase === 1) return { text: "Waiting for your first visitors\u2026" };
    return { text: "Visitors arriving. Building first insights." };
  }

  // 2. Proof loop
  if (ctx.hasImproving && ctx.improvingDetail) {
    return { text: `That's working. ${ctx.improvingDetail}`, target: "what-next" };
  }
  if (hasProof) {
    return { text: "A recent change is showing improvement.", target: "proof" };
  }

  // 3. Pattern-level insight
  if (ctx.topActionIsPattern && topSignalLabel) {
    return { text: `Bigger than one product. ${topSignalLabel}`, target: "what-next" };
  }

  // 4. Action-specific
  if (topSignalLabel && topSignalProduct) {
    return { text: `${topSignalLabel} \u2014 start here.`, target: "what-next" };
  }
  if (highPriorityCount > 0 && topSignalLabel) {
    return {
      text: `${highPriorityCount} action${highPriorityCount !== 1 ? "s" : ""} identified. Top: ${topSignalLabel.toLowerCase()}.`,
      target: "what-next",
    };
  }

  // 5. Signals
  if (signalCount > 0) {
    return {
      text: `${signalCount} signal${signalCount !== 1 ? "s" : ""} detected.`,
      target: "signals",
    };
  }

  // 6. Hot visitors
  if (hotVisitorCount > 0) {
    return {
      text: `${hotVisitorCount} high-intent visitor${hotVisitorCount !== 1 ? "s" : ""} browsing now.`,
      target: "live",
    };
  }

  // 7. Live visitors
  if (liveVisitorCount > 0) {
    return {
      text: `${liveVisitorCount} visitor${liveVisitorCount !== 1 ? "s" : ""} in your store.`,
      target: "live",
    };
  }

  // 8. Revenue
  if (revenue7d > 0 && orders7d > 0) {
    return {
      text: `${orders7d} order${orders7d !== 1 ? "s" : ""} this week. Watching for patterns.`,
      target: "revenue",
    };
  }

  // 9. Idle
  return { text: "All quiet. Watching." };
}

// ---------------------------------------------------------------------------
// Sidebar companion — Spark + state dot + dynamic message
// ---------------------------------------------------------------------------
export function SparkCompanion({
  context,
  onNavigate,
}: {
  context: SparkContext;
  onNavigate?: (section: string) => void;
}) {
  const { text, target } = pickMessage(context);
  const state = deriveState(context);
  const isClickable = !!target && !!onNavigate;

  return (
    <div className="mx-2 mb-2">
      {/* Status line: Spark icon + state dot + message */}
      <div
        className={`flex items-start gap-2.5 rounded-lg border border-white/[0.04] bg-white/[0.02] px-3 py-2.5 transition-colors ${
          isClickable ? "cursor-pointer hover:border-white/[0.08] hover:bg-white/[0.03]" : ""
        }`}
        onClick={isClickable ? () => onNavigate(target) : undefined}
        role={isClickable ? "button" : undefined}
      >
        <div className="relative mt-0.5 flex-shrink-0">
          <Image
            src="/branding/hedgespark/spark.png"
            alt=""
            width={16}
            height={16}
            className="flex-shrink-0"
          />
          <div className={`absolute -bottom-px -right-px h-1.5 w-1.5 rounded-full border border-[#06060e] ${STATE_DOT[state]} ${STATE_DOT_GLOW[state]}`}>
            {STATE_ANIMATE[state] && (
              <div className={`absolute inset-0 rounded-full ${STATE_DOT[state]} animate-ping`} style={{ animationDuration: "2s" }} />
            )}
          </div>
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] leading-[1.5] text-slate-400">{text}</p>
          {isClickable && (
            <span className="text-[10px] text-violet-400/40">View →</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline Spark message — for use inside cards
// ---------------------------------------------------------------------------
export function SparkInline({
  message,
  size = 20,
}: {
  message: string;
  size?: number;
}) {
  return (
    <div className="flex items-start gap-2.5">
      <Image
        src="/branding/hedgespark/spark.png"
        alt=""
        width={size}
        height={size}
        className="mt-0.5 flex-shrink-0"
      />
      <p className="min-w-0 text-[12px] leading-[1.55] text-slate-400">
        {message}
      </p>
    </div>
  );
}
