"use client";

import { useState } from "react";
import Image from "next/image";

type OpportunitySignal = {
  product_url?: string;
  signal_type?: string;
  signal_strength?: number;
  explanation?: string;
  detected_at?: string | null;
  human_label?: string;
  human_action?: string;
};

const SIGNAL_COLORS: Record<string, { border: string; badge: string; badgeText: string }> = {
  HIGH_TRAFFIC_NO_CART:        { border: "border-amber-400/20",   badge: "bg-amber-500/15 text-amber-300 ring-amber-400/30",   badgeText: "High Traffic, No Cart" },
  LOW_CONVERSION_ATTENTION:    { border: "border-rose-400/20",    badge: "bg-rose-500/15 text-rose-300 ring-rose-400/30",      badgeText: "Low Conversion" },
  HIGH_ENGAGEMENT_NO_ACTION:   { border: "border-emerald-400/20", badge: "bg-emerald-500/15 text-emerald-300 ring-emerald-400/30", badgeText: "Engaged, Not Buying" },
  DEAD_TRAFFIC:                { border: "border-slate-400/20",   badge: "bg-slate-500/15 text-slate-300 ring-slate-400/30",   badgeText: "Dead Traffic" },
  SCROLL_HIGH_NO_CLICK:        { border: "border-sky-400/20",     badge: "bg-sky-500/15 text-sky-300 ring-sky-400/30",         badgeText: "Deep Scroll, No Click" },
  HIGH_RETURN_LOW_CONVERSION:  { border: "border-orange-400/20",  badge: "bg-orange-500/15 text-orange-300 ring-orange-400/30", badgeText: "Returns Not Converting" },
  RETURN_VISITOR_INTEREST:     { border: "border-cyan-400/20",    badge: "bg-cyan-500/15 text-cyan-300 ring-cyan-400/30",      badgeText: "Return Visitor Interest" },
  TRAFFIC_SPIKE:               { border: "border-violet-400/20",  badge: "bg-violet-500/15 text-violet-300 ring-violet-400/30", badgeText: "Traffic Spike" },
};

const FALLBACK_COLORS = { border: "border-white/[0.08]", badge: "bg-white/5 text-slate-300 ring-white/10", badgeText: "Signal" };

function shortProduct(url?: string): string {
  if (!url) return "Unknown product";
  if (url.startsWith("/products/")) {
    return url.slice(10).replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }
  return url.length > 35 ? url.slice(0, 33) + "…" : url;
}

/** Split a human_action string into actionable bullet steps. */
function toSteps(action: string): string[] {
  // Try splitting on sentence boundaries
  const parts = action
    .split(/(?<=\.)\s+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 10);
  if (parts.length >= 2) return parts.slice(0, 4);
  // Fallback: split on " — " or " and "
  const altParts = action
    .split(/\s*(?:—|;)\s*/)
    .map((s) => s.trim())
    .filter((s) => s.length > 10);
  if (altParts.length >= 2) return altParts.slice(0, 4);
  return [action];
}

const LS_KEY = "hs_recent_actions";

export type RecentAction = {
  product: string;
  productUrl: string;
  action: string;
  signalType: string;
  timestamp: number;
};

function saveAction(action: RecentAction) {
  try {
    const raw = localStorage.getItem(LS_KEY);
    const existing: RecentAction[] = raw ? JSON.parse(raw) : [];
    // Deduplicate by productUrl
    const filtered = existing.filter((a) => a.productUrl !== action.productUrl);
    const updated = [action, ...filtered].slice(0, 5);
    localStorage.setItem(LS_KEY, JSON.stringify(updated));
  } catch { /* localStorage unavailable */ }
}

export function loadRecentActions(): RecentAction[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as RecentAction[];
  } catch {
    return [];
  }
}

type Props = {
  signal: OpportunitySignal;
  isProUser: boolean;
  onUpgrade: () => void;
  onViewSignals: () => void;
  onActionDone?: () => void;
};

