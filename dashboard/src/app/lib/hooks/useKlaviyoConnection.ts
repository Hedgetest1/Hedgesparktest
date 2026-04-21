"use client";

/**
 * useKlaviyoConnection — shared hook for Klaviyo integration UI.
 *
 * Consumers:
 *   - Main /app/* page (via SettingsSection inline, all tiers except
 *     Lite — temporary duplication to be removed in Phase 2)
 *   - Dedicated /app/settings/klaviyo sub-page (all tiers, primary
 *     entry point via the TopBar gear → hub → sub-page)
 *
 * Extracted 2026-04-21 from /app/page.tsx to stop the 80-LOC
 * duplication that would have shipped with the sub-page migration.
 */

import { useCallback, useEffect, useState } from "react";
import { reportFrontendError } from "../error-reporter";

const API_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "";

// Raw auth-bearing fetch — credentials-included, no-store cache, matches
// what /app/page.tsx apiFetch does. Kept local so the hook has no
// cross-file coupling on an un-exported helper.
function authFetch(url: string, init?: RequestInit): Promise<Response> {
  return fetch(url, {
    ...(init || {}),
    credentials: "include",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
}

function dispatchSessionExpired(): void {
  if (typeof window === "undefined") return;
  try {
    window.dispatchEvent(new CustomEvent("hs:session-expired"));
  } catch {
    /* ignore */
  }
}

export type KlaviyoStatus = {
  status: string;
  has_key: boolean;
  key_hint: string | null;
  last_verified_at: string | null;
  last_error: string | null;
  last_sync_at: string | null;
  last_sync_error: string | null;
};

export type KlaviyoMessage = {
  type: "ok" | "err";
  text: string;
};

export type UseKlaviyoConnectionResult = {
  status: KlaviyoStatus | null;
  isConnected: boolean;
  keyInput: string;
  setKeyInput: (v: string) => void;
  connecting: boolean;
  showReplace: boolean;
  setShowReplace: (v: boolean) => void;
  message: KlaviyoMessage | null;
  setMessage: (m: KlaviyoMessage | null) => void;
  connect: () => Promise<void>;
  disconnect: () => Promise<void>;
};

export function useKlaviyoConnection(
  shop: string | null | undefined,
): UseKlaviyoConnectionResult {
  const [status, setStatus] = useState<KlaviyoStatus | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [showReplace, setShowReplace] = useState(false);
  const [message, setMessage] = useState<KlaviyoMessage | null>(null);

  useEffect(() => {
    if (!shop) return;
    let active = true;
    authFetch(`${API_BASE}/merchant/integrations`)
      .then((r) => {
        if (r.status === 401 || r.status === 403) {
          dispatchSessionExpired();
          return null;
        }
        return r.ok ? r.json() : null;
      })
      .then((d) => {
        if (active && d?.klaviyo) setStatus(d.klaviyo);
      })
      .catch((err: unknown) => {
        const e = err as { name?: string; message?: string } | null;
        reportFrontendError({
          component: "KlaviyoStatus",
          error_type: (e && e.name) || "IntegrationsStatusError",
          message: (e && e.message) || "klaviyo status fetch failed",
          severity: "info",
        });
      });
    return () => {
      active = false;
    };
  }, [shop]);

  const isConnected = status?.status === "connected";

  const connect = useCallback(async () => {
    if (!keyInput.trim()) return;
    setConnecting(true);
    setMessage(null);
    try {
      // Step 1: save key
      const saveRes = await authFetch(
        `${API_BASE}/merchant/integrations/klaviyo`,
        {
          method: "PUT",
          body: JSON.stringify({
            klaviyo_private_key: keyInput.trim(),
          }),
        },
      );
      if (!saveRes.ok) {
        const err = await saveRes
          .json()
          .catch(() => ({ detail: "Save failed" }));
        setMessage({ type: "err", text: err.detail || "Save failed" });
        return;
      }

      // Step 2: auto-verify immediately
      const testRes = await authFetch(
        `${API_BASE}/merchant/integrations/klaviyo/test`,
        { method: "POST" },
      );
      const testData = testRes.ok
        ? await testRes.json().catch(() => ({}))
        : {};

      // Step 3: refresh status
      const sRes = await authFetch(`${API_BASE}/merchant/integrations`);
      const s = sRes.ok ? await sRes.json().catch(() => ({})) : {};
      if (s?.klaviyo) setStatus(s.klaviyo);

      if (testData.status === "connected") {
        setKeyInput("");
        setShowReplace(false);
        setMessage({
          type: "ok",
          text: "Klaviyo connected successfully",
        });
      } else {
        setMessage({
          type: "err",
          text: testData.detail || "Key saved but verification failed",
        });
      }
    } catch {
      setMessage({ type: "err", text: "Network error" });
    } finally {
      setConnecting(false);
    }
  }, [keyInput]);

  const disconnect = useCallback(async () => {
    setMessage(null);
    try {
      const res = await authFetch(
        `${API_BASE}/merchant/integrations/klaviyo`,
        { method: "DELETE" },
      );
      if (res.ok) {
        const data = await res.json();
        setStatus(data);
        setShowReplace(false);
        setMessage({ type: "ok", text: "Klaviyo disconnected" });
      }
    } catch {
      setMessage({ type: "err", text: "Disconnect failed" });
    }
  }, []);

  return {
    status,
    isConnected,
    keyInput,
    setKeyInput,
    connecting,
    showReplace,
    setShowReplace,
    message,
    setMessage,
    connect,
    disconnect,
  };
}
