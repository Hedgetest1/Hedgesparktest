"use client";

/**
 * ProductTour (ζ1) — 4-step onboarding guide for first-time merchants.
 *
 * Zero external dependencies. Zero copy jargon.
 *
 * Anchors to existing components via `data-tour` attribute. The tour
 * dims the rest of the page, highlights the target with a glowing
 * border, and shows a tooltip with "Next →" / "Skip" controls.
 *
 * Shows on first visit only (localStorage flag). Skippable. Keyboard
 * navigable. Mobile-friendly (repositions if target is off-screen).
 *
 * Steps (branded language, no jargon):
 *  1. ROI Hero       — "This is the money we've saved you"
 *  2. Trust Center   — "Let HedgeSpark act on your behalf — safely"
 *  3. Margin Health  — "Your safety cushion, always visible"
 *  4. MTA Compare    — "See which channels actually bring the sale"
 *
 * Each step anchors to data-tour="step-id" on the target element.
 */

import { useEffect, useLayoutEffect, useState } from "react";

const LS_KEY = "hs_product_tour_v1_dismissed";

type Step = {
  id: string;
  title: string;
  body: string;
  placement?: "bottom" | "right" | "left" | "top";
};

const STEPS: Step[] = [
  {
    id: "roi-hero",
    title: "This is the money we've saved you",
    body:
      "The big number at the top is the real cash HedgeSpark kept in your pocket " +
      "over the last 30 days, proven against a control group. Click it anytime " +
      "to see exactly how we measured it.",
    placement: "bottom",
  },
  {
    id: "trust-center",
    title: "Let HedgeSpark act — safely",
    body:
      "Grant us permission to run price tests and nudges automatically, within " +
      "bounds you set. No blind trust — you pick the max discount, the confidence " +
      "threshold, and the panic stop is always one click away.",
    placement: "top",
  },
  {
    id: "margin-health",
    title: "Your safety cushion",
    body:
      "Your profit headroom — how much of every € you keep after costs. " +
      "If you try a discount that would push you into the red, we refuse it. " +
      "Drag the slider inside to test what-if scenarios.",
    placement: "top",
  },
  {
    id: "mta",
    title: "What actually brings the sale",
    body:
      "Every marketing channel judged 5 different ways. Click any row to see " +
      "real customer journeys that went through that channel — a receipt for " +
      "every number on this page.",
    placement: "top",
  },
];

type Rect = { top: number; left: number; width: number; height: number };

