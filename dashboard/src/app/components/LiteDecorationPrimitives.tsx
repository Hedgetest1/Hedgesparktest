"use client";

/**
 * LiteDecorationPrimitives — ambient craft layer for /app/lite v5.
 *
 * Spec: /docs/LITE_VISUAL_SPEC_v5.md Addendum 2026-04-21, §B.
 *
 * Six pure-SVG primitives, no data, no logic. Shared by every zone
 * so the decorative language is coherent by construction. Mood:
 * Linear + Stripe Press + Vercel — elegante, calmo, trust. Never
 * urgency. Never cartoon. Never the Spark mascot (excluded by
 * founder on 2026-04-21 for Lite v5).
 *
 * Every primitive is `pointer-events: none` — decoration is
 * decoration, not interaction.
 */

import { CSSProperties, ReactNode } from "react";

// ─────────────────────────────────────────────────────────────────────
// 1. Grain
// ─────────────────────────────────────────────────────────────────────
// Violet-tinted SVG noise, 3% opacity, 180×180 tile. Every card gets
// one as the lowest paint layer — elevates cards from flat-plastic
// to "material". Uses SVG feTurbulence for procedural noise.

const GRAIN_SVG = encodeURIComponent(
  `<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'>
    <filter id='n'>
      <feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' seed='3'/>
      <feColorMatrix values='0 0 0 0 0.655 0 0 0 0 0.545 0 0 0 0 0.98 0 0 0 0.55 0'/>
    </filter>
    <rect width='100%' height='100%' filter='url(#n)' opacity='0.30'/>
  </svg>`,
);

export function Grain({
  opacity = 0.03,
  className = "",
}: {
  opacity?: number;
  className?: string;
}) {
  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute inset-0 ${className}`}
      style={{
        backgroundImage: `url("data:image/svg+xml,${GRAIN_SVG}")`,
        backgroundSize: "180px 180px",
        opacity,
        mixBlendMode: "overlay",
      }}
    />
  );
}

// ─────────────────────────────────────────────────────────────────────
// 2. Constellation
// ─────────────────────────────────────────────────────────────────────
// 5-9 dots + thin lines — asymmetric ornament in the top-right corner
// quadrant of a card. Reads as a signature, not a pattern. Dots
// twinkle sequentially (400ms stagger, 3s cycle). The constellation
// shape is deterministic-random: seeded by the `variant` prop so
// different cards get visually-distinct constellations without being
// actually random at render time (SSR-safe).

const CONSTELLATION_VARIANTS: Record<
  string,
  { points: Array<[number, number]>; edges: Array<[number, number]> }
> = {
  // Coordinates are within a 160×120 box, top-right aligned.
  leo: {
    points: [
      [22, 14],
      [58, 30],
      [94, 22],
      [130, 46],
      [112, 80],
      [70, 86],
      [38, 60],
    ],
    edges: [
      [0, 1],
      [1, 2],
      [2, 3],
      [3, 4],
      [4, 5],
      [5, 6],
      [6, 0],
      [1, 6],
    ],
  },
  lyra: {
    points: [
      [30, 18],
      [80, 10],
      [124, 36],
      [110, 78],
      [58, 94],
      [18, 66],
    ],
    edges: [
      [0, 1],
      [1, 2],
      [2, 3],
      [3, 4],
      [4, 5],
      [5, 0],
    ],
  },
  vega: {
    points: [
      [16, 22],
      [50, 48],
      [90, 20],
      [130, 54],
      [100, 90],
      [56, 96],
      [26, 76],
    ],
    edges: [
      [0, 1],
      [1, 2],
      [2, 3],
      [3, 4],
      [4, 5],
      [5, 6],
      [6, 0],
    ],
  },
  orion: {
    points: [
      [20, 10],
      [72, 30],
      [120, 14],
      [140, 62],
      [88, 78],
      [42, 66],
      [14, 92],
    ],
    edges: [
      [0, 1],
      [1, 2],
      [1, 3],
      [1, 4],
      [4, 5],
      [5, 0],
      [5, 6],
    ],
  },
  corvus: {
    points: [
      [28, 24],
      [66, 16],
      [104, 36],
      [136, 70],
      [92, 84],
      [48, 72],
    ],
    edges: [
      [0, 1],
      [1, 2],
      [2, 3],
      [3, 4],
      [4, 5],
      [5, 0],
    ],
  },
};

export function Constellation({
  variant = "leo",
  color = "#a78bfa",
  opacity = 0.16,
  className = "",
  position = "top-right",
}: {
  variant?: keyof typeof CONSTELLATION_VARIANTS;
  color?: string;
  opacity?: number;
  className?: string;
  position?: "top-right" | "top-left" | "bottom-right" | "bottom-left";
}) {
  const data = CONSTELLATION_VARIANTS[variant] ?? CONSTELLATION_VARIANTS.leo;

  const positionClass =
    position === "top-right"
      ? "top-5 right-5"
      : position === "top-left"
        ? "top-5 left-5"
        : position === "bottom-right"
          ? "bottom-5 right-5"
          : "bottom-5 left-5";

  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute ${positionClass} ${className}`}
      style={{ opacity }}
    >
      <svg
        width={160}
        height={120}
        viewBox="0 0 160 120"
        style={{ overflow: "visible" }}
      >
        {data.edges.map(([a, b], i) => {
          const [x1, y1] = data.points[a];
          const [x2, y2] = data.points[b];
          return (
            <line
              key={`edge-${i}`}
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              stroke={color}
              strokeWidth={0.6}
              strokeOpacity={0.5}
            />
          );
        })}
        {data.points.map(([x, y], i) => (
          <circle
            key={`pt-${i}`}
            cx={x}
            cy={y}
            r={1.6}
            fill={color}
            style={{
              animation: `lite-twinkle 3s ease-in-out ${i * 0.4}s infinite`,
            }}
          />
        ))}
      </svg>
      <style jsx>{`
        @keyframes lite-twinkle {
          0%,
          100% {
            opacity: 0.35;
            transform: scale(1);
          }
          50% {
            opacity: 1;
            transform: scale(1.35);
          }
        }
      `}</style>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 3. AtmosphericWash
