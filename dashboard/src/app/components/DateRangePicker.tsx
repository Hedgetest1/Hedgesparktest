"use client";

/**
 * DateRangePicker — global 2026-grade date range filter.
 *
 * Visual + a11y contract: docs/DATE_RANGE_PICKER_VISUAL_SPEC.md
 *
 * Born 2026-04-27 from Phase 3B (brutal Lite vs $0-70 audit closure).
 * The merchant's escape from "fixed last 7d / 30d windows that feel
 * like 2018" — every tile in the dashboard subscribes to this picker
 * via DateRangeContext + useDateRange().
 *
 * Design choices (see spec for full rationale):
 *   - Sticky top-bar, full width on desktop, collapses to button on
 *     mobile (< 768px). z-index 30.
 *   - 8 preset buttons (Today / Yesterday / Last 7d / Last 30d / MTD /
 *     QTD / YTD / Custom) — dropdown panel below the trigger button.
 *   - Custom range uses native HTML <input type="date"> for both desktop
 *     and mobile (excellent native UX, free a11y, zero re-implementation
 *     of calendar grid).
 *   - Apply confirms custom range; presets apply immediately on click.
 *   - Keyboard: Esc closes, Tab/Shift+Tab cycles, Arrow Up/Down navigates
 *     preset list, Enter activates.
 *   - Brand colors: amber #e8a04e for active state, slate-100 for text,
 *     border-white/[0.08] for chrome — matches dashboard visual canon.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  DateRange, DateRangePreset, rangeFromPreset, useDateRange,
} from "./DateRangeContext";

const PRESETS: { key: DateRangePreset; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "last_7_days", label: "Last 7 days" },
  { key: "last_30_days", label: "Last 30 days" },
  { key: "mtd", label: "Month to date" },
  { key: "qtd", label: "Quarter to date" },
  { key: "ytd", label: "Year to date" },
  { key: "custom", label: "Custom range" },
];

function formatRangeLabel(range: DateRange): string {
  const preset = PRESETS.find((p) => p.key === range.preset);
  if (preset && range.preset !== "custom") return preset.label;
  // Custom: format as "Apr 1 – Apr 7" or "Apr 1, 2025 – Apr 7, 2026"
  const fmt = (iso: string) => {
    const [y, m, d] = iso.split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    return dt.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  };
  if (range.start === range.end) return fmt(range.start);
  return `${fmt(range.start)} – ${fmt(range.end)}`;
}

export function DateRangePicker() {
  const { range, setRange } = useDateRange();
  const [open, setOpen] = useState(false);
  const [customStart, setCustomStart] = useState(range.start);
  const [customEnd, setCustomEnd] = useState(range.end);
  // Keyboard navigation cursor for arrow-up/down preset traversal.
  // -1 = no synthetic focus (Tab-driven focus instead).
  const [activeIndex, setActiveIndex] = useState<number>(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const presetBtnRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Sync local custom inputs when range changes externally
  useEffect(() => {
    setCustomStart(range.start);
    setCustomEnd(range.end);
  }, [range.start, range.end]);

  // Initialize keyboard cursor on the current preset when dropdown opens
  useEffect(() => {
    if (!open) {
      setActiveIndex(-1);
      return;
    }
    const idx = PRESETS.findIndex((p) => p.key === range.preset);
    setActiveIndex(idx >= 0 ? idx : 0);
  }, [open, range.preset]);

  // Move DOM focus to follow keyboard cursor
  useEffect(() => {
    if (!open || activeIndex < 0) return;
    presetBtnRefs.current[activeIndex]?.focus();
  }, [activeIndex, open]);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  // Keyboard handler: Esc closes; ArrowUp/ArrowDown navigate presets;
  // Home/End jump to first/last. Per spec section 7 a11y contract.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => (i + 1) % PRESETS.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => (i - 1 + PRESETS.length) % PRESETS.length);
        return;
      }
      if (e.key === "Home") {
        e.preventDefault();
        setActiveIndex(0);
        return;
      }
      if (e.key === "End") {
        e.preventDefault();
        setActiveIndex(PRESETS.length - 1);
        return;
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const applyPreset = useCallback((preset: DateRangePreset) => {
    if (preset === "custom") {
      // Switch to custom but stay open — user picks dates next + clicks
      // Apply. Seed the custom inputs with the current effective range
      // so they don't reset to default — feels natural to refine the
      // current view into a custom span.
      setRange(rangeFromPreset("custom", customStart, customEnd));
      return;
    }
    setRange(rangeFromPreset(preset));
    setOpen(false);
    triggerRef.current?.focus();
  }, [setRange, customStart, customEnd]);

  const applyCustom = useCallback(() => {
    if (!customStart || !customEnd) return;
    if (customEnd < customStart) return; // browser validation should catch
    setRange({ preset: "custom", start: customStart, end: customEnd });
    setOpen(false);
    triggerRef.current?.focus();
  }, [customStart, customEnd, setRange]);

  return (
    <div
      ref={containerRef}
      className="relative inline-block"
    >
      {/* Trigger button */}
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="group inline-flex items-center gap-2 rounded-xl border border-white/[0.08] bg-slate-800/60 px-3.5 py-2 text-[13px] font-medium text-slate-100 transition hover:bg-slate-800 hover:border-white/[0.12] focus:outline-none focus:ring-2 focus:ring-[#e8a04e]/50"
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={`Date range, currently ${formatRangeLabel(range)}`}
      >
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
          <rect x="1.5" y="2.5" width="11" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.2"/>
          <path d="M1.5 5.5h11" stroke="currentColor" strokeWidth="1.2"/>
          <path d="M4.5 1.5v2M9.5 1.5v2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
        </svg>
        <span className="tabular-nums">{formatRangeLabel(range)}</span>
        <svg width="10" height="10" viewBox="0 0 10 10" className={`transition-transform ${open ? "rotate-180" : ""}`} aria-hidden>
          <path d="M2 4l3 3 3-3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
        </svg>
      </button>

      {/* Dropdown panel — desktop dropdown OR mobile bottom sheet
          per spec section 6. < md breakpoint switches to fixed
          bottom-aligned full-width sheet for tap-friendly mobile UX. */}
      {open && (
        <>
          {/* Mobile backdrop (only visible < md) */}
          <div
            className="fixed inset-0 z-20 bg-black/40 backdrop-blur-sm md:hidden"
            aria-hidden
            onClick={() => setOpen(false)}
          />
          <div
            className={[
              // Mobile: fixed bottom sheet
              "fixed inset-x-0 bottom-0 z-30 max-h-[85vh] overflow-y-auto rounded-t-2xl border-t border-white/[0.08] bg-[#0e0e1a] p-3 shadow-[0_-30px_60px_-15px_rgba(0,0,0,0.5)] pb-[env(safe-area-inset-bottom,16px)]",
              // Desktop: absolute dropdown
              "md:absolute md:right-0 md:bottom-auto md:inset-x-auto md:mt-2 md:min-w-[280px] md:max-h-none md:rounded-2xl md:border md:border-white/[0.08] md:p-2 md:shadow-[0_30px_60px_-15px_rgba(0,0,0,0.5)] md:pb-2",
            ].join(" ")}
            role="listbox"
            aria-label="Date range presets"
          >
            {/* Mobile drawer header */}
            <div className="mb-2 flex items-center justify-between border-b border-white/[0.05] pb-2 md:hidden">
              <div className="text-[13px] font-bold text-slate-100">Date range</div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-lg p-1 text-slate-300 hover:bg-white/[0.06] focus:outline-none focus:ring-2 focus:ring-[#e8a04e]/50"
                aria-label="Close date range picker"
              >
                <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden>
                  <path d="M4 4l10 10M14 4L4 14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                </svg>
              </button>
            </div>

          {/* Presets */}
          <div className="space-y-0.5">
            {PRESETS.map((p, i) => {
              const active = range.preset === p.key;
              return (
                <button
                  key={p.key}
                  ref={(el) => { presetBtnRefs.current[i] = el; }}
                  type="button"
                  onClick={() => applyPreset(p.key)}
                  className={`w-full text-left rounded-lg px-3 py-2 text-[13px] font-medium transition ${
                    active
                      ? "bg-[#e8a04e]/[0.12] text-[#e8a04e]"
                      : "text-slate-200 hover:bg-white/[0.04]"
                  } focus:outline-none focus:ring-2 focus:ring-[#e8a04e]/50`}
                  role="option"
                  aria-selected={active}
                  tabIndex={activeIndex === i ? 0 : -1}
                >
                  <span className="flex items-center justify-between">
                    {p.label}
                    {active && (
                      <span className="text-[10px] text-emerald-400" aria-hidden>✓</span>
                    )}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Custom range inputs — visible when "Custom range" preset
              is the current OR active selection */}
          {range.preset === "custom" && (
            <div className="mt-2 border-t border-white/[0.05] pt-3 px-1 space-y-2">
              <label className="block text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
                Custom range
              </label>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <input
                  type="date"
                  value={customStart}
                  onChange={(e) => setCustomStart(e.target.value)}
                  max={customEnd || undefined}
                  className="rounded-lg border border-white/[0.08] bg-slate-900/60 px-2.5 py-1.5 text-[12px] text-slate-100 focus:outline-none focus:ring-2 focus:ring-[#e8a04e]/50"
                  aria-label="Start date"
                />
                <span className="text-[11px] text-slate-400" aria-hidden>→</span>
                <input
                  type="date"
                  value={customEnd}
                  onChange={(e) => setCustomEnd(e.target.value)}
                  min={customStart || undefined}
                  max={new Date().toISOString().slice(0, 10)}
                  className="rounded-lg border border-white/[0.08] bg-slate-900/60 px-2.5 py-1.5 text-[12px] text-slate-100 focus:outline-none focus:ring-2 focus:ring-[#e8a04e]/50"
                  aria-label="End date"
                />
              </div>
              <button
                type="button"
                onClick={applyCustom}
                disabled={!customStart || !customEnd || customEnd < customStart}
                className="w-full rounded-lg bg-[#e8a04e] px-3 py-2 text-[12px] font-bold text-slate-900 transition hover:bg-[#f5b562] disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-[#e8a04e]/50"
              >
                Apply custom range
              </button>
            </div>
          )}
          </div>
        </>
      )}
    </div>
  );
}
