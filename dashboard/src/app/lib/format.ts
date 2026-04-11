/**
 * Shared formatting utilities for HedgeSpark dashboard.
 *
 * Centralizes all number / currency / URL formatting to avoid
 * duplicate definitions across components.
 */

export function fmtCurrency(value: number, currency: string): string {
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    return `${currency} ${Math.round(value)}`;
  }
}

export function fmtCompact(value: number, currency: string): string {
  try {
    if (value >= 1000) {
      return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
        notation: "compact",
      }).format(value);
    }
    return fmtCurrency(value, currency);
  } catch {
    return `${Math.round(value)}`;
  }
}

export function fmtNumber(value: unknown): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US").format(Math.round(value));
}

export function fmtPct(value: unknown): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

export function fmtDecimal(value: unknown, digits = 1): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function fmtScore(value: unknown): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return Math.round(value).toString();
}

export function shortUrl(url: string): string {
  try {
    const path = new URL(url).pathname.replace(/\/$/, "");
    const parts = path.split("/").filter(Boolean);
    return "/" + parts.slice(-2).join("/");
  } catch {
    return url.length > 48 ? "…" + url.slice(-46) : url;
  }
}

export function shortProduct(url?: string): string {
  if (!url) return "Unknown product";
  if (url.startsWith("/products/")) {
    return url.slice(10).replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }
  try {
    const path = new URL(url).pathname;
    if (path.includes("/products/")) {
      const handle = path.split("/products/")[1]?.replace(/\/$/, "") ?? url;
      return handle.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    }
  } catch { /* use fallback */ }
  return url.length > 35 ? url.slice(0, 33) + "…" : url;
}

export function prettyText(value?: string): string {
  if (!value) return "—";
  return value
    .toLowerCase()
    .split("_")
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(" ");
}
