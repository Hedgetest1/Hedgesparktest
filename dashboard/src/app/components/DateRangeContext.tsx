"use client";

/**
 * DateRangeContext — global date-range state for the dashboard.
 *
 * Born 2026-04-27 from Phase 3B (brutal Lite vs $0-70 audit closure).
 * Single source of truth for the global DateRangePicker; every analytics
 * tile subscribes via useDateRange() and re-fetches when the range
 * changes.
 *
 * Initial-state resolution order:
 *   1. URL params (?range=last_7_days, ?range=custom&start=...&end=...)
 *   2. localStorage["hs_date_range"] (mirror of last selection)
 *   3. default = last_7_days
 *
 * On range change:
 *   - URL updated via replaceState (no history pollution)
 *   - localStorage updated for next-visit persistence
 *   - Tile consumers re-fetch (their useCardFetch is keyed on
 *     queryString, which changes when range changes)
 *
 * The provider lives at the page root above all tile renders. The
 * picker component reads/writes via useDateRange(); tiles only read.
 */

import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from "react";

export type DateRangePreset =
  | "today"
  | "yesterday"
  | "last_7_days"
  | "last_30_days"
  | "mtd"
  | "qtd"
  | "ytd"
  | "custom";

export type DateRange = {
  preset: DateRangePreset;
  start: string; // YYYY-MM-DD (inclusive)
  end: string;   // YYYY-MM-DD (inclusive)
};

export type DateRangeContextValue = {
  range: DateRange;
  setRange: (next: DateRange) => void;
  /**
   * Whether the "compare to previous period" toggle is on. Auto-derives
   * a comparison range of equal length immediately preceding the primary
   * range — the industry-standard semantic (Lifetimely, Northbeam,
   * Triple Whale all do the same).
   */
  compareEnabled: boolean;
  setCompareEnabled: (next: boolean) => void;
  /**
   * Auto-derived comparison bounds. Null when compareEnabled is false.
   * Tile consumers read these to decide whether to render a delta.
   */
  compareStart: string | null;
  compareEnd: string | null;
  /**
   * Query-string fragment to append to backend analytics URLs, e.g.
   * "start_date=2026-04-01&end_date=2026-04-07". When compareEnabled
   * is true, also includes "&compare_start=...&compare_end=...".
   * Empty string when the merchant hasn't set anything yet (provider
   * is uninitialised before client mount). Tiles use this to key their
   * useCardFetch — toggling compare invalidates cache automatically.
   */
  queryString: string;
};

const DateRangeContext = createContext<DateRangeContextValue | null>(null);

const STORAGE_KEY = "hs_date_range";
const COMPARE_STORAGE_KEY = "hs_date_range_compare";
const DEFAULT_PRESET: DateRangePreset = "last_7_days";

// ---------------------------------------------------------------------------
// Date math — preset → concrete YYYY-MM-DD start/end (in browser-local tz)
// ---------------------------------------------------------------------------

function fmtDate(d: Date): string {
  // Use browser-local date components, not UTC, so the merchant's
  // "today" matches what their dashboard claim is.
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}
function startOfQuarter(d: Date): Date {
  const q = Math.floor(d.getMonth() / 3) * 3;
  return new Date(d.getFullYear(), q, 1);
}
function startOfYear(d: Date): Date {
  return new Date(d.getFullYear(), 0, 1);
}
function addDays(d: Date, n: number): Date {
  const out = new Date(d);
  out.setDate(out.getDate() + n);
  return out;
}

function parseLocalDate(yyyymmdd: string): Date {
  const [y, m, d] = yyyymmdd.split("-").map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1);
}

/**
 * Auto-derive comparison bounds = primary range shifted back by its own
 * span. For "last 7 days" (Apr 21–27) the comparison is Apr 14–20.
 * Returns null when bounds are malformed.
 */
export function deriveCompareBounds(
  start: string, end: string
): { compareStart: string; compareEnd: string } | null {
  try {
    const s = parseLocalDate(start);
    const e = parseLocalDate(end);
    if (Number.isNaN(s.getTime()) || Number.isNaN(e.getTime())) return null;
    const spanDays = Math.round((e.getTime() - s.getTime()) / 86_400_000) + 1;
    if (!Number.isFinite(spanDays) || spanDays < 1) return null;
    const compareEnd = addDays(s, -1);
    const compareStart = addDays(compareEnd, -(spanDays - 1));
    return { compareStart: fmtDate(compareStart), compareEnd: fmtDate(compareEnd) };
  } catch {
    return null;
  }
}

export function rangeFromPreset(
  preset: DateRangePreset,
  customStart?: string,
  customEnd?: string,
): DateRange {
  const today = new Date();
  if (preset === "today") {
    const t = fmtDate(today);
    return { preset, start: t, end: t };
  }
  if (preset === "yesterday") {
    const y = fmtDate(addDays(today, -1));
    return { preset, start: y, end: y };
  }
  if (preset === "last_7_days") {
    return {
      preset, start: fmtDate(addDays(today, -6)), end: fmtDate(today),
    };
  }
  if (preset === "last_30_days") {
    return {
      preset, start: fmtDate(addDays(today, -29)), end: fmtDate(today),
    };
  }
  if (preset === "mtd") {
    return {
      preset, start: fmtDate(startOfMonth(today)), end: fmtDate(today),
    };
  }
  if (preset === "qtd") {
    return {
      preset, start: fmtDate(startOfQuarter(today)), end: fmtDate(today),
    };
  }
  if (preset === "ytd") {
    return {
      preset, start: fmtDate(startOfYear(today)), end: fmtDate(today),
    };
  }
  // custom
  return {
    preset: "custom",
    start: customStart ?? fmtDate(addDays(today, -6)),
    end: customEnd ?? fmtDate(today),
  };
}

