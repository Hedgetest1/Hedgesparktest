"use client";

/**
 * LanguageSwitcher — drop-in language picker.
 *
 * Reads/writes via lib/i18n. Triggers a window event so any component
 * subscribing can re-render. Minimal, no router involvement.
 */

import { useEffect, useState } from "react";
import { getLocale, setLocale, supportedLocales, type Locale } from "../lib/i18n";

const LABELS: Record<Locale, string> = {
  en: "EN",
  it: "IT",
  es: "ES",
  fr: "FR",
  de: "DE",
};

export function LanguageSwitcher() {
  const [current, setCurrent] = useState<Locale>("en");

  useEffect(() => { setCurrent(getLocale()); }, []);

  const change = (loc: Locale) => {
    setLocale(loc);
    setCurrent(loc);
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("hs:locale-changed", { detail: loc }));
      // Force a re-render of the page to pick up new translations
      window.location.reload();
    }
  };

  return (
    <div className="flex items-center gap-1 rounded-full border border-white/[0.08] bg-white/[0.02] p-1" role="group" aria-label="Language">
      {supportedLocales().map((loc) => (
        <button
          key={loc}
          onClick={() => change(loc)}
          aria-label={`Switch to ${LABELS[loc]}`}
          aria-pressed={current === loc}
          className={`rounded-full px-2.5 py-1 text-[11px] font-bold tabular-nums transition-all ${
            current === loc
              ? "bg-[#d4893a]/20 text-[#d4893a]"
              : "text-slate-500 hover:text-slate-300"
          }`}
        >
          {LABELS[loc]}
        </button>
      ))}
    </div>
  );
}
