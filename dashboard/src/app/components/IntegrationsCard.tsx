"use client";

/**
 * IntegrationsCard — Phase Ω ecosystem hub.
 *
 * Compact panel that surfaces:
 *  - Outbound webhooks (subscriptions count + failure flag)
 *  - Ad network connections (Meta / Google / TikTok status)
 *
 * Each row is a one-line summary that links to the full settings page.
 * Designed for the dashboard sidebar — minimal real estate, max signal.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";

type WebhookSub = {
  id: number;
  target_url: string;
  event_types: string[];
  status: string;
  consecutive_failures: number;
  auto_disabled: boolean;
};

type AdConn = {
  network: string;
  status: string;
  account_name?: string | null;
  last_synced_at?: string | null;
};

const NETWORK_LABEL: Record<string, string> = {
  meta: "Meta Ads",
  google: "Google Ads",
  tiktok: "TikTok Ads",
};

export function IntegrationsCard({
  apiBase,
  isProUser,
}: {
  apiBase: string;
  isProUser: boolean;
}) {
  const [webhooks, setWebhooks] = useState<WebhookSub[]>([]);
  const [ads, setAds] = useState<AdConn[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiBase || !isProUser) { setLoading(false); return; }
    let active = true;
    Promise.all([
      apiClient.GET("/pro/webhooks/subscriptions"),
      apiClient.GET("/pro/ads/connections"),
    ])
      .then(([wRes, aRes]) => {
        if (!active) return;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const w = (wRes.data as any) ?? { subscriptions: [] };
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const a = (aRes.data as any) ?? { connections: [] };
        setWebhooks(w.subscriptions || []);
        setAds(a.connections || []);
      })
      .catch(() => { if (active) { setWebhooks([]); setAds([]); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiBase, isProUser]);

  if (!isProUser) return null;

  if (loading) {
    return (
      <div className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
        <div className="h-3 w-32 rounded bg-white/[0.06]" />
        <div className="mt-3 space-y-2">
          {[0, 1].map((i) => (<div key={i} className="h-10 rounded bg-white/[0.04]" />))}
        </div>
      </div>
    );
  }

  const failingWebhooks = webhooks.filter((w) => w.consecutive_failures > 0 || w.auto_disabled);
  const connectedAds = ads.filter((a) => a.status === "connected");
  const allNetworks: AdConn[] = ["meta", "google", "tiktok"].map((net) => {
    const found = ads.find((a) => a.network === net);
    return found || { network: net, status: "not_connected" };
  });

  return (
    <section
      className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5"
      aria-labelledby="integrations-heading"
      role="region"
    >
      <div className="mb-3">
        <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]" aria-hidden="true">
          Integrations
        </div>
        <h3 id="integrations-heading" className="text-[15px] font-bold text-white">Ecosystem connections</h3>
        <p className="mt-1 text-[11px] text-slate-500">
          {webhooks.length} webhook{webhooks.length === 1 ? "" : "s"} · {connectedAds.length}/3 ad networks
        </p>
      </div>

      {/* Ad networks */}
      <div className="mb-4">
        <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-slate-500">
          Ad networks
        </div>
        <div className="space-y-1.5">
          {allNetworks.map((a) => {
            const connected = a.status === "connected";
            return (
              <div
                key={a.network}
                className="flex items-center justify-between rounded-lg border border-white/[0.04] bg-white/[0.015] px-3 py-2"
              >
                <span className="text-[12px] text-slate-200">{NETWORK_LABEL[a.network]}</span>
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tabular-nums ${
                    connected
                      ? "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-400/30"
                      : "bg-white/[0.04] text-slate-500 ring-1 ring-white/[0.06]"
                  }`}
                >
                  {connected ? "connected" : "not connected"}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Outbound webhooks */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <div className="text-[10px] font-medium uppercase tracking-wide text-slate-500">
            Outbound webhooks
          </div>
          {failingWebhooks.length > 0 && (
            <span className="rounded-full bg-rose-500/15 px-2 py-0.5 text-[9px] font-bold uppercase text-rose-300 ring-1 ring-rose-400/30">
              {failingWebhooks.length} failing
            </span>
          )}
        </div>
        {webhooks.length === 0 ? (
          <div className="rounded-lg border border-dashed border-white/[0.08] bg-white/[0.01] p-3 text-center text-[11px] text-slate-500">
            No webhook subscriptions yet. Pipe events into Zapier, n8n, or your own backend.
          </div>
        ) : (
          <div className="space-y-1.5">
            {webhooks.slice(0, 4).map((w) => (
              <div
                key={w.id}
                className="flex items-center justify-between rounded-lg border border-white/[0.04] bg-white/[0.015] px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12px] text-slate-300">{w.target_url}</div>
                  <div className="text-[10px] text-slate-500">
                    {w.event_types.length} event{w.event_types.length === 1 ? "" : "s"}
                  </div>
                </div>
                <span
                  className={`flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${
                    w.status === "active"
                      ? "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-400/30"
                      : "bg-white/[0.04] text-slate-500 ring-1 ring-white/[0.06]"
                  }`}
                >
                  {w.status}
                </span>
              </div>
            ))}
            {webhooks.length > 4 && (
              <div className="text-center text-[10px] text-slate-500">
                +{webhooks.length - 4} more
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
