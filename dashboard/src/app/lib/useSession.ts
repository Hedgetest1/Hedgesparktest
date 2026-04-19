"use client";

/**
 * useSession — shared auth+plan hook for the `/app/*` Three Floors.
 *
 * Every floor route (Pulse, Intelligence, Operations) needs the same
 * session-resolution logic: check for `hs_session` cookie, fetch
 * /merchant/plan, derive tier. Rather than duplicating 80 lines across
 * four page files, floor pages call `useSession()` and render based
 * on its state.
 *
 * The main /app/page.tsx predates this hook and has inline auth logic
 * for historical reasons — it can be refactored to use this hook in
 * a follow-up commit without changing behavior. Phase 1.8.1 ships the
 * hook as additive.
 *
 * `?as=starter` preview mode is honored here too: if the URL query
 * contains `as=starter` or `as=lite`, the hook forces `tier="lite"`
 * regardless of the real merchant plan. Matches the behavior
 * established in `/app/page.tsx:applyTier` (Phase 1.0-bis).
 */

import { useCallback, useEffect, useState } from "react";
import { apiClient } from "./api-client";

export type Tier = "lite" | "pro";

export type SessionState = {
  shop: string | null;
  tier: Tier;
  isProUser: boolean;
  isPreviewing: boolean;
  resolved: boolean;
  /** Force re-fetch. Useful after billing redirect returns. */
  refresh: () => void;
};

function readPreviewParam(): boolean {
  if (typeof window === "undefined") return false;
  const p = new URLSearchParams(window.location.search).get("as");
  return p === "starter" || p === "lite";
}

export function useSession(): SessionState {
  const [shop, setShop] = useState<string | null>(null);
  const [tier, setTier] = useState<Tier>("lite");
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [resolved, setResolved] = useState(false);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const { data } = await apiClient.GET("/merchant/plan");
        if (cancelled) return;
        if (data && data.shop_domain) {
          setShop(data.shop_domain);
          const isPro =
            data.plan === "pro" && data.billing_active === true;
          const preview = readPreviewParam();
          setIsPreviewing(preview);
          setTier(preview ? "lite" : isPro ? "pro" : "lite");
        }
      } catch {
        // Session fetch failed — treat as unauthenticated. Floor
        // pages render a redirect-to-install prompt when shop is null.
      } finally {
        if (!cancelled) setResolved(true);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [tick]);

  return {
    shop,
    tier,
    isProUser: tier === "pro",
    isPreviewing,
    resolved,
    refresh,
  };
}
