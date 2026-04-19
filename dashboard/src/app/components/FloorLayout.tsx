"use client";

/**
 * FloorLayout — shared wrapper for Intelligence and Operations floor
 * pages (and any future floor routes).
 *
 * Handles:
 *   1. Session resolution via useSession (tier detection + preview-mode
 *      honoring for `?as=starter`)
 *   2. Redirect to /install when no authenticated shop
 *   3. Preview banner sticky top
 *   4. Sidebar with FloorSelector + correct active floor
 *   5. TopBar
 *   6. Scrollable <main> slot for floor-specific content
 *
 * /app/page.tsx (Pulse floor) does NOT use this layout today — it
 * predates the hook and has complex billing-callback / OAuth-redirect
 * logic that needs careful migration. Phase 2+ will migrate it.
 *
 * Usage:
 *   export default function MyFloorPage() {
 *     return (
 *       <FloorLayout floor="intelligence">
 *         {(session) => (
 *           <div>...your content using session.isProUser etc...</div>
 *         )}
 *       </FloorLayout>
 *     );
 *   }
 */

import { useState, type ReactNode } from "react";
import { Sidebar, type Floor } from "./Sidebar";
import { TopBar } from "./TopBar";
import { PreviewBanner } from "./PreviewBanner";
import { useSession, type SessionState } from "../lib/useSession";

export function FloorLayout({
  floor,
  children,
}: {
  floor: Floor;
  children: (session: SessionState) => ReactNode;
}) {
  const session = useSession();
  const [collapsed, setCollapsed] = useState(false);

  if (!session.resolved) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#07070f] text-slate-400">
        <div className="animate-pulse text-[14px]">Loading your plan…</div>
      </div>
    );
  }

  if (!session.shop) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 bg-[#07070f] text-slate-300">
        <p className="text-[14px]">Your session expired.</p>
        <a
          href="/install"
          className="rounded-lg bg-[#d4893a] px-4 py-2 text-[13px] font-bold text-white"
        >
          Reconnect your store
        </a>
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[#07070f] text-white">
      <PreviewBanner isPreviewing={session.isPreviewing} />
      <Sidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed((c) => !c)}
        activeSection=""
        onNavigate={() => {}}
        tier={session.tier}
        currentFloor={floor}
      />

      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar
          shop={session.shop}
          tier={session.tier}
          onTierToggle={() => {}}
          trial={{ daysRemaining: null, isPaidPro: session.isProUser }}
        />

        <main className="flex-1 overflow-y-auto px-6 py-10 lg:px-10">
          <div className="mx-auto max-w-[72rem]">
            {children(session)}
          </div>
        </main>
      </div>
    </div>
  );
}
