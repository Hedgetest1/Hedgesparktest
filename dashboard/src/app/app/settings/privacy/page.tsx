"use client";

/**
 * /app/settings/privacy — GDPR Art. 22 automated-targeting opt-out.
 *
 * Gives merchants a first-class toggle to opt out of automated
 * decision-making on their storefront data. Opt-out signals propagate
 * to every intelligence pipeline (nudge selection, lift attribution,
 * holdout measurement) via `merchant_opt_out:{shop}` Redis flag.
 *
 *   POST /merchant/object    — opt out
 *   POST /merchant/unobject  — opt back in
 *   GET  /merchant/privacy/preferences — current state
 *
 * Migrated 2026-04-21 (Phase 2) from the inline SettingsSection.
 */

import Link from "next/link";
import { FloorLayout } from "../../../components/FloorLayout";
import { usePrivacyOptOut } from "../../../lib/hooks/usePrivacyOptOut";
import type { SessionState } from "../../../lib/useSession";

export default function PrivacySettingsPage() {
  return (
    <FloorLayout floor="settings">
      {(session) => <PrivacySurface session={session} />}
    </FloorLayout>
  );
}

function PrivacySurface({ session }: { session: SessionState }) {
  const p = usePrivacyOptOut(session.shop);

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
          <span className="text-slate-300">Privacy</span>
        </div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
          Privacy (GDPR Art. 22)
        </h1>
        <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-slate-400">
          Opt out of automated decision-making on your storefront data.
          When enabled, HedgeSpark stops running nudge selection,
          holdout-based attribution, and any pipeline that produces
          automated actions. Baseline analytics (revenue, cohorts,
          benchmarks) continue — those are descriptive, not decisional.
        </p>
      </div>

      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div
              className={`mb-2 inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[10.5px] font-bold uppercase tracking-[0.14em] ${
                p.optedOut
                  ? "border-amber-400/30 bg-amber-500/[0.08] text-amber-300"
                  : "border-emerald-400/30 bg-emerald-500/[0.08] text-emerald-300"
              }`}
            >
              <span
                aria-hidden="true"
                className={`h-1.5 w-1.5 rounded-full ${
                  p.optedOut ? "bg-amber-400" : "bg-emerald-400"
                }`}
              />
              {p.optedOut ? "Opted out" : "Automation active"}
            </div>
            <h3 className="text-[16px] font-bold text-white">
              {p.optedOut
                ? "Automated decision-making is OFF"
                : "Automated decision-making is ON"}
            </h3>
            <p className="mt-1 max-w-xl text-[12.5px] leading-relaxed text-slate-400">
              {p.optedOut
                ? "HedgeSpark will not run nudge selection, action recommendations, or holdout-based measurement on your data until you opt back in."
                : "HedgeSpark selects nudges, recommends actions, and runs holdout measurement on your storefront data. You can switch this off any time."}
            </p>
          </div>
          <button
            onClick={p.toggle}
            disabled={p.loading || !p.resolved}
            className={`flex-shrink-0 rounded-lg px-5 py-2.5 text-[12.5px] font-bold uppercase tracking-[0.1em] transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
              p.optedOut
                ? "bg-emerald-500/90 text-white hover:bg-emerald-500"
                : "border border-amber-400/40 bg-amber-500/[0.08] text-amber-300 hover:border-amber-400/60 hover:bg-amber-500/[0.14]"
            }`}
          >
            {p.loading
              ? "Updating…"
              : p.optedOut
                ? "Opt back in"
                : "Opt out"}
          </button>
        </div>

        <div className="mt-5 rounded-lg border border-white/[0.04] bg-white/[0.015] px-4 py-3 text-[11.5px] leading-relaxed text-slate-400">
          <div className="mb-1 font-semibold text-slate-300">
            What changes when you opt out
          </div>
          <ul className="list-inside list-disc space-y-1">
            <li>Nudge selection engine stops choosing messages for your store</li>
            {/* data-truth-allowed: prose copy ("prevented-€ numbers" is product narrative for the only EUR-pricing tier we currently sell at) */}
            <li>Holdout-vs-treatment attribution pauses (no prevented-€ numbers)</li>
            <li>Automated action recommendations stop (no "Take action" CTAs)</li>
            <li>Baseline analytics (revenue, cohorts, peer benchmarks) continue</li>
            <li>Your preference is logged with a 72h-breach-ready audit trail</li>
          </ul>
        </div>
      </div>
    </>
  );
}
