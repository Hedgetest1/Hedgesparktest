"use client";

/**
 * useTileFetch — DRY hook for the 11 Lite analytics tiles.
 *
 * Born 2026-04-27 from Phase 3B residual close — the 11 tiles in
 * LiteBaseAnalytics.tsx were each open-coding the same 25-line
 * useState/useEffect/setLoading/setError boilerplate. Each new endpoint
 * also had to remember to thread compareStart/compareEnd through query
 * params + dep array. That's a perfect drift surface.
 *
 * This hook centralizes:
 *   - useDateRange consumption (range + compareEnabled-derived bounds)
 *   - loading / error / data state
 *   - active-flag cleanup against unmount + rapid-range-change races
 *   - tick-based retry handle
 *   - Re-fetch deps: tick, range.start, range.end, compareStart, compareEnd
 *
 * The fetcher receives a typed query-params object (matches the FastAPI
 * dependency shape) and returns the apiClient.GET result. Caller hands
 * back `{data, error}` — same shape openapi-typescript emits.
 *
 * Migration target: every tile in LiteBaseAnalytics.tsx that previously
 * destructured useDateRange + maintained its own state.
 */

import { useEffect, useState } from "react";
import { useDateRange } from "./DateRangeContext";

export type TileFetchQuery = {
  start_date: string;
  end_date: string;
  compare_start?: string;
  compare_end?: string;
};

export type TileFetchResult<T> = {
  data: T | null;
  loading: boolean;
  error: boolean;
  retry: () => void;
  /** The current resolved range — exposed so tiles don't need a second
   *  useDateRange() call just to read the start/end for headers. */
  range: { preset: string; start: string; end: string };
};

export function useTileFetch<T>(
  fetcher: (query: TileFetchQuery) => Promise<{ data?: T; error?: unknown }>,
): TileFetchResult<T> {
  const { range, compareStart, compareEnd } = useDateRange();
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true); setError(false);
    fetcher({
      start_date: range.start,
      end_date: range.end,
      compare_start: compareStart ?? undefined,
      compare_end: compareEnd ?? undefined,
    })
      .then(({ data: j, error: err }) => {
        if (!active) return;
        if (err || !j) setError(true);
        else setData(j);
      })
      .catch(() => { if (active) setError(true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, range.start, range.end, compareStart, compareEnd]);

  return {
    data,
    loading,
    error,
    retry: () => setTick(t => t + 1),
    range,
  };
}
