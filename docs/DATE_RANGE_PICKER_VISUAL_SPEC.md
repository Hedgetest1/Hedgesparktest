# Date Range Picker Globale — Visual Spec

**Born 2026-04-27 from Phase 3B (brutal Lite vs $0-70 audit closure).**

The third "burning gap": current dashboard uses fixed windows
(today/yesterday, last 7d, last 14d, 30d, 90d). Every $0-70 competitor
ships an arbitrary date range picker. Founder feedback: "non voglio
sciocchezze del 2018, siamo nel 2026, superiamo tutti".

This spec is the contract for the global date-range component +
backend params + URL state. Built atomically — the pattern set here
applies to all 19 Lite tiles + Pro consumers.

## 1. Position & layout

- **Sticky top-bar** above the section content, BELOW the existing
  TopBar (NotificationBell + settings gear + tier badge).
- Full-width on the section column; on mobile, collapses to a
  single button that opens a full-screen drawer.
- z-index above page content but BELOW UpgradeModal / drawers /
  toasts (compute: 30 — between content (10) and overlays (60)).
- Animates in/out with the existing brand fade-in (200ms) — no
  bounce, no slide. The bar is a tool, not a feature.

## 2. Default selection

- **"Last 7 days"** — matches the most-viewed dashboard surface
  (`section-lite-last7`) so the global state lands on a user-
  recognized window when nothing else is set.
- URL param `range=last_7_days` reflects the selection. Empty URL
  param → default. Custom range → `range=custom&start=YYYY-MM-DD&
  end=YYYY-MM-DD`.

## 3. Presets

The 8 industry-standard presets, in this order:

| Order | Label | Range | Backend computed |
|---|---|---|---|
| 1 | Today | today (shop tz) | end=today, start=today |
| 2 | Yesterday | yesterday (shop tz) | end=yesterday, start=yesterday |
| 3 | Last 7 days | rolling 7 ending today | end=today, start=today−6 |
| 4 | Last 30 days | rolling 30 ending today | end=today, start=today−29 |
| 5 | Month to date | from start of current month | end=today, start=month_start |
| 6 | Quarter to date | from start of current quarter | end=today, start=quarter_start |
| 7 | Year to date | from start of current year | end=today, start=year_start |
| 8 | Custom range | user picks start + end | end=user, start=user |

Preset state is canonical: backend re-computes on each request from
the preset KEY, not from the cached start/end. This survives
midnight rollovers (the cached "today" stops being today).

## 4. Visual — desktop

```
┌──────────────────────────────────────────────────────────────────┐
│  [ 📅  Last 7 days  ▾ ]   vs.  [ ☐ previous period ]   [↻ today]│
│         ^primary CTA          ^secondary toggle      ^utility   │
└──────────────────────────────────────────────────────────────────┘
```

- Primary CTA: rounded-xl button, slate-800 bg, slate-100 text,
  border-white/[0.08]. Font 13px medium. Calendar icon left,
  caret-down right. On open → dropdown panel below.
- Compare toggle: small checkbox + "previous period" / "same period
  last year" radio. Checked state = amber `#e8a04e` accent.
  When ON, every tile shows the delta vs the comparison range.
- "Today" reset: small ghost button, returns to default preset.
- Hover lift: -1px translate, subtle shadow. Click = open dropdown.

## 5. Dropdown panel — desktop

```
┌──────────────────────────────────────┐
│  PRESETS              CUSTOM         │
│  ─ Today              ┌─ Calendar ─┐ │
│  ─ Yesterday          │ April 2026 │ │
│  ─ Last 7 days ✓     │  S M T W T │ │
│  ─ Last 30 days       │  ...       │ │
│  ─ MTD                │  ...       │ │
│  ─ QTD                │  start ●  │ │
│  ─ YTD                │  ●●●●● end │ │
│  ─ Custom             │            │ │
│                       └────────────┘ │
│                       [ Apply ]      │
└──────────────────────────────────────┘
```

Two-column layout: presets left (clickable list, current = amber
underline + emerald checkmark), custom calendar right (only
expanded when "Custom" preset selected). Apply button confirms
selection + closes panel + triggers refetch.

## 6. Mobile — full-screen drawer

On viewports < 768px the dropdown becomes a bottom sheet:

