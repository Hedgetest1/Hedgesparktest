"use client";

/**
 * useCostDefaults — shared hook for the shop-wide cost defaults form.
 *
 * Manages:
 *   - current defaults (GET /pro/costs/defaults)
 *   - 5 form inputs (COGS %, shipping per order, payment %, payment flat,
 *     monthly ad spend) with string state for inline edit
 *   - save handler (PATCH /pro/costs/defaults) with transient ok/err message
 *   - sync handler (POST /pro/costs/sync-from-shopify) same pattern
 *
 * The caller can subscribe to `onSavedSuccess` to re-fetch dependent data
 * (e.g. /analytics/pnl) after a successful save — the hook does NOT know
 * about P&L or other consumers.
 *
 * Extracted 2026-04-21 (Phase 2) from /app/page.tsx to break the 200-LOC
 * coupling between Settings and the main dashboard.
 */

import { useCallback, useEffect, useState } from "react";
import { apiClient } from "../api-client";
import type { paths } from "../api-types";
import { reportFrontendError } from "../error-reporter";

type CostDefaultsPayload =
  paths["/pro/costs/defaults"]["get"]["responses"]["200"]["content"]["application/json"];

type CostDefaultsShopifySyncPayload = {
  status: string;
  message?: string;
};

export type CostDefaultsFormInputs = {
  cogsPct: string;
  shipping: string;
  payPct: string;
  payFlat: string;
  adSpend: string;
};

export type CostDefaultsMessage = {
  type: "ok" | "err";
  text: string;
};

export type UseCostDefaultsResult = {
  data: CostDefaultsPayload | null;
  form: CostDefaultsFormInputs;
  setFormField: (key: keyof CostDefaultsFormInputs, value: string) => void;
  saving: boolean;
  savedMsg: CostDefaultsMessage | null;
  save: () => Promise<void>;
  syncing: boolean;
  syncMsg: CostDefaultsMessage | null;
  sync: () => Promise<void>;
};

function parseOrNull(s: string): number | null {
  if (!s || !s.trim()) return null;
  const n = parseFloat(s.trim());
  return Number.isFinite(n) ? n : null;
}

export function useCostDefaults(
  shop: string | null | undefined,
  opts: { onSavedSuccess?: () => void } = {},
): UseCostDefaultsResult {
  const { onSavedSuccess } = opts;
  const [data, setData] = useState<CostDefaultsPayload | null>(null);
  const [form, setForm] = useState<CostDefaultsFormInputs>({
    cogsPct: "",
    shipping: "",
    payPct: "",
    payFlat: "",
    adSpend: "",
  });
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<CostDefaultsMessage | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<CostDefaultsMessage | null>(null);

  const setFormField = useCallback(
    (key: keyof CostDefaultsFormInputs, value: string) => {
      setForm((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  // Seed form from server on shop resolve.
  useEffect(() => {
    if (!shop) return;
    let active = true;
    apiClient
      .GET("/pro/costs/defaults")
      .then((res) => {
        if (!active || res.data == null) return;
        setData(res.data);
        setForm({
          cogsPct:
            res.data.default_cogs_pct != null
              ? String(Math.round(res.data.default_cogs_pct * 100))
              : "",
          shipping:
            res.data.default_shipping_per_order != null
              ? String(res.data.default_shipping_per_order)
              : "",
          payPct:
            res.data.payment_pct != null
              ? String((res.data.payment_pct * 100).toFixed(2))
              : "",
          payFlat:
            res.data.payment_flat != null
              ? String(res.data.payment_flat)
              : "",
          adSpend:
            res.data.ad_spend_manual_monthly != null
              ? String(res.data.ad_spend_manual_monthly)
              : "",
        });
      })
      .catch((err: unknown) => {
        const e = err as { name?: string; message?: string } | null;
        reportFrontendError({
          component: "CostConfig",
          error_type: (e && e.name) || "CostConfigFetchError",
          message: (e && e.message) || "cost config fetch failed",
          severity: "info",
        });
      });
    return () => {
      active = false;
    };
  }, [shop]);

  const save = useCallback(async () => {
    if (!shop) return;
    setSaving(true);
    setSavedMsg(null);

    const cogsPctNum = parseOrNull(form.cogsPct);
    const payPctNum = parseOrNull(form.payPct);

    try {
      const res = await apiClient.PATCH("/pro/costs/defaults", {
        params: {},
        headers: { "Content-Type": "application/json" },
        body: {
          default_cogs_pct: cogsPctNum != null ? cogsPctNum / 100 : null,
          default_shipping_per_order: parseOrNull(form.shipping),
          payment_pct: payPctNum != null ? payPctNum / 100 : null,
          payment_flat: parseOrNull(form.payFlat),
          ad_spend_manual_monthly: parseOrNull(form.adSpend),
          currency: null,
        },
      });
      if (res.data != null) {
        setData(res.data);
        setSavedMsg({
          type: "ok",
          text: "Saved — Profit Intelligence updating…",
        });
        onSavedSuccess?.();
      } else {
        setSavedMsg({ type: "err", text: "Save failed — please retry." });
      }
    } catch {
      setSavedMsg({ type: "err", text: "Save failed — please retry." });
    } finally {
      setSaving(false);
      setTimeout(() => setSavedMsg(null), 4000);
    }
  }, [shop, form, onSavedSuccess]);

  const sync = useCallback(async () => {
    if (!shop) return;
    setSyncing(true);
    setSyncMsg(null);
    try {
      const res = await apiClient.POST("/pro/costs/sync-from-shopify", {});
      const payload = res.data as CostDefaultsShopifySyncPayload | undefined;
      if (payload == null) {
        setSyncMsg({ type: "err", text: "Sync failed — please retry." });
      } else if (payload.status === "ok") {
        setSyncMsg({
          type: "ok",
          text: payload.message || "Shopify costs imported.",
        });
        onSavedSuccess?.();
      } else {
        setSyncMsg({
          type: "err",
          text: payload.message || "Sync returned no data.",
        });
      }
    } catch {
      setSyncMsg({ type: "err", text: "Sync failed — please retry." });
    } finally {
      setSyncing(false);
      setTimeout(() => setSyncMsg(null), 6000);
    }
  }, [shop, onSavedSuccess]);

  return {
    data,
    form,
    setFormField,
    saving,
    savedMsg,
    save,
    syncing,
    syncMsg,
    sync,
  };
}
