"use client";

/**
 * /app/settings/costs — Per-product cost configuration.
 *
 * First standalone settings page in HedgeSpark. Establishes the pattern
 * for all future settings surfaces (Klaviyo, webhooks, team, outbound
 * webhooks, etc.) per `feedback_ux_coherence_across_surfaces.md`:
 *
 *   - Uses FloorLayout shell (floor="intelligence" since costs are
 *     Pro-tier P&L material, not a new floor concept).
 *   - Breadcrumb header "← Intelligence / Settings / Product costs"
 *     tells the merchant where they are + how to return.
 *   - Amber section title, slate body, emerald CTA for primary actions.
 *   - Sticky save bar appears only when changes are pending.
 *   - Loading / error / empty states via _CardStates primitives.
 *
 * Backend endpoints consumed (all already typed via OpenAPI codegen):
 *   GET  /pro/costs/products      — list current per-product costs
 *   POST /pro/costs/products      — bulk upsert edited rows
 *   POST /pro/costs/sync-from-shopify — import COGS from Shopify Admin API
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { FloorLayout } from "../../../components/FloorLayout";
import { apiClient } from "../../../lib/api-client";
import type { components } from "../../../lib/api-types";

type ProductCostRow = components["schemas"]["ProductCostRow"];
type ProductCostsListResponse = components["schemas"]["ProductCostsListResponse"];
type ShopifyCogsSyncResponse = components["schemas"]["ShopifyCogsSyncResponse"];

type EditableRow = {
  id: number;
  product_key: string;
  product_title: string | null;
  cogs_per_unit: string; // string for inline-edit; "" means unset
  shipping_cost_per_unit: string;
  currency: string | null;
  source: string;
  updated_at: string | null;
  dirty: boolean;
};

type SyncMsg = { kind: "ok" | "err"; text: string; at: number } | null;

function rowToEditable(r: ProductCostRow): EditableRow {
  return {
    id: r.id,
    product_key: r.product_key,
    product_title: r.product_title ?? null,
    cogs_per_unit: r.cogs_per_unit == null ? "" : String(r.cogs_per_unit),
    shipping_cost_per_unit:
      r.shipping_cost_per_unit == null ? "" : String(r.shipping_cost_per_unit),
    currency: r.currency ?? null,
    source: r.source,
    updated_at: r.updated_at ?? null,
    dirty: false,
  };
}

function parseOptional(val: string): number | null {
  const t = val.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

function isValidInput(val: string): boolean {
  const t = val.trim();
  if (t === "") return true; // empty = unset, valid
  const n = Number(t);
  return Number.isFinite(n) && n >= 0;
}

function formatAge(iso: string | null): string {
  if (!iso) return "never";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "recently";
  const ageMs = Date.now() - then;
  if (ageMs < 0) return "just now";
  const mins = Math.floor(ageMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function sourceBadgeTheme(source: string): { label: string; color: string } {
  if (source === "shopify_admin_api")
    return { label: "from Shopify", color: "#10b981" };
  if (source === "manual") return { label: "you set this", color: "#e8a04e" };
  return { label: source, color: "#94a3b8" };
}

export default function ProductCostsSettingsPage() {
  return (
    <FloorLayout floor="intelligence">
      {({ isProUser }) => <ProductCostsSurface isProUser={isProUser} />}
    </FloorLayout>
  );
}

function ProductCostsSurface({ isProUser }: { isProUser: boolean }) {
  const [rows, setRows] = useState<EditableRow[] | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    "loading"
  );
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<SyncMsg>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<SyncMsg>(null);

  const load = useCallback(async () => {
    setLoadState("loading");
    setLoadError(null);
    const { data, error } = await apiClient.GET("/pro/costs/products");
    if (error || !data) {
      setLoadError("We couldn't load your product costs right now. Try again in a moment.");
      setLoadState("error");
      return;
    }
    const list = (data as ProductCostsListResponse).products ?? [];
    setRows(list.map(rowToEditable));
    setLoadState("ready");
  }, []);

  useEffect(() => {
    if (!isProUser) return;
    load();
  }, [isProUser, load]);

  const dirtyCount = useMemo(
    () => (rows ?? []).filter((r) => r.dirty).length,
    [rows]
  );
  const hasInvalid = useMemo(
    () =>
      (rows ?? []).some(
        (r) =>
          r.dirty &&
          (!isValidInput(r.cogs_per_unit) || !isValidInput(r.shipping_cost_per_unit))
      ),
    [rows]
  );

  const updateRow = (id: number, patch: Partial<EditableRow>) => {
    setRows((prev) =>
      prev
        ? prev.map((r) => (r.id === id ? { ...r, ...patch, dirty: true } : r))
        : prev
    );
  };

  const handleSave = async () => {
    if (!rows || dirtyCount === 0 || hasInvalid) return;
    setSaving(true);
    setSaveMsg(null);
    const dirtyRows = rows.filter((r) => r.dirty);
    const { data, error } = await apiClient.POST("/pro/costs/products", {
      body: {
        products: dirtyRows.map((r) => ({
          product_key: r.product_key,
          product_title: r.product_title,
          cogs_per_unit: parseOptional(r.cogs_per_unit),
          shipping_cost_per_unit: parseOptional(r.shipping_cost_per_unit),
          currency: r.currency,
        })),
      },
    });
    setSaving(false);
    if (error || !data) {
      setSaveMsg({
        kind: "err",
        text: "Save failed. Your changes are still in the form — try again.",
        at: Date.now(),
      });
      return;
    }
    const result = data as components["schemas"]["ProductCostsBulkResponse"];
    setSaveMsg({
      kind: "ok",
      text: `Saved — ${result.updated} updated · ${result.inserted} added.`,
      at: Date.now(),
    });
    // Refresh to get authoritative data (including server-normalized values)
    await load();
  };

  const handleSync = async () => {
    if (syncing) return;
    setSyncing(true);
    setSyncMsg(null);
    const { data, error } = await apiClient.POST("/pro/costs/sync-from-shopify", {});
    setSyncing(false);
    if (error || !data) {
      setSyncMsg({
        kind: "err",
        text: "Sync failed. Shopify might be rate-limited — try again in a minute.",
        at: Date.now(),
      });
      return;
    }
    const result = data as ShopifyCogsSyncResponse;
    if (result.status === "ok") {
      setSyncMsg({
        kind: "ok",
        text: `Imported ${result.inserted + result.updated} product costs from Shopify (${result.inserted} new, ${result.updated} refreshed${result.skipped_no_cost > 0 ? `, ${result.skipped_no_cost} products had no cost set in Shopify` : ""}).`,
        at: Date.now(),
      });
      await load();
    } else if (result.status === "empty") {
      setSyncMsg({
        kind: "ok",
        text:
          result.message ||
          "Shopify returned no products with costs set. Add costs in Shopify Admin → Products → Inventory, or enter them below.",
        at: Date.now(),
      });
    } else {
      setSyncMsg({
        kind: "err",
        text: result.message || "Shopify sync reported an error.",
        at: Date.now(),
      });
    }
  };

  if (!isProUser) {
    return (
      <div className="rounded-2xl border border-[#e8a04e]/25 bg-gradient-to-br from-[#e8a04e]/[0.06] to-transparent p-6">
        <h1 className="text-[22px] font-bold text-white">Pro-only settings</h1>
        <p className="mt-2 max-w-xl text-[13.5px] leading-relaxed text-slate-400">
          Per-product cost configuration powers the P&L Intelligence floor.
          Upgrade to Pro to unlock it.
        </p>
        <Link
          href="/app?upgrade=1"
          className="mt-4 inline-flex rounded-lg bg-[#d4893a] px-5 py-2.5 text-[13px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-[#e8a04e]"
        >
          Upgrade to Pro
        </Link>
      </div>
    );
  }

  return (
    <>
      {/* Breadcrumb + title */}
      <div className="mb-8">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] font-semibold text-slate-500">
          <Link
            href="/app/pro"
            className="text-slate-400 hover:text-[#e8a04e]"
          >
            ← Pro
          </Link>
          <span>/</span>
          <span className="uppercase tracking-[0.16em] text-slate-600">
            Settings
          </span>
          <span>/</span>
          <span className="text-slate-300">Product costs</span>
        </div>
        <div className="text-[11px] font-bold uppercase tracking-[0.2em] text-[#e8a04e]">
          Settings · Product costs
        </div>
        <h1 className="mt-3 text-[2rem] font-extrabold leading-[1.1] text-[#e8a04e] sm:text-[2.5rem]">
          What each product costs you
        </h1>
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-slate-400">
          Tell HedgeSpark the unit cost of each product — the P&L,
          profitability rankings, and every recommendation that mentions
          margin get more accurate as soon as you enter these numbers. Pull
          them from Shopify with one click, or set them by hand.
        </p>
      </div>

      {/* Hero — Shopify sync CTA */}
      <section
        className="mb-6 rounded-2xl border border-emerald-400/20 bg-emerald-500/[0.05] p-5"
        aria-labelledby="sync-heading"
      >
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <h2
              id="sync-heading"
              className="text-[15px] font-bold text-emerald-300"
            >
              Pull costs from Shopify
            </h2>
            <p className="mt-1 max-w-lg text-[12.5px] leading-relaxed text-slate-400">
              Reads <span className="font-mono text-slate-300">inventory_items.cost</span>{" "}
              for every product variant in your store. Safe to run multiple times
              — we never overwrite values you entered by hand.
            </p>
          </div>
          <button
            type="button"
            onClick={handleSync}
            disabled={syncing}
            className="shrink-0 rounded-lg bg-emerald-500/90 px-5 py-2.5 text-[13px] font-bold uppercase tracking-[0.08em] text-white transition-colors hover:bg-emerald-400 disabled:opacity-60"
          >
            {syncing ? "Syncing…" : "Sync from Shopify"}
          </button>
        </div>
        {syncMsg && (
          <div
            className={`mt-3 rounded-lg border px-3 py-2 text-[12px] ${
              syncMsg.kind === "ok"
                ? "border-emerald-400/30 bg-emerald-500/[0.08] text-emerald-200"
                : "border-rose-400/30 bg-rose-500/[0.08] text-rose-200"
            }`}
            role="status"
          >
            {syncMsg.text}
          </div>
        )}
      </section>

      {/* Table — product list */}
      <section
        className="mb-24 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5"
        aria-labelledby="products-heading"
      >
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2
              id="products-heading"
              className="text-[15px] font-bold text-white"
            >
              {rows ? `${rows.length} products` : "Your products"}
            </h2>
            <p className="mt-0.5 text-[11px] text-slate-500">
              Click any value to edit · changes saved only when you hit Save
            </p>
          </div>
        </div>

        {loadState === "loading" && (
          <div className="animate-pulse space-y-2">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="h-14 rounded-lg bg-white/[0.03]" />
            ))}
          </div>
        )}

        {loadState === "error" && (
          <div className="rounded-lg border border-rose-400/25 bg-rose-500/[0.06] p-4 text-[13px] text-rose-200">
            {loadError}
            <button
              type="button"
              onClick={load}
              className="ml-3 underline hover:text-rose-100"
            >
              Retry
            </button>
          </div>
        )}

        {loadState === "ready" && rows && rows.length === 0 && (
          <div className="rounded-xl border border-white/[0.05] bg-white/[0.01] p-6 text-center">
            <div className="text-[22px]">📦</div>
            <h3 className="mt-2 text-[15px] font-bold text-white">
              No product costs yet
            </h3>
            <p className="mx-auto mt-2 max-w-md text-[12.5px] leading-relaxed text-slate-400">
              Click <span className="font-semibold text-emerald-300">Sync from Shopify</span>{" "}
              above to pull costs automatically for every variant, or enter them
              one-by-one in Shopify Admin → Products → Inventory and sync again.
            </p>
          </div>
        )}

        {loadState === "ready" && rows && rows.length > 0 && (
          <ul className="divide-y divide-white/[0.04]" aria-label="Product cost rows">
            {/* header row */}
            <li className="grid grid-cols-[1fr_120px_120px_90px_auto] gap-3 pb-2 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">
              <span>Product</span>
              <span className="text-right">COGS / unit</span>
              <span className="text-right">Shipping / unit</span>
              <span className="text-right">Currency</span>
              <span className="text-right">Source</span>
            </li>
            {rows.map((r) => {
              const theme = sourceBadgeTheme(r.source);
              const cogsValid = isValidInput(r.cogs_per_unit);
              const shipValid = isValidInput(r.shipping_cost_per_unit);
              return (
                <li
                  key={r.id}
                  className={`grid grid-cols-[1fr_120px_120px_90px_auto] items-center gap-3 py-2.5 ${
                    r.dirty ? "bg-amber-500/[0.04]" : ""
                  }`}
                >
                  <div className="min-w-0">
                    <div
                      className="truncate text-[13px] font-semibold text-slate-200"
                      title={r.product_title || r.product_key}
                    >
                      {r.product_title || r.product_key}
                    </div>
                    <div className="truncate text-[10px] text-slate-500">
                      {r.product_key} · updated {formatAge(r.updated_at)}
                    </div>
                  </div>
                  <input
                    type="text"
                    inputMode="decimal"
                    value={r.cogs_per_unit}
                    onChange={(e) =>
                      updateRow(r.id, { cogs_per_unit: e.target.value })
                    }
                    placeholder="—"
                    aria-label={`COGS per unit for ${r.product_title || r.product_key}`}
                    className={`h-9 w-full rounded-md border bg-white/[0.03] px-2.5 text-right text-[12.5px] text-slate-100 outline-none tabular-nums focus:border-[#e8a04e]/50 focus:ring-2 focus:ring-[#e8a04e]/30 ${
                      cogsValid
                        ? "border-white/[0.08]"
                        : "border-rose-400/60 focus:border-rose-400/80 focus:ring-rose-400/30"
                    }`}
                  />
                  <input
                    type="text"
                    inputMode="decimal"
                    value={r.shipping_cost_per_unit}
                    onChange={(e) =>
                      updateRow(r.id, { shipping_cost_per_unit: e.target.value })
                    }
                    placeholder="—"
                    aria-label={`Shipping cost per unit for ${r.product_title || r.product_key}`}
                    className={`h-9 w-full rounded-md border bg-white/[0.03] px-2.5 text-right text-[12.5px] text-slate-100 outline-none tabular-nums focus:border-[#e8a04e]/50 focus:ring-2 focus:ring-[#e8a04e]/30 ${
                      shipValid
                        ? "border-white/[0.08]"
                        : "border-rose-400/60 focus:border-rose-400/80 focus:ring-rose-400/30"
                    }`}
                  />
                  <div className="text-right text-[12px] tabular-nums text-slate-400">
                    {r.currency || "—"}
                  </div>
                  <div
                    className="justify-self-end rounded-full px-2 py-0.5 text-[10px] font-semibold"
                    style={{
                      color: theme.color,
                      background: theme.color + "1A",
                      border: `1px solid ${theme.color}33`,
                    }}
                  >
                    {theme.label}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* Sticky save bar */}
      {dirtyCount > 0 && (
        <div
          className="pointer-events-none fixed inset-x-0 bottom-0 z-30 px-4 pb-4 lg:left-[240px]"
          role="region"
          aria-label="Unsaved changes"
        >
          <div className="pointer-events-auto mx-auto flex max-w-[72rem] items-center justify-between gap-4 rounded-xl border border-amber-400/30 bg-gradient-to-br from-[#1a1a2a] to-[#12141e] px-4 py-3 shadow-2xl">
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-bold text-amber-300">
                {dirtyCount} {dirtyCount === 1 ? "change" : "changes"} pending
              </div>
              {hasInvalid ? (
                <div className="text-[11px] text-rose-300">
                  Fix the values highlighted in red before saving. Numbers must
                  be zero or positive.
                </div>
              ) : saveMsg ? (
                <div
                  className={`text-[11px] ${
                    saveMsg.kind === "ok" ? "text-emerald-300" : "text-rose-300"
                  }`}
                >
                  {saveMsg.text}
                </div>
              ) : (
                <div className="text-[11px] text-slate-400">
                  Your changes are in the form. Save them to apply to the P&L.
                </div>
              )}
            </div>
            <div className="flex shrink-0 gap-2">
              <button
                type="button"
                onClick={load}
                disabled={saving}
                className="rounded-lg border border-white/[0.1] bg-white/[0.03] px-3 py-2 text-[12px] font-bold text-slate-300 hover:bg-white/[0.06] disabled:opacity-60"
              >
                Discard
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={saving || hasInvalid}
                className="rounded-lg bg-[#e8a04e] px-4 py-2 text-[12.5px] font-bold uppercase tracking-[0.08em] text-[#1a1a2a] transition-colors hover:bg-[#f0b36b] disabled:opacity-60"
              >
                {saving ? "Saving…" : `Save ${dirtyCount}`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Save-success toast when no dirty rows remain (displayed briefly) */}
      {dirtyCount === 0 && saveMsg?.kind === "ok" && (
        <div
          className="pointer-events-none fixed inset-x-0 bottom-0 z-30 px-4 pb-4 lg:left-[240px]"
          role="status"
        >
          <div className="pointer-events-auto mx-auto max-w-[72rem] rounded-xl border border-emerald-400/30 bg-gradient-to-br from-emerald-500/[0.15] to-[#12141e] px-4 py-3 text-[12.5px] font-semibold text-emerald-200 shadow-2xl">
            ✓ {saveMsg.text}
          </div>
        </div>
      )}
    </>
  );
}
