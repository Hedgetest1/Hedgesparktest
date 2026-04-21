"use client";

/**
 * /app/settings/klaviyo — Klaviyo integration configuration.
 *
 * Moved from the in-page SettingsSection (which still renders for
 * Pro/Scale until Phase 2 removes the inline duplication). This
 * standalone sub-page is the single source of truth going forward.
 *
 * Reads + writes merchant's Klaviyo private key:
 *   PUT    /merchant/integrations/klaviyo     — save key
 *   POST   /merchant/integrations/klaviyo/test — verify on save
 *   DELETE /merchant/integrations/klaviyo     — disconnect
 *   GET    /merchant/integrations             — status
 *
 * Pattern follows /app/settings/costs: FloorLayout + breadcrumb +
 * amber title + emerald CTA. Uses the shared useKlaviyoConnection
 * hook to avoid duplicating 80 LOC against the main page's inline
 * SettingsSection.
 */

import Link from "next/link";
import { FloorLayout } from "../../../components/FloorLayout";
import { useKlaviyoConnection } from "../../../lib/hooks/useKlaviyoConnection";
import type { SessionState } from "../../../lib/useSession";

export default function KlaviyoSettingsPage() {
  return (
    <FloorLayout floor="intelligence">
      {(session) => <KlaviyoSurface session={session} />}
    </FloorLayout>
  );
}

function KlaviyoSurface({ session }: { session: SessionState }) {
  const k = useKlaviyoConnection(session.shop);

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
          <span className="text-slate-300">Klaviyo</span>
        </div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
          Klaviyo integration
        </h1>
        <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-slate-400">
          Forward HedgeSpark intelligence events (abandoned high-intent
          visitors, at-risk cohorts) into your Klaviyo flows. Uses your
          private API key — we encrypt it at rest and only call Klaviyo
          for events you&apos;ve opted in.
        </p>
      </div>

      {k.isConnected ? (
        <ConnectedPanel
          status={k.status}
          showReplace={k.showReplace}
          setShowReplace={k.setShowReplace}
          keyInput={k.keyInput}
          setKeyInput={k.setKeyInput}
          connecting={k.connecting}
          onConnect={k.connect}
          onDisconnect={k.disconnect}
          message={k.message}
        />
      ) : (
        <DisconnectedPanel
          keyInput={k.keyInput}
          setKeyInput={k.setKeyInput}
          connecting={k.connecting}
          onConnect={k.connect}
          message={k.message}
        />
      )}
    </>
  );
}

function DisconnectedPanel({
  keyInput,
  setKeyInput,
  connecting,
  onConnect,
  message,
}: {
  keyInput: string;
  setKeyInput: (v: string) => void;
  connecting: boolean;
  onConnect: () => Promise<void>;
  message: { type: "ok" | "err"; text: string } | null;
}) {
  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
      <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
        Not connected
      </div>
      <h3 className="text-[16px] font-bold text-white">
        Connect your Klaviyo account
      </h3>
      <p className="mt-1 text-[12.5px] leading-relaxed text-slate-400">
        Paste your Klaviyo Private API Key below. Find it in your
        Klaviyo dashboard under{" "}
        <span className="font-mono text-[12px]">
          Account → Settings → API Keys → Private API Keys
        </span>
        .
      </p>

      <div className="mt-5 flex flex-col gap-3 sm:flex-row">
        <input
          type="password"
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          placeholder="pk_xxxxxxxxxxxxxxxxxxxxxxxx"
          onKeyDown={(e) => {
            if (e.key === "Enter" && keyInput.trim() && !connecting) {
              onConnect();
            }
          }}
          className="flex-1 rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-2.5 font-mono text-[13px] text-slate-200 placeholder:text-slate-600 focus:border-[#e8a04e]/60 focus:outline-none"
          autoComplete="off"
          spellCheck={false}
        />
        <button
          onClick={onConnect}
          disabled={!keyInput.trim() || connecting}
          className="rounded-lg bg-emerald-500/90 px-5 py-2.5 text-[13px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {connecting ? "Connecting…" : "Connect"}
        </button>
      </div>

      {message && (
        <div
          className={`mt-3 rounded-lg px-3 py-2 text-[12.5px] ${
            message.type === "ok"
              ? "bg-emerald-500/[0.08] text-emerald-300"
              : "bg-rose-500/[0.08] text-rose-300"
          }`}
        >
          {message.text}
        </div>
      )}
    </div>
  );
}

