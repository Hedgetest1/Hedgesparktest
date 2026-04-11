/**
 * currency.ts — Display currency logic for the HedgeSpark dashboard.
 *
 * Central source of truth for everything related to the merchant's chosen
 * display currency preference (USD/EUR toggle in Settings).
 *
 * Design principles
 * -----------------
 * 1. Honest by default: when the native currency of the data differs from
 *    the display choice, amounts are converted with a static FX rate AND
 *    the output is prefixed with "≈" so merchants can tell at a glance
 *    that the number is an approximation.
 *
 * 2. Deterministic: no live FX API calls, no LLM, no runtime fetches.
 *    The FX_RATES table is a static Python dict — when we want fresher
 *    rates, add a daily cron worker that fetches ECB reference rates and
 *    writes to a JSON file this module reads at build time. Never per-request.
 *
 * 3. Safe fallback: the formatter wraps Intl.NumberFormat in try/catch
 *    and falls back to a simple `$X` / `€X` style so it never throws.
 *
 * 4. Scalable: O(1) per format call. No per-merchant caches, no state.
 *    Ready for 10k merchants from day one.
 */

export type DisplayCurrency = "USD" | "EUR";

/**
 * Static FX rates. Update periodically (monthly sweep recommended) or wire
 * to a daily ECB cron worker. Keep this as the ONLY place FX rates live in
 * the frontend — the source of truth.
 *
 * Last updated: 10 April 2026
 */
export const FX_RATES: Record<string, Record<string, number>> = {
  USD: { EUR: 0.92 },
  EUR: { USD: 1.09 },
};

/**
 * Create a reusable money formatter bound to a specific (native, display)
 * currency pair. When native === display, amounts are returned as-is with
 * the correct symbol. When they differ, the formatter converts with the
 * static FX rate and prefixes "≈".
 *
 * Example:
 *   const fmt = createMoneyFormatter("EUR", "USD");
 *   fmt(1000); // → "≈ €920"
 *
 *   const fmt2 = createMoneyFormatter("USD", "USD");
 *   fmt2(1000); // → "$1,000"
 *
 * The returned function signature is `(amount) => string` so it drops in
 * wherever you'd use a basic formatter.
 */
export function createMoneyFormatter(
  displayCurrency: DisplayCurrency = "USD",
  nativeCurrency: string = "USD",
): (value: number | null | undefined) => string {
  const native = (nativeCurrency || "USD").toUpperCase();

  return (value: number | null | undefined): string => {
    if (value == null || value === 0) return "—";

    let amount = value;
    let converted = false;

    // Only convert when BOTH sides are in a currency we recognize.
    // For exotic currencies (GBP, JPY, BRL, ...), render natively without
    // attempting a conversion — we'd rather show the real number in the
    // shop's own currency than a fake approximation.
    if (
      (native === "USD" || native === "EUR") &&
      native !== displayCurrency
    ) {
      const rate = FX_RATES[native]?.[displayCurrency];
      if (rate) {
        amount = value * rate;
        converted = true;
      }
    }

    // The currency we actually render in (display if converted, native otherwise).
    const effectiveCurrency = converted ? displayCurrency : native;

    try {
      const formatted = new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: effectiveCurrency,
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
      }).format(amount);
      return converted ? `≈ ${formatted}` : formatted;
    } catch {
      // Safe fallback when an unknown currency trips Intl.NumberFormat.
      // Cover the common symbols explicitly, default to $.
      const sym =
        effectiveCurrency === "EUR" ? "€" :
        effectiveCurrency === "GBP" ? "£" :
        effectiveCurrency === "JPY" ? "¥" :
        "$";
      return `${converted ? "≈ " : ""}${sym}${Math.round(amount).toLocaleString()}`;
    }
  };
}

/**
 * One-shot formatter — convenient when you don't need to reuse a bound
 * formatter. Semantically equivalent to:
 *
 *   createMoneyFormatter(display, native)(amount)
 *
 * Use `createMoneyFormatter` when rendering many amounts in a loop (faster).
 * Use `formatDisplayMoney` for one-off calls.
 */
export function formatDisplayMoney(
  amount: number | null | undefined,
  nativeCurrency: string = "USD",
  displayCurrency: DisplayCurrency = "USD",
): string {
  return createMoneyFormatter(displayCurrency, nativeCurrency)(amount);
}

/**
 * localStorage key used to persist the merchant's display preference.
 * Exported so any component can subscribe / reset it if needed.
 */
export const DISPLAY_CURRENCY_STORAGE_KEY = "hs_display_currency";

/**
 * Read the saved display currency from localStorage. Returns the fallback
 * when localStorage is unavailable (SSR) or contains an invalid value.
 */
export function readSavedDisplayCurrency(fallback: DisplayCurrency = "USD"): DisplayCurrency {
  if (typeof window === "undefined") return fallback;
  try {
    const saved = window.localStorage.getItem(DISPLAY_CURRENCY_STORAGE_KEY);
    if (saved === "USD" || saved === "EUR") return saved;
  } catch {
    /* localStorage disabled or blocked — fall through to default */
  }
  return fallback;
}

/**
 * Persist the display currency choice to localStorage. Safe for SSR.
 */
export function writeSavedDisplayCurrency(currency: DisplayCurrency): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(DISPLAY_CURRENCY_STORAGE_KEY, currency);
  } catch {
    /* localStorage disabled — silently fail, UI still works in-memory */
  }
}
