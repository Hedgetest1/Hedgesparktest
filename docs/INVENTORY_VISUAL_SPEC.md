# Inventory KPIs — Visual Spec

**Born 2026-04-28 from Gap #4 of `project_brutal_audit_0_70_2026_04_27.md`**
(BLOCCANTE under the 0-60 parity doctrine).

Every $0-60 Shopify analytics tool ships inventory KPIs in their
entry tier — Mipler $19 has full inventory reports, Shopify Stocky
ships free for Plus / basic for non-Plus, and dedicated tools like
Inventory Planner $349+ only differentiate at the multi-warehouse
forecasting end. For our €39 Lite tier the parity baseline is the
Mipler-class report set: low stock, out of stock, days of cover,
sell-through rate, reorder alerts.

This spec is the contract for: a daily inventory snapshot pipeline
(uses existing `read_products` OAuth scope — **no new Shopify
permission required**), 5 backend KPI endpoints, an Inventory KPIs
dashboard card under Customer Insights, and a "Stock at risk" feed
in the daily digest existing-slot.

---

## 0. Why this architecture (and what we ruled out)

| Option | Verdict |
|---|---|
| New OAuth scope `read_inventory` | ❌ **REJECTED** — re-OAuth requires every existing merchant to re-grant the app, and the simple aggregate `inventory_quantity` is already exposed via `read_products`. We can ship full Lite parity without scope expansion. |
| Multi-location `read_inventory` + `read_locations` | ❌ Out of band — multi-location is Pro/Scale moat, not $0-60 parity. Future Pro sprint. |
| **Daily snapshot via Shopify Admin REST `/products.json`** | ✅ **CHOSEN** — uses existing scope, fits within rate limits (40 req/min/store), aggregation_worker pattern already proven. |
| Real-time webhooks for inventory updates | ❌ Shopify `inventory_levels/update` requires PCD approval for non-Plus. Out of band. Daily snapshot covers 95% of merchant queries (low-stock alerts, days-of-cover). |
| Build directly on top of existing `get_product_inventory(product_url)` helper | ✅ **CHOSEN** — extend the helper to bulk-fetch all variants, snapshot daily |

**Reference:** `feedback_audit_pro_scale_before_build.md` — audit
results below.

---

## 1. Existing HedgeSpark machinery (the foundation we reuse)

| Existing primitive | Where | Role in this spec |
|---|---|---|
| `read_products` OAuth scope | `app/api/shopify_oauth.py` | Already granted; covers `variant.inventory_quantity` aggregate. **No new scope needed.** |
| `get_product_inventory(db, shop_domain, product_url)` | `app/services/shopify_admin.py` | Existing per-product helper. Extended to bulk-fetch variant list. |
| `aggregation_worker.py` (5min cycle) | `app/workers/aggregation_worker.py` | New daily inventory snapshot phase added here. Worker is already a singleton (no race on writes). |
| `shop_orders` daily aggregation | various rollup paths | Used for the SALES RATE side of days-of-cover computation. |
| `_CardStates` + `useCardFetch` | dashboard primitives | Card consumes via standard pattern. |
| Existing daily/weekly digest slots | `email_orchestrator` | Stock-at-risk feed inserted into the daily digest content (no new email channel). |

The pipeline DOES NOT add new external dependencies. Shopify Admin
API is already the canonical source for product data; we just add
a daily inventory_quantity snapshot column.

---

## 2. The three surfaces

| Surface | Audience | Content |
|---|---|---|
| **Inventory KPIs card** (`/app/`, under Customer Insights) | All tiers | Current low-stock count + out-of-stock count + days-of-cover for top-revenue products + 1-line headline "X SKUs need a reorder soon" |
| **Inventory drawer** (click the card) | All tiers | Full per-product table: name, current qty, days of cover, sell-through 30d, reorder threshold, recommended reorder qty |
| **Daily digest stock alert** | All tiers (slot-replacement) | If 1+ SKU is below reorder threshold, the digest's daily content includes "Stock at risk: <name> — <X> days of cover" |

