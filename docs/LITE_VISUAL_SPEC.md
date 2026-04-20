# /app/lite — Visual Spec v3 (2026-04-20, founder-decided)

**Status:** APPROVED by founder. Starting Commit 1.

## Layout

```
┌─ Today · your 3 moves (hero) ─────────────────────────┐
└───────────────────────────────────────────────────────┘

┌─ cass 1 ─┐ ┌─ cass 2 ─┐ ┌─ cass 3 ─┐
│ Title    │ │          │ │          │
│ BIG NUM  │ │          │ │          │
└──────────┘ └──────────┘ └──────────┘

┌─ cass 4 ─┐ ┌─ cass 5 ─┐ ┌─ cass 6 ─┐
│          │ │          │ │          │
└──────────┘ └──────────┘ └──────────┘

┌─ Expanded panel (only when any cassettone clicked) ────┐
│ Title                                                   │
│ Subtitle                                                │
│ Warm copy — "what you're seeing" (idiot-proof)         │
│                                                         │
│ Analysis + donut chart (center)                         │
│                                                         │
│ What to do next (real actions, backend-sourced)         │
└─────────────────────────────────────────────────────────┘

┌─ Live Radar + World Map ────────────────────────────────┐
└─────────────────────────────────────────────────────────┘

┌─ Spark Status ──────────────────────────────────────────┐
└─────────────────────────────────────────────────────────┘
```

Removed: LiteTourPrimer (founder: "togli il tour primer, non serve").

## Cassettone design

Landing DNA applied 1:1:
- `rounded-3xl border border-white/[0.06] bg-[#0e0e1a]`
- `p-6 sm:p-8`
- `shadow-[0_20px_80px_-20px_rgba(0,0,0,0.5)]`
- Colored accent (vertical bar left OR dot top, semantic to the feature)
- Cursor pointer + hover: border `white/[0.12]`
- **No arrow, no plus icon.** Click anywhere on the card opens the expanded panel.
- When open: border shifts to the accent color.

Internal:
- Eyebrow (10px uppercase tracking-wide, accent color): category
- Title (17px bold white): feature name
- **Hero number** (3rem extrabold, accent color)
- Meta (12px slate-500): "this month" / "right now" / timeframe

## The 6 cassettoni (real backend data per feature)

| # | Title | Hero number | Accent | Endpoint |
|---|---|---|---|---|
| 1 | Revenue at risk | `€X` | amber `#fbbf24` | `GET /pro/revenue-at-risk` → `total_at_risk_eur` |
| 2 | Daily brief | `N findings` | violet `#a78bfa` | `GET /brief/today` → `signals_count` |
| 3 | Abandoned intent | `M visitors` | rose `#f87171` | `GET /analytics/abandoned-intent` → top-product count |
| 4 | Live opportunities | `K pages` | amber `#e8a04e` | `GET /analytics/live-opportunities` → visible opps count |
| 5 | Visitor intent | `H hot` | rose `#f87171` | `GET /analytics/visitor-intent-classification` → `hot_visitors` |
| 6 | Hot products | `P top` | emerald `#34d399` | Products in `dashboard/overview` → `topProducts.length` |

All numbers come from endpoints already consumed today by the
existing deep cards. **No new backend endpoints needed for Commit 1.**

## Expanded panel — structure

Per founder: "titolo e poi sottotitolo e poi copy a prova di scemo,
warm, spiega molto chiaramente cosa sta vedendo, poi la parte di
analisi al centro, poi cosa dovrebbe fare".

Each expanded panel (same 6 slots):

1. **Title** — big, same as cassettone title.
2. **Subtitle** — 1 line, metric in context (e.g., "€1,240 at risk
   this month, across 4 signals").
3. **Warm copy** (idiot-proof) — 2-3 sentences in plain language:
   "What you're seeing" from Spark's voice.
4. **Analysis** (center) — the existing deep card's content
   (buyer-vs-nonbuyer grid, opps list, pillar pills, etc) + **donut
   chart** (see §charts).
5. **What to do next** — real actions only:
   - For features that have `sparkActions` → pull relevant actions
   - For features with `recommended_action` field in payload (like
     Live Opportunities) → surface that
   - For features without direct actions → "Open [X] for details"
     navigating to Pro counterpart OR static copy pulled from the
     component's existing `description` prop

**No invented content.** If the backend can't supply a specific
recommendation, the panel shows the generic "Here's the methodology
behind this number" copy pulled from the component's existing
DetailDrawer content where available (e.g., CohortSummaryCard's
DrawerHowCalculated block).

## Donut charts (one per cassettone, real data)

Founder: "grafici dedicati a torta". Small SVG donut (inline, no
lib), 160×160px, amber-stroke ring on background, colored segments.

| Feature | Donut segments | Source |
|---|---|---|
| Revenue at risk | prevented (emerald) / at risk (amber) OR 5-way component breakdown | `data.components[*].loss_eur`, `data.prevented_eur_this_month` |
| Daily brief | findings by signal_type | `metrics_snapshot[*].signal_type` aggregated |
| Abandoned intent | leak_points distribution | existing `leak_point` field in top-products |
| Live opportunities | priority tiers (HIGH_INTENT / ENGAGED / LOW) | existing `signal_type` field |
| Visitor intent | Hot / Warm / Cold proportions | existing `hot_visitors / warm / cold` |
| Hot products | top 3 products by views proportion | `topProducts[0..2].total_views` |

All segments use the palette from CLAUDE.md §4:
- Rose = bad/critical
- Amber = warning
- Emerald = good/positive
- Violet = intelligence
- Slate = neutral

Donut center shows the hero number (same as cassettone, for visual
continuity between collapsed and expanded states).

## Interaction

- Click cassettone → expand its panel. If another is open, close it first.
- Click the SAME cassettone again → collapse.
- Click the "Collapse" button top-right of expanded panel → collapse.
- Radar + Spark Status stay fixed at the bottom; the expanded panel
  animates in/out between grid and radar with a 200ms height
  transition + fade.
- One cassettone open at a time. No multi-open.

## Real-data contract (founder-mandated)

> "i dati di descrizione/analisi e suggerimento/consiglio/what to do
> devono essere supportati dalla nostra infrastruttura reale"

Implementation rule:
- Every number rendered → traceable to one endpoint call or
  page-level state derived from endpoint.
- Every "what to do" action → comes from either:
  - `sparkActions` (deterministic decision engine)
  - Backend-provided `recommended_action` field on the data item
  - Static "methodology" copy from existing component's
    DrawerHowCalculated / DrawerExplainer blocks (no fabricated
    recommendations)
- Every "analysis" block → uses the EXACT render the deep card
  already uses. No invented tables, no mock data.

If a data path is broken (e.g., `metrics_snapshot` is empty), the
expanded panel shows the empty state inherited from the underlying
component (CardEmpty / existing empty-state copy).

## Commit plan

1. **Commit 1** (this one): Cassettone component + 2×3 grid +
   expand state + wire existing deep cards as expanded content.
   Kill LiteTourPrimer. No charts yet.
2. **Commit 2**: Expanded-panel structure (title/subtitle/warm-copy/
   analysis/what-to-do). Rewrite existing deep cards to render
   inside this structure (don't replace — compose).
3. **Commit 3**: 6 donut charts inline-SVG, real data sourced.

## Anti-flip-flop

I've botched /app/lite visual 4 times today. Before edit:
1. Read this spec.
2. If about to change a color/size/layout not in the spec, STOP,
   update the spec first, get tacit approval (ship the spec update
   as a doc-only commit), then code.
3. No reactive typography reverts.
