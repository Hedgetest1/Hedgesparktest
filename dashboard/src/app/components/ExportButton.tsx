"use client";

/**
 * ExportButton — CSV export primitive for Lite surfaces.
 *
 * Strada 3.4 (2026-04-20). Small button placed in section headers:
 * "Export CSV" → fetches /analytics/export?surface=<name>, triggers
 * a browser download, reports success/failure inline.
 *
 * Uses plain fetch with credentials: "include" so the hs_session
 * cookie is sent. Not via apiClient because apiClient is typed for
 * JSON responses and this endpoint returns text/csv.
 */

import { useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

type Surface =
  | "rars"
  | "benchmarks"
  | "benchmarks_vertical"
  | "pnl"
  | "cohorts_monthly"
  | "attribution";

type Format = "csv" | "pdf";

export function ExportButton({
  surface,
  label,
  accentColor = "#e8a04e",
  format = "csv",
}: {
  surface: Surface;
  label?: string;
  accentColor?: string;
  format?: Format;
}) {
  const [state, setState] = useState<"idle" | "loading" | "ok" | "error">("idle");

  const handleClick = async () => {
    if (state === "loading") return;
    setState("loading");
    try {
      const url = `${API_BASE}/analytics/export?surface=${encodeURIComponent(surface)}&format=${format}`;
      const res = await fetch(url, { credentials: "include" });
      if (!res.ok) throw new Error(`export failed: ${res.status}`);
      const blob = await res.blob();
      const cd = res.headers.get("Content-Disposition") || "";
      const match = cd.match(/filename="?([^"]+)"?/);
      const filename = match?.[1] || `${surface}.${format}`;
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
      setState("ok");
      setTimeout(() => setState("idle"), 2500);
    } catch {
      setState("error");
      setTimeout(() => setState("idle"), 3000);
    }
  };

  const defaultLabel = format === "pdf" ? "Export PDF" : "Export CSV";
  const displayLabel =
    state === "loading" ? "Preparing…" :
    state === "ok" ? "Downloaded ✓" :
    state === "error" ? "Retry" :
    (label || defaultLabel);

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={state === "loading"}
      className="inline-flex items-center gap-1.5 rounded-lg border bg-white/[0.02] px-3 py-1.5 text-[11px] font-bold uppercase tracking-wider transition-colors hover:bg-white/[0.06] disabled:opacity-60"
      style={{
        color: state === "error" ? "#f87171" : accentColor,
        borderColor: state === "error" ? "rgba(248,113,113,0.3)" : `${accentColor}40`,
      }}
      aria-label={`Export ${surface} as CSV`}
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2.2}
        className="h-3 w-3"
        aria-hidden="true"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
      </svg>
      {displayLabel}
    </button>
  );
}
