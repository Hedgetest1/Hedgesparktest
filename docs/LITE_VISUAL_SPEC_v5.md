# /app Lite — Visual Spec v5 (2026-04-21, "Spark Daily")

**Status:** Approved direction 2026-04-21 — primary-slot reframe of `/app`
when `tier === "lite"`. Supersedes v4 for the primary-slot layout. v4's
cassettone drawer structure is preserved verbatim inside the new Deeper
drawer → Retention tab (see §7). Nothing is deleted.

## 0. Why v5 exists

v4 made each cassettone rich (drawer storytelling inline). Good. But
**above the cassettoni**, the Lite primary slot accumulated 6 full
sections across ~14 charts/tables/waterfalls/cohort grids — Peer
benchmarks, Vertical benchmarks, P&L + MarginDrag, Channel attribution,
CohortSummary + MonthlyCohorts + GatewayProducts, then
`LiteCassettoniGrid`. A merchant on day-1 of a €15k Shopify store sees
a compendium analitico before having scrolled and doesn't know where to
look.

Founder feedback 2026-04-21:
> "Dashboard Merchant Lite non mi sembra avere quella semplicità di
> utilizzo, quei grafici e quei copy tali da avere un design/visual di
> facilità di comprensione e utilizzo a prova di scemo."

v5 re-frames the Lite primary slot as a **merchant morning brief
narrated by Spark**. Six zones, single column, narrative-first. Every
chart, table, waterfall, cohort grid from v4 is preserved — moved from
primary into a side drawer ("Deeper") one click away.

## 1. The 5 merchant-experience gates (acceptance criteria)

Founder directive 2026-04-21: "once work is done, ask yourself [these]".
Embedded here so every implementation decision is anchored to them.
**Not shipped until all five are answered YES with evidence.**

| # | Gate | Self-runnable test (CTO before merge) |
|---|---|---|
| 1 | Merchant understands in <10s | Open `/app/lite` fresh, 10s timer, articulate: "how much is leaking today + which product is the biggest leak". Pass = yes, without scrolling past Zone 3. |
| 2 | Lite is easy to use | Every primary action (see fix, go deeper, ask Spark) reachable in ≤2 taps, no hover-dependent action moves layout, no horizontal scroll at 375px, Lighthouse CLS < 0.05. |
| 3 | Drawer preserves all depth | Pick 10 random v4 primitives (DrawerExplainer / DrawerBigStat / DrawerKeyValueList / DrawerHowCalculated / DrawerNextAction). Each must be retrievable in v5 Deeper drawer within 2 clicks, with its formula/inputs/note/advice intact. Document in `/docs/lite_v5_depth_audit.md`. |
| 4 | Visuals serve storytelling + are beautiful | Each of the 3 primary charts (Leak Gauge, Week Ridge, Live Counter) passes "remove it — is the story weaker?" Must be YES. Screenshots in `/docs/lite_v5_visual_audit.md`. |
| 5 | €39 justified + competitor retirement | Side-by-side screenshots vs Lifetimely €39, Peel entry, Varos entry. Our v5 must look like the €39 product; theirs must look like €15 products. Document in `/docs/lite_v5_competitor_audit.md`. |

If gate 5 fails, we don't ship — we iterate the spec.

## 2. The 6 zones — top to bottom, single column

Reading rhythm: greeting → one-number verdict → 3 actions → weekly
trend → memory → ask. Mobile and desktop are identical vertical flow;
no multi-column in Lite.

### Zone 1 — "Spark Says" (hero narrativo, opens the day)

**Container:** rounded-3xl, `bg-gradient-to-br from-[#0e0e1a] via-[#0a0a14] to-[#0e0a1a]`, `border-white/[0.06]`, p-7 sm:p-10. Top stripe `h-[2px] bg-gradient-to-r from-transparent via-[#e8a04e] to-transparent opacity-60` (signature of the hero canon). Subtle purple blur top-right (violet = intelligence).

