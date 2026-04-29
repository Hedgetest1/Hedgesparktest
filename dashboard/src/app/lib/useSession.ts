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
 * `?as=lite` preview mode is honored here too: if the URL query
 * contains `as=lite`, the hook forces `tier="lite"` regardless of
 * the real merchant plan. Matches the behavior established in
 * `/app/page.tsx:applyTier` (Phase 1.0-bis).
 */

import { useCallback, useEffect, useState } from "react";
import { apiClient } from "./api-client";
import type { paths } from "./api-types";

type MerchantMePayload =
  paths["/merchant/me"]["get"]["responses"]["200"]["content"]["application/json"];

const API_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "";

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
  return p === "lite";
}

function readRememberedShop(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const local = window.localStorage.getItem("hs_last_shop");
    if (local) return local;
  } catch {
    // localStorage blocked — fall through to cookie hint
  }
  // Parent-domain hint cookie — survives localStorage clears that are
  // subdomain-scoped (common with some tracking-protection extensions)
  // and survives JWT session expiry. Used as a recovery signal only,
  // never trusted for authentication.
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)hs_shop=([^;]+)/);
  if (match) {
    try {
      return decodeURIComponent(match[1]);
    } catch {
      return null;
    }
  }
  return null;
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

    // Single attempt at /merchant/me. Returns the shop payload on
    // success, null on any failure (401, 500, network error, timeout).
    const tryMe = async (): Promise<MerchantMePayload | null> => {
      try {
        const { data } = await apiClient.GET("/merchant/me");
        return (data as MerchantMePayload | undefined) ?? null;
      } catch {
        return null;
      }
    };

    async function load() {
      // Step 1: try the authenticated session endpoint (cookie-based).
      // Retry up to 2 times with increasing delays (1.5s, 3s) to absorb
      // the PM2-mid-restart window (~2-5s typical for wishspark-backend).
      // Without these retries, every backend deploy kicks returning
      // merchants to "Reconnect my store" even though their cookie is
      // still valid.
      const retryDelaysMs = [1500, 3000];
      let data = await tryMe();
      for (const delayMs of retryDelaysMs) {
        if (data != null) break;
        await new Promise((r) => setTimeout(r, delayMs));
        data = await tryMe();
      }
      if (cancelled) return;

      if (data && data.shop_domain) {
        setShop(data.shop_domain);
        try {
          window.localStorage.setItem("hs_last_shop", data.shop_domain);
        } catch {
          // localStorage blocked (private browsing) — session
          // cookie is still valid so we can still show the
          // dashboard. Don't treat this as fatal.
        }
        const isPro = data.plan === "pro" && data.billing_active === true;
        const preview = readPreviewParam();
        setIsPreviewing(preview);
        setTier(preview ? "lite" : isPro ? "pro" : "lite");
        setResolved(true);
        return;
      }

      // Step 2: no valid session cookie. Try the same recovery path
      // /app/page.tsx uses — remembered shop from localStorage →
      // bootstrap via /auth/session (issues a fresh cookie and
      // returns here). This is the behavior that was missing in the
      // initial Phase 1.8.1 useSession and caused intermittent
      // "Reconnect my store" prompts whenever the cookie went cold
      // (e.g., cross-subdomain SameSite edge cases, browser cookie
      // pruning, third-party cookie blockers).
      const remembered = readRememberedShop();
      if (remembered && API_BASE) {
        // Full page navigation — the /auth/session endpoint sets the
        // cookie server-side and redirects back to the dashboard.
        window.location.href =
          `${API_BASE}/auth/session?shop=${encodeURIComponent(remembered)}`;
        // Don't call setResolved(true); the page is about to unload.
        return;
      }

      // Step 3: truly no way to identify the merchant. Render the
      // reconnect UI (`shop === null` in FloorLayout).
      setResolved(true);
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
