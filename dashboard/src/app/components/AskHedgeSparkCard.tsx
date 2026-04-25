"use client";

/**
 * AskHedgeSparkCard — Phase Ω knowledge graph NL query.
 *
 * Chat-like input → /pro/kg/query → structured answer. Deterministic
 * intent matching (no LLM cost), supports questions like:
 *   "why did revenue drop today?"
 *   "show me top customers"
 *   "any anomalies?"
 *   "campaign performance"
 *
 * Killer because: every other dashboard makes the merchant click around
 * to find an answer. We let them ask in their own language.
 */

import { useEffect, useRef, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import { t } from "../lib/i18n";

type Answer = {
  intent: string;
  answer: string;
  data?: unknown;
  graph_stats?: { nodes: number; edges: number; node_types: Record<string, number> };
};

type KGStats = {
  shop_domain: string;
  nodes: number;
  edges: number;
  node_types: Record<string, number>;
  edge_types: Record<string, number>;
  built_at: string | null;
};

function formatBuildAge(builtAt: string | null): string {
  if (!builtAt) return "just now";
  const then = Date.parse(builtAt);
  if (Number.isNaN(then)) return "recently";
  const ageMs = Date.now() - then;
  if (ageMs < 0) return "just now";
  const mins = Math.floor(ageMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

const SUGGESTIONS = [
  "Why did revenue drop today?",
  "Show me top customers",
  "Any anomalies?",
  "Campaign performance",
  "Refund summary",
];

export function AskHedgeSparkCard({
  apiBase,
  shop: _shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [voiceSupported, setVoiceSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [kgStats, setKgStats] = useState<KGStats | null>(null);
  const recognitionRef = useRef<any>(null);

  useEffect(() => {
    if (!isProUser) return;
    let cancelled = false;
    (async () => {
      const { data: j, error: err } = await apiClient.GET("/pro/kg/stats");
      if (cancelled || err || !j) return;
      setKgStats(j as unknown as KGStats);
    })();
    return () => {
      cancelled = true;
    };
  }, [isProUser]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) return;
    setVoiceSupported(true);
    const r = new SR();
    r.continuous = false;
    r.interimResults = true;
    r.lang = navigator.language || "en-US";
    r.onresult = (ev: any) => {
      const transcript = Array.from(ev.results)
        .map((res: any) => res[0].transcript)
        .join("");
      setQuestion(transcript);
      if (ev.results[ev.results.length - 1].isFinal) {
        setListening(false);
        // Auto-submit on final result
        setTimeout(() => ask(transcript), 100);
      }
    };
    r.onerror = () => setListening(false);
    r.onend = () => setListening(false);
    recognitionRef.current = r;
    return () => { try { r.stop(); } catch {} };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleVoice = () => {
    const r = recognitionRef.current;
    if (!r) return;
    if (listening) {
      try { r.stop(); } catch {}
      setListening(false);
    } else {
      setQuestion("");
      setAnswer(null);
      setError(null);
      try { r.start(); setListening(true); } catch {}
    }
  };

  const ask = async (q: string) => {
    if (!q.trim() || !apiBase) return;
    setLoading(true);
    setError(null);
    setAnswer(null);
    try {
      const { data: j, error: err } = await apiClient.POST("/pro/kg/query", {
        body: { question: q },
      });
      if (err || !j) throw new Error("query failed");
      setAnswer(j as unknown as Answer);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to ask");
    } finally {
      setLoading(false);
    }
  };

  if (!isProUser) return null;

  return (
    <section
      className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5"
      aria-labelledby="ask-hedge-spark-heading"
      role="region"
    >
      <div className="mb-3">
        <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]" aria-hidden="true">
          {t("ask.eyebrow")}
        </div>
        <h3 id="ask-hedge-spark-heading" className="text-[15px] font-bold text-white">
          {t("ask.title")}
        </h3>
        <p className="mt-1 text-[11px] text-slate-400">
          {t("ask.sub")}
        </p>
        {kgStats && kgStats.nodes > 0 && (
          <div
            className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-[#d4893a]/20 bg-[#d4893a]/[0.06] px-2.5 py-1 text-[10px] font-medium text-[#d4893a]/90"
            title="HedgeSpark builds a live knowledge graph from your store events and uses it to answer your questions with real data."
            role="status"
          >
            <span aria-hidden="true">●</span>
            <span>
              answering from{" "}
              <b className="font-bold text-[#d4893a]">{kgStats.nodes.toLocaleString("en")}</b>{" "}
              entities ·{" "}
              <b className="font-bold text-[#d4893a]">{kgStats.edges.toLocaleString("en")}</b>{" "}
              relationships · refreshed {formatBuildAge(kgStats.built_at)}
            </span>
          </div>
        )}
      </div>

      <div className="flex gap-2">
        <label htmlFor="ask-hs-input" className="sr-only">Ask Hedge Spark a question</label>
        <input
          id="ask-hs-input"
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && ask(question)}
          placeholder={listening ? "Listening…" : t("ask.placeholder")}
          aria-label="Ask a question about your store"
          aria-busy={loading}
          className="min-h-[44px] min-w-0 flex-1 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[16px] sm:text-[13px] text-slate-100 placeholder-slate-500 outline-none focus-visible:border-[#d4893a]/60 focus-visible:ring-2 focus-visible:ring-[#d4893a]/30"
        />
        {voiceSupported && (
          <button
            type="button"
            onClick={toggleVoice}
            title={listening ? "Stop listening" : "Ask with voice"}
            className={`min-h-[44px] min-w-[44px] rounded-lg border px-3 py-2 text-[12px] font-bold transition-all focus-visible:ring-2 focus-visible:ring-[#d4893a]/40 ${
              listening
                ? "border-rose-400/40 bg-rose-500/15 text-rose-300 animate-pulse"
                : "border-white/[0.08] bg-white/[0.04] text-slate-300 hover:border-[#d4893a]/30 hover:text-[#d4893a]"
            }`}
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-14 0m7 7v3m-4 0h8M12 14a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
          </button>
        )}
        <button
          onClick={() => ask(question)}
          disabled={loading || !question.trim()}
          className="min-h-[44px] rounded-lg border border-[#d4893a]/30 bg-[#d4893a]/15 px-4 py-2 text-[12px] font-bold text-[#d4893a] hover:bg-[#d4893a]/25 disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[#d4893a]/40"
        >
          {loading ? "…" : t("ask.button")}
        </button>
      </div>

      {/* Suggestions */}
      {!answer && !loading && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => { setQuestion(s); ask(s); }}
              className="rounded-full border border-white/[0.06] bg-white/[0.02] px-2.5 py-1 text-[11px] text-slate-400 hover:border-white/[0.12] hover:text-slate-200"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="mt-3 rounded-lg border border-rose-400/20 bg-rose-500/[0.05] p-3 text-[12px] text-rose-300">
          {error}
        </div>
      )}

      {answer && (
        <div
          className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.025] p-4"
          role="status"
          aria-live="polite"
        >
          <div className="mb-1 flex items-center gap-2">
            <span className="rounded bg-[#d4893a]/15 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-[#d4893a]">
              {answer.intent.replace(/_/g, " ")}
            </span>
          </div>
          <p className="text-[13px] leading-relaxed text-slate-200">{answer.answer}</p>
          {answer.graph_stats && (
            <div className="mt-3 border-t border-white/[0.05] pt-2 text-[10px] text-slate-400">
              graph: {answer.graph_stats.nodes} entities · {answer.graph_stats.edges} relationships
            </div>
          )}
        </div>
      )}
    </section>
  );
}