// ---------------------------------------------------------------------------
// Initial state — URL → localStorage → default
// ---------------------------------------------------------------------------

function readInitialRange(): DateRange {
  if (typeof window === "undefined") {
    return rangeFromPreset(DEFAULT_PRESET);
  }
  const params = new URLSearchParams(window.location.search);
  const presetParam = params.get("range") as DateRangePreset | null;
  if (presetParam === "custom") {
    const s = params.get("start");
    const e = params.get("end");
    if (s && e) return rangeFromPreset("custom", s, e);
  }
  if (presetParam && presetParam !== "custom") {
    return rangeFromPreset(presetParam);
  }
  // localStorage fallback
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored) as DateRange;
      if (parsed.preset === "custom") {
        return rangeFromPreset("custom", parsed.start, parsed.end);
      }
      // Re-compute start/end from preset (survives midnight rollover —
      // a "last_7_days" stored yesterday should be today minus 6, not
      // yesterday minus 6).
      return rangeFromPreset(parsed.preset);
    }
  } catch {
    // localStorage unavailable / malformed JSON — fall through to default
  }
  return rangeFromPreset(DEFAULT_PRESET);
}

function readInitialCompareEnabled(): boolean {
  if (typeof window === "undefined") return false;
  const params = new URLSearchParams(window.location.search);
  const compareParam = params.get("compare");
  if (compareParam === "1") return true;
  if (compareParam === "0") return false;
  try {
    const stored = window.localStorage.getItem(COMPARE_STORAGE_KEY);
    return stored === "1";
  } catch {
    return false;
  }
}

function writePersistedCompare(enabled: boolean) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(COMPARE_STORAGE_KEY, enabled ? "1" : "0");
  } catch {
    // quota exceeded / private mode — silent fail
  }
  try {
    const url = new URL(window.location.href);
    if (enabled) {
      url.searchParams.set("compare", "1");
    } else {
      url.searchParams.delete("compare");
    }
    window.history.replaceState({}, "", url.toString());
  } catch {
    // history API unavailable — silent fail
  }
}

function writePersistedRange(range: DateRange) {
  if (typeof window === "undefined") return;
  // localStorage mirror
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(range));
  } catch {
    // quota exceeded / private mode — silent fail
  }
  // URL sync — replaceState avoids pushing history entries on every
  // preset toggle (would clutter back-button stack).
  try {
    const url = new URL(window.location.href);
    if (range.preset === "custom") {
      url.searchParams.set("range", "custom");
      url.searchParams.set("start", range.start);
      url.searchParams.set("end", range.end);
    } else {
      url.searchParams.set("range", range.preset);
      url.searchParams.delete("start");
      url.searchParams.delete("end");
    }
    window.history.replaceState({}, "", url.toString());
  } catch {
    // history API unavailable (very old browser) — silent fail
  }
}

// ---------------------------------------------------------------------------
// Provider + hook
// ---------------------------------------------------------------------------

export function DateRangeProvider({ children }: { children: React.ReactNode }) {
  // Initial state must be deterministic for SSR to match. The first
  // render uses default; useEffect fixes it from URL/storage on mount.
  const [range, setRangeState] = useState<DateRange>(() =>
    rangeFromPreset(DEFAULT_PRESET)
  );
  const [compareEnabled, setCompareEnabledState] = useState<boolean>(false);

  useEffect(() => {
    setRangeState(readInitialRange());
    setCompareEnabledState(readInitialCompareEnabled());
  }, []);

  const setRange = useCallback((next: DateRange) => {
    setRangeState(next);
    writePersistedRange(next);
  }, []);

  const setCompareEnabled = useCallback((next: boolean) => {
    setCompareEnabledState(next);
    writePersistedCompare(next);
  }, []);

  const compareBounds = useMemo(
    () => (compareEnabled ? deriveCompareBounds(range.start, range.end) : null),
    [compareEnabled, range.start, range.end],
  );

  const queryString = useMemo(() => {
    const base = `start_date=${range.start}&end_date=${range.end}`;
    if (compareBounds) {
      return `${base}&compare_start=${compareBounds.compareStart}&compare_end=${compareBounds.compareEnd}`;
    }
    return base;
  }, [range.start, range.end, compareBounds]);

  const value: DateRangeContextValue = {
    range,
    setRange,
    compareEnabled,
    setCompareEnabled,
    compareStart: compareBounds?.compareStart ?? null,
    compareEnd: compareBounds?.compareEnd ?? null,
    queryString,
  };

  return (
    <DateRangeContext.Provider value={value}>
      {children}
    </DateRangeContext.Provider>
  );
}

export function useDateRange(): DateRangeContextValue {
  const ctx = useContext(DateRangeContext);
  if (!ctx) {
    // Allow tiles to render outside the provider (e.g., Pro pages
    // that haven't been wired yet) by returning a stable empty
    // string — they fall back to legacy `days` window automatically.
    return {
      range: rangeFromPreset(DEFAULT_PRESET),
      setRange: () => {},
      compareEnabled: false,
      setCompareEnabled: () => {},
      compareStart: null,
      compareEnd: null,
      queryString: "",
    };
  }
  return ctx;
}