**Layout (desktop):** flex row — [Spark avatar 64×64 with `hs-float-gentle`, left] · [narrative 3 lines, center flex-1] · [CTA, right]. Mobile: stack avatar on top, narrative, CTA full-width.

**Copy — always 3 lines:**

1. **Greeting** (15px `text-slate-300` font-medium): `Good morning, {shop_display_name}.`
2. **Headline** (1.5rem → 1.75rem extrabold `text-cream`, leading-[1.08]): `This morning I noticed {€ total_at_risk} leaking in {count} places.` The `{€ total_at_risk}` inline in `text-[#e8a04e]`.
3. **Detail** (15px `text-slate-400`): `The biggest is {top_product} — {views} views, {carts} carts.` `{top_product}` bold cream.

**CTA** (`hs-cta-gradient`, rounded-2xl, px-6 py-3, 15px font-bold): `Show me the 3 fixes →` — scrolls smooth to Zone 3.

**Timestamp** (11px `text-slate-500`, below CTA): `Updated {HH:mm} · next refresh in {N} min`.

**Empty state (cold start, <5 min of data):**
```
Good morning, {shop_display_name}.
I'm still watching your first visitors.
Give me ~5 minutes and I'll be back with your first brief.
```
No CTA. Timestamp line reads `Watching your storefront · first brief in ~5 min`.

**Data source:** `/pro/revenue-at-risk` (total + count), top product derived from top abandoned_intent OR top RARS component with a product reference, `{shop_display_name}` from session (fallback to shop domain minus `.myshopify.com`).

### Zone 2 — "The Leak Gauge" (one-number hero)

**Container:** same canvas style, top stripe `via-[#d4893a]` (deep amber — this is *the* differentiator hero).

**Header:**
- Eyebrow (11px uppercase tracking-[0.22em] `text-[#e8a04e]`): `MONEY AT RISK · THIS MONTH`
- H2 (per SectionHeading primitive, 1.75-2rem extrabold `#e8a04e`): `The number no other Shopify tool shows you`

**Hero number:** 4.5rem mobile / 5.5rem desktop, extrabold, `text-[#d4893a]`, `tabular-nums`, `textShadow: 0 0 60px rgba(212,137,58,0.2)`. Countdown tween via existing `useCountUp` on mount + on value change.

**Inline right of hero number** (stacked on mobile): two emerald tags —
- `{€ prevented_this_week}` with small label `saved this week`
- `{€ prevented_total_month}` with small label `saved this month`

These are the 40% growth-frame counterweight to the 60% loss-frame hero.

**Below hero number — 3 Leak Bars (iOS-signal-style):**

Three horizontal rows, each:
- 11px uppercase label left: `PRODUCT LEAKS` / `CART LEAKS` / `RETENTION RISK`
- 3 vertical signal bars right (low / mid / high), active level filled:
  - `PRODUCT LEAKS` bars in `rose-400`
  - `CART LEAKS` bars in `#e8a04e`
  - `RETENTION RISK` bars in `violet-400`
- One-line interpretation below each row (13px slate-400):
  - `PRODUCT LEAKS`: `{N} products losing views without carts`
  - `CART LEAKS`: `{N} carts per day abandoned at checkout`
  - `RETENTION RISK`: `Week-4 repeat rate {delta}`

**Signal level derivation** (deterministic, per row — published in Deeper drawer methodology so merchants can audit):

| Row | Metric | Baseline | Low (1 bar) | Mid (2 bars) | High (3 bars) |
|---|---|---|---|---|---|
| Product leaks | Count of products with views>10 AND carts=0 in last 24h | shop's own 14-day rolling median of same count | current ≤ 0.8× baseline | 0.8–1.2× baseline | > 1.2× baseline |
| Cart leaks | Carts abandoned per day, rolling 3 days | shop's own 14-day rolling median of same | current ≤ 0.8× baseline | 0.8–1.2× baseline | > 1.2× baseline |
| Retention risk | Current 4-week cohort repeat rate | 8-cohort rolling average of same shop | current ≥ 95% of baseline | 80–95% of baseline | < 80% of baseline |

