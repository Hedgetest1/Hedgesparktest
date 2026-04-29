"use client";

/**
 * ExportButton — CSV / PDF / Google Sheets export primitive.
 *
 * Strada 3.4 (2026-04-20) original CSV/PDF, extended 2026-04-29 with
 * "sheets" format for G4 Lite parity gap close (Better Reports $19.90,
 * Report Pundit Free, Mipler $9.99 all ship Sheets export at $0-60).
 *
 * Three formats:
 *   csv    — fetches /analytics/export?surface=X&format=csv → file download
 *   pdf    — fetches /analytics/export?surface=X&format=pdf → file download
 *   sheets — fetches CSV, parses it, POSTs to /analytics/export-to-sheets,
 *            opens the new Google Sheet in a new tab. Requires merchant
 *            to have connected Google in Settings → Google Sheets export
 *            (409 from API if not, redirects merchant to that page).
 *
 * Uses plain fetch with credentials: "include" so the hs_session cookie
 * is sent. CSV/PDF can't use apiClient because their endpoints return
 * text/csv or application/pdf, not JSON.
 */

import { useState } from "react";
import { apiClient } from "../lib/api-client";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

type Surface =
  | "rars"
  | "benchmarks"
  | "benchmarks_vertical"
  | "pnl"
  | "cohorts_monthly"
  | "attribution"
  | "inventory"
  // Per-row surfaces added 2026-04-29 — produce 1 row per customer/
  // product/country/variant/segment (10-1000+ rows depending on shop).
  | "top_customers_ltv"
  | "top_products"
  | "orders_by_country"
  | "top_variants"
  | "rfm_segments";

type Format = "csv" | "pdf" | "sheets";

// CSV parser — handles quoted fields with embedded commas AND newlines.
// Per RFC 4180 section 2.6, fields containing line breaks must be
// enclosed in double quotes; literal quote chars escape via "". We
// parse the entire blob as a single state machine vs a line-at-a-time
// approach so multiline quoted strings (rare in HedgeSpark exports
// but legitimate per spec) parse correctly. Returns rows[][cells[]].
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"' && text[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (ch === '"') {
        inQuotes = false;
      } else {
        cur += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(cur);
      cur = "";
    } else if (ch === "\n" || ch === "\r") {
      // End of unquoted row. Push accumulated cell + row, skip CRLF.
      row.push(cur);
      cur = "";
      if (row.length > 0 && !(row.length === 1 && row[0] === "")) {
        rows.push(row.map((s) => s.trim()));
      }
      row = [];
      if (ch === "\r" && text[i + 1] === "\n") i++;
    } else {
      cur += ch;
    }
  }
  // Final cell + row (no trailing newline).
  if (cur.length > 0 || row.length > 0) {
    row.push(cur);
    if (!(row.length === 1 && row[0] === "")) {
      rows.push(row.map((s) => s.trim()));
    }
  }
  return rows;
}

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
      // ── Sheets format: fetch CSV, parse, push to Google Sheets API.
      if (format === "sheets") {
        // 1. Fetch the same CSV the CSV-button would download.
        const csvUrl = `${API_BASE}/analytics/export?surface=${encodeURIComponent(surface)}&format=csv`;
        const csvRes = await fetch(csvUrl, { credentials: "include" });
        if (!csvRes.ok) throw new Error(`csv fetch failed: ${csvRes.status}`);
        const csvText = await csvRes.text();

        // 2. Parse CSV into headers + rows (RFC 4180 — quoted multiline OK).
        const parsed = parseCsv(csvText);
        if (parsed.length === 0) throw new Error("empty_export");
        const headers = parsed[0];
        const rows = parsed.slice(1);

        // 3. Push to Google Sheets via /analytics/export-to-sheets.
        //    apiClient (typed) — backend errors come through `error`,
        //    HTTP status via response is normalized.
        const today = new Date().toISOString().slice(0, 10);
        const title = `HedgeSpark · ${surface} · ${today}`;
        const { data: sheetsBody, error, response: sheetsResp } =
          await apiClient.POST("/analytics/export-to-sheets", {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            body: { title, headers, rows } as any,
          });

        if (sheetsResp?.status === 409) {
          // Not connected — guide merchant to Settings.
          window.location.href = "/app/settings/google-sheets";
          return;
        }
        if (sheetsResp?.status === 503) {
          throw new Error("sheets_not_configured");
        }
        if (error || !sheetsBody) throw new Error("sheets_create_failed");

        const body = sheetsBody as unknown as { url: string };
        // 4. Open the new sheet in a new tab — merchant lands on
        //    their data immediately. Browsers' popup-blocker is fine
        //    here because the click was a direct user gesture.
        window.open(body.url, "_blank", "noopener,noreferrer");
        setState("ok");
        setTimeout(() => setState("idle"), 4000);
        return;
      }

      // ── CSV / PDF: download via existing /analytics/export endpoint.
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

  const defaultLabel =
    format === "pdf" ? "Export PDF" :
    format === "sheets" ? "Export to Sheets" :
    "Export CSV";
  const loadingLabel =
    format === "sheets" ? "Creating sheet…" : "Preparing…";
  const okLabel =
    format === "sheets" ? "Opened in Sheets ✓" : "Downloaded ✓";
  const displayLabel =
    state === "loading" ? loadingLabel :
    state === "ok" ? okLabel :
    state === "error" ? "Retry" :
    (label || defaultLabel);

  // Sheets icon (vs the down-arrow for CSV/PDF) — rendered only for
  // format === "sheets" so the button is visually distinct from the
  // download-arrow buttons next to it.
  const Icon = format === "sheets" ? (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.2}
      className="h-3 w-3"
      aria-hidden="true"
    >
      <rect x="4" y="3" width="16" height="18" rx="1.5" />
      <path strokeLinecap="round" d="M4 9h16M4 15h16M9 3v18M15 3v18" />
    </svg>
  ) : (
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
  );

  const ariaLabel =
    format === "sheets"
      ? `Export ${surface} to Google Sheets`
      : `Export ${surface} as ${format.toUpperCase()}`;

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
      aria-label={ariaLabel}
    >
      {Icon}
      {displayLabel}
    </button>
  );
}
