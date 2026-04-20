"use client";

/**
 * SlackSettings — per-merchant Slack webhook management.
 *
 * Strada 3.5 (2026-04-20). The merchant-facing surface for the Slack
 * integration: paste an incoming-webhook URL, we post a confirmation
 * to the channel, and from that point the daily morning brief lands
 * in Slack too (in addition to email).
 *
 * One input, two buttons (connect / disconnect), status indicator.
 * Never shows the webhook URL back to the user — it's write-only,
 * stored encrypted backend-side.
 *
 * Founder simplicity bar: less friction than pasting an API key.
 * No bot-token rotation, no OAuth review, no channel picker.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";
import type { components } from "../lib/api-types";

type StatusResponse = components["schemas"]["SlackStatusResponse"];

export function SlackSettings() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [webhookInput, setWebhookInput] = useState("");
  const [busy, setBusy] = useState<null | "connect" | "test" | "disconnect">(null);
  const [message, setMessage] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const loadStatus = async () => {
    setLoading(true);
    try {
      const { data } = await apiClient.GET("/merchant/slack/status");
      setStatus((data as StatusResponse) ?? null);
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadStatus();
  }, []);

  const showMessage = (kind: "ok" | "err", text: string) => {
    setMessage({ kind, text });
    setTimeout(() => setMessage(null), 5000);
  };

  const handleConnect = async () => {
    if (!webhookInput.trim()) {
      showMessage("err", "Paste your Slack webhook URL first.");
      return;
    }
    setBusy("connect");
    try {
      const { data, error } = await apiClient.POST("/merchant/slack/connect", {
        body: { webhook_url: webhookInput.trim() },
      });
      if (error) {
        showMessage(
          "err",
          (error as { detail?: string })?.detail ||
            "Couldn't validate that webhook. Double-check the URL and try again.",
        );
      } else if (data && (data as { ok?: boolean }).ok === false) {
        showMessage(
          "err",
          (data as { error?: string }).error ||
            "Saved but couldn't post the test message. Check your Slack channel permissions.",
        );
      } else {
        setWebhookInput("");
        showMessage(
          "ok",
          "Connected. A confirmation message just landed in your Slack channel.",
        );
      }
    } catch {
      showMessage("err", "Connection failed. Try again in a moment.");
    } finally {
      setBusy(null);
      loadStatus();
    }
  };

  const handleTest = async () => {
    setBusy("test");
    try {
      const { data, error } = await apiClient.POST("/merchant/slack/test", {});
      if (error) {
        showMessage("err", "Test failed — is the webhook still valid?");
      } else if (data && (data as { ok?: boolean }).ok === false) {
        showMessage("err", (data as { error?: string }).error || "Test failed.");
      } else {
        showMessage("ok", "Test message sent — check your Slack channel.");
      }
    } catch {
      showMessage("err", "Test failed. Try again in a moment.");
    } finally {
      setBusy(null);
      loadStatus();
    }
  };

  const handleDisconnect = async () => {
    setBusy("disconnect");
    try {
      await apiClient.DELETE("/merchant/slack");
      showMessage("ok", "Slack disconnected. Your brief will stop landing in Slack.");
    } catch {
      showMessage("err", "Disconnect failed. Try again.");
    } finally {
      setBusy(null);
      loadStatus();
    }
  };

  const connected = status?.connected ?? false;

  return (
    <div className="rounded-2xl border border-[#4A154B]/[0.25] bg-gradient-to-br from-[#0f0a14] via-[#0a0a14] to-[#0b0c18] p-6 sm:p-7">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-[#e8a04e]">
              Slack integration
            </div>
            {!loading && (
              <StatusBadge connected={connected} status={status?.status ?? "not_connected"} />
            )}
          </div>
          <h3 className="mt-2 text-[1.25rem] font-extrabold leading-tight text-white sm:text-[1.4rem]">
            Get your morning brief in Slack
          </h3>
          <p className="mt-2 max-w-2xl text-[13px] leading-relaxed text-slate-400">
            Paste an incoming-webhook URL from your Slack workspace. Your
            daily brief will arrive in that channel every morning in
            addition to your email. No bot install, no OAuth — just one
            URL.
          </p>
        </div>
      </div>

      {!connected ? (
        <>
          <div className="mt-5 flex flex-col gap-3 sm:flex-row">
            <input
              type="text"
              placeholder="https://hooks.slack.com/services/T.../B.../XXX"
              value={webhookInput}
              onChange={(e) => setWebhookInput(e.target.value)}
              disabled={busy !== null}
              className="flex-1 rounded-lg border border-white/[0.1] bg-white/[0.03] px-4 py-2.5 text-[13px] font-mono text-white placeholder:text-slate-600 focus:border-[#e8a04e]/50 focus:outline-none"
              aria-label="Slack webhook URL"
            />
            <button
              type="button"
              onClick={handleConnect}
              disabled={busy !== null || !webhookInput.trim()}
              className="flex-shrink-0 rounded-lg bg-gradient-to-br from-[#4A154B] to-[#6d1f6f] px-5 py-2.5 text-[12.5px] font-bold text-white transition-all hover:from-[#6d1f6f] hover:to-[#4A154B] disabled:opacity-60"
            >
              {busy === "connect" ? "Connecting…" : "Connect Slack"}
            </button>
          </div>
          <p className="mt-3 text-[11.5px] leading-relaxed text-slate-500">
            Create a webhook: <span className="text-slate-400">your Slack workspace → Apps → Incoming Webhooks → Add to Slack → choose a channel → copy the URL.</span>
          </p>
        </>
      ) : (
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={handleTest}
            disabled={busy !== null}
            className="rounded-lg border border-white/[0.1] bg-white/[0.03] px-4 py-2 text-[12px] font-bold text-slate-200 transition-colors hover:bg-white/[0.06] disabled:opacity-60"
          >
            {busy === "test" ? "Sending…" : "Send test message"}
          </button>
          <button
            type="button"
            onClick={handleDisconnect}
            disabled={busy !== null}
            className="rounded-lg border border-rose-400/25 bg-rose-500/[0.06] px-4 py-2 text-[12px] font-bold text-rose-300 transition-colors hover:bg-rose-500/[0.12] disabled:opacity-60"
          >
            {busy === "disconnect" ? "Disconnecting…" : "Disconnect"}
          </button>
        </div>
      )}

      {status?.last_error && !connected && (
        <div className="mt-4 rounded-lg border border-rose-400/20 bg-rose-500/[0.05] px-4 py-2.5 text-[12px] leading-relaxed text-rose-300">
          <span className="font-bold uppercase tracking-wider text-[10px]">Last error:</span>{" "}
          <span className="text-slate-300">{status.last_error}</span>
        </div>
      )}

      {message && (
        <div
          className={`mt-4 rounded-lg border px-4 py-2.5 text-[12.5px] leading-relaxed ${
            message.kind === "ok"
              ? "border-emerald-400/25 bg-emerald-500/[0.06] text-emerald-300"
              : "border-rose-400/25 bg-rose-500/[0.06] text-rose-300"
          }`}
        >
          {message.text}
        </div>
      )}
    </div>
  );
}

function StatusBadge({ connected, status }: { connected: boolean; status: string }) {
  if (connected) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/[0.1] px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-emerald-300">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400" />
        Connected
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-500/[0.1] px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-rose-300">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-rose-400" />
        Error
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-500/[0.1] px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-slate-500" />
      Not connected
    </span>
  );
}