// ─────────────────────────────────────────────────────────────────────
// Soft radial gradient emanating from one corner. Creates "room
// atmosphere" without becoming a flashy hero. Shifts corner over 12s
// very slowly — the room breathes. Data never pulses; decoration does.

export function AtmosphericWash({
  color = "#a78bfa",
  corner = "top-right",
  size = 420,
  opacity = 0.08,
  breathing = true,
  className = "",
}: {
  color?: string;
  corner?: "top-right" | "top-left" | "bottom-right" | "bottom-left";
  size?: number;
  opacity?: number;
  breathing?: boolean;
  className?: string;
}) {
  const pos: Record<string, CSSProperties> = {
    "top-right": { top: -size / 3, right: -size / 3 },
    "top-left": { top: -size / 3, left: -size / 3 },
    "bottom-right": { bottom: -size / 3, right: -size / 3 },
    "bottom-left": { bottom: -size / 3, left: -size / 3 },
  };

  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute ${className}`}
      style={{
        ...pos[corner],
        width: size,
        height: size,
        borderRadius: "50%",
        background: color,
        filter: "blur(180px)",
        opacity,
        animation: breathing ? "lite-breathe 12s ease-in-out infinite" : undefined,
      }}
    >
      <style jsx>{`
        @keyframes lite-breathe {
          0%,
          100% {
            transform: scale(1);
            opacity: ${opacity};
          }
          50% {
            transform: scale(1.08);
            opacity: ${opacity * 1.25};
          }
        }
      `}</style>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 4. OrnamentalFlourish
// ─────────────────────────────────────────────────────────────────────
// Thin horizontal SVG — a stroke with 2-3 decorative nodes along it,
// ~240×12px. Editorial gesture under a hero headline. Low opacity,
// never moves, never pulses. Signature decoration.

export function OrnamentalFlourish({
  color = "#a78bfa",
  width = 240,
  opacity = 0.32,
  className = "",
}: {
  color?: string;
  width?: number;
  opacity?: number;
  className?: string;
}) {
  const height = 12;
  const midY = height / 2;
  // Main stroke from 0→width, with three decorative nodes:
  // - small circle at x=0.22*width
  // - diamond at x=0.5*width (center gesture)
  // - small circle at x=0.78*width
  const nodeL = width * 0.22;
  const nodeR = width * 0.78;
  const center = width / 2;

  return (
    <svg
      aria-hidden="true"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={`pointer-events-none ${className}`}
      style={{ opacity, overflow: "visible" }}
    >
      <line
        x1={0}
        y1={midY}
        x2={width}
        y2={midY}
        stroke={color}
        strokeWidth={0.8}
      />
      <circle cx={nodeL} cy={midY} r={2} fill={color} />
      <circle cx={nodeR} cy={midY} r={2} fill={color} />
      {/* Center diamond */}
      <g transform={`rotate(45 ${center} ${midY})`}>
        <rect
          x={center - 3}
          y={midY - 3}
          width={6}
          height={6}
          fill="none"
          stroke={color}
          strokeWidth={0.8}
        />
      </g>
    </svg>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 5. BreathingGroup
// ─────────────────────────────────────────────────────────────────────
// Slow opacity breathing for ornament groups. 6-8s cycle. NEVER
// applied to data (data is stable; decoration breathes).

export function BreathingGroup({
  children,
  period = 7,
  className = "",
  style,
}: {
  children: ReactNode;
  period?: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none ${className}`}
      style={{
        ...style,
        animation: `lite-breath ${period}s ease-in-out infinite`,
      }}
    >
      {children}
      <style jsx>{`
        @keyframes lite-breath {
          0%,
          100% {
            opacity: 0.72;
          }
          50% {
            opacity: 1;
          }
        }
      `}</style>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 6. DropCap
