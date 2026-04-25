"use client";

/**
 * /app/settings/cost-defaults — Shop-wide cost defaults.
 *
 * Distinct from /app/settings/costs (per-product COGS). This surface
 * manages SHOP-LEVEL defaults used as fallback when a product has no
 * per-product cost row:
 *
 *   default_cogs_pct               — % of revenue treated as COGS
 *   default_shipping_per_order     — flat shipping cost per order
 *   payment_pct + payment_flat     — payment processor fees
 *   ad_spend_manual_monthly        — monthly ad spend entry (pre-API)
 *
 * Plus a one-click "Import from Shopify" action that pulls
 * inventory_items.cost for every variant and upserts product_costs.
 *
 * Migrated 2026-04-21 (Phase 2 of settings sub-page migration) from
 * the inline SettingsSection in /app/page.tsx.
 */

import Link from "next/link";
import { FloorLayout } from "../../../components/FloorLayout";
import { useCostDefaults } from "../../../lib/hooks/useCostDefaults";
import type { SessionState } from "../../../lib/useSession";

export default function CostDefaultsPage() {
  return (
    <FloorLayout floor="settings">
      {(session) => <CostDefaultsSurface session={session} />}
    </FloorLayout>
  );
}

function CostDefaultsSurface({ session }: { session: SessionState }) {
  const cd = useCostDefaults(session.shop);

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
          <span className="text-slate-300">Cost defaults</span>
        </div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
          Cost defaults
        </h1>
        <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-slate-400">
          Shop-wide cost fallbacks applied when a product has no
          per-product cost row. These drive the P&L precision score and
          every margin-aware recommendation. Per-product overrides still
          win — use{" "}
          <Link
            href="/app/settings/costs"
            className="text-[#e8a04e] hover:underline"
          >
            Product costs
          </Link>{" "}
          for SKU-level precision.
        </p>
      </div>

      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <div className="mb-5">
          <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
            Shopify import
          </div>
          <p className="text-[12.5px] leading-relaxed text-slate-400">
            Pulls <span className="font-mono text-slate-300">inventory_items.cost</span>
            {" "}from your Shopify Admin for every variant and upserts
            product_costs. Run this once to seed per-product rows, then
            fill any gaps by hand.
          </p>
          <button
            onClick={cd.sync}
            disabled={cd.syncing}
            className="mt-3 inline-flex items-center gap-2 rounded-lg border border-[#e8a04e]/40 bg-[#e8a04e]/[0.08] px-4 py-2 text-[12.5px] font-bold text-[#e8a04e] transition-colors hover:border-[#e8a04e]/60 hover:bg-[#e8a04e]/[0.14] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {cd.syncing ? "Importing…" : "Import costs from Shopify"}
          </button>
          {cd.syncMsg && (
            <div
              className={`mt-2 rounded-lg px-3 py-2 text-[12px] ${
                cd.syncMsg.type === "ok"
                  ? "bg-emerald-500/[0.08] text-emerald-300"
                  : "bg-rose-500/[0.08] text-rose-300"
              }`}
            >
              {cd.syncMsg.text}
            </div>
          )}
        </div>

        <div className="border-t border-white/[0.06] pt-5">
          <div className="mb-4 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
            Manual fallback values
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <LabeledInput
              label="Default COGS %"
              hint="40 means 40% of revenue → COGS"
              suffix="%"
              value={cd.form.cogsPct}
              onChange={(v) => cd.setFormField("cogsPct", v)}
              inputMode="decimal"
            />
            <LabeledInput
              label="Default shipping per order"
              hint="in shop currency"
              value={cd.form.shipping}
              onChange={(v) => cd.setFormField("shipping", v)}
              inputMode="decimal"
            />
            <LabeledInput
              label="Payment processor %"
              hint="e.g. 2.9 for 2.9%"
              suffix="%"
              value={cd.form.payPct}
              onChange={(v) => cd.setFormField("payPct", v)}
              inputMode="decimal"
            />
            <LabeledInput
              label="Payment processor flat"
              hint="e.g. 0.30 per order"
              value={cd.form.payFlat}
              onChange={(v) => cd.setFormField("payFlat", v)}
              inputMode="decimal"
            />
            <LabeledInput
              label="Monthly ad spend (manual)"
              hint="pre-ad-platform-integration placeholder"
              value={cd.form.adSpend}
              onChange={(v) => cd.setFormField("adSpend", v)}
              inputMode="decimal"
              fullWidth
            />
          </div>

          <div className="mt-5 flex items-center gap-3">
            <button
              onClick={cd.save}
              disabled={cd.saving}
              className="rounded-lg bg-emerald-500/90 px-5 py-2.5 text-[13px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {cd.saving ? "Saving…" : "Save defaults"}
            </button>
            {cd.savedMsg && (
              <span
                className={`text-[12px] ${
                  cd.savedMsg.type === "ok"
                    ? "text-emerald-300"
                    : "text-rose-300"
                }`}
              >
                {cd.savedMsg.text}
              </span>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function LabeledInput({
  label,
  hint,
  suffix,
  value,
  onChange,
  inputMode = "text",
  fullWidth,
}: {
  label: string;
  hint?: string;
  suffix?: string;
  value: string;
  onChange: (v: string) => void;
  inputMode?: "text" | "decimal" | "numeric";
  fullWidth?: boolean;
}) {
  return (
    <div className={fullWidth ? "sm:col-span-2" : ""}>
      <label className="block text-[11.5px] font-semibold text-slate-300">
        {label}
      </label>
      {hint && (
        <div className="mt-0.5 text-[10.5px] text-slate-400">{hint}</div>
      )}
      <div className="mt-1.5 flex items-center rounded-lg border border-white/[0.08] bg-white/[0.03] focus-within:border-[#e8a04e]/60">
        <input
          type="text"
          inputMode={inputMode}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="flex-1 bg-transparent px-3 py-2 font-mono text-[13px] text-slate-200 placeholder:text-slate-600 focus:outline-none"
          placeholder="—"
        />
        {suffix && (
          <span className="px-3 text-[12px] text-slate-400">{suffix}</span>
        )}
      </div>
    </div>
  );
}
