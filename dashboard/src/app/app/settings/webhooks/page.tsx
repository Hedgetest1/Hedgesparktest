"use client";

/**
 * /app/settings/webhooks — Signal webhooks (Zapier / Make / Shopify
 * Flow / Slack / custom endpoints).
 *
 * Wraps ConnectToolsPanel in the standard FloorLayout shell so
 * merchants reach the webhook config via TopBar gear → hub rather
 * than the retired inline SettingsSection.
 */

import Link from "next/link";
import { FloorLayout } from "../../../components/FloorLayout";
import { ConnectToolsPanel } from "../../../components/ConnectToolsPanel";
import type { SessionState } from "../../../lib/useSession";

const API_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "";

export default function SignalWebhooksPage() {
  return (
    <FloorLayout floor="settings">
      {(session) => <SignalWebhooksSurface session={session} />}
    </FloorLayout>
  );
}

function SignalWebhooksSurface({ session }: { session: SessionState }) {
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
          <span className="text-slate-300">Signal webhooks</span>
        </div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
          Signal webhooks
        </h1>
        <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-slate-400">
          Pipe HedgeSpark intelligence events (abandoned high-intent,
          cohort slip, refund cluster, etc.) to any URL. Works with
          Zapier, Make, n8n, Shopify Flow, Slack, or your own server.
          HMAC-signed so you can verify authenticity on the receiving
          end.
        </p>
      </div>

      <ConnectToolsPanel
        apiBase={API_BASE}
        shop={session.shop || ""}
        isProUser={session.isProUser}
      />
    </>
  );
}