function ConnectedPanel({
  status,
  showReplace,
  setShowReplace,
  keyInput,
  setKeyInput,
  connecting,
  onConnect,
  onDisconnect,
  message,
}: {
  status: { key_hint: string | null; last_verified_at: string | null } | null;
  showReplace: boolean;
  setShowReplace: (v: boolean) => void;
  keyInput: string;
  setKeyInput: (v: string) => void;
  connecting: boolean;
  onConnect: () => Promise<void>;
  onDisconnect: () => Promise<void>;
  message: { type: "ok" | "err"; text: string } | null;
}) {
  return (
    <div className="rounded-2xl border border-emerald-400/25 bg-emerald-500/[0.04] p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-emerald-400/30 bg-emerald-500/[0.08] px-3 py-1 text-[10.5px] font-bold uppercase tracking-[0.14em] text-emerald-300">
            <span
              aria-hidden="true"
              className="h-1.5 w-1.5 rounded-full bg-emerald-400"
            />
            Connected
          </div>
          <h3 className="text-[16px] font-bold text-white">
            Klaviyo active
          </h3>
          <p className="mt-1 text-[12.5px] leading-relaxed text-slate-400">
            Key hint:{" "}
            <span className="font-mono text-[12px] text-slate-300">
              {status?.key_hint || "•••• ••••"}
            </span>
            {status?.last_verified_at && (
              <>
                {" · "}last verified{" "}
                <span className="text-slate-300">
                  {new Date(status.last_verified_at).toLocaleString()}
                </span>
              </>
            )}
          </p>
        </div>
        <button
          onClick={onDisconnect}
          className="flex-shrink-0 rounded-lg border border-rose-400/30 bg-rose-500/[0.08] px-4 py-2 text-[12px] font-semibold text-rose-300 transition-colors hover:border-rose-400/50 hover:bg-rose-500/[0.14]"
        >
          Disconnect
        </button>
      </div>

      <div className="mt-5 flex items-center justify-between gap-4 rounded-lg border border-white/[0.06] bg-white/[0.015] px-4 py-3">
        <span className="text-[12.5px] text-slate-300">
          Rotated or regenerated your key?
        </span>
        <button
          onClick={() => setShowReplace(!showReplace)}
          className="text-[12px] font-semibold text-[#e8a04e] hover:text-[#fbbf24]"
        >
          {showReplace ? "Cancel" : "Replace key"}
        </button>
      </div>

      {showReplace && (
        <div className="mt-3 flex flex-col gap-3 sm:flex-row">
          <input
            type="password"
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder="pk_xxxxxxxxxxxxxxxxxxxxxxxx"
            onKeyDown={(e) => {
              if (e.key === "Enter" && keyInput.trim() && !connecting) {
                onConnect();
              }
            }}
            className="flex-1 rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-2.5 font-mono text-[13px] text-slate-200 placeholder:text-slate-600 focus:border-[#e8a04e]/60 focus:outline-none"
            autoComplete="off"
            spellCheck={false}
          />
          <button
            onClick={onConnect}
            disabled={!keyInput.trim() || connecting}
            className="rounded-lg bg-emerald-500/90 px-5 py-2.5 text-[13px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {connecting ? "Verifying…" : "Replace"}
          </button>
        </div>
      )}

      {message && (
        <div
          className={`mt-3 rounded-lg px-3 py-2 text-[12.5px] ${
            message.type === "ok"
              ? "bg-emerald-500/[0.10] text-emerald-300"
              : "bg-rose-500/[0.10] text-rose-300"
          }`}
        >
          {message.text}
        </div>
      )}
    </div>
  );
}
