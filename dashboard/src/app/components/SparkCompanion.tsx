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

  // 1. Cold start — store is warming up
  if (coldStartPhase !== undefined && coldStartPhase < 3) {
    if (coldStartPhase === 0) return { text: "Setting up your store connection. I\u2019ll start watching once we\u2019re linked." };
    if (coldStartPhase === 1) return { text: "Tracker is live. Waiting for your first visitors \u2014 shouldn\u2019t be long." };
    return { text: "Visitors are arriving. Building your first insights now." };
  }

  // 2. Proof loop — a specific action is showing improvement
  if (ctx.hasImproving && ctx.improvingDetail) {
    return { text: `That's working. ${ctx.improvingDetail}`, target: "what-next" };
  }

  // 2b. General proof exists
  if (hasProof) {
    return { text: "One of your recent changes is showing improvement \u2014 check the proof.", target: "proof" };
  }

  // 3. Pattern-level insight — store-wide issue detected
  if (ctx.topActionIsPattern && topSignalLabel) {
    return {
      text: `This looks bigger than one product. ${topSignalLabel}`,
      target: "what-next",
    };
  }

  // 4. Action-engine driven — direct, specific
  if (topSignalLabel && topSignalProduct) {
    return {
      text: `${topSignalLabel} \u2014 start here.`,
      target: "what-next",
    };
  }

  if (highPriorityCount > 0 && topSignalLabel) {
    return {
      text: `${highPriorityCount} action${highPriorityCount !== 1 ? "s" : ""} identified. Top: ${topSignalLabel.toLowerCase()}.`,
      target: "what-next",
    };
  }

  // 4. Signals without specific actions
  if (signalCount > 0) {
    return {
      text: `${signalCount} signal${signalCount !== 1 ? "s" : ""} detected \u2014 check what needs attention.`,
      target: "signals",
    };
  }

  // 5. Hot visitors
  if (hotVisitorCount > 0) {
    return {
      text: `${hotVisitorCount} high-intent visitor${hotVisitorCount !== 1 ? "s" : ""} browsing now.`,
      target: "live",
    };
  }

  // 6. Live visitors
  if (liveVisitorCount > 0) {
    return {
      text: `${liveVisitorCount} visitor${liveVisitorCount !== 1 ? "s" : ""} in your store right now.`,
      target: "live",
    };
  }

  // 7. Revenue context
  if (revenue7d > 0 && orders7d > 0) {
    return {
      text: `${orders7d} order${orders7d !== 1 ? "s" : ""} this week. Watching for patterns.`,
      target: "revenue",
    };
  }

  // 8. Fallback
  return { text: "All quiet. I\u2019ll surface anything worth knowing." };
}

// ---------------------------------------------------------------------------
// Sidebar companion — real mascot + contextual clickable message
// ---------------------------------------------------------------------------
export function SparkCompanion({
  context,
  onNavigate,
}: {
  context: SparkContext;
  onNavigate?: (section: string) => void;
}) {
  const { text, target } = pickMessage(context);
  const isClickable = !!target && !!onNavigate;

  return (
    <div className="mx-2 mb-2">
      {/* Mascot + name row */}
      <div className="mb-1.5 flex items-center gap-2 px-1">
        <div className="relative flex-shrink-0">
          <Image
            src="/branding/hedgespark-mascot.png"
            alt="Spark"
            width={28}
            height={28}
            className="flex-shrink-0"
          />
          <span className="hs-sparkle absolute -right-1 -top-0.5 text-[8px] leading-none text-amber-300/70">
            ✦
          </span>
        </div>
        <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-violet-400/50">
          Spark
        </span>
      </div>

      {/* Speech bubble — clickable when target exists */}
      <div
        className={`relative rounded-xl border border-white/[0.06] bg-white/[0.025] px-3 py-2.5 transition-colors ${
          isClickable ? "cursor-pointer hover:border-violet-400/15 hover:bg-white/[0.04]" : ""
        }`}
        onClick={isClickable ? () => onNavigate(target) : undefined}
        role={isClickable ? "button" : undefined}
      >
        {/* Pointer */}
        <div className="absolute -top-[5px] left-[18px] h-2.5 w-2.5 rotate-45 border-l border-t border-white/[0.06] bg-white/[0.025]" />
        <p className="text-[11px] leading-[1.55] text-slate-400">
          {text}
        </p>
        {isClickable && (
          <span className="mt-1 block text-[10px] text-violet-400/50 transition-colors group-hover:text-violet-400/70">
            View →
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline Spark message — for use inside cards (RevenueHero, funnel, etc.)
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
        src="/branding/hedgespark-mascot.png"
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
