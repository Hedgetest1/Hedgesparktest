/**
 * Dashboard formatters — pure, framework-free.
 *
 * Extracted from app/page.tsx as part of the Phase Ω⁶ split (see
 * _components/README.md). Keep this file small and side-effect free.
 */

export function formatNumber(value: unknown): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US").format(Math.round(value));
}

export function formatScore(value: unknown): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return Math.round(value).toString();
}

export function formatDecimal(value: unknown, digits = 1): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function formatPct(value: unknown): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

export function prettyText(value?: string): string {
  if (!value) return "—";
  return value
    .toLowerCase()
    .split("_")
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(" ");
}

export function impactClass(value?: string): string {
  switch ((value || "").toUpperCase()) {
    case "HIGH":
      return "bg-rose-500/15 text-rose-300 ring-1 ring-rose-400/30";
    case "MEDIUM":
      return "bg-amber-500/15 text-amber-300 ring-1 ring-amber-400/30";
    case "LOW":
      return "bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-400/30";
    default:
      return "bg-white/5 text-slate-400 ring-1 ring-white/10";
  }
}

export function intentDotClass(intent?: string): string {
  switch ((intent || "").toUpperCase()) {
    case "HOT":
      return "bg-rose-400 shadow-[0_0_10px_rgba(251,113,133,0.7)]";
    case "WARM":
      return "bg-amber-300 shadow-[0_0_10px_rgba(252,211,77,0.7)]";
    default:
      return "bg-slate-400 shadow-[0_0_10px_rgba(148,163,184,0.5)]";
  }
}