```
─────  drawer header — "Date range" + close × ─────
  Today
  Yesterday
  Last 7 days ✓
  ...
  Custom range
  ─ when Custom selected ─
  [start date input]
  [end date input]
─────  Apply (full-width primary)  ─────
─────  vs. previous period [toggle]  ─────
```

- Tap-friendly 44px hit targets
- Native `<input type="date">` for custom (avoids re-implementing
  a calendar grid; iOS / Android pickers are excellent already)
- Apply button bottom-fixed

## 7. Accessibility

- `role="combobox"` on primary CTA, `aria-expanded` toggles.
- `role="listbox"` on preset list; `role="option"` on each preset
  with `aria-selected`.
- Keyboard:
    - `Tab` / `Shift+Tab` — focus moves through controls
    - `Enter` / `Space` — opens dropdown / activates option
    - `Esc` — closes dropdown without applying
    - `Arrow Up/Down` — navigate presets
    - `Home` / `End` — first / last preset
- Focus visible: 2px ring rose-300/50 (matches existing dashboard).
- Reduced motion: `prefers-reduced-motion: reduce` disables the
  fade-in (instant show/hide).
- Screen reader announces "Date range, currently Last 7 days" on
  focus; on selection change announces "Date range changed to
  Last 30 days". Live region with `aria-live="polite"`.

## 8. Backend contract

Every analytics endpoint that previously took a `days` param now
ALSO accepts:

- `start_date` (YYYY-MM-DD, optional)
- `end_date` (YYYY-MM-DD, optional)
- `compare_start` (YYYY-MM-DD, optional)
- `compare_end` (YYYY-MM-DD, optional)

Behavior:

- When `start_date` AND `end_date` provided → use that range
  (in shop tz; both inclusive).
- When neither provided → fall back to legacy `days` window
  (backward compat, no breakage of any existing integration).
- When only one provided → 400 validation error
  (`{"detail": "start_date and end_date must both be provided"}`).
- Range validation: `end_date >= start_date`, `end_date <= today`
  (in shop tz), span `<= 730 days`. Beyond → 400 with reason.
- Compare params optional; when provided, response includes
  comparison fields (`prev_*` mirroring main metrics).

Shared parser: `app/core/date_range.py::DateRangeQuery` Pydantic
model + `get_date_range` FastAPI dependency. One source of truth,
imported by every analytics endpoint. New endpoints inherit the
behavior automatically by adding the dependency.

## 9. Frontend contract

- React context: `DateRangeContext` provides `{range, setRange,
  comparisonEnabled, setComparisonEnabled}` to the entire `/app`
  subtree.
- Provider lives at the page root (above section render).
- Tiles subscribe via `useDateRange()` hook → re-fetch when range
  changes (key on `range.start_date + range.end_date` in
  `useCardFetch`).
- URL state sync: `useEffect` on range change → `replaceState` so
  reload preserves selection. Initial mount reads URL.
- Persistence: `localStorage["hs_date_range"]` mirror so the next
  visit lands on the prior selection (overrideable via URL).

## 10. Cold-start behavior

- On a brand-new merchant with no orders yet, the picker defaults
  to "Last 7 days" but every tile still shows its existing
  empty-state copy ("Watching your storefront…"). The picker is
  not a data source — it's a filter. No data → empty state.

## 11. What this ISN'T

- NOT a calendar app (no event marking, no recurrence, no notes)
- NOT a saved-views feature (each visit reads from URL/storage,
  no per-merchant saved presets stored backend-side)
- NOT a per-tile picker (one global picker, every tile
  subscribes — coherence over per-card flexibility)
- NOT in the path of new email channels (per founder mandate
  2026-04-27 "ALTRO non mandiamo")

## 12. Implementation order

1. Backend: `app/core/date_range.py` shared parser + dependency
2. Backend: extend `today_snapshot`, `lite_extras` endpoints
   (15-20 endpoints) — each gets `Depends(get_date_range)` + the
   conditional branch
3. Frontend: `DateRangeContext` + `DateRangePicker` component +
   `useDateRange` hook
4. Frontend: wire picker into `/app/lite` top-bar + every tile
   subscribes
5. Tests: backend (param validation + cache key + range math) +
   frontend (preset rendering, custom range, URL sync)
6. Verify end-to-end: change range → all tiles refresh → URL
   reflects → reload preserves
