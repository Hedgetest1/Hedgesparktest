"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import Image from "next/image";
import { reportFrontendError } from "../lib/error-reporter";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";
const AUTO_OPEN_KEY = "hs_spark_chat_auto_opened";
const POLL_MS = 30_000; // 30 seconds — resolutions + proactive
const STORAGE_KEY_PREFIX = "hs_spark_msgs_";
const MAX_STORED_MESSAGES = 80; // cap localStorage to prevent unbounded growth

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  classification?: string;
  severity?: string;
  incident_id?: number | null;
  repair_attempted?: boolean;
  subtle?: boolean;
  proactive?: boolean; // system-initiated check-in message
  timestamp: number;
};

function apiHeaders(): HeadersInit {
  return { "Content-Type": "application/json" };
}

// ---------------------------------------------------------------------------
// Persistence — scoped by shop domain or "disconnected"
// ---------------------------------------------------------------------------

function storageKey(connected: boolean): string {
  // Connected merchants scope by the cookie session (same storage key is fine
  // since they'll always be on the same domain). Disconnected gets its own key.
  return `${STORAGE_KEY_PREFIX}${connected ? "connected" : "disconnected"}`;
}

function saveMessages(messages: ChatMessage[], connected: boolean): void {
  try {
    // Only persist non-welcome, non-subtle messages + last N messages to cap size
    const toStore = messages.slice(-MAX_STORED_MESSAGES);
    localStorage.setItem(storageKey(connected), JSON.stringify(toStore));
  } catch {
    // localStorage full or unavailable — non-critical
  }
}

