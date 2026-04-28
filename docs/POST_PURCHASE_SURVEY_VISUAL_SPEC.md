# Post-Purchase Survey — Visual Spec

**Born 2026-04-28 from Gap #7 of `project_brutal_audit_0_70_2026_04_27.md`**
(BLOCCANTE under the 0-60 parity doctrine).

The 7th burning gap from the brutal $0-70 audit: KnoCommerce
(45%+ response rate), Fairing, Zigpoll, Pathlight all ship
**post-purchase attribution surveys** as their core wedge — and
**Pathlight + Zigpoll Lite are free**, putting this feature inside
the 0-60 parity envelope.

This spec is the contract for the customer-facing capture surface
(Shopify Checkout UI Extension, native on-page on every Shopify
tier) + backend storage + dashboard card + Pro customization +
NotificationBell integration. Built atomically; the extension
infrastructure unlocks future survey questions (NPS, product
feedback, churn-intent) without re-bootstrapping.

---

## 0. Why this architecture (and what we ruled out)

| Option | Coverage | Response rate | Verdict |
|---|---|---|---|
| `spark-tracker.js` script tag on Thank-You | ❌ blocked | n/a | Shopify sandbox forbids arbitrary script tags on checkout/thank-you; this is exactly why `spark-pixel.js` exists |
| Email-link survey from merchant template | ✅ all tiers | 10-15% | Sub-parity vs every $0-70 competitor (45%+); rejected |
| Email survey from `hello@hedgesparkhq.com` | ⚠️ deliverability | 8-12% | Phishing-perception risk + DPA complexity; rejected |
| **Shopify Checkout UI Extension** | ✅ all tiers | 45%+ | **CHOSEN** — industry-standard, native, sandbox-trusted |
| Post-purchase Page Extension (legacy) | ⚠️ Plus-only | 50%+ | Plus exclusivity kills Lite parity; rejected |

**Reference:** `shopify.dev/docs/apps/build/checkout/thank-you-order-status/add-survey`
— extension targets `purchase.thank-you.block.render` and
`customer-account.order-status.block.render` are available to
Basic/Starter/Advanced/Plus alike (since Aug 2024).

---

## 1. The three surfaces

| Surface | Audience | Built with |
|---|---|---|
| **Survey card (Thank-You + Order-Status)** | merchant's customers | Shopify Checkout UI Extension (Preact + Polaris) |
| **Dashboard card "How customers find you"** | merchant | Next.js (canonical _CardStates primitives) |
| **Settings: customize question** (Pro only) | merchant | Next.js form |

NotificationBell pulse + SparkChat reveal reuse existing primitives
(no new email channel — §3.1 originality constraint).

---

## 2. Customer-facing surface — the Shopify extension

### 2.1 Layout

- **Inline block** rendered as the LAST card on Thank-You page
  (below "What's next?" Shopify-default content) — does not block
  the order summary.
- Mobile-first; full-width within the Polaris content column.
- One question at a time. No multi-page flow on Lite.
- Card states: pre-submit (chips), submitting (spinner), post-submit
  ("Thanks!" with subtle ✓), dismissed (collapses).

### 2.2 Default question (Lite preset, customizable in Pro)

> **How did you hear about us?**
>
> [ Instagram ]  [ TikTok ]  [ Google ]  [ Friend ]  [ Email ]
>
> [ Other → reveals 1-line text input ]

Polaris components used: `Stack`, `Heading`, `Text`, `Choice List`
(or `Button` chips), `Text Field`, `Button` (submit), `Icon`
(dismiss).

### 2.3 Behavior

1. On `purchase.thank-you.block.render` mount → fetch
   `GET /survey/config?shop={shop_domain}` (Redis-cached 10min).
2. Render question + chips. Chip-select highlights one option,
   reveals "Other" text input only when "Other" is selected.
3. Submit button enabled when ≥1 chip selected (or Other text ≥1
   char).
4. POST `/survey/response` with `{shop_domain, order_id,
   question_key, answer_choice, answer_text, consent_given}`.
5. On 200 → morph to "Thanks!" with subtle ✓ icon, fade out after
   3s.
6. On dedup (409) → silently render "Already answered, thanks ✓".
7. On error → fail silently (no error UI to customer; backend logs
   via `_hsReportErr`).
