"use client";

/**
 * /app/settings/team — Multi-user team access per shop.
 *
 * Wraps YourTeamPanel in the standard FloorLayout shell. Team
 * membership is backend-native (GET/POST/DELETE /pro/team/members);
 * this page is just the configuration surface.
 */

import Link from "next/link";
import { FloorLayout } from "../../../components/FloorLayout";
import { YourTeamPanel } from "../../../components/YourTeamPanel";
import type { SessionState } from "../../../lib/useSession";

const API_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "";

export default function TeamSettingsPage() {
  return (
    <FloorLayout floor="intelligence">
      {(session) => <TeamSurface session={session} />}
    </FloorLayout>
  );
}

function TeamSurface({ session }: { session: SessionState }) {
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
          <span className="text-slate-300">Team</span>
        </div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
          Team
        </h1>
        <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-slate-400">
          Invite colleagues with role-based access to this shop&apos;s
          HedgeSpark workspace. Role permissions apply across every
          floor of the dashboard.
        </p>
      </div>

      <YourTeamPanel
        apiBase={API_BASE}
        shop={session.shop || ""}
        isProUser={session.isProUser}
      />
    </>
  );
}