function loadMessages(connected: boolean): ChatMessage[] | null {
  try {
    const raw = localStorage.getItem(storageKey(connected));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length === 0) return null;
    return parsed as ChatMessage[];
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Welcome messages — only shown on first visit (not restored from storage)
// ---------------------------------------------------------------------------

function buildInitialMessages(connected: boolean, onboardingHint?: string): ChatMessage[] {
  const msgs: ChatMessage[] = [];

  if (connected) {
    msgs.push({
      id: "welcome-1", role: "assistant",
      text: "Thanks for testing HedgeSpark. This matters.",
      timestamp: Date.now() - 5000,
    });
    msgs.push({
      id: "welcome-2", role: "assistant",
      text: "If anything breaks, feels confusing, or could be better, write it here.",
      timestamp: Date.now() - 4000,
    });
    msgs.push({
      id: "welcome-3", role: "assistant",
      text: "Every bug, suggestion, and friction point you report is reviewed by the HedgeSpark development team.",
      timestamp: Date.now() - 3000,
    });
    msgs.push({
      id: "welcome-4", role: "assistant",
      text: "I can also help explain what you\u2019re seeing while you test.",
      timestamp: Date.now() - 2000,
    });
    msgs.push({
      id: "signature", role: "assistant",
      text: "\u2014 HedgeSpark development team",
      subtle: true,
      timestamp: Date.now() - 1000,
    });
  } else {
    msgs.push({
      id: "welcome-1", role: "assistant",
      text: "Hey \u2014 thanks for being part of the HedgeSpark test phase. This really matters.",
      timestamp: Date.now() - 4000,
    });
    msgs.push({
      id: "welcome-2", role: "assistant",
      text: "Every bug you report, every confusing step, every thing that feels off \u2014 you\u2019re directly helping shape this into a top product.",
      timestamp: Date.now() - 3000,
    });
    msgs.push({
      id: "welcome-3", role: "assistant",
      text: "While you set things up, use this chat to tell me anything that doesn\u2019t work, doesn\u2019t make sense, or could be better.",
      timestamp: Date.now() - 2000,
    });
    msgs.push({
      id: "signature", role: "assistant",
      text: "\u2014 HedgeSpark development team",
      subtle: true,
      timestamp: Date.now() - 1000,
    });
  }

  if (onboardingHint) {
    msgs.push({
      id: "onboarding-hint", role: "assistant",
      text: onboardingHint,
      timestamp: Date.now(),
    });
  }

  return msgs;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SupportChat({ connected = true, onboardingHint }: { connected?: boolean; onboardingHint?: string }) {
  const [open, setOpen] = useState(false);

  // Auto-open once per session
  useEffect(() => {
    try {
      if (sessionStorage.getItem(AUTO_OPEN_KEY)) return;
      sessionStorage.setItem(AUTO_OPEN_KEY, "1");
      setOpen(true);
    } catch { /* SSR or sessionStorage unavailable */ }
  }, []);

  // Initialize messages: restore from localStorage or build fresh welcome
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    const stored = loadMessages(connected);
    if (stored && stored.length > 0) return stored;
    return buildInitialMessages(connected, onboardingHint);
  });
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Track delivered resolution + proactive IDs to prevent duplicates
  const deliveredIds = useRef<Set<string>>(new Set(
    // Pre-populate from restored messages to avoid re-injecting
    messages
      .filter((m) => m.id.startsWith("res-") || m.id.startsWith("pro-"))
      .map((m) => m.id)
  ));

  // Scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // Persist messages to localStorage on every change
  useEffect(() => {
    saveMessages(messages, connected);
  }, [messages, connected]);

  // ---------------------------------------------------------------------------
  // Unified polling — resolutions + proactive messages
  // ---------------------------------------------------------------------------
  const poll = useCallback(async () => {
    if (!API_BASE || !connected) return;

    // Poll resolutions
    try {
      const res = await fetch(`${API_BASE}/chat/support/resolutions`, {
        headers: apiHeaders(), credentials: "include", cache: "no-store",
      });
      if (res.ok) {
        const resolutions: { incident_id: number; resolution_summary: string; resolved_at: string | null }[] = await res.json();
        for (const r of resolutions) {
          const key = `res-${r.incident_id}`;
          if (deliveredIds.current.has(key)) continue;
          deliveredIds.current.add(key);

          setMessages((prev) => [...prev, {
            id: key, role: "assistant",
            text: r.resolution_summary,
            incident_id: r.incident_id,
            timestamp: Date.now(),
          }]);

          fetch(`${API_BASE}/chat/support/resolutions/${r.incident_id}/ack`, {
            method: "POST", headers: apiHeaders(), credentials: "include",
          }).catch((err: unknown) => {
            // Ack failure means the resolution will be re-delivered on the
            // next poll cycle — annoying but not broken. Report so a
            // systematically-broken ack endpoint gets noticed.
            const e = err as { name?: string; message?: string } | null;
            reportFrontendError({
              component: "SupportChat.resolutionAck",
              error_type: e?.name ?? "FetchError",
              message: e?.message ?? "Failed to ack support resolution",
              severity: "info",
            });
          });
        }
      }
    } catch { /* silent */ }

    // Poll proactive messages
    try {
      const res = await fetch(`${API_BASE}/chat/support/proactive`, {
        headers: apiHeaders(), credentials: "include", cache: "no-store",
      });
      if (res.ok) {
        const proactives: { id: string; message: string; created_at: string }[] = await res.json();
        for (const p of proactives) {
          const key = `pro-${p.id}`;
          if (deliveredIds.current.has(key)) continue;
          deliveredIds.current.add(key);

          setMessages((prev) => [...prev, {
            id: key, role: "assistant",
            text: p.message,
            proactive: true,
            timestamp: Date.now(),
          }]);

          // Acknowledge so backend won't return it again
          fetch(`${API_BASE}/chat/support/proactive/${p.id}/ack`, {
            method: "POST", headers: apiHeaders(), credentials: "include",
          }).catch((err: unknown) => {
            const e = err as { name?: string; message?: string } | null;
            reportFrontendError({
              component: "SupportChat.proactiveAck",
              error_type: e?.name ?? "FetchError",
              message: e?.message ?? "Failed to ack proactive message",
              severity: "info",
            });
          });
        }
      }
    } catch { /* silent — endpoint may not exist yet */ }
  }, [connected]);

  useEffect(() => {
    if (!connected) return;
    const initial = setTimeout(poll, 3000);
    const interval = setInterval(poll, POLL_MS);
    return () => { clearTimeout(initial); clearInterval(interval); };
  }, [connected, poll]);

  // ---------------------------------------------------------------------------
  // Message sending
  // ---------------------------------------------------------------------------
  async function handleSend() {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`, role: "user", text, timestamp: Date.now(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    if (!connected) {
      setMessages((prev) => [...prev, {
        id: `b-${Date.now()}`, role: "assistant",
        text: "I can\u2019t process messages until your store is connected. Once you complete the Shopify setup, I\u2019ll be fully active here \u2014 diagnostics, repairs, and all.",
        timestamp: Date.now(),
      }]);
      setLoading(false);
      return;
    }

    try {
      const res = await fetch(`${API_BASE}/chat/support`, {
        method: "POST", headers: apiHeaders(), credentials: "include",
        body: JSON.stringify({ message: text }),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      setMessages((prev) => [...prev, {
        id: `b-${Date.now()}`, role: "assistant",
        text: data.message || "Couldn\u2019t process that. Try again.",
        classification: data.classification,
        severity: data.severity,
        incident_id: data.incident_id,
        repair_attempted: data.repair_attempted,
        timestamp: Date.now(),
      }]);
    } catch {
      setMessages((prev) => [...prev, {
        id: `e-${Date.now()}`, role: "assistant",
        text: "Connection issue. Try again in a moment.",
        timestamp: Date.now(),
      }]);
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  const buttonLabel = connected ? "Ask Spark" : "Talk to Spark";
  const headerTitle = connected ? "Ask Spark" : "Spark";
  const headerSub = connected ? "Store help & diagnostics" : "Your test companion";
  const placeholder = connected ? "Ask Spark something..." : "Tell Spark what you need...";

  /* ── Floating button ── */
  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className={`fixed bottom-5 right-5 z-50 flex items-center gap-2.5 rounded-xl shadow-[0_2px_20px_rgba(0,0,0,0.4)] backdrop-blur-sm transition-all duration-200 ${
          connected
            ? "bg-white/[0.06] px-4 py-2.5 text-[13px] font-medium text-slate-300 hover:bg-white/[0.1] hover:text-white"
            : "bg-violet-600/90 px-5 py-3 text-[14px] font-semibold text-white hover:bg-violet-500"
        }`}
      >
        <Image
          src="/branding/hedgespark/spark.png"
          alt=""
          width={connected ? 18 : 22}
          height={connected ? 18 : 22}
          className="flex-shrink-0"
        />
        {buttonLabel}
      </button>
    );
  }

  /* ── Chat panel ── */
  return (
    <div className="fixed bottom-5 right-5 z-50 flex h-[520px] w-[380px] flex-col overflow-hidden rounded-2xl border border-white/[0.08] bg-[#0c0c1a] shadow-2xl shadow-black/60">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-white/[0.06] bg-white/[0.02] px-4 py-3">
        <Image
          src="/branding/hedgespark/spark.png"
          alt=""
          width={24}
          height={24}
          className="flex-shrink-0"
        />
        <div className="flex-1">
          <p className="text-[14px] font-semibold text-white">{headerTitle}</p>
          <p className="text-[10px] text-slate-500">{headerSub}</p>
        </div>
        <button
          onClick={() => setOpen(false)}
          className="rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-white/[0.05] hover:text-slate-300"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            {msg.subtle ? (
              <p className="px-1 text-[11px] italic text-slate-600">{msg.text}</p>
            ) : (
              <div
                className={`max-w-[85%] rounded-xl px-3 py-2 text-[13px] leading-[1.5] ${
                  msg.role === "user"
                    ? "bg-violet-600/30 text-white"
                    : msg.proactive
                      ? "border border-violet-500/20 bg-violet-500/[0.06] text-slate-300"
                      : "border border-white/[0.06] bg-white/[0.03] text-slate-300"
                }`}
              >
                <p className="whitespace-pre-wrap">{msg.text}</p>
                {msg.incident_id && (
                  <p className="mt-1.5 text-[10px] text-violet-400/60">
                    Incident #{msg.incident_id} logged
                  </p>
                )}
                {msg.repair_attempted && (
                  <p className="mt-1 text-[10px] text-emerald-400/60">
                    Auto-repair triggered
                  </p>
                )}
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.03] px-3 py-2">
              <div className="flex gap-1">
                <span className="hs-pulse h-1.5 w-1.5 rounded-full bg-violet-400/50" style={{ animationDelay: "0ms" }} />
                <span className="hs-pulse h-1.5 w-1.5 rounded-full bg-violet-400/50" style={{ animationDelay: "150ms" }} />
                <span className="hs-pulse h-1.5 w-1.5 rounded-full bg-violet-400/50" style={{ animationDelay: "300ms" }} />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-white/[0.06] bg-white/[0.02] px-3 py-3">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={loading}
            className="flex-1 rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-[13px] text-white placeholder-slate-600 outline-none transition-colors focus:border-violet-500/30 disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || loading}
            className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-violet-600/80 text-white transition-all hover:bg-violet-500 disabled:opacity-30"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}
