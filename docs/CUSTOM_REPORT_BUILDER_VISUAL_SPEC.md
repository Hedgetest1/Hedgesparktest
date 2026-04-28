# Custom Report Builder — Visual Spec

**Born 2026-04-28 from Gap #1 of `project_brutal_audit_0_70_2026_04_27.md`**
(BLOCCANTE under the 0-60 parity doctrine).

The brutal $0-70 audit flagged "custom report builder" as a parity
gap vs Better Reports ($19.90/mo), Mipler ($similar tier), Report
Pundit, Report Toaster — every $0-60 specialty reporting app on
the Shopify App Store ships a builder. The category leader Better
Reports has 100+ templates, drag-drop pivot, custom formulas, and
scheduled email delivery. Lifetimely / Triple Whale / Polar do
NOT ship custom builders — they ship FIXED dashboards — so the
gap is real and unique to the specialty-reports band.

This spec is the contract for: a unified Reports Hub (Lite + Pro
parity), a Lite + Pro builder that lets merchants compose
metric × dimension × date-range queries on top of HedgeSpark's
existing aggregation tables, named report save, and on-screen +
CSV/PDF rendering. Built atomically; the builder reuses every
existing primitive (date-range picker, ExportButton, _CardStates,
compare-toggle, /analytics/export endpoint family).

---

## 0. Why this architecture (and what we ruled out)

| Option | Verdict |
|---|---|
| Drag-drop pivot grid à la Better Reports (heavy) | ❌ 5-7d build, founder territory on visual paradigm. Out of scope for sprint #1. Could revisit as Scale-tier feature after merchants validate the simpler approach. |
| Sidebar dashboard widgets (Periscope-style) | ❌ Out of band — SaaS BI tools, not Shopify-app paradigm |
| **Templated builder: pick metric × dimension × filter** | ✅ **CHOSEN** — covers 80% of merchant queries with 20% of UX surface. Reuses every HedgeSpark primitive. **Lite + Pro** per `feedback_0_60_parity_doctrine.md` (Better Reports $19.90 → entry band → Lite). |
| Pro-gate the builder | ❌ **REJECTED** — violates parity doctrine ("every $0-60 competitor feature → WE BUILD" applies to Lite at €39). Builder is in Lite. Pro layers differentiators ON TOP, not gating parity. |
| **Reports Hub: 6 existing exports + custom-reports section** | ✅ **CHOSEN** — discoverability win without new behavior; same hub serves Lite and Pro |
| Schedule reports via NEW email channel | ❌ Violates `feedback_0_60_parity_doctrine.md`'s 1-daily/1-weekly cap |
| **Saved report can REPLACE the daily/weekly digest slot** | ✅ **CHOSEN** — merchant chooses which custom report fills the existing slot. Available to Lite + Pro. |
| Lite ships custom formulas + predictive forecast + holdout-lift + peer-overlay | ✅ **CHOSEN** — formulas + forecast = parity ($0-60); holdout-lift + peer-overlay = HedgeSpark unique-value layer that lives in Lite per founder doctrine "merchants meet HedgeSpark at Lite, the moat must be visible there too" (2026-04-28). |
| Anomaly detection callouts in this sprint | ❌ **REJECTED** — Triple Whale ships at $129+, not $0-60 → out of band, defer to future sprint. Roadmap in §4.9. |

**Reference:** `feedback_audit_pro_scale_before_build.md` — grep
existing surface BEFORE building. Audit results below.

---

## 1. Existing HedgeSpark machinery (the foundation we reuse)

| Existing primitive | Where | Role in this spec |
|---|---|---|
| `/analytics/export?surface=<name>` | `app/api/lite_export.py` | 6 fixed surfaces (rars, benchmarks, benchmarks_vertical, pnl, cohorts_monthly, attribution) → CSV/PDF. Lite Reports Hub lists these unchanged. |
| `ExportButton` component | `dashboard/src/app/components/ExportButton.tsx` | Reused as the export action on every Reports Hub tile + builder result. |
| Date range picker globale | `DateRangeProvider` + `useDateRange` (Phase 3B) | Pro builder's date input is the same component. URL-state-aware. |
| Compare-to-previous toggle | Phase 3B residual | Pro builder gets a one-click compare toggle. Same primitive. |
| `useCardFetch<T>` + `_CardStates` | `_CardStates.tsx` | Builder result card uses these. |
| Aggregation worker | `aggregation_worker.py` (5min cycle) | Pre-computes per-day rollups in `product_metrics`, `store_metrics`, `cohort_summary`. The builder reads from these — never raw events. |
| `merchant_export.py` | GDPR Art. 15 full data export | Untouched; orthogonal to the report builder. |

