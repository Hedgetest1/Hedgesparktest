"use client";

/**
 * _CardStates — unified loading / error / empty visuals for Pro cards.
 *
 * Rationale
 * ---------
 * Prior to Phase Ω⁷ hardening, ~18 Pro cards did `fetch(...).catch(() => {})`
 * with no visual fallback. A backend 500 or network hiccup would leave the
 * merchant staring at a permanent loading spinner OR a blank card with zero
 * indication that the feature was broken. Competitors would never ship this.
 *
 * This module exposes four pieces every card should reuse:
 *
 *   <CardSkeleton />  — loading shimmer
 *   <CardError />     — graceful error box with retry button
 *   <CardEmpty />     — "no data yet" with optional ETA / call-to-action
 *   useCardFetch()    — hook that wraps a fetch and exposes {data, state, retry}
 *
 * The aim is that EVERY card renders *something* at all times, and that
 * every failure mode is self-explanatory to the merchant — no silent holes.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { reportFrontendError } from "../lib/error-reporter";

export type CardFetchState = "loading" | "ready" | "empty" | "error";

export function CardSkeleton({ label }: { label?: string }) {
  return (
    <div
      className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5"
      role="status"
      aria-live="polite"
      aria-label={label || "Loading"}
    >
      <div className="h-3 w-44 rounded bg-white/[0.06]" />
      <div className="mt-3 h-20 rounded bg-white/[0.04]" />
      <span className="sr-only">{label || "Loading content"}</span>
    </div>
  );
}

export function CardError({
  message,
  onRetry,
  label,
}: {
  message?: string;
  onRetry?: () => void;
  label?: string;
}) {
  return (
    <div
      className="rounded-2xl border border-rose-400/20 bg-rose-500/[0.04] p-5"
      role="alert"
      aria-label={label || "Card failed to load"}
    >
      <div className="mb-1 flex items-center gap-2">
        <span aria-hidden className="text-rose-300">•</span>
        <div className="text-[11px] font-bold uppercase tracking-[0.16em] text-rose-300">
          Couldn&apos;t load this card
        </div>
      </div>
      <p className="text-[12px] leading-relaxed text-slate-400">
        {message ||
          "We hit a temporary hiccup pulling the data. Your core metrics are safe — this card will recover automatically."}
      </p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-lg border border-rose-300/30 bg-rose-500/10 px-3 py-1.5 text-[11px] font-semibold text-rose-200 transition hover:bg-rose-500/20 focus:outline-none focus:ring-2 focus:ring-rose-300/50"
        >
          Retry now
        </button>
      )}
    </div>
  );
}

export function CardEmpty({
  title,
  body,
  eta,
  accent = "slate",
}: {
  title: string;
  body: string;
  eta?: string;
  accent?: "slate" | "violet" | "amber" | "emerald";
}) {
  const accentClass: Record<string, string> = {
    slate: "border-white/[0.08] bg-white/[0.01] text-slate-500",
    violet: "border-violet-400/15 bg-violet-500/[0.03] text-violet-200/80",
    amber: "border-amber-400/15 bg-amber-500/[0.03] text-amber-200/80",
    emerald: "border-emerald-400/15 bg-emerald-500/[0.03] text-emerald-200/80",
  };
  return (
    <div
      className={`rounded-xl border border-dashed px-4 py-6 text-center ${accentClass[accent]}`}
    >
      <div className="text-[12px] font-semibold text-slate-200">{title}</div>
      <p className="mt-1 text-[11px] leading-relaxed">{body}</p>
      {eta && (
        <div className="mt-2 inline-block rounded-full bg-white/[0.04] px-2 py-0.5 text-[10px] font-semibold text-slate-400">
          {eta}
        </div>
      )}
    </div>
  );
}

/**
 * useCardFetch — typed fetch wrapper with automatic loading/error/empty state.
 *
 * Every failed fetch is reported to the self-healing pipeline via
 * reportFrontendError so the autonomous repair loop sees card-level
 * breakage. Pass `component` to identify the caller in ops_alerts;
 * when omitted we derive it from the URL path tail.
 *
 * Usage:
 *   const { data, state, retry } = useCardFetch<PayloadT>({
 *     url: `${apiBase}/pro/foo`,
 *     enabled: isProUser && !!apiBase,
 *     isEmpty: (d) => !d.entries?.length,
 *     component: "FooCard",
 *   });
 */