NotificationBell pulse: NONE (warm Lite, not push-spam — per §3.1
originality constraint).

---

## 3. Inventory KPIs dashboard card

### 3.1 Position & class

- Section: **Customer Insights** (Lite + Pro), positioned next to
  the post-purchase survey card from Gap #7.
- Tile class: medium (per `feedback_card_proportions_consistency.md`)
- Title: amber `#e8a04e`, extrabold 1.25rem → *"Stock health"*
- Subtitle (one line): *"Where your stock is heading."*

### 3.2 Card content

```
Stock health
Where your stock is heading.

  3                   12
  Out of stock        Days of cover, top product

  ⚠ 2 SKUs need reorder soon
  • Blue Hoodie — 4 days left
  • Red Mug — 6 days left

  See full inventory →
```

Calm, factual, merchant-friendly. No urgency-trick framing.

### 3.3 States

- **Loading**: `<CardSkeleton />`
- **Empty (no inventory data yet)**: `<CardEmpty title="We're listening" subtitle="Your first inventory snapshot lands within 24h of install." />`
- **Error**: `<CardError onRetry={...} />`
- **Populated**: as above

---

## 4. Inventory drawer

Opened by clicking the card. Shows a paginated table:

| Column | Source | Format |
|---|---|---|
| Product | `inventory_snapshots.product_title` | Truncated 50 chars |
| Current qty | `inventory_snapshots.inventory_quantity` (latest) | Number |
| Sales rate (30d) | `shop_orders.line_items` aggregated | `X.X / day` |
| Days of cover | `current_qty / sales_rate` | Days, color-coded (≤7=rose, ≤30=amber, >30=emerald) |
| Sell-through 30d | `units_sold_30d / (units_sold_30d + current_qty)` | `XX%` |
| Reorder hint | `current_qty - (sales_rate * lead_time_days)` | "Reorder soon" / "OK" |

`lead_time_days` defaults to 14 (industry median). Per-shop
override UI under `/app/settings/inventory` is
**(R-blocker:sprint>1d)** — out of scope for this commit; v1
ships with the 14-day default. Trigger to un-park: 1+ merchant
explicitly requests override OR vertical benchmarks show >25%
of merchants would benefit from custom values.

CSV export reuses existing `<ExportButton surface="inventory">`
pattern (new surface added to lite_export.py).

---

## 5. Backend contract

### 5.1 Schema (Alembic migration — TIER_2)

```sql
CREATE TABLE inventory_snapshots (
  id BIGSERIAL PRIMARY KEY,
  shop_domain TEXT NOT NULL,
  product_url TEXT NOT NULL,                    -- canonical key (matches product_metrics)
  product_title TEXT,
  variant_id TEXT,                              -- NULL when aggregating across variants
  inventory_quantity INTEGER NOT NULL,
  snapshot_date DATE NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_inventory_shop_product_date UNIQUE
    (shop_domain, product_url, COALESCE(variant_id, ''), snapshot_date)
);

CREATE INDEX idx_inventory_shop_date
  ON inventory_snapshots (shop_domain, snapshot_date DESC);

-- Partial index for the "current state" query (latest row per product)
CREATE INDEX idx_inventory_shop_product_latest
  ON inventory_snapshots (shop_domain, product_url, snapshot_date DESC);
```

Storage estimate: 10k merchants × 50 SKUs × 90d retention =
45M rows ≈ 5GB. Fits within Postgres budget.

Retention: `retention_task.py` purges rows older than 90 days.
Sales-rate computation uses 30d window, so 90d snapshot retention
is the safe floor.

### 5.2 Endpoints (all `require_merchant_session`)

| Method | Path | Body / Query |
|---|---|---|
| GET | `/merchant/inventory/kpis` | Returns headline numbers (out_of_stock_count, low_stock_count, days_of_cover_top, top_at_risk[]) |
| GET | `/merchant/inventory/details` | Paginated full table for the drawer (name, qty, sales rate, days_of_cover, sell_through, reorder_hint) |
| GET | `/merchant/inventory/snapshot-status` | Returns last_fetched_at + count of products tracked + worker health |
| GET | `/analytics/export?surface=inventory&format=csv` | CSV export of full inventory table |