function getAnchorRect(id: string): Rect | null {
  const el = document.querySelector<HTMLElement>(`[data-tour="${id}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {
    top: r.top + window.scrollY,
    left: r.left + window.scrollX,
    width: r.width,
    height: r.height,
  };
}

export function ProductTour({ isProUser }: { isProUser: boolean }) {
  const [active, setActive] = useState(false);
  const [stepIdx, setStepIdx] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);

  // Only show once — after first login for Pro merchants
  useEffect(() => {
    if (!isProUser) return;
    try {
      if (localStorage.getItem(LS_KEY) === "1") return;
    } catch {
      return;
    }
    const t = setTimeout(() => setActive(true), 1200);
    return () => clearTimeout(t);
  }, [isProUser]);

  // Update anchor rect whenever step changes or window resizes
  useLayoutEffect(() => {
    if (!active) return;
    const updateRect = () => {
      const r = getAnchorRect(STEPS[stepIdx].id);
      setRect(r);
      if (r) {
        // Scroll target into view smoothly
        window.scrollTo({
          top: Math.max(0, r.top - window.innerHeight / 3),
          behavior: "smooth",
        });
      }
    };
    updateRect();
    window.addEventListener("resize", updateRect);
    window.addEventListener("scroll", updateRect);
    return () => {
      window.removeEventListener("resize", updateRect);
      window.removeEventListener("scroll", updateRect);
    };
  }, [active, stepIdx]);

  // Keyboard navigation
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismiss();
      else if (e.key === "ArrowRight" || e.key === "Enter") next();
      else if (e.key === "ArrowLeft") prev();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, stepIdx]);

  const dismiss = () => {
    try {
      localStorage.setItem(LS_KEY, "1");
    } catch {}
    setActive(false);
  };

  const next = () => {
    if (stepIdx >= STEPS.length - 1) {
      dismiss();
    } else {
      setStepIdx(stepIdx + 1);
    }
  };
  const prev = () => setStepIdx(Math.max(0, stepIdx - 1));

  if (!active) return null;

  const step = STEPS[stepIdx];
  const hasRect = rect !== null;

  // Tooltip position — below the anchor by default; falls back to center
  const tooltipTop = hasRect
    ? Math.min(
        rect!.top + rect!.height + 16,
        window.scrollY + window.innerHeight - 280,
      )
    : window.scrollY + window.innerHeight / 2 - 140;
  const tooltipLeft = hasRect
    ? Math.max(
        24,
        Math.min(rect!.left, window.innerWidth - 420 - 24),
      )
    : window.innerWidth / 2 - 200;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1500,
        pointerEvents: "none",
      }}
    >
      {/* Dimmed overlay with spotlight cut-out */}
      <svg
        width="100%"
        height="100%"
        style={{ position: "absolute", inset: 0, pointerEvents: "auto" }}
        onClick={dismiss}
      >
        <defs>
          <mask id="tour-mask">
            <rect width="100%" height="100%" fill="white" />
            {hasRect && (
              <rect
                x={rect!.left - window.scrollX - 8}
                y={rect!.top - window.scrollY - 8}
                width={rect!.width + 16}
                height={rect!.height + 16}
                rx="16"
                ry="16"
                fill="black"
              />
            )}
          </mask>
        </defs>
        <rect
          width="100%"
          height="100%"
          fill="rgba(6,10,20,0.75)"
          mask="url(#tour-mask)"
        />
      </svg>

      {/* Glowing border around anchor */}
      {hasRect && (
        <div
          style={{
            position: "absolute",
            top: rect!.top - window.scrollY - 8,
            left: rect!.left - window.scrollX - 8,
            width: rect!.width + 16,
            height: rect!.height + 16,
            borderRadius: "16px",
            border: "2px solid #e8a04e",
            boxShadow: "0 0 0 6px rgba(232,160,78,0.2), 0 0 40px rgba(232,160,78,0.35)",
            pointerEvents: "none",
            animation: "tourPulse 2s ease-in-out infinite",
          }}
        />
      )}
      <style>{`
        @keyframes tourPulse {
          0%, 100% { box-shadow: 0 0 0 6px rgba(232,160,78,0.2), 0 0 40px rgba(232,160,78,0.35); }
          50%      { box-shadow: 0 0 0 12px rgba(232,160,78,0.12), 0 0 60px rgba(232,160,78,0.5); }
        }
      `}</style>

      {/* Tooltip */}
      <div
        style={{
          position: "absolute",
          top: tooltipTop - window.scrollY,
          left: tooltipLeft,
          width: "420px",
          maxWidth: "calc(100vw - 48px)",
          background: "linear-gradient(135deg, #0f172a 0%, #1a1006 100%)",
          border: "1px solid rgba(232,160,78,0.35)",
          borderRadius: "16px",
          padding: "20px 22px",
          boxShadow: "0 24px 64px rgba(0,0,0,0.6)",
          pointerEvents: "auto",
          animation: "tourSlide 0.35s cubic-bezier(0.16,1,0.3,1)",
        }}
      >
        <style>{`
          @keyframes tourSlide {
            from { opacity: 0; transform: translateY(8px); }
            to   { opacity: 1; transform: translateY(0); }
          }
        `}</style>

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "4px",
          }}
        >
          <div
            style={{
              color: "#e8a04e",
              fontSize: "11px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.08em",
            }}
          >
            Getting started · {stepIdx + 1} of {STEPS.length}
          </div>
          <button
            onClick={dismiss}
            style={{
              background: "transparent",
              border: "none",
              color: "#94a3b8",
              fontSize: "12px",
              cursor: "pointer",
            }}
          >
            Skip tour
          </button>
        </div>

        <h3
          style={{
            color: "#fff",
            fontSize: "18px",
            fontWeight: 800,
            margin: "4px 0 8px",
            letterSpacing: "-0.01em",
          }}
        >
          {step.title}
        </h3>

        <p
          style={{
            color: "#cbd5e1",
            fontSize: "14px",
            lineHeight: 1.55,
            margin: "0 0 18px",
          }}
        >
          {step.body}
        </p>

        {/* Progress dots */}
        <div style={{ display: "flex", gap: "6px", marginBottom: "16px" }}>
          {STEPS.map((s, i) => (
            <div
              key={s.id}
              style={{
                flex: 1,
                height: "4px",
                borderRadius: "2px",
                background:
                  i <= stepIdx ? "#e8a04e" : "rgba(148,163,184,0.2)",
                transition: "background 0.3s ease",
              }}
            />
          ))}
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", gap: "8px" }}>
          {stepIdx > 0 ? (
            <button
              onClick={prev}
              style={{
                padding: "9px 14px",
                borderRadius: "8px",
                background: "transparent",
                border: "1px solid rgba(148,163,184,0.3)",
                color: "#cbd5e1",
                fontSize: "13px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              ← Back
            </button>
          ) : (
            <div />
          )}
          <button
            onClick={next}
            style={{
              padding: "9px 18px",
              borderRadius: "8px",
              background: "linear-gradient(135deg, #e8a04e 0%, #f59e0b 100%)",
              color: "#0f172a",
              fontSize: "13px",
              fontWeight: 800,
              border: "none",
              cursor: "pointer",
              boxShadow: "0 4px 14px rgba(232,160,78,0.35)",
            }}
          >
            {stepIdx === STEPS.length - 1 ? "Got it — let's go" : "Next →"}
          </button>
        </div>
      </div>
    </div>
  );
}