**Cold-start handling:**
- <14 days of store data → no baseline yet → render `Watching… first baseline ready in {N} days` in place of the 3 bars (not a faked "low" level — honesty first).
- Fewer than 10 data points in the metric window → same `Watching…` state per row.
- Once a baseline exists for ≥1 row, all rows that have baseline always render. Rows still warming show `Watching…` individually — never greyed-out, always explained.

**Why 0.8× / 1.2× (not 1.0× / 2.0×):**
- 0.8× allows small negative drift to register as "low" — rewards stability.
- 1.2× flags meaningful above-baseline leaks without being paranoid.
- Thresholds chosen so steady state (current ≈ baseline) lands squarely in "mid" — the neutral reading; the merchant sees "mid" and understands "business as usual".

**Why 95% / 80% for retention (asymmetric vs leak rows):**
- Repeat rate moves slower than leak counts. A 5% dip below cohort average is a noticeable cool-off; a 20% dip is a full segment-churn alarm.
- The asymmetric framing matches merchant intuition that "retention drift" is scarier than "retention flatness" — mid/high thresholds are tighter than leak rows.

**Data sources for thresholds:**
- Product leak count + 14-day median → computed in `/pro/abandoned-intent` payload (add `baseline_median_14d` field, additive).
- Cart leak count + 14-day median → computed in `/pro/checkout-health` payload (add same field, additive).
- Retention current + 8-cohort avg → computed in `/analytics/cohorts/summary` payload (add `cohort_avg_repeat_rate_8w` field, additive).

Three additive payload fields land in the same backend pre-work PR as the new `/analytics/week-ridge` and `/merchant/spark-memory` endpoints (§9).

**Why iOS-signal-style (not progress bars):** progress bars imply "completion" — wrong semantic. Signal bars imply "intensity of an ongoing signal" — correct semantic for leak pressure.

**Data source:** `/pro/revenue-at-risk` (hero number + prevented), plus the 3 leak levels derived from `/pro/abandoned-intent` + `/pro/checkout-health` + `/pro/cohorts/summary` week-4 deltas.

### Zone 3 — "Today's 3 Fixes" (heart-action)

**Container:** top stripe `via-[#e8a04e]`.

**Header:**
- Eyebrow: `YOUR NEXT MOVES`
- H2: `What to do first`

**3 rows, stacked full-width even on desktop (not a grid — stacked improves cognitive flow):**