### 5.3 Aggregation worker phase

`aggregation_worker.py` adds a new phase that fires daily at the
shop's local 03:00 (offpeak):

```
for shop in eligible_shops_today():
    products = shopify_admin.bulk_get_products(shop)
    snapshots = []
    for p in products:
        for variant in p.variants:
            snapshots.append({
                shop_domain: shop,
                product_url: p.handle_to_url(),
                product_title: p.title,
                variant_id: variant.id,
                inventory_quantity: variant.inventory_quantity,
                snapshot_date: today_in_shop_tz(shop),
            })
    upsert_snapshots(snapshots)
```

Rate limit handling: Shopify Admin REST → 40 req/min/store. Bulk
products endpoint paginates 250/page. Per merchant: 50 SKUs ≈ 1
page = 1 request. At 10k merchants spread over a 24h window =
~7 req/min average — well below ceiling.

Failure mode: on Shopify rate-limit (429), back off + retry next
cycle. On 401 (token revoked), skip + flag the merchant. On 5xx,
retry once + skip.

### 5.4 KPI computation

```
days_of_cover = current_qty / (units_sold_last_30d / 30.0)
sell_through_30d = units_sold_last_30d / (units_sold_last_30d + current_qty)
low_stock = days_of_cover <= 14   # configurable, lead_time_days default
out_of_stock = current_qty == 0
top_at_risk = top 3 by lowest days_of_cover with current_qty > 0
```

Edge cases:
- `units_sold_last_30d == 0` → days_of_cover = ∞ → render *"No recent sales"*
- `current_qty == 0` → "Out of stock — restock to recover sales"
- Sales rate volatile → smoothed via 7-day rolling average

---

## 6. Privacy + scale invariants

- **No PII**: inventory data is product-level, not customer.
- **GDPR Art. 17**: `gdpr_processor.py` cascade extended with
  `inventory_snapshots`.
- **10k-merchant scale**:
  - Aggregation worker spreads writes over 24h
  - Index `idx_inventory_shop_product_latest` covers the "current state" query
  - 90d retention via `retention_task.py`
  - No per-request Admin API call (cached in our DB)
- **Schedule cap**: same as digest exemption — stock alert is part
  of the existing daily digest content, not a new email channel.

---

## 7. TIER classification + founder approval

| Item | Tier | Approval needed |
|---|---|---|
| Migration (`migrations/zzzc_inventory_snapshots.py`) | **TIER_2** | **Explicit founder GO required for the schema** — see below |
| Backend endpoints + aggregation worker phase | TIER_0 | Sprint-scoped grant from earlier "procedi" |
| Dashboard card + drawer | TIER_0 | – |
| OAuth scope expansion | – | **NOT REQUIRED** — uses existing `read_products` |
| Tests (pytest + vitest + smoke) | TIER_0 | – |

**TIER_2 schema ask (explicit):**

```sql
CREATE TABLE inventory_snapshots (...)  -- as in §5.1
ALTER … extend gdpr_processor cascade
```

Per `feedback_settings_is_tier_agnostic_chrome.md` and the
session-scope grant pattern, the migration is the only TIER_2
artifact in this sprint. No OAuth touch, no billing, no webhook.

---

## 8. Test plan

### 8.1 pytest

- happy path: snapshot insert + KPI computation
- tenant isolation: shop A cannot read shop B inventory
- empty state: 0 snapshots → KPI returns "no data yet" shape
- volatile sales: 7-day rolling average stabilises days-of-cover
- low_stock threshold respects shop-configured `lead_time_days`
  (when `merchant.inventory_lead_time_days` is non-NULL)
- aggregation worker: rate-limit retry path + 401 token-revoked path
- retention: rows >90d purged

### 8.2 vitest

- `<InventoryKpisCard />` cold-start
- populated state with 3 at-risk SKUs
- error state + retry
- drawer table sorts by days_of_cover ascending by default

