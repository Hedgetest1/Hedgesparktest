"use client";

/**
 * /app/settings/inventory — Per-shop inventory lead-time override.
 *
 * Single-field setting: how many days of cover the engine should consider
 * "at risk" before flagging a product for reorder. Default 14 days, range
 * 1-365. Tier-agnostic chrome (require_merchant_session backend, all tiers).
 *
 * The setting feeds:
 *   - /merchant/inventory/kpis             (low-stock + reorder hint)
 *   - /merchant/inventory/details          (drawer reorder hint per row)
 *   - daily digest "stock at risk" content
 *
 * On save, the backend invalidates the 10-min KPI cache so the dashboard
 * reflects the new lead time on the next load.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { FloorLayout } from "../../../components/FloorLayout";
import { apiClient } from "../../../lib/api-client";
import type { paths } from "../../../lib/api-types";
import type { SessionState } from "../../../lib/useSession";

const MIN_DAYS = 1;
const MAX_DAYS = 365;
const DEFAULT_DAYS = 14;

type SettingsResponse =
  paths["/merchant/inventory/settings"]["get"]["responses"]["200"]["content"]["application/json"];

type Msg = { type: "ok" | "err"; text: string } | null;

export default function InventorySettingsPage() {
  return (
    <FloorLayout floor="settings">
      {(session) => <InventorySettingsSurface session={session} />}
    </FloorLayout>
  );
}

function InventorySettingsSurface({ session }: { session: SessionState }) {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [input, setInput] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<Msg>(null);

  useEffect(() => {
    if (!session.shop) return;
    let active = true;
    apiClient
      .GET("/merchant/inventory/settings")
      .then((res) => {
        if (!active) return;
        if (res.data) {
          setData(res.data);
          setInput(
            res.data.lead_time_days != null
              ? String(res.data.lead_time_days)
              : "",
          );
        }
        setLoading(false);
      })
      .catch(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [session.shop]);

  const parseInput = (): { value: number | null; error: string | null } => {
    const trimmed = input.trim();
    if (trimmed === "") return { value: null, error: null }; // clears override
    const n = Number(trimmed);
    if (!Number.isFinite(n) || !Number.isInteger(n)) {
      return { value: null, error: "Enter a whole number of days." };
    }
    if (n < MIN_DAYS || n > MAX_DAYS) {
      return {
        value: null,
        error: `Enter between ${MIN_DAYS} and ${MAX_DAYS} days.`,
      };
    }
    return { value: n, error: null };
  };

  const save = async () => {
    setMsg(null);
    const { value, error } = parseInput();
    if (error) {
      setMsg({ type: "err", text: error });
      return;
    }
    setSaving(true);
    try {
      const res = await apiClient.PATCH("/merchant/inventory/settings", {
        body: { lead_time_days: value },
      });
      if (res.error || !res.data) {
        const detail =
          (res.error as { detail?: string } | undefined)?.detail || "Save failed.";
        throw new Error(detail);
      }
      const body = res.data;
      setData(body);
      setInput(body.lead_time_days != null ? String(body.lead_time_days) : "");
      setMsg({
        type: "ok",
        text:
          value === null
            ? `Cleared. Using default ${DEFAULT_DAYS} days.`
            : `Saved. Using ${value} days.`,
      });
    } catch (e) {
      setMsg({
        type: "err",
        text: e instanceof Error ? e.message : "Save failed.",
      });
    } finally {
      setSaving(false);
    }
  };

  const clear = async () => {
    setInput("");
    setMsg(null);
    setSaving(true);
    try {
      const res = await apiClient.PATCH("/merchant/inventory/settings", {
        body: { lead_time_days: null },
      });
      if (res.error || !res.data) {
        throw new Error("Clear failed.");
      }
      setData(res.data);
      setMsg({ type: "ok", text: `Cleared. Using default ${DEFAULT_DAYS} days.` });
    } catch (e) {
      setMsg({
        type: "err",
        text: e instanceof Error ? e.message : "Clear failed.",
      });
    } finally {
      setSaving(false);
    }
  };

  const effective = data?.effective_lead_time_days ?? DEFAULT_DAYS;
  const isOverride = data?.lead_time_days != null;

  return (
    <>
      <div className="mb-8">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] font-semibold text-slate-400">
          <Link href="/app" className="text-slate-400 hover:text-[#e8a04e]">
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
          <span className="text-slate-300">Inventory</span>
        </div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
          Inventory
        </h1>
        <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-slate-400">
          How many days of cover before HedgeSpark flags a product for
          reorder. The default works for most shops; raise it if your
          supplier needs more lead time, lower it if you turn around stock
          quickly. Saved instantly — the dashboard recalculates next load.
        </p>
      </div>

      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <div className="mb-5">
          <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
            Reorder lead time
          </div>
          <p className="text-[12.5px] leading-relaxed text-slate-400">
            A product is flagged "Reorder soon" when current stock divided
            by daily sales is less than this number.
          </p>
        </div>

        <div className="grid gap-4 sm:max-w-md">
          <div>
            <label
              htmlFor="lead-time-input"
              className="block text-[11.5px] font-semibold text-slate-300"
            >
              Lead time
            </label>
            <div className="mt-0.5 text-[10.5px] text-slate-400">
              Whole days, between {MIN_DAYS} and {MAX_DAYS}. Leave empty to
              use the default ({DEFAULT_DAYS} days).
            </div>
            <div className="mt-1.5 flex items-center rounded-lg border border-white/[0.08] bg-white/[0.03] focus-within:border-[#e8a04e]/60">
              <input
                id="lead-time-input"
                type="text"
                inputMode="numeric"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={loading || saving}
                className="flex-1 bg-transparent px-3 py-2 font-mono text-[13px] text-slate-200 placeholder:text-slate-600 focus:outline-none disabled:opacity-50"
                placeholder={String(DEFAULT_DAYS)}
                aria-describedby="lead-time-help"
              />
              <span className="px-3 text-[12px] text-slate-400">days</span>
            </div>
          </div>

          <div
            id="lead-time-help"
            className="rounded-lg bg-white/[0.02] px-3 py-2 text-[11.5px] text-slate-400"
          >
            <div>
              <span className="text-slate-500">Currently using:</span>{" "}
              <span className="font-mono font-semibold text-slate-200">
                {effective} days
              </span>
              {!isOverride && (
                <span className="ml-2 text-slate-500">(default)</span>
              )}
              {isOverride && (
                <span className="ml-2 text-emerald-400/80">(custom)</span>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={save}
              disabled={loading || saving}
              className="rounded-lg bg-emerald-500/90 px-5 py-2.5 text-[13px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {saving ? "Saving…" : "Save"}
            </button>
            {isOverride && (
              <button
                onClick={clear}
                disabled={loading || saving}
                className="rounded-lg border border-white/[0.08] bg-white/[0.02] px-4 py-2.5 text-[12.5px] font-semibold text-slate-300 transition-colors hover:border-white/[0.16] hover:bg-white/[0.04] disabled:cursor-not-allowed disabled:opacity-40"
              >
                Reset to default
              </button>
            )}
            {msg && (
              <span
                role="status"
                aria-live="polite"
                className={`text-[12px] ${
                  msg.type === "ok" ? "text-emerald-300" : "text-rose-300"
                }`}
              >
                {msg.text}
              </span>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