Each row (rounded-2xl, border-white/[0.06], bg-[#0e0e1a]/60, p-5, hover:border-white/[0.14]):

```
[①]  Silk Pillowcase                           €68/day at risk
     68 views, 0 carts. Check the photos + price.       →
```

- Ordinal badge (28px circle, amber/rose/violet accent by leak type): 22px extrabold tabular
- Product/label (17px cream font-semibold)
- Loss euro (17px extrabold right-aligned, color by leak type)
- Fix copy one line (14px slate-300)
- Arrow right (→ 14px, opacity 0.6 → 1 on hover)

**Click behavior:** opens the Deeper drawer pre-scrolled to the matching tab (Peers / P&L / Attribution / Retention) and the specific product/section inside it.

**Row data sources (in priority order):**
- Row 1: top RARS component with a product reference (typically `abandoned_high_intent` → specific product)
- Row 2: second-highest RARS component
- Row 3: if third component is `goal_gap` → `Your {weekday} target · {€ short}`; else next RARS component

**Fix copy templates (keyed on `leak_point`):**
- `photo_leak`: `{views} views, {carts} carts. Check the photos + price.`
- `price_leak`: `{visitors} visitors, {carts} carts. Check price vs competitors.`
- `stock_leak`: `{N} variants out of stock. Refresh inventory.`
- `checkout_leak`: `{N} carts abandoned at checkout. Check shipping + payment.`
- `goal_gap`: `Last week you hit this target by {day}. You're behind by {€}.`

**Edge case — <3 leak sources active:**
Remaining rows render:
```
[✓]  Looking clean here — nothing to fix right now.
```
Background emerald-500/[0.03], border emerald-400/[0.15].

### Zone 4 — "Your Store This Week" (trend narrative)

**Container:** top stripe `via-[#34d399]` (emerald — growth framing dominant in this zone).

**Header:**
- Eyebrow: `YOUR WEEK`
- H2: `How the last 7 days went`

**Chart — "Week Ridge" (the signature primary chart):**
- SVG 100% width × 120px height (100px mobile)
- 7 day columns (rolling 7 days ending today)
- **Two overlapping area layers with smooth curves:**
  - Back layer: at-risk amount, `#d4893a` / opacity 0.35, stroke `#d4893a` / opacity 0.5 / 1.5px
  - Front layer: prevented amount, `#34d399` / opacity 0.7, stroke `#34d399` / 1.5px
- Smoothing: cubic Bezier with tension 0.3 — curves but not wavy
- Y-scale: `max(max(risk_day), max(prevented_day))` ceiling, auto-fit
- X-axis: 7 tick labels (Mon/Tue/…/Sun or rolling dates), 11px `text-slate-500`, no grid lines
- No axis title, no tooltip on hover (click opens Deeper → Retention for full table)
- Draw-in animation on first mount (stroke-dasharray 1.2s ease-out)

**Below the chart — one interpretation sentence (16px cream):**

Deterministic based on week totals:

| Condition | Sentence |
|---|---|
| `prevented > at_risk * 1.1` | `You saved €{prevented} this week — **+{delta}%** better than last week.` |
| `at_risk > prevented * 1.1` | `€{at_risk} leaked this week, vs €{prevented} saved. Let's close the gap.` |
| otherwise | `€{prevented} saved vs €{at_risk} at risk — steady week.` |
| cold start (<7 days) | `Watching your week build. First full 7-day read ready {day}.` |

The `{delta}%` is week-over-week change in prevented amount.

**Data source:** new endpoint `/analytics/week-ridge` — returns `{ days: [{date, at_risk_eur, prevented_eur}] × 7, week_over_week_pct }`. Derived from existing RARS history + prevented_events table. Backend pre-work, §9.

### Zone 5 — "Spark's Memory" (rolling log, humble surface)

**Container:** border-white/[0.04], bg-white/[0.01], p-6 sm:p-8. No top stripe (humble — memory is secondary to the verdict).

**Header:**
- Eyebrow (slate-400, not amber — humble): `WHAT I'VE NOTICED RECENTLY`
- H2 (1.5rem extrabold slate-200, not amber): `Spark's memory`

**5 timeline rows, vertical:**
- Left column (80px, 10px `text-slate-500`, tabular-nums): relative timestamp — `2h ago` / `yesterday` / `2 days` / `3 days` / `4 days`
- Right column (flex-1, 13.5px `text-slate-300`, leading-relaxed): event sentence
- Optional 4×4 dot accent at sentence start, colored by event type
- Spacing: `space-y-3`, each row py-2

**Event sentence templates (always first-person Spark, max 12 words):**

| Event type | Template | Dot color |
|---|---|---|
| `abandoned_detected` | `I noticed {product} lost intent.` | rose |
| `prevention_success` | `{product} recovered — your {change} worked.` | emerald |
| `brief_summary` | `{day} brief: you saved €{amount} on {signal_type}.` | amber |
| `cohort_milestone` | `Your best {period} so far — {metric}.` | emerald |
| `unusual_pattern` | `I started watching a new visitor pattern from {source}.` | violet |
| `target_hit` | `You hit your {weekday} target by {day}.` | emerald |
| `target_missed` | `Your {weekday} target fell short by €{amount}.` | rose |

**Data source:** new endpoint `/merchant/spark-memory` — returns last 5 notable events from union of `ops_alert` (abandoned, prevention), `holdout_events` (prevention success), `monthly_cohorts` (milestones), `traffic_source_log` (unusual patterns). Ranked by recency. Backend pre-work, §9.

**No click interaction in v5.** Future enhancement could deep-link to relevant drawer tab.

### Zone 6 — "Ask Spark" (existing component, reincorniciato)

**Container:** same canvas style, no top stripe.

**Header:**
- Eyebrow: `ASK ME ANYTHING`
- H2: `Need more context? Ask me anything about your store.`

**Content:** existing `AskHedgeSparkCard` component, verbatim. Only the wrapping label is new.

No changes to the component's internals, so zero risk to the existing behaviour.

## 3. Copy voice — Spark as narrator

**Tone:** Bloomberg-terminal-in-prose, NOT Duolingo. Factual, numeric, first-person, short.

**Rules:**
1. First person: `I noticed`, `I saved you`, `I'm watching`
2. Max 12 words per sentence
3. Currency in merchant's symbol, inline with prose (never in a separate "Currency: EUR" label)
4. Loss-framing dominates 60% ("leaking", "at risk", "short", "bleed"), growth-framing 40% ("saved", "recovered", "prevented", "hit")
5. Zero jargon: `CVR` → `conversion`, `COGS` → `costs`, `ARPC` → `what each customer spends`, `abandonment rate` → `carts abandoned`
6. No "personality quotes" from Spark. Spark is a reporter, not a mascot.
7. Numbers always rounded to merchant-relevant precision (euros, not cents; percentages to 1 decimal max)

**Acceptance test for copy:** read any Spark line aloud. Does it sound like something a CFO would say to their founder in a morning stand-up? If yes, ship. If it sounds like a chatbot, rewrite.

## 4. Color & type tokens (locked to CLAUDE.md §4 canon)

| Element | Token | Notes |
|---|---|---|
| Background | `bg-[#07070f]` | canonical dark |
| Card shell | `bg-[#0e0e1a]/60` or `bg-gradient-to-br from-[#0e0e1a]` | |
| Primary text (headline on card) | `text-cream` (#faf7f0) | warm off-white |
| Numbers on card | `text-white` or semantic accent | |
| Secondary text | `text-slate-300/400/500` by importance | |
| Hero number (leak) | `text-[#d4893a]` | deep amber |
| Hero number (prevented) | `text-emerald-400` | |
| Eyebrow amber | `text-[#e8a04e]` uppercase tracking-[0.22em] 11px font-bold | |
| Violet accent (intelligence, retention) | `text-[#a78bfa]` / `text-violet-400` | |
| Rose accent (leak, alert) | `text-rose-400` / `#f87171` | |
| Typography | `font-sans` (Geist) throughout, `font-mono` only for tabular numbers | |
| H2 titles | per SectionHeading primitive (1.75–2rem extrabold `#e8a04e`) | |
| Zone 1 line 2 (hero narrative) | 1.5rem sm:1.75rem extrabold `text-cream` | |

**Forbidden in Lite v5:**
- Tailwind amber-200/300/400/500 scale (use canonical `#d4893a` / `#e8a04e` / `#fbbf24`)
- Pure `text-white` for narrative text (use cream or slate-300)
- Any gradient on non-wordmark section titles (§4 canon)

## 5. The 3 primary charts (and only 3)

Every other chart/waterfall/cohort-grid from v4 moves to Deeper drawer.

### Chart 1 — Leak Gauge (Zone 2)
3 rows of iOS-signal-style bars (3 levels each). Deterministic thresholds. Colors semantic. No axes. ~120px total height.

### Chart 2 — Week Ridge (Zone 4)
Double-area chart, 7 days, amber back / emerald front, smooth curves, no chrome. 100-120px height. One interpretation sentence below.

### Chart 3 — Live Counter (Zone 2)
The hero number with tween animation + textShadow glow. Visual weight = 80% of Zone 2.

**Removal test:** for each, ask — "if I remove this, is the story weaker?" All three must answer YES. If any answers NO, it goes to drawer.

## 6. Motion specs

| Element | Motion |
|---|---|
| All zones entry | Scroll-reveal staggered (reuse `R` component from landing), delays 0.04/0.08/0.12/0.16/0.20/0.24 per zone index |
| Spark avatar | `hs-float-gentle` (existing, 4.5s ±3px) |
| Hero number | `useCountUp` tween on mount + value change (existing) |
| Leak bars | Each bar fills in sequence, 100ms stagger, 600ms total |
| Week Ridge | `stroke-dasharray` draw-in on first mount, 1200ms ease-out |
| Drawer open/close | Slide 400ms cubic-bezier(0.16,1,0.3,1) |
| Zero hover-layout-shift | Canon §4 non-negotiable |

## 7. The Deeper drawer

**Trigger:**
- Persistent "Deeper" chip top-right of `/app` header, always visible on Lite (label: `Deeper →` amber border, hover glow)
- Also triggered by: clicking a Zone 3 row → opens drawer at the matching tab + pre-scrolled to the relevant section

**Container:**
- Slide-over from right
- 560px wide on desktop, full-screen on mobile
- Backdrop: `bg-black/60 backdrop-blur-sm`
- Animation: slide-in 400ms

**Header inside drawer:**
- HedgeSpark wordmark (small, 24px height)
- Title: `Deeper`
- Subtitle (12px slate-400): `All your waterfalls, cohort grids, and attribution — one click away. We just don't lead with them because you don't need them to know what's broken right now.`
- Close button top-right

**Tab bar (sticky below header):**
- 4 horizontal tabs: `Peers · P&L · Attribution · Retention`
- Active: amber underline 2px, label `text-[#e8a04e]`
- Inactive: `text-slate-400`

**Tab content — components moved from primary, unchanged:**

| Tab | Components (in order) |
|---|---|
| Peers | `PeerBenchmarksCard`, `VerticalBenchmarksCard` |
| P&L | `PnlReport`, `MarginDragCard` |
| Attribution | `ChannelAttributionCard` |
| Retention | `CohortSummaryCard`, `MonthlyCohortsCard`, `GatewayProductsCard`, **full `LiteCassettoniGrid` (all 6 v4 cassettoni verbatim — Revenue at Risk, Daily Brief, Abandoned Intent, Live Opportunities, Visitor Intent, Hot Products)** |

**Per-tab footer:** amber CTA chip `Ask Spark about {tab_name} →` that pre-fills `AskHedgeSparkCard` with a scoped question (e.g. `"What's driving my peer gap on conversion rate?"`).

**State persistence:**
- `localStorage.lite_deeper_last_tab` — last opened tab
- `localStorage.lite_deeper_{tab}_scroll` — scroll position per tab
- On drawer reopen, restores both

**Why Retention is the big tab:** it holds the full cassettoni grid with v4's rich storytelling drawers (DrawerExplainer + DrawerBigStat + DrawerKeyValueList + DrawerHowCalculated + DrawerNextAction). Gate 3 (depth preserved) passes because every cassettone's methodology/formula/inputs/advice lives there, unchanged.

## 8. Data contract

Every number in Lite v5 must be derivable from a real query. No
fabrication. Empty states say "—" or "Watching…", never fake a value.

**Existing endpoints (reused):**
- `/pro/revenue-at-risk` — Zone 1 total+count, Zone 2 hero+prevented, Zone 3 top-3 components
- `/pro/abandoned-intent` — Zone 1 top-product, Zone 3 fix-copy keyed on leak_point
- `/analytics/benchmarks` + `/analytics/benchmarks_vertical` — Drawer Peers
- `/analytics/pnl` + `/pro/margin-drag` — Drawer P&L
- `/analytics/attribution` — Drawer Attribution
- `/analytics/cohorts/summary` + `/pro/cohorts/monthly` + `/pro/cohorts/ltv/products` — Drawer Retention

**New endpoints (backend pre-work, §9):**
- `/analytics/week-ridge` — Zone 4 chart payload
- `/merchant/spark-memory` — Zone 5 timeline payload

## 9. Backend pre-work (before frontend commit)

**New endpoint 1: `/analytics/week-ridge`**
- GET, requires merchant session
- Response: `{ days: [{date: ISO, at_risk_eur: number, prevented_eur: number}], week_over_week_pct: number, currency: string }`
- Derived from: existing RARS history table + prevented_events table, rolling 7 days ending today (shop timezone)
- Tests: cold-start returns fewer days; no fabricated zero-pad; currency matches shop ccy cache
- TIER: 0 (additive read endpoint)

**New endpoint 2: `/merchant/spark-memory`**
- GET, requires merchant session
- Response: `{ events: [{ timestamp: ISO, relative_label: string, event_type: string, sentence: string, dot_color: string }], count: number }`
- Derived from: union of `ops_alert` (abandoned/prevention), `holdout_events` (prevention success), `monthly_cohorts` (milestones), `traffic_source_log` (unusual patterns), ranked by recency, top 5
- Sentence generation: deterministic template per event_type (see Zone 5 table), no LLM
- Tests: empty state (cold start); 5 event types all render; timestamps respect shop timezone
- TIER: 0

Both endpoints follow scale checklist §12 of CLAUDE.md: indexed, TTL'd where cached, no N+1.

**Additive payload fields (same backend PR, 3 existing endpoints):**

| Endpoint | New field | Purpose | Default if cold |
|---|---|---|---|
| `/pro/abandoned-intent` | `baseline_median_14d: number` | Product-leak threshold baseline (Zone 2 row 1) | `null` (triggers "Watching…") |
| `/pro/checkout-health` | `baseline_median_14d: number` | Cart-leak threshold baseline (Zone 2 row 2) | `null` (same) |
| `/analytics/cohorts/summary` | `cohort_avg_repeat_rate_8w: number` | Retention-risk threshold baseline (Zone 2 row 3) | `null` (same) |

All three are additive — existing clients ignore the new field, v5 clients use it. No breaking change. Tests verify cold-start returns `null`, not `0`, so the UI can distinguish "no baseline yet" from "baseline is zero".

## 10. Rollback / feature flag

**Env var:** `NEXT_PUBLIC_LITE_SPARK_DAILY` (boolean, default `false`)

- When `false` → renders v4 current Lite layout verbatim (no visual change)
- When `true` → renders v5 Spark Daily

**Plan:**
1. Ship v5 behind flag = `false` (prod continues serving v4)
2. Flip to `true` on staging, run gate audits (§1)
3. Flip to `true` in prod only after gates 1-5 pass
4. If regression in prod: one-line revert by flipping to `false` in env

v4 code path stays intact for at least 30 days post-flip. After 30 days green, v4 code path is removed in a cleanup commit.

## 11. Anti-flip-flop discipline

1. **Read this spec before editing any file.** If there's ambiguity,
   the spec wins; if the spec is silent, update the spec first.
2. **Changing a zone structure / color / layout not in the spec** →
   STOP, update spec, doc-only commit, then code.
3. **No reactive typography reverts under implementation pressure.**
4. **Copy voice stays Spark-first-person.** Don't drift to
   "HedgeSpark noticed…" corporate voice under review pressure.
5. **Real-data contract absolute.** Never fabricate a leak, a week
   ridge, a memory event. `—` is honest; fake kills trust on day-1.

## 12. Implementation plan (atomic commits)

1. **Commit A** (this doc) — atomic, reviewable. No code change.
2. **Commit B** (backend): `/analytics/week-ridge` endpoint + tests. TIER 0.
3. **Commit C** (backend): `/merchant/spark-memory` endpoint + tests. TIER 0.
4. **Commit D** (frontend flagged): `LiteSparkDaily.tsx` + `LiteDeeperDrawer.tsx` new files; wire into `/app/page.tsx` behind `NEXT_PUBLIC_LITE_SPARK_DAILY` flag. v4 stays intact as fallback. No visible change in prod.
5. **Commit E** (audit docs): `/docs/lite_v5_depth_audit.md`, `/docs/lite_v5_visual_audit.md`, `/docs/lite_v5_competitor_audit.md` — filled in with real screenshots and gate results on staging.
6. **Commit F** (prod flip): `NEXT_PUBLIC_LITE_SPARK_DAILY=true` in prod env, after gates 1-5 pass.
7. **Commit G** (cleanup, +30 days): remove v4 code path, delete orphaned imports.

## 13. What stays in v5 that was in v4 (additive contract)

**Nothing is deleted.** Everything in v4 Lite primary slot is either
(a) in the new v5 primary slot, or (b) in the Deeper drawer. Mapping:

| v4 primary slot component | v5 location |
|---|---|
| `LiteRarsHero` | Zone 2 (re-styled as "Leak Gauge") |
| PeerBenchmarks + VerticalBenchmarks section | Drawer → Peers tab |
| P&L + MarginDrag section | Drawer → P&L tab |
| Channel attribution section | Drawer → Attribution tab |
| CohortSummary + MonthlyCohorts + GatewayProducts section | Drawer → Retention tab |
| `LiteCassettoniGrid` (v4 cassettoni with drawer richness) | Drawer → Retention tab, below cohorts |
| `AnalyticsAssistant` | Zone 6 (re-labeled "Ask Spark") |

Added in v5 that didn't exist before:
- Zone 1 "Spark Says" narrative hero
- Zone 4 "Week Ridge" chart + interpretation
- Zone 5 "Spark's Memory" timeline
- Deeper drawer architecture

## 14. Devil's advocate — recorded risks (re-check at each commit)

From the proposal pushback; any of these that materializes is a rollback signal.

1. **"Cute not powerful"** — merchants who came from Lifetimely expect immediate waterfall/cohort depth. Mitigation: Deeper drawer subtitle explicit ("all your waterfalls, one click away"). If gate 5 fails on this specifically, iterate to a persistent "Data view" toggle side-by-side with Spark Daily.
2. **Mascot-as-narrator = gimmick risk** — Clippy feeling. Mitigation: Spark voice is Bloomberg-in-prose, zero personality quotes, all numbers real. Copy audit per §3.
3. **Grafici eliminati = merchant ritual lost** — merchant who watched P&L every morning loses their ritual. Mitigation: Drawer remembers last-opened tab → merchant re-opens directly to P&L.
4. **Implementation time** — 3-4 days full. Mitigation: feature flag allows shipping to prod `false`, then flipping per-merchant for beta testing before universal flip.

## 15. Success criteria for v5 to be called "done"

- All 5 gates §1 pass with recorded evidence
- `audit_claude_md_redis_keys.py` clean (any new Redis keys catalogued in CLAUDE.md §13)
- `./venv/bin/python -m pytest tests/ -q` green (all tests, no exclusions)
- `npx next build` green with zero warnings
- Lighthouse CLS < 0.05 on `/app/lite` both desktop and mobile
- Deep audit `/docs/lite_v5_*.md` committed with screenshots
- Rubric score ≥ 9.6 across the 7 domains (`project_brutal_scoring_rubric.md`)

If rubric comes in below 9.6, we iterate before flipping the flag in
prod. The "11/10 unreachable" bar is the one that ships, not the
"9/10 is fine" one.
