"use client";

/**
 * AnalyticsAssistant — Spark's AI analytics chat.
 *
 * Strada 4 dominance move (2026-04-20). Closes the Triple-Whale-Moby
 * gap at the €39 tier. Merchant asks questions in plain English;
 * Spark pulls numbers from existing deterministic services (RARS,
 * Brief, Benchmarks, Cohorts, Attribution, P&L) and composes a
 * grounded narrative answer via LLM. Every cited number comes from
 * a real service call — the LLM only composes the prose.
 *
 * UX: inline chat with 3 suggested-question chips on first load,
 * followup chips after each answer. Typing own question supported.
 * Keeps the last exchange visible — not a full thread (we don't
 * need one for a lean assistant).
 *
 * Graceful degradation: if the backend reports `degraded=true`
 * (LLM budget exhausted / provider down), the answer shows a small
 * indicator so the merchant knows they got the deterministic fallback
 * rather than the full LLM-composed narrative.
 */

import { useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import type { components } from "../lib/api-types";

type AnalyticsAskResponse = components["schemas"]["AnalyticsAskResponse"];

const INITIAL_SUGGESTIONS = [
  "What's my biggest revenue leak right now?",
  "How do I compare to peers in my band?",
  "Which product drives the highest LTV?",
];

export function AnalyticsAssistant() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<AnalyticsAskResponse | null>(null);
  const [lastAsked, setLastAsked] = useState<string>("");

  const ask = async (q: string) => {
    const trimmed = q.trim();
    if (!trimmed || loading) return;
    setLoading(true);
    setLastAsked(trimmed);
    setQuestion("");
    try {
      const { data, error } = await apiClient.POST("/chat/analytics", {
        body: { question: trimmed },
      });
      if (error || !data) {
        setResponse({
          answer:
            "Couldn't reach Spark right now. Your data is safe — try again in a moment.",
          data_sources: [],
          suggested_followups: INITIAL_SUGGESTIONS,
          degraded: true,
        });
      } else {
        setResponse(data as AnalyticsAskResponse);
      }
    } catch {
      setResponse({
        answer: "Couldn't reach Spark right now. Try again in a moment.",
        data_sources: [],
        suggested_followups: INITIAL_SUGGESTIONS,
        degraded: true,
      });
    } finally {
      setLoading(false);
    }
  };

  const suggestions = (response?.suggested_followups?.length ?? 0) > 0
    ? (response?.suggested_followups ?? INITIAL_SUGGESTIONS)
    : INITIAL_SUGGESTIONS;

  return (
    <section
      aria-labelledby="lite-assistant-heading"
      className="relative mb-8 overflow-hidden rounded-3xl border border-[#c026d3]/[0.18] bg-gradient-to-br from-[#140a1a] via-[#0a0a14] to-[#0b0c18] p-7 sm:p-9"
    >
      <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-[#7c3aed] via-[#c026d3] to-[#e8a04e] opacity-60" />
      <div className="pointer-events-none absolute -right-32 -top-32 h-[340px] w-[340px] rounded-full bg-[#c026d3]/[0.05] blur-[150px]" />

      <div className="relative">
        <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-[#e8a04e]">
              Ask Spark
            </div>
            <h2
              id="lite-assistant-heading"
              className="mt-2 text-[1.5rem] font-extrabold leading-[1.08] tracking-tight text-white sm:text-[1.75rem]"
            >
              Your analytics assistant, answering in plain English
            </h2>
            <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-slate-400">
              Ask about anything on your dashboard — revenue leaks,
              retention, peer position, product LTV, attribution.
              Spark composes answers grounded in the same numbers the
              surfaces above show. Never invents data; if a question
              needs data we don&apos;t have yet, Spark says so.
            </p>
          </div>
        </div>

        {/* Input */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(question);
          }}
          className="flex flex-col gap-3 sm:flex-row"
        >
          <input
            type="text"
            placeholder="Ask Spark about your store..."
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            disabled={loading}
            maxLength={500}
            className="flex-1 rounded-lg border border-white/[0.1] bg-white/[0.03] px-4 py-3 text-[14px] text-white placeholder:text-slate-600 focus:border-[#c026d3]/50 focus:outline-none"
            aria-label="Your question"
          />
          <button
            type="submit"
            disabled={loading || !question.trim()}
            className="flex-shrink-0 rounded-lg bg-gradient-to-br from-[#7c3aed] via-[#c026d3] to-[#e8a04e] px-6 py-3 text-[13px] font-bold text-white transition-all hover:opacity-90 disabled:opacity-60"
          >
            {loading ? "Thinking…" : "Ask"}
          </button>
        </form>

        {/* Suggestions — initial or followups */}
        <div className="mt-4 flex flex-wrap gap-2">
          {suggestions.slice(0, 3).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => ask(s)}
              disabled={loading}
              className="rounded-full border border-white/[0.08] bg-white/[0.02] px-3.5 py-1.5 text-[12px] text-slate-300 transition-colors hover:border-white/[0.2] hover:bg-white/[0.05] disabled:opacity-60"
            >
              {s}
            </button>
          ))}
        </div>

        {/* Response */}
        {lastAsked && (
          <div className="mt-6 rounded-2xl border border-white/[0.06] bg-[#0b0b14]/70 p-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-500">
                You asked
              </div>
              {response?.degraded && (
                <div className="flex items-center gap-1.5 text-[10.5px] font-bold uppercase tracking-wider text-amber-400">
                  <span
                    className="inline-block h-1.5 w-1.5 rounded-full bg-amber-400"
                    aria-hidden="true"
                  />
                  Data-only (LLM unavailable)
                </div>
              )}
            </div>
            <p className="mb-5 text-[14px] leading-relaxed text-slate-200 italic">
              &ldquo;{lastAsked}&rdquo;
            </p>

            {loading ? (
              <div className="flex items-center gap-2 text-[13px] text-slate-400">
                <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[#c026d3]" />
                Spark is pulling the numbers from your shop…
              </div>
            ) : response ? (
              <>
                <div className="mb-4 whitespace-pre-line text-[14.5px] leading-relaxed text-slate-200">
                  {response.answer}
                </div>
                {(response.data_sources?.length ?? 0) > 0 && (
                  <div className="flex flex-wrap items-center gap-2 border-t border-white/[0.04] pt-3">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
                      Based on
                    </span>
                    {(response.data_sources ?? []).map((s) => (
                      <span
                        key={s}
                        className="rounded-md border border-violet-400/20 bg-violet-500/[0.06] px-2 py-0.5 text-[11px] text-violet-300"
                      >
                        {s.replace(/_/g, " ")}
                      </span>
                    ))}
                  </div>
                )}
              </>
            ) : null}
          </div>
        )}
      </div>
    </section>
  );
}
