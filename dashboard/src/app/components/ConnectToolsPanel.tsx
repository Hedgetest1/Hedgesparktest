"use client";

/**
 * ConnectToolsPanel — "Connect to Other Tools"
 *
 * Settings panel for outbound signal webhooks. Merchants register a URL
 * (Zapier, Make, n8n, Shopify Flow, Slack, their own server) and subscribe
 * to events. Includes a test-ping button + HMAC signing secret display.
 *
 * API: GET/POST/DELETE /pro/signal-webhooks + POST /pro/signal-webhooks/{id}/test
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";

type WebhookRow = {
  id: string;
  url: string;
  events: string[];
  active: boolean;
  created_at: string;
  last_delivery_at: string | null;
  last_delivery_status: string | null;
};

type ListResponse = {
  shop_domain: string;
  webhooks: WebhookRow[];
  available_events: string[];
};

type CreateResponse = {
  webhook: WebhookRow;
  signing_secret: string;
  signature_header: string;
};

const EVENT_LABELS: Record<string, string> = {
  high_intent_abandon: "Visitor almost bought, left",
  goal_at_risk: "A target is slipping",
  semantic_drift: "Silent data issue detected",
  refund_spike: "Refunds spiking on a product",
  below_benchmark: "Below peer benchmark",
  nudge_holdout_win: "A nudge is proven effective",
  test_ping: "Test ping",
};

export function ConnectToolsPanel({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [list, setList] = useState<ListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [newUrl, setNewUrl] = useState("");
  const [selectedEvents, setSelectedEvents] = useState<string[]>([
    "high_intent_abandon",
    "goal_at_risk",
    "semantic_drift",
  ]);
  const [revealedSecret, setRevealedSecret] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const { data: j, error: err } = await apiClient.GET("/pro/signal-webhooks");
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if (!err && j) setList(j as any);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) { setLoading(false); return; }
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, shop, isProUser]);

  async function handleSave() {
    setError(null);
    if (!newUrl.startsWith("https://")) {
      setError("URL must start with https://");
      return;
    }
    if (selectedEvents.length === 0) {
      setError("Pick at least one event.");
      return;
    }
    setSaving(true);
    try {
      const { data: j, error: err } = await apiClient.POST("/pro/signal-webhooks", {
        body: { url: newUrl.trim(), events: selectedEvents },
      });
      if (err || !j) {
        setError("Create failed.");
        return;
      }
      const resp = j as unknown as CreateResponse;
      setRevealedSecret(resp.signing_secret);
      setNewUrl("");
      setAdding(false);
      await load();
    } catch {
      setError("Create failed.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    try {
      await apiClient.DELETE("/pro/signal-webhooks/{webhook_id}", {
        params: { path: { webhook_id: id } },
      });
      await load();
    } catch {
      // silent
    }
  }

  async function handleTest(id: string) {
    setTestResult(null);
    try {
      const { data: j, error: err } = await apiClient.POST(
        "/pro/signal-webhooks/{webhook_id}/test",
        { params: { path: { webhook_id: id } } },
      );
      if (err || !j) {
        setTestResult("Test failed.");
        return;
      }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const first = ((j as any).results || [])[0];
      setTestResult(
        first
          ? `Test ping: ${first.status} (HTTP ${first.http_status || "n/a"})`
          : "Test ping sent.",
      );
    } catch {
      setTestResult("Test failed.");
    }
  }

  function toggleEvent(ev: string) {
    setSelectedEvents((prev) =>
      prev.includes(ev) ? prev.filter((e) => e !== ev) : [...prev, ev],
    );
  }

  if (!isProUser) return null;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
            Connect to Other Tools
          </div>
          <h3 className="text-[15px] font-bold text-white">
            Send HedgeSpark signals to your own stack
          </h3>
          <p className="mt-1 text-[11px] text-slate-400">
            Works with Shopify Flow · Zapier · Make · n8n · Slack · your own webhook
          </p>
        </div>
        {!adding && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-1 text-[11px] font-semibold text-slate-300 transition-colors hover:border-white/[0.2] hover:text-white"
          >
            + Add webhook
          </button>
        )}
      </div>

      {revealedSecret && (
        <div className="mb-4 rounded-xl border border-amber-400/25 bg-amber-500/[0.06] p-3">
          <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-amber-300">
            ⚠️ Copy this signing secret now — it won&apos;t be shown again
          </div>
          <div className="mt-2 flex items-center gap-2">
            <code className="flex-1 break-all rounded bg-[#0b0b14] px-2 py-1 text-[11px] font-mono text-amber-200">
              {revealedSecret}
            </code>
            <button
              type="button"
              onClick={() => {
                navigator.clipboard?.writeText(revealedSecret);
              }}
              className="rounded-md bg-amber-500/20 px-3 py-1 text-[10px] font-bold text-amber-200 hover:bg-amber-500/30"
            >
              Copy
            </button>
          </div>
          <p className="mt-2 text-[10px] text-amber-200/70">
            Verify incoming requests with the <code className="bg-white/5 px-1">X-HedgeSpark-Signature</code> header
            (HMAC-SHA256 of the body with this secret).
          </p>
          <button
            type="button"
            onClick={() => setRevealedSecret(null)}
            className="mt-2 text-[10px] text-amber-200 underline"
          >
            I&apos;ve copied it, dismiss
          </button>
        </div>
      )}

      {adding && (
        <div className="mb-4 rounded-xl border border-white/[0.06] bg-white/[0.03] p-3">
          <input
            type="url"
            value={newUrl}
            onChange={(e) => setNewUrl(e.target.value)}
            placeholder="https://your-server.com/hedgespark-webhook"
            className="mb-2 w-full rounded-md border border-white/[0.08] bg-[#0b0b14] px-2 py-1.5 text-[12px] text-slate-200 placeholder-slate-600"
          />
          <div className="mb-2 flex flex-wrap gap-1.5">
            {(list?.available_events || []).map((ev) => {
              const isOn = selectedEvents.includes(ev);
              return (
                <button
                  key={ev}
                  type="button"
                  onClick={() => toggleEvent(ev)}
                  className={`rounded-full border px-2.5 py-0.5 text-[10px] font-semibold transition-colors ${
                    isOn
                      ? "border-[#d4893a]/50 bg-[#d4893a]/15 text-[#e8a04e]"
                      : "border-white/[0.08] bg-white/[0.02] text-slate-400 hover:border-white/[0.18] hover:text-slate-200"
                  }`}
                >
                  {EVENT_LABELS[ev] || ev}
                </button>
              );
            })}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="rounded-md bg-[#d4893a] px-3 py-1 text-[11px] font-bold text-white transition-colors hover:bg-[#e8a04e] disabled:opacity-50"
            >
              {saving ? "Saving…" : "Create webhook"}
            </button>
            <button
              type="button"
              onClick={() => { setAdding(false); setError(null); }}
              className="rounded-md px-3 py-1 text-[11px] text-slate-400 hover:text-slate-200"
            >
              Cancel
            </button>
          </div>
          {error && <div className="mt-1 text-[10px] text-rose-400">{error}</div>}
        </div>
      )}

      {loading ? (
        <div className="animate-pulse space-y-2">
          <div className="h-10 rounded bg-white/[0.04]" />
        </div>
      ) : !list || list.webhooks.length === 0 ? (
        <p className="text-[12px] text-slate-400">
          No webhooks yet. Add one to route HedgeSpark signals into your automations.
        </p>
      ) : (
        <div className="space-y-2">
          {list.webhooks.map((w) => (
            <div
              key={w.id}
              className="rounded-xl border border-white/[0.04] bg-white/[0.015] p-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12px] font-mono text-slate-200">{w.url}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {w.events.map((ev) => (
                      <span
                        key={ev}
                        className="rounded-full border border-white/[0.08] bg-white/[0.02] px-2 py-0.5 text-[9px] text-slate-400"
                      >
                        {EVENT_LABELS[ev] || ev}
                      </span>
                    ))}
                  </div>
                  {w.last_delivery_status && (
                    <div className="mt-1 text-[10px] text-slate-400">
                      Last delivery: <span className={w.last_delivery_status === "delivered" ? "text-emerald-400" : "text-rose-400"}>{w.last_delivery_status}</span>
                      {w.last_delivery_at && (
                        <span> · {new Date(w.last_delivery_at).toLocaleString()}</span>
                      )}
                    </div>
                  )}
                </div>
                <div className="flex flex-shrink-0 gap-1">
                  <button
                    type="button"
                    onClick={() => handleTest(w.id)}
                    className="rounded-md border border-white/[0.08] bg-white/[0.02] px-2 py-1 text-[10px] font-semibold text-slate-300 hover:border-white/[0.2]"
                  >
                    Test
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(w.id)}
                    className="rounded-md border border-white/[0.08] bg-white/[0.02] px-2 py-1 text-[10px] font-semibold text-slate-400 hover:border-rose-400/30 hover:text-rose-400"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          ))}
          {testResult && <div className="text-[11px] text-slate-400">{testResult}</div>}
        </div>
      )}
    </div>
  );
}
