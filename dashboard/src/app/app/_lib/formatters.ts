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

/**
 * Compact money formatter — the shared replacement for the dozens of
 * local `fmtEur(n)` helpers scattered across dashboard cards, every
 * one of which hardcoded `€`.
 *
 *   formatMoneyCompact(0)          → "$0"   (default currency = USD)
 *   formatMoneyCompact(0, "EUR")   → "€0"
 *   formatMoneyCompact(1234)       → "$1.2k"
 *   formatMoneyCompact(123456)     → "$123k"
 *   formatMoneyCompact(1_234_567)  → "$1.2M"
 *   formatMoneyCompact(-500, "GBP") → "-£500"
 *
 * The symbol comes from a 24-code ISO 4217 mapping that mirrors the
 * backend `app/core/currency.py` table. Unknown codes render as
 * `CODE amount` (safer than guessing a glyph).
 *
 * Why not just call `Intl.NumberFormat`?
 *   1. Intl produces `€1,234` not `€1.2k` — the k/M compaction is the
 *      entire reason we have local helpers.
 *   2. Intl throws on invalid currency codes — we want a safe fallback.
 *   3. Stable output across browsers/locales — our dashboard is EN-only,
 *      we don't want locale-dependent separators.
 *
 * Match the backend app.core.currency.currency_symbol() table so a
 * merchant sees the same symbol on server-rendered pages and on client
 * dashboards.
 */
const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: "$",
  EUR: "€",
  GBP: "£",
  CAD: "CA$",
  AUD: "A$",
  NZD: "NZ$",
  JPY: "¥",
  CNY: "¥",
  CHF: "CHF ",
  SEK: "kr",
  NOK: "kr",
  DKK: "kr",
  PLN: "zł",
  CZK: "Kč",
  HUF: "Ft",
  BRL: "R$",
  MXN: "MX$",
  INR: "₹",
  SGD: "S$",
  HKD: "HK$",
  KRW: "₩",
  ZAR: "R",
  AED: "د.إ ",
  ILS: "₪",
};

export function currencySymbol(code?: string | null): string {
  const c = (code || "USD").toUpperCase().trim();
  return CURRENCY_SYMBOLS[c] ?? `${c} `;
}

export function formatMoneyCompact(
  value: number | null | undefined,
  currency: string = "USD",
): string {
  const sym = currencySymbol(currency);
  if (value == null || Number.isNaN(value)) return `${sym}0`;
  if (value === 0) return `${sym}0`;
  const neg = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${neg}${sym}${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000)    return `${neg}${sym}${Math.round(abs / 1000)}k`;
  if (abs >= 1_000)     return `${neg}${sym}${(abs / 1000).toFixed(1)}k`;
  return `${neg}${sym}${Math.round(abs).toLocaleString("en")}`;
}