export function TopSignalCard({ signal, isProUser, onUpgrade, onViewSignals, onActionDone }: Props) {
  const [panelOpen, setPanelOpen] = useState(false);
  const [markedDone, setMarkedDone] = useState(false);
  const [reminded, setReminded] = useState(false);

  const colors = SIGNAL_COLORS[signal.signal_type || ""] ?? FALLBACK_COLORS;
  const productName = shortProduct(signal.product_url);

  // If dismissed, show compact "done" state
  if (markedDone) {
    return (
      <div className="hs-fade-up flex items-center gap-3 rounded-2xl border border-emerald-400/15 bg-emerald-500/[0.04] px-5 py-4">
        <svg className="h-5 w-5 flex-shrink-0 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
        </svg>
        <div className="min-w-0 flex-1">
          <span className="text-[13px] font-medium text-emerald-300">Done — {productName}</span>
          <p className="mt-0.5 text-[11px] text-emerald-400/60">
            We'll measure the impact over the next 7 days.
          </p>
        </div>
      </div>
    );
  }

  // If reminded, show minimized state
  if (reminded) {
    return (
      <div className="hs-fade-up flex items-center gap-3 rounded-2xl border border-white/[0.06] bg-white/[0.02] px-5 py-3">
        <span className="text-[13px] text-slate-400">
          📌 {productName} — you'll see this in your next digest.
        </span>
        <button
          onClick={() => setReminded(false)}
          className="ml-auto flex-shrink-0 text-[11px] text-slate-600 hover:text-slate-400"
        >
          Show again
        </button>
      </div>
    );
  }

  return (
    <div className={`hs-fade-up rounded-2xl border ${colors.border} bg-gradient-to-br from-white/[0.04] to-transparent p-5`}>
      {/* Row 1: Badge + product name */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <span className={`inline-flex rounded-full px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ${colors.badge}`}>
            {colors.badgeText || signal.signal_type}
          </span>
          <h3 className="mt-2 text-[15px] font-semibold text-white leading-snug">
            {productName}
          </h3>
        </div>
        {/* Mascot removed — signal card is data, not Spark commentary */}
      </div>

      {/* Row 2: Problem */}
      <p className="mt-3 text-[13px] leading-[1.55] text-slate-300">
        {signal.human_label || signal.explanation || "A revenue opportunity was detected for this product."}
      </p>

      {/* Row 3: Action area */}
      {isProUser && signal.human_action ? (
        <>
          {/* Collapsed: action preview + "Fix this" button */}
          {!panelOpen && (
            <div className="mt-3 flex items-center gap-3">
              <button
                onClick={() => setPanelOpen(true)}
                className="rounded-lg bg-emerald-500/20 px-4 py-2 text-[12px] font-semibold text-emerald-200 transition hover:bg-emerald-500/30"
              >
                Fix this →
              </button>
              <span className="text-[11px] text-slate-500 truncate">
                {signal.human_action.slice(0, 60)}{signal.human_action.length > 60 ? "…" : ""}
              </span>
            </div>
          )}

          {/* Expanded: full action panel */}
          {panelOpen && (
            <div className="mt-3 rounded-xl border border-emerald-400/15 bg-emerald-500/[0.04] p-4">
              {/* Hedgehog line */}
              <div className="mb-3 flex items-start gap-2.5">
                <Image src="/branding/hedgespark/spark.png" alt="" width={18} height={18} className="mt-0.5 flex-shrink-0 opacity-80" />
                <p className="text-[12px] leading-[1.5] text-emerald-300/80">
                  This is one of the highest-impact fixes right now.
                </p>
              </div>

              {/* Steps */}
              <div className="mb-4">
                <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-emerald-300/60">
                  Steps to fix this
                </div>
                <ul className="space-y-2">
                  {toSteps(signal.human_action).map((step, i) => (
                    <li key={i} className="flex items-start gap-2.5">
                      <span className="mt-[3px] flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-[9px] font-bold text-emerald-300">
                        {i + 1}
                      </span>
                      <span className="text-[13px] leading-[1.5] text-slate-200">
                        {step}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Action buttons */}
              <div className="flex gap-2.5">
                <button
                  onClick={() => {
                    saveAction({
                      product: productName,
                      productUrl: signal.product_url || "",
                      action: signal.human_action || "Action taken",
                      signalType: signal.signal_type || "",
                      timestamp: Date.now(),
                    });
                    setMarkedDone(true);
                    onActionDone?.();
                  }}
                  className="rounded-lg bg-emerald-500/20 px-4 py-2 text-[12px] font-semibold text-emerald-200 transition hover:bg-emerald-500/30"
                >
                  ✓ Mark as done
                </button>
                <button
                  onClick={() => { setPanelOpen(false); setReminded(true); }}
                  className="rounded-lg border border-white/[0.08] px-4 py-2 text-[12px] font-medium text-slate-400 transition hover:border-white/[0.12] hover:text-slate-300"
                >
                  Remind me later
                </button>
              </div>
            </div>
          )}
        </>
      ) : !isProUser ? (
        <button
          onClick={onUpgrade}
          className="mt-3 flex w-full items-center justify-between rounded-xl border border-violet-400/15 bg-violet-500/[0.05] px-4 py-3 text-left transition hover:border-violet-400/25 hover:bg-violet-500/[0.08]"
        >
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-violet-300/70">
              Fix available
            </div>
            <p className="mt-0.5 text-[12px] text-slate-400">
              Upgrade to Pro to see the exact action and track the result.
            </p>
          </div>
          <span className="flex-shrink-0 rounded-full border border-violet-400/25 bg-violet-500/15 px-2.5 py-1 text-[10px] font-semibold text-violet-300">
            Pro →
          </span>
        </button>
      ) : null}

      {/* Row 4: View all signals */}
      {!panelOpen && (
        <div className="mt-3 flex items-center justify-between">
          <button
            onClick={onViewSignals}
            className="text-[12px] font-medium text-violet-300/60 transition hover:text-violet-200"
          >
            View all signals →
          </button>
          {signal.signal_strength != null && (
            <span className="text-[11px] tabular-nums text-slate-600">
              {Math.round(signal.signal_strength * 100)}% strength
            </span>
          )}
        </div>
      )}
    </div>
  );
}