### 8.3 Preflight audits

- response_model: all endpoints
- input_bounds: pagination params capped
- tenant_isolation: all 4 endpoints
- gdpr_redact_coverage: inventory_snapshots added
- alembic check: clean
- audit_dashboard_a11y: card states

### 8.4 Synthetic worker test

- Mock Shopify Admin response with 50 products × 2 variants
- Run worker phase
- Assert 100 snapshot rows for the shop on today's date
- Verify next run UPSERTs (no duplicate today rows)

---

## 9. Voice (founder direction 2026-04-28)

Calm, merchant-friendly. Defaults applied:

| Element | Default copy |
|---|---|
| Card title | **Stock health** |
| Card subtitle | *"Where your stock is heading."* |
| Headline state | *"X SKUs need a reorder soon"* (calm, no exclamation) |
| Out of stock | *"Out of stock"* (factual) |
| Days of cover | *"X days of cover"* (no urgency-trick framing) |
| Reorder hint | *"Reorder soon"* / *"OK"* (binary, calm) |
| Empty state | *"We're listening — first snapshot within 24h"* |
| Edge: no sales | *"No recent sales — set a 30-day target before stock decisions"* |

---

## 10. Devil's advocate / pre-mortem

| Risk | Mitigation |
|---|---|
| Shopify Admin API rate-limit (40 req/min/store) at 10k merchants | Aggregation spread over 24h window; per-merchant 1-2 req/day average |
| Stale snapshot during the day (merchant restocks at 14:00, snapshot fired at 03:00) | "Last updated X hours ago" timestamp on card; manual "Refresh now" button bypasses cache |
| Days-of-cover for new products (no sales history) | Render *"No recent sales — needs more data"* (calm, not "infinite cover") |
| Multi-location merchant: aggregate qty hides per-location stockout | Surface in card as *"Total across locations: X"*; per-location is Pro/Scale future |
| Token revocation mid-cycle | Worker handles 401 → flag merchant; existing `merchant.install_status` already tracks this |
| Storage growth at 10k merchants × 1000 SKUs (large catalogs) | Retention 90d + index on (shop_domain, snapshot_date) keeps queries bounded |

---

## 11. Effort breakdown

| Task | Effort | Tier |
|---|---|---|
| Migration `inventory_snapshots` + GDPR cascade + Merchant.inventory_lead_time_days col | 0.5d | **TIER_2** |
| Bulk inventory fetcher + aggregation worker phase + retention | 1.0d | TIER_0 |
| 4 backend endpoints + KPI computation + tests | 0.75d | TIER_0 |
| Dashboard card + drawer + CSV export wiring | 0.75d | TIER_0 |
| Tests (pytest + vitest + worker synthetic) | 0.5d | TIER_0 |
| Atomic deploy + pipeline integration (invariant_monitor for retention age) | 0.5d | TIER_0 |
| **Total** | **~4d** | mixed |

Pipeline integration this time follows the proven Gap #1 pattern:
invariant_monitor check (snapshot freshness < 36h) + observability
spike detector covers /merchant/inventory/* via existing detectors.

---

## 12. Success criteria

- ✅ Daily worker writes snapshots for every active merchant within 24h of install
- ✅ Card renders for all tiers with correct numbers
- ✅ Drawer paginates correctly, CSV export works
- ✅ Stock-at-risk SKUs surface in daily digest
- ✅ All preflight audits green; pytest 100% pass; vitest 100% pass
- ✅ E2E smoke covers the card → drawer → CSV flow

---

**Ready for founder review of:**
1. The 5 voice defaults in §9 — calm merchant-friendly OK?
2. Default `lead_time_days` = 14 — OK or different?
3. Card title *"Stock health"* + subtitle *"Where your stock is heading."* — voice OK?
4. **TIER_2 grant on the migration** (`inventory_snapshots` + 1 column on `merchants`) — explicit GO required.

On approval I proceed: migration → worker phase → endpoints → card → drawer → tests → atomic deploy. ~4d.
