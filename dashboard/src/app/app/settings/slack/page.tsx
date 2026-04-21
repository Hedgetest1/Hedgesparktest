"use client";

/**
 * /app/settings/slack — Slack integration configuration.
 *
 * Moved from Lite primary dashboard bottom (2026-04-21 founder
 * directive: "Slack da Lite va rimosso e messo come Klaviyo in
 * settings"). The SlackSettings component is self-contained; this
 * page wraps it in the standard FloorLayout shell + breadcrumb so it
 * matches the pattern established by /app/settings/costs.
 */

import Link from "next/link";
import { FloorLayout } from "../../../components/FloorLayout";
import { SlackSettings } from "../../../components/SlackSettings";

export default function SlackSettingsPage() {
  return (
    <FloorLayout floor="settings">
      {() => <SlackSurface />}
    </FloorLayout>
  );
}

function SlackSurface() {
  return (
    <>
      <div className="mb-8">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] font-semibold text-slate-500">
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
          <span className="text-slate-300">Slack</span>
        </div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
          Slack integration
        </h1>
        <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-slate-400">
          Pipe the daily merchant summary (Revenue-at-Risk, top leaks,
          recoveries) to your team&apos;s Slack channel. One-click OAuth
          connect. Disconnect any time.
        </p>
      </div>

      <SlackSettings />
    </>
  );
}