8. Local dedup: `shopify.storage.local.set("hs_survey_<order_id>",
   "submitted")` so refresh doesn't re-render.

### 2.4 What we DO NOT do (Lite-warm constraints)

- ❌ no popup/modal/overlay (warm, not interruptive)
- ❌ no multi-question flow (1 question only on Lite)
- ❌ no email follow-up (warm, no spam — §3.1)
- ❌ no PII collection (no name/email asked; order_id only)
- ❌ no third-party tracking pixels in the extension

### 2.5 Consent

Shopify Checkout extensions inherit the merchant's customer
privacy consent state via `shopify.customerPrivacy.consent`.
Pre-submit, the extension reads consent. If `analytics === false`
the extension renders a minimal "Thanks for your order!" message
without the survey prompt — no fetch, no storage write.

---

## 3. Merchant-facing dashboard card

### 3.1 Position & class

- **Section:** Customer Insights (Lite + Pro both — per 0-60 parity)
- **Tile class:** medium (per `feedback_card_proportions_consistency.md`)
- **Adjacent to:** existing acquisition/customer cards in Customer
  Insights section

### 3.2 Content

- **Title:** amber `#e8a04e`, extrabold 1.25rem
  → *"How customers find you"*
- **Body:** horizontal bar chart, top option highlighted in amber,
  others slate. Last 30d window (respects global date range picker
  from Phase 3B).
- **One-line summary above chart:**
  → *"Last 30 days: <X>% of <N> shoppers heard about you via
  <top_option>"*
- **Footer link:** *"See all responses →"* opens drawer with full
  list (text answers redacted past PII guard).

### 3.3 Card states (canonical _CardStates primitives, mandatory)

- **Loading:** `<CardSkeleton />`
- **Error:** `<CardError onRetry={...} />`
- **Empty (no responses yet):** `<CardEmpty title="We're listening"
  subtitle="First survey response will appear here within 24h of
  your next order" eta={null} />`
- **Populated:** bar chart + summary

### 3.4 Hook used