The builder DOES NOT add a new aggregation pipeline. It composes
from already-rolled-up tables. This keeps it scale-safe to 10k
merchants without per-query strain on event tables.

---

## 2. The three surfaces

| Surface | Audience | Content |
|---|---|---|
| **Reports Hub** (`/app/reports`) | Lite + Pro | Discoverability page. 6 existing fixed reports as tiles + "Custom reports" section with saved reports + "+ New report" CTA. Identical for Lite and Pro at the catalog level. |
| **Builder wizard** (`/app/reports/new`, `/app/reports/[id]/edit`) | **Lite + Pro** | Metric × dimension × filter × date selector → live preview → save with name → exportable. Pro adds differentiator panels (see §4.8). |
| **Saved-report viewer** (`/app/reports/[id]`) | Lite + Pro | Read-only render of a saved report. Same chart + CSV/PDF buttons as the wizard preview. Pro adds differentiator overlays. |

NotificationBell pulse: NONE. Reports Hub is pull, not push (per
warm-Lite originality §3.1).

---

## 3. Reports Hub — `/app/reports`

### 3.1 Layout

- **Sidebar nav entry**: NEW item "Reports" (icon: bar chart). Above
  Settings, below Insights.
- **Page header**: amber `#e8a04e` extrabold "Reports", subtitle
  "Pull every number you've earned. Keep what you need, schedule
  what matters."
- **Two sections** stacked vertically:
  1. **Standard reports** (Lite + Pro): 6 tiles for rars,
     benchmarks, benchmarks_vertical, pnl, cohorts_monthly,
     attribution — each tile shows surface title + 1-line
     description + CSV button + PDF button.
  2. **Custom reports** (Lite + Pro): saved reports as smaller
     tiles + a prominent "+ New report" CTA card. Identical
     section for both tiers.

### 3.2 Standard report tile (Lite + Pro)

```
┌─────────────────────────────────────────┐
│  Revenue at Risk                        │  ← amber title
│  Components breakdown — last 30 days    │  ← 1-line description
│                                         │
│   [ Export CSV ]  [ Export PDF ]        │  ← reused ExportButton
└─────────────────────────────────────────┘
```

Click outside the export buttons → navigates to the relevant
section in the dashboard (e.g., `/app/#section-lite-rars`).

### 3.3 Custom report tile (Pro)

```
┌─────────────────────────────────────────┐
│  Top channels by revenue (mine)         │  ← user-named
│  metric=revenue dim=channel range=30d   │  ← config summary
│  Last run: 2 hours ago                  │
│                                         │
│   [ View ] [ Edit ] [ CSV ]  [ PDF ]    │
└─────────────────────────────────────────┘
```

### 3.4 Empty states (canonical _CardStates primitives)

- Lite + Pro merchant with 0 saved reports → CardEmpty with
  `accent="amber"`, body: "Build your first custom report to slice
  the data your way." + "+ New report" link.

---

## 4. Builder wizard — `/app/reports/new` (Lite + Pro)

### 4.1 Layout — three columns

```
┌──────────┬────────────────┬─────────────┐
│ Metric   │  Dimension     │  Live       │
│ catalog  │  catalog       │  preview    │
│          │                │             │
│ [pick 1] │  [pick 0..2]   │  chart +    │
│          │                │  table      │
│          │                │             │
└──────────┴────────────────┴─────────────┘
       ↓ bottom bar
[ Date range picker ]  [ Compare ]  [ Save ]  [ Export CSV/PDF ]
```

On mobile the columns collapse to a stepper (Metric → Dimension →
Filters → Preview).

### 4.2 Metric catalog (Lite + Pro — full parity)

Single-select. 12 metrics, all pre-aggregated in existing tables.
Per `feedback_0_60_parity_doctrine.md` ALL 12 are Lite (Better
Reports $19.90 ships full metric catalog at entry tier; we match):

