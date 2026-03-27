"use client";

import { useEffect, useState } from "react";

/**
 * Lightweight data-fetching hook for Hedge Spark API endpoints.
 *
 * Handles: loading state, cleanup on unmount, session credentials, error swallowing.
 * Does NOT handle polling — callers that need polling still use their own useEffect.
 */
export function useApiFetch<T>(
  url: string | null,
): { data: T | null; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!url) {
      setLoading(false);
      return;
    }

    let active = true;
    setLoading(true);

    fetch(url, {
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      cache: "no-store",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => {
        if (active) setData(json);
      })
      .catch(() => {})
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => { active = false; };
  }, [url]);

  return { data, loading };
}
