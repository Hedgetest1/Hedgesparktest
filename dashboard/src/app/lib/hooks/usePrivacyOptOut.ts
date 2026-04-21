"use client";

/**
 * usePrivacyOptOut — shared hook for the Art. 22 automated-targeting
 * opt-out toggle (GDPR).
 *
 * Manages:
 *   GET  /merchant/privacy/preferences  — read current opt-out state
 *   POST /merchant/object              — opt out of automated targeting
 *   POST /merchant/unobject            — opt back in
 *
 * Privacy endpoints are first-class GDPR surfaces: failures are
 * reported via reportFrontendError at WARNING severity so the
 * self-healing pipeline catches broken consent flows early (Art. 32
 * security by design).
 *
 * Extracted 2026-04-21 (Phase 2) from /app/page.tsx.
 */

import { useCallback, useEffect, useState } from "react";
import { apiClient } from "../api-client";
import { reportFrontendError } from "../error-reporter";

const API_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "";

export type UsePrivacyOptOutResult = {
  optedOut: boolean;
  loading: boolean;
  /** Resolved once after first successful status fetch. */
  resolved: boolean;
  toggle: () => Promise<void>;
};

export function usePrivacyOptOut(
  shop: string | null | undefined,
): UsePrivacyOptOutResult {
  const [optedOut, setOptedOut] = useState(false);
  const [loading, setLoading] = useState(false);
  const [resolved, setResolved] = useState(false);

  useEffect(() => {
    if (!shop) return;
    let active = true;
    apiClient
      .GET("/merchant/privacy/preferences")
      .then(({ data: d }) => {
        const prefs = d as { opt_out_automated_targeting?: boolean } | null;
        if (active && prefs && prefs.opt_out_automated_targeting != null) {
          setOptedOut(!!prefs.opt_out_automated_targeting);
        }
        if (active) setResolved(true);
      })
      .catch((err: unknown) => {
        const e = err as { name?: string; message?: string } | null;
        reportFrontendError({
          component: "privacyPreferences",
          error_type: e?.name ?? "FetchError",
          message:
            e?.message ?? "Failed to fetch /merchant/privacy/preferences",
          severity: "warning",
        });
        if (active) setResolved(true);
      });
    return () => {
      active = false;
    };
  }, [shop]);

  const toggle = useCallback(async () => {
    if (!shop || loading) return;
    setLoading(true);
    const endpoint = optedOut ? "/merchant/unobject" : "/merchant/object";
    try {
      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (res.ok) {
        setOptedOut((prev) => !prev);
      }
    } catch (err: unknown) {
      const e = err as { name?: string; message?: string } | null;
      reportFrontendError({
        component: "privacyToggle",
        error_type: e?.name ?? "ToggleError",
        message: e?.message ?? "Failed to toggle privacy opt-out",
        severity: "warning",
      });
    } finally {
      setLoading(false);
    }
  }, [shop, loading, optedOut]);

  return { optedOut, loading, resolved, toggle };
}