| Metric | Source table |
|---|---|
| Revenue | `shop_orders.total_price` |
| Orders | `shop_orders.id` count |
| AOV | revenue / orders |
| Conversion rate | `product_metrics.purchase_rate` |
| Refund amount | `shop_orders` (Phase Class D) |
| Discount amount | `shop_orders.discount_amount` (Phase Class D) |
| Tax amount | `shop_orders.tax_amount` (Phase Class D) |
| Repeat-rate | cohort_summary |
| Customer LTV | cohort_summary |
| Revenue at Risk | RARS pipeline |
| Active visitors | events |
| Survey response top-choice | survey_responses (Gap #7) |

Each metric ships with a one-line tooltip explaining what it is.

### 4.3 Dimension catalog (Lite + Pro — full parity)

Multi-select up to 2 dimensions (group by). 10 dimensions:

| Dimension | Source | Notes |
|---|---|---|
| Channel | UTM-deterministic (Channel Attribution) | – |
| Product | `shop_orders.line_items[0].title` | – |
| Country | `shop_orders.shipping_address.country_code` | – |
| Customer cohort | first-purchase month | – |
| Time (day/week/month) | `shop_orders.created_at` | Auto granularity by range |
| Discount code | `shop_orders.discount_codes[0]` | – |
| Payment method | `shop_orders.payment_method` (Class D) | – |
| Hour of day | `shop_orders.created_at` HH | Lite + Pro |
| First-purchase channel | per-customer first-touch | LTV-style |
| Survey choice | `survey_responses.answer_choice` | Cross-Gap #7 wiring |

When 2 dimensions are picked → result is a pivot table
(rows × columns). When 1 → bar chart + ranked list. When 0 →
single big number + sparkline trend.

### 4.4 Filters (Lite + Pro)

Optional filter chips. Up to 3 simultaneous filters:

- Channel = X
- Product = X
- Country = X
- Customer segment = high-value / new / churn-risk
- Date range (auto from picker)

Filters AND together. NO OR logic in v1 (keeps the SQL bounded).

### 4.5 Preview pane

Live updates on every change. Uses canonical `useCardFetch` hook.
Loading state: `<CardSkeleton />`. Error: `<CardError />` with
retry. Empty (filter combo returns 0 rows): `<CardEmpty />` with
"Try a wider date range or fewer filters."

### 4.6 Save action

- Modal asking for report name (3-60 chars).
- Optional: "Replace daily digest with this report" toggle (only
  visible if the merchant has the daily digest enabled in
  Settings). Sets `merchant.scheduled_report_id` to the new
  report. Per `feedback_0_60_parity_doctrine.md` exemption, the
  digest cap stays at 1 daily + 1 weekly — the merchant just
  swaps which report fills the slot.
- POST `/merchant/reports` → returns the saved report with `id`.
- Redirect to `/app/reports/[id]`.

### 4.7 Export action

CSV/PDF buttons reuse `<ExportButton surface="custom" reportId={id}>`
— a new `surface=custom&report_id=<id>` mode added to
`/analytics/export`. Available to Lite + Pro identically.

### 4.8 Lite — parity baseline + HedgeSpark unique-value layer

Per `feedback_0_60_parity_doctrine.md` rule "$0-60 parity → we
ship + on top: clarity + accuracy + unique features", Lite ships
EVERYTHING below — parity items AND HedgeSpark moat features. The
founder's call (2026-04-28): merchants meet HedgeSpark at Lite, so
the unique value layer must be in Lite too, not gated to Pro.

| Feature | Reference | What it does |
|---|---|---|
| **Custom formulas** (parity) | Better Reports $19.90 | "Formula" metric type — merchant types `(Revenue * 0.7) / Orders` etc. Stored on `merchant_saved_reports.formula` (TEXT, server-validated against an allow-list of metric tokens + arithmetic operators — no eval, no SQLi). |
| **Predictive forecast** (parity + moat) | Lifetimely $19 narrow predicted-LTV | "Where this is heading — next 30/60/90 days" toggle on the saved report. CI-bounded projection lines from the existing Holt-style pipeline (Gap #6 SKU forecast generalised). Saved on `forecast_horizon` (NULL = off). |
| **Holdout-measured lift** (HedgeSpark moat) | No competitor at any tier | When the underlying metric has a holdout cohort active, the report annotates each dimension slice with the holdout-measured delta and p-value: e.g., *"This channel is up €420/wk vs holdout, p<0.05"*. Reads from `execution_baselines` + the existing holdout pipeline. Calm, factual, merchant-friendly tone. |
| **Peer-network overlay** (HedgeSpark moat) | No competitor (cross-merchant network effect specific to HedgeSpark) | Each dimension row gets a peer-percentile annotation: *"You're in the top 22% of stores in your category for this."*. Reads from `vertical_benchmarks` (already populated by aggregation worker). |

All four ship as PART of Lite. No tier gating.

### 4.9 Out-of-scope for this sprint (Pro/Scale moat)

| Feature | R-blocker class + reason |
|---|---|
| **Anomaly detection callouts** (`anomaly_fusion` cells highlighted "biggest 4-week drop") | **(R-blocker:tier_2-approval)** — Triple Whale Moby AI ships this at $129+, not $0-60. Out of $0-60 parity envelope, lives in Pro/Scale moat, requires fresh founder approval to expand Lite scope OR ship under Pro tier. Trigger un-park: founder approves Pro-tier feature ramp. |

When the future sprint activates anomaly callouts, they layer on
the same `merchant_saved_reports` + same builder UI. No
re-architecture needed.

---

## 5. Saved-report viewer — `/app/reports/[id]` (Lite, all tiers)

Read-only. Same render as the builder's preview pane plus:

- Title (the user-given name) at top
- "Last run: <time>" timestamp
- "Edit" button → `/app/reports/[id]/edit`
- "Delete" button (modal confirmation) → `DELETE /merchant/reports/[id]`
- "Schedule" / "Unschedule" toggle (if eligible — see §4.6)
- CSV / PDF buttons
- Forecast lines if `forecast_horizon` is set (§4.8)
- Custom-formula metric rendering if `formula` is set (§4.8)
- Holdout-lift annotations per dimension slice when applicable (§4.8)
- Peer-network percentile dots per row (§4.8)

Anomaly detection callouts (§4.9) are NOT shipped this sprint.
The data model accommodates them without changes when activated
in a future sprint.

---

## 6. Backend contract

### 6.1 Schema (Alembic migration — TIER_2)

```sql
CREATE TABLE merchant_saved_reports (
  id BIGSERIAL PRIMARY KEY,
  shop_domain TEXT NOT NULL,
  name VARCHAR(60) NOT NULL,
  metric VARCHAR(40) NOT NULL,                 -- e.g. 'revenue'
  dimensions JSONB NOT NULL DEFAULT '[]'::jsonb, -- ['channel'] or ['channel','time']
  filters JSONB NOT NULL DEFAULT '{}'::jsonb,    -- {'country':'IT'}
  date_range_preset VARCHAR(32) NOT NULL DEFAULT 'last_30_days',
  custom_start DATE,                            -- only when preset='custom'
  custom_end DATE,
  compare_enabled BOOLEAN NOT NULL DEFAULT false,
  scheduled BOOLEAN NOT NULL DEFAULT false,
  scheduled_cadence VARCHAR(16),                -- 'daily' | 'weekly' | NULL
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_run_at TIMESTAMPTZ,
  CONSTRAINT uq_msr_shop_name UNIQUE (shop_domain, name)
);

CREATE INDEX idx_msr_shop_updated
  ON merchant_saved_reports (shop_domain, updated_at DESC);

CREATE INDEX idx_msr_scheduled
  ON merchant_saved_reports (shop_domain)
  WHERE scheduled = true;
```

Storage estimate at 10k merchants × 5 saved reports avg = 50k rows
total. Negligible.

### 6.2 Endpoints

All endpoints `require_merchant_session` (no Pro-gating in this
sprint — full Lite parity). Future Pro/Scale endpoints listed in
§4.9 are NOT shipped here.

| Method | Path | Body / Query |
|---|---|---|
| GET | `/merchant/reports/standard` | Returns metadata for the 6 standard surfaces |
| GET | `/merchant/reports` | List saved reports for shop |
| POST | `/merchant/reports` | Create saved report (incl. optional `formula` and `forecast_horizon`) |
| GET | `/merchant/reports/{id}` | Fetch one saved report |
| PUT | `/merchant/reports/{id}` | Update saved report |
| DELETE | `/merchant/reports/{id}` | Soft-delete |
| GET | `/merchant/reports/{id}/data` | Run the report → chart + table data + forecast lines if applicable |
| GET | `/analytics/export?surface=custom&report_id={id}&format=csv\|pdf` | Reuses existing export pipeline |
| POST | `/merchant/reports/{id}/schedule` | Toggle scheduled flag (validates cap 1 daily / 1 weekly) |

All Pro endpoints require `response_model=` per
`audit_response_models`. Pydantic models named
`SavedReportOut`, `SavedReportListOut`, `ReportDataOut`,
`ReportScheduleOut`.

### 6.3 Report execution (`GET /merchant/reports/{id}/data`)

- Runs a parameterized query against pre-aggregated tables
  (`product_metrics`, `cohort_summary`, `shop_orders` rollups).
- NEVER queries raw `events` or `analytics_events` (scale safety).
- Returns `{ rows: [...], headers: [...], chart: {type, data}, total }`.
- Result cached in Redis 5 min: `hs:report:run:v1:{shop}:{id}`.
- Cache invalidated when the saved report is edited (PUT).

### 6.4 Scheduling

- `scheduled=true` swaps the merchant's daily/weekly digest content
  to this report's CSV.
- Existing `email_orchestrator` is extended (NOT a new channel) to
  fetch report data when sending the digest.
- Cap enforced server-side: if a merchant tries to schedule a 2nd
  report with the same cadence, returns 409 with a clear error.

---

## 7. Privacy, GDPR, scale invariants

- **GDPR Art. 17**: `gdpr_processor.py` cascade extended with
  `merchant_saved_reports` (sibling rule applied per
  `feedback_bugs_dont_inherit.md`).
- **No PII in saved reports**: name + config are merchant data
  only (no customer email/name in the saved spec).
- **Scale**: report execution always reads from pre-aggregated
  tables; partial index `idx_msr_scheduled` keeps the email
  orchestrator's "what reports run now" query bounded.
- **Rate limit**: 60 builder previews per minute per shop (Redis
  SETEX).
- **Daily digest slot cap**: `scheduled` can only be `true` for ≤1
  daily + ≤1 weekly per shop, enforced via partial unique index
  + 409 at PUT-time.

---

## 8. Versioning + deploy path

Identical pattern to Gap #7:
- Alembic migration → backend restart → dashboard build via
  `deploy.sh` → atomic verify via `audit_dashboard_live --strict`.
- No Shopify CLI involvement (this is dashboard-side only, no
  storefront extension).

---

## 9. Test plan

### 9.1 pytest (backend)

- happy-path create + list + get + update + delete report
- shop A cannot read shop B's reports (tenant isolation)
- date_range_preset = 'custom' requires custom_start + custom_end
- max name length 60 chars enforced
- max 50 saved reports per shop (sanity ceiling)
- scheduling 2nd daily report → 409
- DELETE soft-deletes (set name to a tombstone) — schema supports
  hard delete; soft deletes are out of scope v1
- report execution returns expected shape for every metric ×
  dimension combo (parametrized test)

### 9.2 vitest (dashboard)

- Reports Hub renders 6 standard tiles + Custom section (Lite + Pro)
- Builder wizard: metric+dimension select → preview updates
- Saved-report viewer: edit/delete buttons gated to owner
- Mobile stepper layout collapses correctly

### 9.3 Preflight audits

- `audit_response_models` ← all 9 endpoints
- `audit_input_bounds` ← `dimensions` JSONB capped at 2 entries,
  `filters` capped at 3 keys, name max 60
- `audit_tenant_isolation` ← all endpoints
- `audit_gdpr_redact_coverage` ← merchant_saved_reports added
- `audit_dashboard_fetches` ← all calls via apiClient
- `audit_dashboard_a11y` ← clean (slate-400+ for small text, etc.)

---

## 10. Voice direction (founder call 2026-04-28)

> "tono calmo MERCHANT FRIENDLY"

All copy in this surface follows the calm-merchant-friendly tone:
- no urgency tricks ("act now!", "limited time")
- no jargon ("CR" → "Conversion rate"; "MRR" → "Monthly revenue")
- short, conversational, reassuring sentences
- second-person ("you", "your store") not third-person
- numbers always have units + context

Defaults applied across the spec (founder can override any specific
string post-implementation; voice direction is the binding rule):

| Item | Default copy |
|---|---|
| Sidebar nav label | **Reports** |
| Hub subtitle | *"Every number from your store, exactly the way you need it."* |
| Metric labels | Canonical (Revenue, Orders, AOV, Conversion rate, Refund amount, Discount amount, Tax amount, Repeat-rate, Customer LTV, Revenue at Risk, Active visitors, Survey response). Idiot-proof per CLAUDE.md §5. |
| CTA new report | **+ New report** (clean, minimal) |
| Delete behavior | **Soft-delete** (preserves audit trail; merchant sees it as "removed", admin can recover within 30d) |
| Custom formula UX | Single-line text field with allow-listed tokens; below it a thin help line: *"Use any metric name, +, -, *, /, and parentheses. Example: (Revenue * 0.7) / Orders"*. No button-builder in v1. |
| Forecast horizon copy | **"Where this is heading — next {30/60/90} days"** |
| Holdout-lift annotation | *"This {channel/product/etc.} is bringing in {€/£/$}X more per week than the holdout group (p<0.05)"* — calm, factual, no hype |
| Peer-network annotation | *"You're in the top {N}% of stores in your category for this."* — calm, no aggressive comparison |

---

## 11. Devil's advocate / pre-mortem

**If a paying Pro merchant reported it broken at 9am tomorrow,
where would the bug be?**

| Risk | Mitigation |
|---|---|
| Report execution SQL injection via `metric` / `dimension` strings | Both fields validated against ALLOWED enums at API boundary; query is built from parametrized clauses, never string concat |
| Two merchants have reports with identical names cross-shop | UNIQUE (shop_domain, name) constraint; cross-shop queries already tenant-isolated |
| Scheduled report sent to wrong merchant | Each digest run reads `merchant_saved_reports` filtered by `shop_domain` of the recipient; no cross-shop leak path |
| Pivot table with 1000+ rows kills the dashboard | LIMIT 1000 enforced server-side; UI paginates client-side (50/page) |
| Storage growth at 10k merchants | Saved reports are config rows (~500 bytes each), not data. 10k × 5 = 50k rows. Negligible. |
| Compare-toggle drift between Reports and dashboard | Reuses the same `resolve_compare_utc_bounds` chokepoint — single source of truth |
| Custom date range outside aggregation worker's coverage | Worker pre-aggregates at day granularity for last 365d; queries beyond that fall back to live-aggregate from shop_orders with explicit "older than 1y" warning |
| Concurrent saves on same report (two tabs) | `updated_at` timestamp is the lww winner; loud warning shown if updated_at drifted between fetch + save |

**What I have NOT tried to disprove yet** (will verify during build):
- Whether `survey_responses.answer_choice` as a dimension exposes
  too few rows for meaningful slicing on small merchants — may
  add a "needs N rows" empty-state hint per dimension.
- The "swap which report fills the daily digest" interaction —
  needs UX walkthrough on the email orchestrator side.

---

## 12. Effort breakdown

| Task | Effort | Tier |
|---|---|---|
| Migration + model (incl. `formula`, `forecast_horizon` cols) + GDPR cascade | 0.5d | TIER_2 |
| Backend endpoints + report executor + forecast wiring | 1.5d | TIER_0 |
| Holdout-lift wiring (reuses `execution_baselines` pipeline) | 0.5d | TIER_0 |
| Peer-network overlay wiring (reuses `vertical_benchmarks` aggregates) | 0.5d | TIER_0 |
| Reports Hub page | 0.5d | TIER_0 |
| Builder wizard (incl. formula input + forecast toggle + moat overlays) | 1.0d | TIER_0 |
| Saved-report viewer + scheduling | 0.5d | TIER_0 |
| Tests (pytest + vitest) | 0.5d | TIER_0 |
| Sidebar nav entry + atomic deploy | 0.25d | TIER_0 |
| **Total** | **~6d** | mixed |

Only anomaly detection callouts (§4.9) are explicitly out of this
budget — separate future sprint.

TIER_2 = founder approval on schema migration. Same blanket-grant
flow used for Gap #7.

---

## 13. Success criteria

- ✅ `/app/reports` renders for both Lite and Pro; standard
  surfaces tile-link + export-button work
- ✅ Pro merchant builds + saves + exports a report end-to-end
- ✅ Scheduled digest content swaps to the saved report when
  `scheduled=true`
- ✅ All preflight audits green; pytest 100% pass; vitest 100%
- ✅ Mobile layout legible (stepper), tablet+desktop = three-column
- ✅ Tenant isolation verified; no cross-shop access path

---

**Ready for founder review of items §10.1–5. On approval I proceed
Task #13 → #19 in sequence.**