// ─────────────────────────────────────────────────────────────────────
// Typography gesture: serif drop-cap for Zone 1 greeting. Only used
// once per zone, at a narrative opening. The rest of the text stays
// Geist sans — the drop-cap is the editorial gesture, not a style
// shift.

export function DropCap({
  letter,
  className = "",
  style,
}: {
  letter: string;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span
      className={`inline-block ${className}`}
      style={{
        fontFamily: '"Lora", "PT Serif", "Cormorant Garamond", Georgia, serif',
        fontStyle: "italic",
        fontWeight: 500,
        fontSize: "4.5rem",
        lineHeight: 1,
        verticalAlign: "top",
        marginTop: "-0.2rem",
        marginRight: "0.4rem",
        background:
          "linear-gradient(135deg, #c4b5fd 0%, #a78bfa 55%, #faf7f0 100%)",
        WebkitBackgroundClip: "text",
        WebkitTextFillColor: "transparent",
        backgroundClip: "text",
        ...style,
      }}
    >
      {letter}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Standard decoration preset — Card Atmosphere
// ─────────────────────────────────────────────────────────────────────
// Convenience wrapper: puts Grain + AtmosphericWash + (optional)
// Constellation on any card with one line. Children render normally
// above the decoration layer.

export function CardAtmosphere({
  washColor = "#a78bfa",
  washCorner = "top-right",
  constellation,
  grain = true,
  constellationColor = "#a78bfa",
  constellationOpacity = 0.14,
}: {
  washColor?: string;
  washCorner?: "top-right" | "top-left" | "bottom-right" | "bottom-left";
  constellation?: keyof typeof CONSTELLATION_VARIANTS | null;
  grain?: boolean;
  constellationColor?: string;
  constellationOpacity?: number;
}) {
  return (
    <>
      {grain && <Grain opacity={0.035} />}
      <AtmosphericWash
        color={washColor}
        corner={washCorner}
        opacity={0.08}
      />
      {constellation && (
        <Constellation
          variant={constellation}
          color={constellationColor}
          opacity={constellationOpacity}
          position="top-right"
        />
      )}
    </>
  );
}
