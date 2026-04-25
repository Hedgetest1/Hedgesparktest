"use client";

/**
 * DetailDrawer — side-panel cassetto per drill-down.
 *
 * Pattern: Pulse Store cassettoni → user clicks a card, drawer slides
 * in from the right with deeper explanation, charts, and what the
 * metric actually measures. Closable via ESC, backdrop click, X button.
 *
 * Brand-consistent:
 *   - amber #e8a04e for titles + accents
 *   - rounded-lg corners
 *   - dark gradient backgrounds
 *   - generous padding
 *
 * Accessibility:
 *   - Focus trap via body scroll lock
 *   - ESC key closes
 *   - aria-label on backdrop
 *
 * Copy rule (π4): every drawer includes a "Cosa sto guardando?" block in
 * plain merchant language. No jargon. No "holdout", no "attribution",
 * no "cohort". Just what it means + why it matters.
 */

import { useEffect, useId, useRef } from "react";

export type DrawerChartPoint = {
  label: string;
  value: number;
  subLabel?: string;
};

export function DetailDrawer({
  open,
  onClose,
  title,
  subtitle,
  icon,
  children,
  widthPx = 520,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  icon?: string;
  children: React.ReactNode;
  widthPx?: number;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const subtitleId = useId();

  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key === "Tab" && panelRef.current) {
        const focusables = panelRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        const visible = Array.from(focusables).filter(
          (el) => !el.hasAttribute("disabled") && el.offsetParent !== null,
        );
        if (visible.length === 0) return;
        const first = visible[0];
        const last = visible[visible.length - 1];
        const active = document.activeElement as HTMLElement | null;
        if (e.shiftKey && active === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);

    const focusTimer = window.setTimeout(() => {
      closeButtonRef.current?.focus();
    }, 50);

    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(focusTimer);
      const target = restoreFocusRef.current;
      if (target && typeof target.focus === "function") {
        target.focus();
      }
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={subtitle ? subtitleId : undefined}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1100,
        display: "flex",
        justifyContent: "flex-end",
      }}
    >
      {/* Backdrop */}
      <button
        type="button"
        onClick={onClose}
        aria-label="Close drawer"
        tabIndex={-1}
        style={{
          position: "absolute",
          inset: 0,
          background: "rgba(0,0,0,0.65)",
          backdropFilter: "blur(8px)",
          border: "none",
          cursor: "pointer",
          padding: 0,
        }}
      />

      {/* Panel */}
      <div
        ref={panelRef}
        className="hs-drawer-panel"
        style={{
          position: "relative",
          width: `min(${widthPx}px, 100vw)`,
          height: "100vh",
          background: "linear-gradient(180deg, #0b1220 0%, #0e1726 100%)",
          borderLeft: "1px solid rgba(232,160,78,0.25)",
          boxShadow: "-24px 0 64px rgba(0,0,0,0.6)",
          display: "flex",
          flexDirection: "column",
          animation: "slideInFromRight 0.35s cubic-bezier(0.16,1,0.3,1)",
        }}
      >
        <style>{`
          @keyframes slideInFromRight {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
          }
          @media (max-width: 640px) {
            .hs-drawer-panel {
              width: 100vw !important;
              border-left: none !important;
            }
          }
          @media (prefers-reduced-motion: reduce) {
            .hs-drawer-panel {
              animation: none !important;
            }
          }
        `}</style>

        {/* Header */}
        <div
          style={{
            padding: "24px 28px 20px",
            borderBottom: "1px solid rgba(148,163,184,0.1)",
            display: "flex",
            alignItems: "flex-start",
            gap: "14px",
            flexShrink: 0,
          }}
        >
          {icon && (
            <div aria-hidden="true" style={{ fontSize: "32px", lineHeight: 1 }}>
              {icon}
            </div>
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2
              id={titleId}
              style={{
                color: "#e8a04e",
                fontSize: "22px",
                fontWeight: 800,
                margin: 0,
                letterSpacing: "-0.02em",
              }}
            >
              {title}
            </h2>
            {subtitle && (
              <div
                id={subtitleId}
                style={{ color: "#94a3b8", fontSize: "13px", marginTop: "4px" }}
              >
                {subtitle}
              </div>
            )}
          </div>
          <button
            ref={closeButtonRef}
            onClick={onClose}
            aria-label="Close drawer"
            type="button"
            style={{
              background: "transparent",
              border: "1px solid rgba(148,163,184,0.25)",
              color: "#94a3b8",
              padding: "4px 10px",
              borderRadius: "8px",
              fontSize: "14px",
              cursor: "pointer",
              lineHeight: 1,
              flexShrink: 0,
            }}
          >
            ✕
          </button>
        </div>

        {/* Scrollable body */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "24px 28px 32px",
          }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Shared drawer building blocks — used across cards for consistency
// ============================================================================

/** The "what am I looking at?" block — mandatory on every drawer (π4 copy). */
export function DrawerExplainer({
  title,
  body,
  why,
}: {
  title?: string;
  body: string;
  why?: string;
}) {
  return (
    <div
      style={{
        padding: "16px 18px",
        borderRadius: "12px",
        background: "rgba(232,160,78,0.06)",
        border: "1px solid rgba(232,160,78,0.25)",
        marginBottom: "20px",
      }}
    >
      <div
        style={{
          color: "#e8a04e",
          fontSize: "11px",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          marginBottom: "8px",
        }}
      >
        {title || "What am I looking at?"}
      </div>
      <div style={{ color: "#e2e8f0", fontSize: "14px", lineHeight: 1.6 }}>
        {body}
      </div>
      {why && (
        <div
          style={{
            marginTop: "10px",
            paddingTop: "10px",
            borderTop: "1px solid rgba(232,160,78,0.15)",
            color: "#cbd5e1",
            fontSize: "13px",
            lineHeight: 1.55,
          }}
        >
          <b style={{ color: "#e8a04e" }}>Why it matters: </b>
          {why}
        </div>
      )}
    </div>
  );
}

/** Big stat block — primary number readout */
export function DrawerBigStat({
  label,
  value,
  sublabel,
  color = "#e8a04e",
}: {
  label: string;
  value: string;
  sublabel?: string;
  color?: string;
}) {
  return (
    <div
      style={{
        padding: "20px 22px",
        borderRadius: "14px",
        background: "linear-gradient(135deg, rgba(15,23,42,0.8) 0%, rgba(30,41,59,0.4) 100%)",
        border: `1px solid ${color}33`,
        marginBottom: "16px",
      }}
    >
      <div
        style={{
          color: "#94a3b8",
          fontSize: "11px",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
        }}
      >
        {label}
      </div>
      <div
        style={{
          color,
          fontSize: "32px",
          fontWeight: 800,
          marginTop: "6px",
          fontVariantNumeric: "tabular-nums",
          letterSpacing: "-0.02em",
          lineHeight: 1,
        }}
      >
        {value}
      </div>
      {sublabel && (
        <div style={{ color: "#94a3b8", fontSize: "13px", marginTop: "8px" }}>
          {sublabel}
        </div>
      )}
    </div>
  );
}

/** Simple bar chart — deterministic SVG, no chart library */
export function DrawerBarChart({
  points,
  maxValue,
  color = "#e8a04e",
  unit = "",
}: {
  points: DrawerChartPoint[];
  maxValue?: number;
  color?: string;
  unit?: string;
}) {
  if (!points.length) return null;
  const max = maxValue ?? Math.max(...points.map((p) => p.value));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {points.map((p, i) => {
        const pct = max > 0 ? (p.value / max) * 100 : 0;
        return (
          <div key={i}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: "12px",
                color: "#cbd5e1",
                marginBottom: "4px",
              }}
            >
              <span>
                {p.label}
                {p.subLabel && (
                  <span style={{ color: "#94a3b8", marginLeft: "6px" }}>
                    · {p.subLabel}
                  </span>
                )}
              </span>
              <span
                style={{
                  color: "#e2e8f0",
                  fontVariantNumeric: "tabular-nums",
                  fontWeight: 600,
                }}
              >
                {p.value.toLocaleString("en")}
                {unit}
              </span>
            </div>
            <div
              style={{
                height: "8px",
                borderRadius: "4px",
                background: "rgba(148,163,184,0.1)",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${pct}%`,
                  height: "100%",
                  background: `linear-gradient(90deg, ${color} 0%, ${color}aa 100%)`,
                  borderRadius: "4px",
                  transition: "width 0.4s cubic-bezier(0.16,1,0.3,1)",
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Sparkline — tiny SVG line chart for trends */
export function DrawerSparkline({
  values,
  color = "#e8a04e",
  height = 48,
}: {
  values: number[];
  color?: string;
  height?: number;
}) {
  if (values.length < 2) return null;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;
  const width = 320;
  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * width;
      const y = height - ((v - min) / range) * (height - 6) - 3;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg width={width} height={height} style={{ maxWidth: "100%" }}>
      <defs>
        <linearGradient id={`spark-grad-${color}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <polygon
        points={`0,${height} ${points} ${width},${height}`}
        fill={`url(#spark-grad-${color})`}
      />
    </svg>
  );
}

/** Row-style key-value list for drawer details */
export function DrawerKeyValueList({
  items,
}: {
  items: { label: string; value: string; color?: string }[];
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "2px",
        borderRadius: "10px",
        overflow: "hidden",
        border: "1px solid rgba(148,163,184,0.1)",
      }}
    >
      {items.map((item, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "11px 14px",
            background: i % 2 === 0 ? "rgba(15,23,42,0.55)" : "rgba(30,41,59,0.3)",
            fontSize: "13px",
          }}
        >
          <span style={{ color: "#94a3b8" }}>{item.label}</span>
          <span
            style={{
              color: item.color || "#e2e8f0",
              fontWeight: 600,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {item.value}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Section heading inside a drawer */
export function DrawerSectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        color: "#94a3b8",
        fontSize: "11px",
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.1em",
        marginBottom: "10px",
        marginTop: "22px",
      }}
    >
      {children}
    </div>
  );
}

/**
 * DrawerHowCalculated — the "where this number comes from" block.
 *
 * Merchants don't trust numbers whose math is opaque. This primitive shows
 * the plain-language formula with inputs → output. If the merchant can't
 * read one sentence and understand the calculation, it fails the π4 test.
 */
export function DrawerHowCalculated({
  formula,
  inputs,
  note,
}: {
  formula: string;
  inputs?: { label: string; value: string }[];
  note?: string;
}) {
  return (
    <div
      style={{
        padding: "14px 16px",
        borderRadius: "12px",
        background: "rgba(148,163,184,0.04)",
        border: "1px solid rgba(148,163,184,0.12)",
        marginTop: "16px",
      }}
    >
      <div
        style={{
          color: "#94a3b8",
          fontSize: "11px",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          marginBottom: "8px",
        }}
      >
        How we calculate this
      </div>
      <div style={{ color: "#e2e8f0", fontSize: "13px", lineHeight: 1.6 }}>
        {formula}
      </div>
      {inputs && inputs.length > 0 && (
        <div
          style={{
            marginTop: "10px",
            display: "flex",
            flexDirection: "column",
            gap: "4px",
          }}
        >
          {inputs.map((input, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: "12px",
              }}
            >
              <span style={{ color: "#94a3b8" }}>{input.label}</span>
              <span
                style={{
                  color: "#cbd5e1",
                  fontWeight: 600,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {input.value}
              </span>
            </div>
          ))}
        </div>
      )}
      {note && (
        <div
          style={{
            marginTop: "10px",
            paddingTop: "10px",
            borderTop: "1px solid rgba(148,163,184,0.12)",
            color: "#94a3b8",
            fontSize: "12px",
            lineHeight: 1.5,
          }}
        >
          {note}
        </div>
      )}
    </div>
  );
}

/**
 * DrawerNextAction — structured call-to-action block.
 *
 * Every drawer must answer "so what do I do about it?". This primitive
 * presents one or two concrete next actions with clear labels and
 * optional description. Primary action uses amber accent; secondary is
 * neutral. Buttons are real focusable controls so the focus trap works.
 */
export function DrawerNextAction({
  primary,
  secondary,
  headline,
}: {
  primary: { label: string; onClick: () => void; description?: string };
  secondary?: { label: string; onClick: () => void };
  headline?: string;
}) {
  return (
    <div
      style={{
        marginTop: "20px",
        padding: "16px 18px",
        borderRadius: "12px",
        background: "linear-gradient(135deg, rgba(232,160,78,0.08) 0%, rgba(232,160,78,0.02) 100%)",
        border: "1px solid rgba(232,160,78,0.25)",
      }}
    >
      {headline && (
        <div
          style={{
            color: "#e8a04e",
            fontSize: "11px",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            marginBottom: "10px",
          }}
        >
          {headline}
        </div>
      )}
      {primary.description && (
        <div
          style={{
            color: "#cbd5e1",
            fontSize: "13px",
            lineHeight: 1.55,
            marginBottom: "12px",
          }}
        >
          {primary.description}
        </div>
      )}
      <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={primary.onClick}
          style={{
            background: "linear-gradient(135deg, #e8a04e 0%, #d48a38 100%)",
            color: "#0b1220",
            border: "none",
            padding: "10px 18px",
            borderRadius: "10px",
            fontSize: "13px",
            fontWeight: 700,
            cursor: "pointer",
            boxShadow: "0 4px 12px rgba(232,160,78,0.2)",
          }}
        >
          {primary.label}
        </button>
        {secondary && (
          <button
            type="button"
            onClick={secondary.onClick}
            style={{
              background: "transparent",
              color: "#cbd5e1",
              border: "1px solid rgba(148,163,184,0.3)",
              padding: "10px 18px",
              borderRadius: "10px",
              fontSize: "13px",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            {secondary.label}
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * DrawerPeerComparison — "where you stand against shops like yours".
 *
 * The network context slot. Converts a merchant number into relative
 * position: median, percentile, gap. Honest comparison only — never
 * flattering, never hiding bad news.
 */
export function DrawerPeerComparison({
  yourValue,
  peerMedian,
  unit = "",
  verdict,
  sampleSize,
}: {
  yourValue: number;
  peerMedian: number;
  unit?: string;
  verdict: "above" | "below" | "on_par";
  sampleSize?: number;
}) {
  const delta = peerMedian > 0 ? ((yourValue - peerMedian) / peerMedian) * 100 : 0;
  const verdictColor =
    verdict === "above"
      ? "#34d399"
      : verdict === "below"
      ? "#fb7185"
      : "#94a3b8";
  const verdictLabel =
    verdict === "above"
      ? "Ahead of peers"
      : verdict === "below"
      ? "Behind peers"
      : "On par with peers";
  const max = Math.max(yourValue, peerMedian) || 1;
  const yourPct = (yourValue / max) * 100;
  const peerPct = (peerMedian / max) * 100;

  return (
    <div
      style={{
        marginTop: "20px",
        padding: "16px 18px",
        borderRadius: "12px",
        background: "rgba(139,92,246,0.04)",
        border: "1px solid rgba(139,92,246,0.2)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "12px",
        }}
      >
        <div
          style={{
            color: "#a78bfa",
            fontSize: "11px",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
          }}
        >
          Shops like yours
        </div>
        <div
          style={{
            color: verdictColor,
            fontSize: "11px",
            fontWeight: 700,
          }}
        >
          {verdictLabel}
          {delta !== 0 && ` · ${delta > 0 ? "+" : ""}${delta.toFixed(0)}%`}
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontSize: "12px",
              marginBottom: "4px",
            }}
          >
            <span style={{ color: "#e2e8f0", fontWeight: 600 }}>You</span>
            <span
              style={{
                color: "#e2e8f0",
                fontWeight: 700,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {yourValue.toLocaleString("en")}
              {unit}
            </span>
          </div>
          <div
            style={{
              height: "6px",
              borderRadius: "3px",
              background: "rgba(148,163,184,0.1)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${yourPct}%`,
                height: "100%",
                background: verdictColor,
                transition: "width 0.4s cubic-bezier(0.16,1,0.3,1)",
              }}
            />
          </div>
        </div>
        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontSize: "12px",
              marginBottom: "4px",
            }}
          >
            <span style={{ color: "#94a3b8" }}>Peer median</span>
            <span
              style={{
                color: "#94a3b8",
                fontWeight: 600,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {peerMedian.toLocaleString("en")}
              {unit}
            </span>
          </div>
          <div
            style={{
              height: "6px",
              borderRadius: "3px",
              background: "rgba(148,163,184,0.1)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${peerPct}%`,
                height: "100%",
                background: "rgba(148,163,184,0.5)",
              }}
            />
          </div>
        </div>
      </div>

      {sampleSize && sampleSize > 0 && (
        <div
          style={{
            marginTop: "10px",
            color: "#94a3b8",
            fontSize: "11px",
          }}
        >
          Based on {sampleSize.toLocaleString("en")} anonymized peer stores
        </div>
      )}
    </div>
  );
}
