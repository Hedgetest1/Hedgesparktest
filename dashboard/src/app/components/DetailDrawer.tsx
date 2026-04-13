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

import { useEffect, useRef } from "react";

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

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      aria-label="detail-drawer-overlay"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1100,
        display: "flex",
        justifyContent: "flex-end",
      }}
    >
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "absolute",
          inset: 0,
          background: "rgba(0,0,0,0.65)",
          backdropFilter: "blur(8px)",
        }}
      />

      {/* Panel */}
      <div
        ref={panelRef}
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
          {icon && <div style={{ fontSize: "32px", lineHeight: 1 }}>{icon}</div>}
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2
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
              <div style={{ color: "#94a3b8", fontSize: "13px", marginTop: "4px" }}>
                {subtitle}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
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
          color: "#64748b",
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
                  <span style={{ color: "#64748b", marginLeft: "6px" }}>
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
        color: "#64748b",
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
