/**
 * sparkVoice.ts — Spark-as-narrator primitives for dashboard surfaces.
 *
 * Frontend mirror of backend/app/services/spark_voice.py. Keep aligned.
 *
 * Consumed by:
 *  - LiteSparkDaily.tsx (Zone 1 greeting, Zone 3 CTA format, Zone 5 memory
 *    rendering helpers)
 *  - [future] Pro dashboard voice-unified cards
 *
 * Coherence rules (HEDGESPARK_MERCHANT_COHERENCE_SPEC.md §1):
 *  - First person singular (Spark narrates)
 *  - Max 12 words per sentence in headlines/CTAs
 *  - Zero jargon (unless glossed in the same element)
 *  - Loss-framing 60% / growth 40%
 *  - Numbers rounded to merchant-relevant precision
 *  - No personality quotes, no emojis outside functional tokens
 */

// ---------------------------------------------------------------------------
// Greetings
// ---------------------------------------------------------------------------

/** Spark time-of-day greeting. 06-11 morning, 12-17 afternoon, else evening. */
export function greetByHour(hour: number, shopDisplayName: string): string {
  let label: string;
  if (hour >= 6 && hour < 12) label = "Good morning";
  else if (hour >= 12 && hour < 18) label = "Hi";
  else label = "Evening";
  return `${label}, ${shopDisplayName}.`;
}

/** Greeting for the overnight-digest email surface. */
export function greetNightShift(shopDisplayName: string): string {
  return `Overnight update, ${shopDisplayName}.`;
}

// ---------------------------------------------------------------------------
// Opening verdict (Zone 1 "Spark Says", second line)
// ---------------------------------------------------------------------------

export interface OpeningVerdictParams {
  totalAtRiskEur: number;
  countPlaces: number;
  preventedEur?: number;
  currencySymbol?: string;
}

/** Three deterministic states: leaking / steady / clean. */
export function openingVerdict(params: OpeningVerdictParams): string {
  const total = Math.round(params.totalAtRiskEur);
  const prevented = Math.round(params.preventedEur ?? 0);
  const sym = params.currencySymbol ?? "€";
  const totalFmt = total.toLocaleString("en-US");
  const preventedFmt = prevented.toLocaleString("en-US");

  if (params.countPlaces >= 1 && total > 0) {
    return `This morning I noticed ${sym}${totalFmt} leaking in ${params.countPlaces} places.`;
  }
  if (total > 0) {
    return `Steady morning — ${sym}${totalFmt} at risk, ${sym}${preventedFmt} prevented.`;
  }
  return "Clean morning — nothing leaking right now.";
}

export interface TopLeakDetailParams {
  topProduct: string | null | undefined;
  views: number | null | undefined;
  carts: number | null | undefined;
}

/** Zone 1 third-line detail, or null if any required field is missing. */
export function topLeakDetail(params: TopLeakDetailParams): string | null {
  if (!params.topProduct || params.views === null || params.views === undefined) {
    return null;
  }
  if (params.carts === null || params.carts === undefined) return null;
  return `The biggest is ${params.topProduct} — ${params.views} views, ${params.carts} carts.`;
}

// ---------------------------------------------------------------------------
// Relative time labels (Zone 5 left column)
// ---------------------------------------------------------------------------

/** Human relative label: `just now` / `Nh ago` / `yesterday` / `N days`. */
export function relativeLabel(eventIso: string, nowIso?: string): string {
  const now = nowIso ? new Date(nowIso) : new Date();
  const evt = new Date(eventIso);
  const deltaSecs = Math.floor((now.getTime() - evt.getTime()) / 1000);
  if (deltaSecs < 0) return "just now";
  const hours = Math.floor(deltaSecs / 3600);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(deltaSecs / 86400);
  if (days === 1) return "yesterday";
  return `${days} days`;
}

// ---------------------------------------------------------------------------
// CTA format (verb + number/product)
// ---------------------------------------------------------------------------

/** Canonical CTA shape: verb + context + arrow. */
export function ctaFormat(verb: string, context: string | number): string {
  return `${verb} ${context} →`;
}

// ---------------------------------------------------------------------------
// Empty / loading / error state phrasing
// ---------------------------------------------------------------------------

export const STATE_PHRASES = {
  /** `Watching… {what} ready in {n} {unit}.` */
  watching: (what: string, n: number, unit: string): string =>
    `Watching… ${what} ready in ${n} ${unit}.`,
  /** `I hit a hiccup loading {what}. Retrying on its own.` */
  hiccup: (what: string): string =>
    `I hit a hiccup loading ${what}. Retrying on its own.`,
  /** `I'm offline — showing your last brief from {timestamp}.` */
  offline: (timestamp: string): string =>
    `I'm offline — showing your last brief from ${timestamp}.`,
  /** `Nothing to show here yet — let's watch together.` */
  noData: (): string => `Nothing to show here yet — let's watch together.`,
} as const;

// ---------------------------------------------------------------------------
// Spark's Memory — event type → dot color name (frontend maps to CSS)
// ---------------------------------------------------------------------------

export const EVENT_DOT_COLORS: Record<string, string> = {
  abandoned_detected: "rose",
  prevention_success: "emerald",
  brief_summary: "amber",
  cohort_milestone: "emerald",
  unusual_pattern: "violet",
  target_hit: "emerald",
  target_missed: "rose",
};

// ---------------------------------------------------------------------------
// Jargon blacklist (for dev-time preview / audit; runtime enforcement in
// backend/scripts/audit_merchant_voice_coherence.py)
// ---------------------------------------------------------------------------

export const JARGON_TOKENS: readonly string[] = Object.freeze([
  "CVR",
  "COGS",
  "CAC",
  "ARPC",
  "MRR",
  "ARR",
  "LTV",
  "AOV",
  "ROAS",
  "attribution window",
  "cohort",
  "p-value",
  "holdout",
  "confidence interval",
]);

// ---------------------------------------------------------------------------
// Shop display-name helper — strips .myshopify.com and title-cases
// ---------------------------------------------------------------------------

/** Turn `acme-store.myshopify.com` into `Acme Store`. */
export function shopDisplayName(shopDomain: string): string {
  const base = shopDomain.replace(/\.myshopify\.com$/i, "");
  return base
    .split(/[-_]/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}
