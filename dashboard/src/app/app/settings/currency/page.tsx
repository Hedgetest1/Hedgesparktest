"use client";

/**
 * /app/settings/currency — Display currency toggle.
 *
 * Moved from the in-page SettingsSection (Phase 1 of settings
 * sub-page migration, 2026-04-21). USD/EUR radio toggle persisted to
 * localStorage via lib/currency.ts helpers. Affects every money
 * figure rendered on the dashboard (all components read the saved
 * preference on mount).
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { FloorLayout } from "../../../components/FloorLayout";
import {
  readSavedDisplayCurrency,
  writeSavedDisplayCurrency,
  type DisplayCurrency,
} from "../../../lib/currency";

export default function CurrencySettingsPage() {
  return (
    <FloorLayout floor="settings">
      {() => <CurrencySurface />}
    </FloorLayout>
  );
}

function CurrencySurface() {
  const [currency, setCurrency] = useState<DisplayCurrency>("USD");
  const [saved, setSaved] = useState(false);

  // Hydrate from localStorage on mount (SSR-safe).
  useEffect(() => {
    setCurrency(readSavedDisplayCurrency());
  }, []);

  function updateCurrency(next: DisplayCurrency) {
    setCurrency(next);
    writeSavedDisplayCurrency(next);
    setSaved(true);
    // Transient confirmation; no server round-trip.
    setTimeout(() => setSaved(false), 2200);
  }

  return (
    <>
      <div className="mb-8">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] font-semibold text-slate-400">
          <Link
            href="/app"
            className="text-slate-400 hover:text-[#e8a04e]"
          >
            ← Dashboard
          </Link>
          <span className="text-slate-600">/</span>
          <Link
            href="/app/settings"
            className="text-slate-400 hover:text-[#e8a04e]"
          >
            Settings
          </Link>
          <span className="text-slate-600">/</span>
          <span className="text-slate-300">Display currency</span>
        </div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
          Display currency
        </h1>
        <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-slate-400">
          What currency the dashboard displays for money values. Your
          Shopify store&apos;s native currency is used for backend
          queries — this is purely a presentation setting and does not
          change any data.
        </p>
      </div>

      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <div className="mb-4 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Select display currency
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {(["USD", "EUR"] as const).map((c) => {
            const active = c === currency;
            return (
              <button
                key={c}
                onClick={() => updateCurrency(c)}
                className={`flex items-center justify-between rounded-xl px-4 py-3.5 text-left transition-colors ${
                  active
                    ? "border border-[#e8a04e]/50 bg-[#e8a04e]/[0.08]"
                    : "border border-white/[0.08] bg-white/[0.015] hover:border-white/[0.16] hover:bg-white/[0.03]"
                }`}
              >
                <div>
                  <div
                    className={`text-[14px] font-bold ${
                      active ? "text-[#e8a04e]" : "text-slate-200"
                    }`}
                  >
                    {c === "USD" ? "US Dollar" : "Euro"}
                  </div>
                  <div className="mt-0.5 text-[11.5px] text-slate-400">
                    Symbol{" "}
                    <span className="font-mono text-slate-300">
                      {/* data-truth-allowed: currency picker IS the source of truth for symbol display (USD/EUR are the only 2 supported currencies) */}
                      {c === "USD" ? "$" : "€"}
                    </span>{" "}
                    · code{" "}
                    <span className="font-mono text-slate-300">{c}</span>
                  </div>
                </div>
                <div
                  className={`h-5 w-5 flex-shrink-0 rounded-full border-2 ${
                    active
                      ? "border-[#e8a04e] bg-[#e8a04e]"
                      : "border-white/20"
                  }`}
                  aria-hidden="true"
                >
                  {active && (
                    <svg
                      viewBox="0 0 20 20"
                      className="h-full w-full p-0.5 text-[#0a0a14]"
                      fill="currentColor"
                    >
                      <path
                        fillRule="evenodd"
                        d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                      />
                    </svg>
                  )}
                </div>
              </button>
            );
          })}
        </div>

        <p
          className="mt-4 text-[12px] text-emerald-300 transition-opacity"
          style={{ opacity: saved ? 1 : 0 }}
          aria-live="polite"
        >
          ✓ Saved · the next page load will reflect {currency}.
        </p>
      </div>
    </>
  );
}
