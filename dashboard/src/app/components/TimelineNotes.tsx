"use client";

/**
 * TimelineNotes — "Timeline Notes"
 *
 * Merchants add dated notes ("Started FB campaign on March 15") that
 * render as vertical lines on every chart + a compact list here. Small
 * UX win, big trust signal — it connects merchant actions to metrics.
 *
 * API: GET/POST/DELETE /pro/annotations
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";

type AnnotationRow = {
  id: string;
  date: string;
  label: string;
  description: string;
  category: string;
  created_at: string;
  author: string;
};

const CATEGORY_LABELS: Record<string, string> = {
  campaign: "Campaign",
  product: "Product launch",
  pricing: "Pricing change",
  site_change: "Site change",
  inventory: "Inventory",
  other: "Other",
};

const CATEGORY_COLORS: Record<string, string> = {
  campaign: "#7c3aed",
  product: "#34d399",
  pricing: "#fbbf24",
  site_change: "#60a5fa",
  inventory: "#f472b6",
  other: "#94a3b8",
};

export function TimelineNotes({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [annotations, setAnnotations] = useState<AnnotationRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [newDate, setNewDate] = useState<string>(() => new Date().toISOString().slice(0, 10));
  const [newLabel, setNewLabel] = useState<string>("");
  const [newCategory, setNewCategory] = useState<string>("campaign");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const { data: j, error: err } = await apiClient.GET("/pro/annotations");
      if (!err && j) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        setAnnotations(((j as any).annotations || []) as AnnotationRow[]);
      }
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
    if (!newLabel.trim()) { setError("Write a short label."); return; }
    setSaving(true);
    try {
      const { error: err } = await apiClient.POST("/pro/annotations", {
        body: {
          date: newDate,
          label: newLabel.trim(),
          category: newCategory,
          description: "",
        },
      });
      if (err) {
        setError("Save failed.");
        return;
      }
      setNewLabel("");
      setAdding(false);
      await load();
    } catch {
      setError("Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    try {
      await apiClient.DELETE("/pro/annotations/{annotation_id}", {
        params: { path: { annotation_id: id } },
      });
      await load();
    } catch {
      // silent
    }
  }

  if (!isProUser) return null;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
            Timeline Notes
          </div>
          <h3 className="text-[15px] font-bold text-white">
            Mark what you changed, see how it moved the numbers
          </h3>
        </div>
        {!adding && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-1 text-[11px] font-semibold text-slate-300 transition-colors hover:border-white/[0.2] hover:text-white"
          >
            + Add note
          </button>
        )}
      </div>

      {adding && (
        <div className="mb-4 rounded-xl border border-white/[0.06] bg-white/[0.03] p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <input
              type="date"
              value={newDate}
              onChange={(e) => setNewDate(e.target.value)}
              className="rounded-md border border-white/[0.08] bg-[#0b0b14] px-2 py-1 text-[12px] text-slate-200"
            />
            <input
              type="text"
              value={newLabel}
              onChange={(e) => setNewLabel(e.target.value)}
              placeholder="e.g. Launched FB retargeting"
              className="flex-1 min-w-[180px] rounded-md border border-white/[0.08] bg-[#0b0b14] px-2 py-1 text-[12px] text-slate-200 placeholder-slate-600"
            />
            <select
              value={newCategory}
              onChange={(e) => setNewCategory(e.target.value)}
              className="rounded-md border border-white/[0.08] bg-[#0b0b14] px-2 py-1 text-[12px] text-slate-200"
            >
              {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="rounded-md bg-[#d4893a] px-3 py-1 text-[11px] font-bold text-white transition-colors hover:bg-[#e8a04e] disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              onClick={() => { setAdding(false); setError(null); }}
              className="rounded-md px-3 py-1 text-[11px] text-slate-400 hover:text-slate-200"
            >
              Cancel
            </button>
          </div>
          {error && <div className="text-[10px] text-rose-400">{error}</div>}
        </div>
      )}

      {loading ? (
        <div className="animate-pulse space-y-2">
          <div className="h-8 rounded bg-white/[0.04]" />
          <div className="h-8 rounded bg-white/[0.04]" />
        </div>
      ) : annotations.length === 0 ? (
        <p className="text-[12px] text-slate-400">
          No notes yet. Drop a marker every time you change something — you&apos;ll see the impact on every chart.
        </p>
      ) : (
        <div className="space-y-1.5">
          {annotations.slice(0, 8).map((a) => {
            const color = CATEGORY_COLORS[a.category] || "#94a3b8";
            return (
              <div
                key={a.id}
                className="flex items-center justify-between gap-3 rounded-lg border border-white/[0.04] bg-white/[0.015] px-3 py-2"
              >
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span
                    className="flex-shrink-0 h-2 w-2 rounded-full"
                    style={{ background: color }}
                  />
                  <span className="text-[11px] font-mono tabular-nums text-slate-500">{a.date}</span>
                  <span className="truncate text-[12px] text-slate-200">{a.label}</span>
                  <span
                    className="flex-shrink-0 rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em]"
                    style={{ color, background: color + "15", border: `1px solid ${color}30` }}
                  >
                    {CATEGORY_LABELS[a.category] || a.category}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => handleDelete(a.id)}
                  className="flex-shrink-0 text-[11px] text-slate-600 hover:text-rose-400"
                  title="Remove note"
                >
                  ✕
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