export function useCardFetch<T>({
  url,
  enabled,
  isEmpty,
  component,
}: {
  url: string;
  enabled: boolean;
  isEmpty?: (data: T) => boolean;
  component?: string;
}) {
  const [data, setData] = useState<T | null>(null);
  const [state, setState] = useState<CardFetchState>("loading");
  const [attempt, setAttempt] = useState(0);
  const activeRef = useRef(true);

  const retry = useCallback(() => {
    setAttempt((n) => n + 1);
  }, []);

  useEffect(() => {
    activeRef.current = true;
    if (!enabled) {
      setState("empty");
      return () => {
        activeRef.current = false;
      };
    }
    setState("loading");

    // Transient-error retry policy. Founder repeatedly caught red
    // CardError boxes flashing during page load. Two failure modes:
    //   (a) cold-start race: first fetch after backend restart can
    //       take 5-10s; prior 3-retry-4s policy timed out before
    //       the backend was ready.
    //   (b) cached stale bundle: an old useCardFetch without retry
    //       can fail on first request before the new bundle loads.
    //
    // New policy: keep retrying with exponential backoff for up to
    // 30 SECONDS (= ~9 attempts: 0.3 / 1 / 2 / 3 / 5 / 5 / 5 / 5 / 4
    // s = 30s). The card stays in skeleton/loading state the whole
    // time — NEVER flashes red mid-recovery. Only after 30s of
    // continuous failure do we surface CardError.
    //
    // 401 (session-expired) and 403 (tier mismatch) are FINAL —
    // no retry, immediate state="error" (those won't fix themselves).
    const RETRY_DELAYS_MS = [300, 1000, 2000, 3000, 5000, 5000, 5000, 5000, 4000];

    async function attemptFetch(retryIdx: number): Promise<void> {
      try {
        const r = await fetch(url, { credentials: "include" });
        if (r.status === 401) {
          if (typeof window !== "undefined") {
            window.dispatchEvent(new Event("hedgespark:session-expired"));
          }
          throw new Error(`HTTP 401`);
        }
        if (r.status === 403) {
          throw new Error(`HTTP 403`);
        }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = (await r.json()) as T;
        if (!activeRef.current) return;
        setData(j);
        setState(isEmpty && isEmpty(j) ? "empty" : "ready");
      } catch (err) {
        if (!activeRef.current) return;
        const msg = (err as Error)?.message || "";
        // Final-state errors: never retry (permanent), surface
        // immediately so the merchant sees the right state.
        const isFinal = msg.includes("HTTP 401") || msg.includes("HTTP 403");
        if (!isFinal && retryIdx < RETRY_DELAYS_MS.length) {
          // KEEP state="loading" during retries so the card shows
          // skeleton instead of red error. Only flip to "error"
          // after retries are exhausted.
          setTimeout(() => {
            if (activeRef.current) attemptFetch(retryIdx + 1);
          }, RETRY_DELAYS_MS[retryIdx]);
          return;
        }
        // Either final-state or retries exhausted: show the error.
        await onError(err);
      }
    }

    async function onError(err: unknown): Promise<void> {
      if (!activeRef.current) return;
      setState("error");
      const e = err as { name?: string; message?: string } | null;
      const derivedComponent =
        component ||
        (() => {
          try {
            const path = new URL(url, "http://_").pathname;
            return `useCardFetch(${path.split("/").filter(Boolean).slice(-2).join("/")})`;
          } catch {
            return "useCardFetch";
          }
        })();
      reportFrontendError({
        component: derivedComponent,
        error_type: (e && e.name) || "CardFetchError",
        message: (e && e.message) || "card fetch failed",
        severity: "warning",
        extra: { url },
      });
    }

    attemptFetch(0);

    return () => {
      activeRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, enabled, attempt]);

  return { data, state, retry } as const;
}
