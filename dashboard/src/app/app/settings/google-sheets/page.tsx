"use client";

/**
 * /app/settings/google-sheets — Google Sheets export integration.
 *
 * G4 Lite parity gap close (2026-04-29). Better Reports $19.90, Report
 * Pundit Free, Mipler $9.99 ship Sheets export at $0-60. We match
 * with `auth/drive.file` scope — non-sensitive, no Google verification
 * required, ship in 1-2 days vs 6 weeks for spreadsheets scope.
 *
 * State machine:
 *   - configured=false  → "Coming soon — admin setup in progress"
 *   - configured=true, connected=false → "Connect your Google Sheets" CTA
 *   - configured=true, connected=true  → "Connected as <email> · Disconnect"
 *
 * The OAuth flow is handled server-side: clicking Connect navigates the
 * browser to /auth/google/start which redirects to Google. After consent
 * Google redirects back to /auth/google/callback which stores the
 * encrypted refresh_token and redirects to /app/settings/google-sheets
 * with ?google=connected (or ?google=error&reason=…).
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FloorLayout } from "../../../components/FloorLayout";
import { apiClient } from "../../../lib/api-client";

type GoogleStatus = {
  configured: boolean;
  connected: boolean;
  email: string | null;
  connected_at: string | null;
};

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "https://api.hedgesparkhq.com";

export default function GoogleSheetsSettingsPage() {
  return (
    <FloorLayout floor="settings">
      {() => <GoogleSheetsSurface />}
    </FloorLayout>
  );
}

function GoogleSheetsSurface() {
  const searchParams = useSearchParams();
  const callbackResult = searchParams.get("google");
  const callbackReason = searchParams.get("reason");

  const [status, setStatus] = useState<GoogleStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    const { data, error } = await apiClient.GET("/merchant/google/status");
    if (error || !data) {
      setStatus({ configured: false, connected: false, email: null, connected_at: null });
    } else {
      setStatus(data as GoogleStatus);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  // Surface callback result from URL query params (set by /auth/google/callback redirect).
  useEffect(() => {
    if (callbackResult === "connected") {
      setMsg({ kind: "ok", text: "Connected to Google Sheets — exports are now available." });
    } else if (callbackResult === "error") {
      setMsg({
        kind: "err",
        text: `Connection failed${callbackReason ? ` (${callbackReason})` : ""}. Try again.`,
      });
    }
  }, [callbackResult, callbackReason]);

  const handleConnect = () => {
    // Server-side OAuth flow — full-page redirect (not a fetch) so
    // Google's consent screen takes over the browser.
    window.location.href = `${API_BASE}/auth/google/start`;
  };

  const handleDisconnect = async () => {
    if (!confirm("Disconnect Google Sheets? Existing exports stay in your Drive — only HedgeSpark's access is revoked.")) {
      return;
    }
    setBusy(true);
    setMsg(null);
    const { error } = await apiClient.POST("/auth/google/disconnect", {});
    setBusy(false);
    if (error) {
      setMsg({ kind: "err", text: "Disconnect failed. Try again or contact support." });
    } else {
      setMsg({ kind: "ok", text: "Disconnected from Google Sheets." });
      loadStatus();
    }
  };

  return (
    <>
      <div className="mb-8">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] font-semibold text-slate-400">
          <Link href="/app" className="text-slate-400 hover:text-[#e8a04e]">
            ← Dashboard
          </Link>
          <span className="text-slate-600">/</span>
          <Link href="/app/settings" className="text-slate-400 hover:text-[#e8a04e]">
            Settings
          </Link>
          <span className="text-slate-600">/</span>
          <span className="text-slate-300">Google Sheets export</span>
        </div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
          Google Sheets export
        </h1>
        <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-slate-400">
          Click &ldquo;Export to Sheets&rdquo; on any HedgeSpark report
          and a brand-new Google Sheet appears in your Drive with the
          data already pasted in. Connect once, export forever.
        </p>
      </div>

      {/* Trust strip — explain the scope honestly. */}
      <section
        className="mb-6 rounded-2xl border border-emerald-400/20 bg-emerald-500/[0.04] p-5"
        aria-labelledby="scope-heading"
      >
        <h2 id="scope-heading" className="text-[15px] font-bold text-emerald-300">
          What we can and can&apos;t access
        </h2>
        <ul className="mt-3 space-y-1.5 text-[12.5px] leading-relaxed text-slate-300">
          <li>
            <span className="text-emerald-300">✓</span> Create new
            Google Sheets in your Drive — and only those sheets.
          </li>
          <li>
            <span className="text-emerald-300">✓</span> Update or delete
            sheets HedgeSpark created (e.g., refresh same sheet on
            export).
          </li>
          <li>
            <span className="text-rose-300">✗</span> Read your existing
            sheets — files you created manually or with other apps stay
            invisible to us.
          </li>
          <li>
            <span className="text-rose-300">✗</span> Access your other
            Drive files — docs, slides, photos, anything outside
            HedgeSpark-created sheets.
          </li>
        </ul>
        <p className="mt-3 max-w-xl text-[11.5px] text-slate-400">
          Technical detail: we use the Google
          {" "}<code className="rounded bg-black/30 px-1 font-mono">drive.file</code>{" "}
          scope. Disconnect any time — your exported sheets stay in
          your Drive.
        </p>
      </section>

      {/* State machine */}
      {loading && (
        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-6 text-center text-[13px] text-slate-400">
          Loading…
        </div>
      )}

      {!loading && status && !status.configured && (
        <section
          className="rounded-2xl border border-amber-400/20 bg-amber-500/[0.05] p-6"
          aria-labelledby="config-heading"
        >
          <h2 id="config-heading" className="text-[15px] font-bold text-amber-300">
            Coming soon
          </h2>
          <p className="mt-2 max-w-xl text-[13px] leading-relaxed text-slate-300">
            HedgeSpark admin is finishing setup of the Google Cloud
            Console OAuth credentials. You&apos;ll see the connect
            button here as soon as it&apos;s ready — no action needed
            from your side.
          </p>
        </section>
      )}

      {!loading && status && status.configured && !status.connected && (
        <section
          className="rounded-2xl border border-[#d4893a]/30 bg-[#d4893a]/[0.06] p-6"
          aria-labelledby="connect-heading"
        >
          <h2 id="connect-heading" className="text-[16px] font-bold text-white">
            Not yet connected
          </h2>
          <p className="mt-2 max-w-xl text-[13px] leading-relaxed text-slate-300">
            One click to connect your Google account. We&apos;ll redirect
            you to Google&apos;s consent screen — you&apos;ll see exactly
            which permission HedgeSpark requests (it&apos;s the
            drive.file scope only).
          </p>
          <button
            type="button"
            onClick={handleConnect}
            className="mt-4 inline-flex items-center gap-2 rounded-lg bg-[#d4893a] px-5 py-2.5 text-[13px] font-bold uppercase tracking-[0.08em] text-white transition-colors hover:bg-[#e8a04e]"
          >
            Connect Google Sheets →
          </button>
        </section>
      )}

      {!loading && status && status.configured && status.connected && (
        <section
          className="rounded-2xl border border-emerald-400/30 bg-emerald-500/[0.06] p-6"
          aria-labelledby="connected-heading"
        >
          <h2 id="connected-heading" className="text-[16px] font-bold text-emerald-300">
            Connected
          </h2>
          <div className="mt-2 text-[13px] text-slate-200">
            <span className="text-slate-400">Authorized as:</span>{" "}
            <span className="font-mono">{status.email || "Google account"}</span>
          </div>
          {status.connected_at && (
            <div className="mt-1 text-[12px] text-slate-400">
              Connected on{" "}
              {new Date(status.connected_at).toLocaleDateString("en-US", {
                month: "short", day: "numeric", year: "numeric",
              })}
            </div>
          )}
          <p className="mt-3 max-w-xl text-[12.5px] leading-relaxed text-slate-400">
            Look for the &ldquo;Export to Google Sheets&rdquo; button on
            any HedgeSpark report. Each click creates a new sheet in
            your Drive — no overwrites.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handleConnect}
              className="rounded-lg border border-white/[0.1] bg-white/[0.03] px-4 py-2 text-[12px] font-semibold text-slate-300 transition-colors hover:bg-white/[0.06]"
            >
              Reconnect (different account)
            </button>
            <button
              type="button"
              onClick={handleDisconnect}
              disabled={busy}
              className="rounded-lg border border-rose-400/30 bg-rose-500/[0.08] px-4 py-2 text-[12px] font-semibold text-rose-300 transition-colors hover:bg-rose-500/[0.15] disabled:opacity-50"
            >
              {busy ? "Disconnecting…" : "Disconnect"}
            </button>
          </div>
        </section>
      )}

      {msg && (
        <div
          className={`mt-6 rounded-lg border px-4 py-3 text-[12.5px] ${
            msg.kind === "ok"
              ? "border-emerald-400/30 bg-emerald-500/[0.08] text-emerald-200"
              : "border-rose-400/30 bg-rose-500/[0.08] text-rose-200"
          }`}
          role="status"
        >
          {msg.text}
        </div>
      )}
    </>
  );
}
