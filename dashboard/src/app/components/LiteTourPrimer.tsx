"use client";

/**
 * LiteTourPrimer — "What am I looking at?" guide for first-time Lite
 * merchants.
 *
 * Why this exists: the founder flagged 2026-04-20 that warm tone and
 * pretty cards aren't enough — a merchant entering the Lite floor for
 * the first time doesn't understand what any of the sections are for.
 * Competitor dashboards (Lifetimely, TrueProfit, BeProfit) fail at this
 * too, but HedgeSpark is €39/mo and needs to out-clarify them from
 * second one.
 *
 * Design:
 *   - One card at the top of /app/lite, above the 3-Actions hero.
 *   - 7 rows: one per Lite section, each with icon + title + "what it
 *     is in one line" + "why it matters in one line".
 *   - Dismiss button persists via localStorage so repeat visits don't
 *     see it.
 *   - Never auto-collapses on scroll (user-hostile).
 *   - Initial render defers to after mount so SSR hydration doesn't
 *     flash the primer for returning merchants who already dismissed.
 */

import { useEffect, useState } from "react";

const DISMISSED_KEY = "hs_lite_tour_primer_dismissed_v1";

type TourEntry = {
  label: string;
  what: string;
  why: string;
  icon: React.ReactNode;
};

const ENTRIES: TourEntry[] = [
  {
    label: "Today · your 3 moves",
    what: "The 3 most impactful actions Spark found for you today.",
    why: "So you never open the dashboard wondering \"what should I do?\".",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-full w-full" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    label: "Revenue at risk",
    what: "How much money your store is about to lose if no one acts.",
    why: "The most honest number in the dashboard. Every euro here is a euro you can save.",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-full w-full" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
      </svg>
    ),
  },
  {
    label: "Abandoned intent",
    what: "Visitors who showed buying signals but left before purchasing.",
    why: "These are your warmest leads. Getting 1 in 5 back is often more valuable than 100 new visitors.",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-full w-full" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12l8.954-8.955c.44-.439 1.152-.439 1.591 0L21.75 12M4.5 9.75v10.125c0 .621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125V9.75M8.25 21h8.25" />
      </svg>
    ),
  },
  {
    label: "Live opportunities",
    what: "Pages on your store leaking visitors right now + what to fix.",
    why: "Fix the leak while it's leaking — recovery rates drop 80% after 24h.",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-full w-full" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15.59 14.37a6 6 0 01-5.84 7.38v-4.8m5.84-2.58a14.98 14.98 0 006.16-12.12A14.98 14.98 0 009.631 8.41m5.96 5.96a14.926 14.926 0 01-5.841 2.58m-.119-8.54a6 6 0 00-7.381 5.84h4.8m2.581-5.84a14.927 14.927 0 00-2.58 5.84m2.699 2.7c-.103.021-.207.041-.311.06a15.09 15.09 0 01-2.448-2.448 14.9 14.9 0 01.06-.312m-2.24 2.39a4.493 4.493 0 00-1.757 4.306 4.493 4.493 0 004.306-1.758M16.5 9a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0z" />
      </svg>
    ),
  },
  {
    label: "Visitor intent",
    what: "How many visitors on your store are Hot, Warm, Cold right now.",
    why: "Hot visitors are 10x more likely to buy than Cold. Knowing the split tells you whether to acquire more or convert better.",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-full w-full" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M18 18.72a9.094 9.094 0 003.741-.479 3 3 0 00-4.682-2.72m.94 3.198l.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0112 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 016 18.719m12 0a5.971 5.971 0 00-.941-3.197m0 0A5.995 5.995 0 0012 12.75a5.995 5.995 0 00-5.058 2.772m0 0a3 3 0 00-4.681 2.72 8.986 8.986 0 003.74.477m.94-3.197a5.971 5.971 0 00-.94 3.197M15 6.75a3 3 0 11-6 0 3 3 0 016 0zm6 3a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0zm-13.5 0a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0z" />
      </svg>
    ),
  },
  {
    label: "Daily brief",
    what: "Spark's one-paragraph summary of today's most important signal.",
    why: "If you only read one card, read this one. It's the TL;DR of your store.",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-full w-full" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
      </svg>
    ),
  },
  {
    label: "Hot products + Live Radar",
    what: "Your top-engaged products + a live map of visitors on the site.",
    why: "Hot products tell you what's working; the radar tells you it's working right now. Spark is earning its rent while you watch.",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-full w-full" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15.042 21.672L13.684 16.6m0 0l-2.51 2.225.569-9.47 5.227 7.917-3.286-.672zM12 2.25V4.5m5.834.166l-1.591 1.591M20.25 10.5H18M7.757 14.743l-1.59 1.59M6 10.5H3.75m4.007-4.243l-1.59-1.59" />
      </svg>
    ),
  },
];

export function LiteTourPrimer() {
  // Start hidden to avoid flash during hydration. Check storage once
  // after mount, reveal only if not previously dismissed.
  const [mounted, setMounted] = useState(false);
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    try {
      const flag = window.localStorage.getItem(DISMISSED_KEY);
      setDismissed(flag === "1");
    } catch {
      // localStorage blocked — show the primer anyway. Better to show
      // than to silently hide.
      setDismissed(false);
    }
    setMounted(true);
  }, []);

  const handleDismiss = () => {
    try {
      window.localStorage.setItem(DISMISSED_KEY, "1");
    } catch {
      // best-effort
    }
    setDismissed(true);
  };

  if (!mounted || dismissed) return null;

  return (
    <section
      aria-labelledby="lite-tour-heading"
      className="relative mb-6 overflow-hidden rounded-3xl border border-[#e8a04e]/20 bg-gradient-to-br from-[#e8a04e]/[0.04] via-transparent to-[#7c3aed]/[0.03] p-6 sm:p-8"
    >
      <div className="pointer-events-none absolute -right-20 -top-20 h-[280px] w-[280px] rounded-full bg-[#e8a04e]/[0.06] blur-[120px]" />
      <div className="relative">
        {/* Header + dismiss */}
        <div className="mb-5 flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.2em] text-[#e8a04e]">
              New here?
            </div>
            <h2
              id="lite-tour-heading"
              className="text-[1.25rem] font-extrabold leading-tight text-white sm:text-[1.5rem]"
            >
              Your Lite dashboard, in 30 seconds.
            </h2>
            <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-slate-400">
              Each block below is a section you&apos;ll see as you scroll.
              Read once, dismiss — I&apos;ll never show this again.
            </p>
          </div>
          <button
            type="button"
            onClick={handleDismiss}
            className="flex-shrink-0 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-[11.5px] font-bold text-slate-300 transition-colors hover:bg-white/[0.06] hover:text-white"
          >
            Got it
          </button>
        </div>

        {/* 7 rows */}
        <ul className="grid gap-2.5">
          {ENTRIES.map((entry, i) => (
            <li
              key={entry.label}
              className="flex items-start gap-3 rounded-xl border border-white/[0.04] bg-white/[0.015] p-3 transition-colors hover:border-white/[0.1]"
            >
              <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-[#e8a04e]/10 p-1.5 text-[#e8a04e]">
                {entry.icon}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-[10px] font-bold tabular-nums text-slate-500">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="text-[13.5px] font-bold text-white">
                    {entry.label}
                  </span>
                </div>
                <div className="mt-0.5 text-[12px] leading-relaxed text-slate-300">
                  {entry.what}
                </div>
                <div className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">
                  Why it matters · {entry.why}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