`useCardFetch<SurveyAggregate>('/merchant/survey/aggregate?range=last_30_days')`
— typed fetch with automatic loading/error/empty transitions
(no silent `.catch(() => {})` — that's a §4 regression).

---

## 4. Settings — Pro only

### 4.1 Path

`/pro/settings/surveys`

### 4.2 Form

| Field | Constraint |
|---|---|
| Question prompt | textarea, max 80 chars, default "How did you hear about us?" |
| Options list | 3-8 entries, drag-reorder, max 24 chars per option |
| "Allow free text (Other)" | toggle, default ON |
| "Show on Order Status page too" | toggle, default ON |

Live preview pane on the right shows the rendered Polaris card.

### 4.3 Save flow

`PUT /pro/survey/config` → invalidates Redis cache key
`hs:survey_cfg:v1:{shop}`. Extension picks up new config on next
fetch (next order; not retroactive).

### 4.4 Lite tier

Settings page **read-only** with the preset; "Customize → Upgrade
to Pro" CTA in amber filled button below the preview. Same lock
pattern as other Pro features.

---

## 5. NotificationBell + SparkChat integration (§3.1 originality)

When the FIRST response of the day arrives for a merchant:

1. NotificationBell pulses (existing primitive, amber dot).
2. Click bell → Spark dropdown reveals tile:
   *"Today's first survey answer just landed: '<top_choice>'"*
3. Click tile → navigate to dashboard with anchor `#how-customers-find-you`.

**No email channel.** No daily/weekly digest line. Lite-warm.

Backend trigger: `survey_responses` insert → if it's the first row
today for the shop → `Redis SETNX hs:survey:first_today:{shop}:{date}
ex=86400` → on success, push `notification_bell_pulse(shop, type=
"survey_response", choice=<choice>)`.

---

## 6. Backend contract

### 6.1 Schema (Alembic migration)

```sql
CREATE TABLE survey_responses (
  id BIGSERIAL PRIMARY KEY,
  shop_domain TEXT NOT NULL,
  order_id TEXT NOT NULL,
  question_key TEXT NOT NULL DEFAULT 'how_did_you_hear',
  answer_choice TEXT,
  answer_text TEXT,
  consent_given BOOLEAN NOT NULL DEFAULT false,
  client_ip_hash TEXT,        -- sha256(ip + daily_salt), no raw IP
  user_agent_hash TEXT,        -- sha256(ua), for rate-limit dedup
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT survey_responses_unique UNIQUE
    (shop_domain, order_id, question_key)
);

CREATE INDEX idx_survey_responses_shop_created
  ON survey_responses (shop_domain, created_at DESC);

CREATE INDEX idx_survey_responses_choice
  ON survey_responses (shop_domain, question_key, answer_choice)
  WHERE answer_choice IS NOT NULL;
```

`merchant_settings` extended with:

```sql
ALTER TABLE merchant_settings
  ADD COLUMN survey_question TEXT DEFAULT 'How did you hear about us?',
  ADD COLUMN survey_options JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN survey_allow_other BOOLEAN DEFAULT true,
  ADD COLUMN survey_show_on_order_status BOOLEAN DEFAULT true;
```

### 6.2 Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/survey/config?shop=<domain>` | public | Redis-cached 10min via `hs:survey_cfg:v1:{shop}` |
| POST | `/survey/response` | public, HMAC-signed by extension | rate-limit 3/min/IP, 1/order via UNIQUE; PII guard on `answer_text` |
| GET | `/merchant/survey/aggregate?range=...` | merchant session | last-N-days distribution, respects date range picker |
| PUT | `/pro/survey/config` | Pro session (`require_pro_session`) | invalidates Redis cache |

### 6.3 PII guard wiring

`answer_text` runs through `app.core.llm_pii_guard.scan_for_pii()`
at the API boundary (same regex stack used for LLM calls). Hits
detected → row stored with `answer_text=NULL` + counter
`hs:survey:pii_violations:{date}` incremented. Original snippet
NEVER logged.

### 6.4 Rate limit

- Per-IP: 3 responses / 60s (Redis SETEX `hs:survey:rl:{ip_hash}`)
- Per-order: 1 response / question_key (DB UNIQUE)
- Per-shop daily cap: 10k responses / day (sanity ceiling, alerts
  via `ops_alert` if exceeded)

### 6.5 Extension HMAC

The extension's POST is signed using the same per-shop pixel
secret as `spark-pixel.js` (existing pattern). Backend verifies
`X-Spark-Signature` header against the shop's secret.

---

## 7. GDPR + privacy

| Concern | Mitigation |
|---|---|
| **Art. 5 retention** | 365d default; `retention_task.py` cron purges rows older than 365d |
| **Art. 17 erasure** | `gdpr_processor.py` extends to `survey_responses` cascade by `shop_domain` (not by customer; we never tie response to customer email) |
| **Art. 21 object** | Customer-side: extension respects `shopify.customerPrivacy.consent.analytics` — false → no fetch, no submit |
| **Art. 32 security** | HMAC-signed POST, no PII in answer_text (regex-blocked), IP hashed with daily salt rotation |
| **DPA** | No new processor — Shopify hosts the extension UI in their sandbox; we only receive the response payload via our own API |

No PII collected by design. The customer is identified only by
`order_id` (already a pseudonym from the merchant's perspective).
This is the lowest-risk GDPR profile of any customer-facing
HedgeSpark surface.

---

## 8. 10k-merchant scale invariants

- ✅ Index `idx_survey_responses_shop_created` covers the
  dashboard aggregate query (last-30d range, shop-scoped).
- ✅ Partial index on `answer_choice` accelerates the
  `GROUP BY answer_choice` count.
- ✅ Redis cache `hs:survey_cfg:v1:{shop}` 10min TTL → ~6/hr/merchant
  fetch worst case → 60k req/hr at 10k merchants → trivial.
- ✅ No N+1: aggregate query is a single GROUP BY; dashboard reads
  it directly (no per-row enrichment).
- ✅ Per-shop daily cap (10k responses) prevents runaway storage.
- ✅ No new worker process; the NotificationBell pulse is
  triggered inline from the POST handler (cheap; one Redis SETNX).

Storage estimate: at 10k merchants × 100 orders/mo × 30%
response rate = ~300k rows/mo → ~36MB/yr. Negligible.

---

## 9. Versioning + deploy path

### 9.1 Repo layout (NEW)

```
/opt/wishspark/
├── shopify/                       # NEW — Shopify CLI app skeleton
│   ├── shopify.app.toml           # links to existing Partners app
│   ├── shopify.web.toml           # references our FastAPI as the web component
│   └── extensions/
│       └── post-purchase-survey/
│           ├── shopify.extension.toml
│           ├── package.json       # @shopify/ui-extensions, preact
│           └── src/
│               ├── ThankYou.jsx   # purchase.thank-you.block.render
│               └── OrderStatus.jsx # customer-account.order-status.block.render
```

### 9.2 Bootstrap (one-time, Task #2)

- `npm i -g @shopify/cli @shopify/app`
- `cd /opt/wishspark/shopify && shopify app config link --client-id=<existing>`
- The existing OAuth app's `client_id` is the Partners app we link
  to. No new Partners app created.
- Add `shopify` directory to repo root. Lint/test scripts ignore
  it (separate package.json).

### 9.3 Extension version pinning

- `SURVEY_EXTENSION_VERSION` constant in
  `app/core/tracker_version.py` (mirrors `TRACKER_VERSION` pattern).
- Bumped on every `extensions/post-purchase-survey/**` change.
- Backend's `/survey/config` returns the current version so the
  dashboard preview can show "extension v3 deployed".

### 9.4 Deploy sequence (Task #9, atomic)

```bash
cd /opt/wishspark/backend
./venv/bin/alembic upgrade head            # schema migration

pm2 restart wishspark-backend              # backend with new endpoints

cd /opt/wishspark/dashboard
./scripts/deploy.sh                        # dashboard build + audit_dashboard_live --strict

cd /opt/wishspark/shopify
shopify app deploy --version=$SURVEY_EXTENSION_VERSION  # extension ship

# Verify on dev store
curl -s 'http://127.0.0.1:8000/survey/config?shop=hedgespark-dev.myshopify.com' \
  | python3 -m json.tool
```

`deploy.sh` extended to include the `shopify app deploy` step.
Failure of any step aborts the chain.

---

## 10. Test plan

### 10.1 pytest (backend)

- `test_survey_response_happy_path` → POST returns 200, row written
- `test_survey_response_dedup` → second POST same order → 409
- `test_survey_response_pii_blocked` → email/phone in `answer_text`
  → row stored with NULL text + counter incremented
- `test_survey_response_rate_limit` → 4th request in 60s → 429
- `test_survey_response_tenant_isolation` → shop A cannot read shop
  B aggregate
- `test_survey_config_redis_cache` → second GET hits cache, no DB
- `test_survey_config_pro_update_invalidates_cache` → PUT clears

### 10.2 vitest (dashboard)

- `HowCustomersFindYouCard` cold-start (empty state copy)
- populated (bar chart + summary)
- single-option-dominant (top option highlighted)
- error + retry button
- Lite read-only Settings tile shows lock + Upgrade CTA
- Pro Settings drag-reorder works, validates 3-8 options

### 10.3 Preflight audits (mandatory pass)

- `audit_sql_schema.py` ← survey_responses + merchant_settings cols
- `audit_tenant_isolation.py` ← all 4 endpoints scoped by shop
- `audit_dashboard_live.py --strict` ← extension preview ref still
  resolves
- `audit_silent_returns.py` ← no `.catch(() => {})` in card
- `audit_jsonb_array_length_guard.py` ← survey_options JSONB read

### 10.4 Extension smoke test

- `shopify app dev` against `hedgespark-dev.myshopify.com`
- Place test order via Shopify checkout simulator
- Verify card renders on Thank-You page
- Submit → verify row in survey_responses
- Refresh → verify "Already answered" state

---

## 11. Founder-domain residuals (need your call before ship)

Per `feedback_copy_is_founder_territory.md`, the items below are
your taste/voice/strategy calls — surface them before the build,
don't auto-decide:

1. **Default question copy** — *"How did you hear about us?"* OK,
   or different wording (e.g. *"Quick question — where'd you find
   us?"*)?
2. **Default 5 options** — Instagram / TikTok / Google / Friend /
   Email — keep this set, or different mix? (Reddit, YouTube,
   Podcast, Influencer, In-store, Print ad?)
3. **Card title on dashboard** — *"How customers find you"* OK?
   Alternatives: *"Where they came from"* / *"Acquisition source"*
   / *"Discovery channels"*.
4. **Lite default behavior** — show extension by default after
   merchant onboarding (zero merchant action needed), or require a
   one-click toggle in Settings to enable? (Default-on = better
   data; default-off = warmer / more consent-respectful.)
5. **Polaris vs custom branding** — accept the Shopify-native
   Polaris look (no full HedgeSpark visual; just merchant logo at
   top), or invest in a brand-skinned variant? (Polaris is the
   industry norm and faster.)

Awaiting your input on items 1-5 before I start Task #2 (bootstrap).

---

## 12. Devil's advocate / pre-mortem

**If this shipped and a paying merchant reported it broken at 9am
tomorrow, where would the bug be?**

| Risk | Mitigation in this spec |
|---|---|
| Customer reloads Thank-You → submits twice | DB UNIQUE constraint (shop+order+key) + `shopify.storage` localStorage flag — both layers |
| Extension fails on Order Status page (different sandbox) | Extension targets BOTH `purchase.thank-you.block.render` AND `customer-account.order-status.block.render`; same Preact code, same `/survey/config` endpoint |
| Refunded order shows survey | Extension fires only on Thank-You / Order-Status — refund happens BEFORE Thank-You is unreachable; OK |
| GDPR consent flips after submission | Extension reads consent before render; if `analytics === false` no fetch, no render |
| Free-text contains PII (email, phone) | `llm_pii_guard.scan_for_pii()` at boundary; row stored with NULL text + counter |
| Rate-limit bypass via IP rotation | Per-shop daily cap (10k) catches volumetric abuse even if per-IP fails |
| Shopify deprecates extension target | `shopify.dev` lists these targets as STABLE since 2024-08; we pin extension version + monitor `Shopify-API-Deprecated-Reason` header |
| Merchant churns (Lite→cancel) | `gdpr_processor.uninstall_erasure` cascades survey_responses by shop_domain |
| 10k merchants × spike day | Index + partial index + Redis cache → 60k req/hr trivial; per-shop daily cap prevents tail |

**What I have NOT tried to disprove yet** (will verify during build):
- Polaris `Choice List` vs `Button[]` chip pattern — visual A/B in
  the extension dev preview (Task #5)
- Localhost extension dev-server reachability via tunnel (Shopify
  CLI uses ngrok-equivalent) — verify before declaring extension
  done

---

## 13. Effort breakdown

| Task | Effort | Tier |
|---|---|---|
| #2 Bootstrap Shopify CLI skeleton + link Partners | 0.5d | TIER_1 (touches OAuth-adjacent infra) |
| #3 Migration | 0.25d | TIER_2 (`migrations/`, needs your approval per §10) |
| #4 Backend endpoints + PII guard | 1.0d | TIER_0 |
| #5 Shopify Checkout UI Extension | 1.5d | TIER_1 (storefront-equivalent, runs in customer browsers) |
| #6 Dashboard card + NotificationBell pulse | 0.5d | TIER_0 |
| #7 Pro Settings UI | 0.5d | TIER_0 |
| #8 Tests (pytest + vitest + extension smoke) | 0.5d | TIER_0 |
| #9 Atomic deploy + verify | 0.25d | TIER_0 |
| **Total** | **~5d** | mixed |

TIER_1 items (#2, #5) require you to authorize blanket TIER_1
scope for this sprint per `feedback_session_scoped_tier_approval.md`,
or per-step approval. Migration #3 is TIER_2 → explicit approval
on the schema before I run `alembic upgrade head`.

---

## 14. Success criteria

- ✅ Survey card renders on Thank-You + Order-Status across 3
  Shopify themes (Dawn, Refresh, Sense) on dev store
- ✅ Response submitted → row in `survey_responses` within 500ms
- ✅ Dashboard card "How customers find you" populates within 24h
  of first response
- ✅ NotificationBell pulses on first response of day, fades on
  click
- ✅ Pro Settings save invalidates Redis cache; extension picks up
  on next order
- ✅ All preflight audits green; pytest 100% pass; vitest 100%
  pass; `audit_dashboard_live --strict` confirms chunks
- ✅ Lite tier sees the same data card as Pro (parity), Pro adds
  customization layer

---

**Ready for founder review of items 1-5 in §11. On approval (and
on TIER_1+TIER_2 grant), execution proceeds Task #2 → #9 in
sequence per the breakdown above.**
