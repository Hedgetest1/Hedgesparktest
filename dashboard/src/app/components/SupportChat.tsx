"use client";

import { useState, useRef, useEffect } from "react";
import Image from "next/image";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  classification?: string;
  severity?: string;
  incident_id?: number | null;
  repair_attempted?: boolean;
  timestamp: number;
};

function apiHeaders(): HeadersInit {
  return { "Content-Type": "application/json" };
}

export function SupportChat({ onboardingHint }: { onboardingHint?: string }) {
  const [open, setOpen] = useState(false);

  // Build initial messages with optional onboarding context
  const initialMessages: ChatMessage[] = [
    {
      id: "welcome",
      role: "assistant",
      text: "Hi! I\u2019m the Hedge Spark support assistant. I can help with setup, features, signals, billing, or any issues you\u2019re seeing.",
      timestamp: Date.now() - 1000,
    },
  ];
  if (onboardingHint) {
    initialMessages.push({
      id: "onboarding-hint",
      role: "assistant",
      text: onboardingHint,
      timestamp: Date.now(),
    });
  }

  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  async function handleSend() {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: "user",
      text,
      timestamp: Date.now(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/chat/support`, {
        method: "POST",
        headers: apiHeaders(),
        credentials: "include",
        body: JSON.stringify({ message: text }),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const data = await res.json();
      const botMsg: ChatMessage = {
        id: `b-${Date.now()}`,
        role: "assistant",
        text: data.message || "Sorry, I couldn\u2019t process that. Please try again.",
        classification: data.classification,
        severity: data.severity,
        incident_id: data.incident_id,
        repair_attempted: data.repair_attempted,
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, botMsg]);
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          id: `e-${Date.now()}`,
          role: "assistant",
          text: "Something went wrong connecting to support. Please try again in a moment.",
          timestamp: Date.now(),
        },
      ]);
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

  // Floating button
  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 z-50 flex h-12 w-12 items-center justify-center rounded-full border border-violet-500/30 bg-violet-600/90 shadow-lg shadow-violet-500/20 transition-all hover:scale-105 hover:bg-violet-500"
        title="Support Chat"
      >
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-white">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      </button>
    );
  }

  return (
    <div className="fixed bottom-6 right-6 z-50 flex h-[520px] w-[380px] flex-col overflow-hidden rounded-2xl border border-white/[0.08] bg-[#0c0c1a] shadow-2xl shadow-black/60">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-white/[0.06] bg-white/[0.02] px-4 py-3">
        <Image
          src="/branding/hedgespark-mascot.png"
          alt="Spark"
          width={28}
          height={28}
          className="flex-shrink-0"
        />
        <div className="flex-1">
          <p className="text-sm font-medium text-white">Hedge Spark Support</p>
          <p className="text-[10px] text-slate-500">Product help &amp; issue tracking</p>
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
            <div
              className={`max-w-[85%] rounded-xl px-3 py-2 text-[13px] leading-[1.5] ${
                msg.role === "user"
                  ? "bg-violet-600/30 text-white"
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
            placeholder="Describe your issue or question..."
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
