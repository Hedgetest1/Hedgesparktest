# /app/lite — Visual Spec v4 (2026-04-20, rich storytelling)

**Status:** Approved direction. v4 ports AbandonedIntent's DetailDrawer
storytelling richness INLINE into the expanded panel for all 6 features.
No redundancy between the 3 sections; each earns its keep.

## What changed from v3

v3 shipped: title/subtitle/warm copy → intro + donut + deep card →
numbered "what to do" list. Founder feedback 2026-04-20:

> "abandonement intent è l'unico che se clicchi sotto apre a destra una
> finestra mooolto esplicativa. Ecco, io volevo portare quella
> finestra non lì, ma in dashboard, nel senso che senza ridondanza mi
> sembra che tutte e tre le componenti /cosa è/analisi/what to do siano
> davvero mancanti di story telling premuroso ed affidabile...il tutto
> per tutte e sei le features."

Translation: bring the AbandonedIntent drawer's rich storytelling INLINE
into the dashboard, for every feature, without the 3 sections stepping
on each other.

## The template — AbandonedIntent drawer deconstructed

From `AbandonedIntentCard.tsx` lines 248–449:

1. **DrawerExplainer** — `body` (mechanics, "what this is") + `why`
   (stakes, "why it matters")
2. **DrawerBigStat** — one killer number, ex: "Biggest leak this week:
   62%"
3. **DrawerKeyValueList** — 3–5 metric rows, ex: "Products leaking
   intent 20 · Browse-stage leaks 12 · Cart-stage leaks 3"
4. **DrawerSectionHeading + ranked list** — detailed items
5. **DrawerHowCalculated** — `formula` + `inputs` + `note` (methodology)
6. **DrawerNextAction** — `headline` + `primary` (label + description)

The v4 spec maps these 6 drawer primitives onto the 3 inline sections
so each section is RICH and DISTINCT.

## The 3 sections, v4 structure

### Section 1 — "What you're seeing" (cosa è)

**Narrative job:** identify + frame the stakes. No numbers beyond
subtitle. Copy-heavy, Spark voice, warm.

```
[Amber title, 1.75–2rem extrabold]
[White subtitle, 15px semibold — DATA-DRIVEN one-liner]

[Body paragraph, 14px slate-300, max-w-3xl]
"What this is, mechanically. 2–3 sentences."

[Mini-label "Why this matters", amber 11px uppercase tracking-wide]
[Why paragraph, 14px slate-300, max-w-3xl]
"The stakes, loss-framed where possible. 2 sentences."
```

Replaces v3's single `warmCopy` string with `mechanics` + `stakes` pair.
Matches DrawerExplainer's body+why shape exactly.

### Section 2 — "The data" (analisi)

**Narrative job:** prove the numbers. Hero stat + metrics list + donut
+ methodology. The deep-card embed is REPLACED by this structured render
(the deep card was redundant with the inline storytelling we're adding).

```
[Violet section card, border + bg tint]
  [Violet icon + heading "THE DATA · WHAT YOU'RE LOOKING AT"]

  [Hero stat — amber accent, DrawerBigStat shape]
    Label (10px slate-500 uppercase): "Biggest leak this week"
    Value (3rem extrabold amber): "62%"
    Sublabel (12px slate-400): "Silk Pillowcase · 220 views · 5 carts · 0 sales"

  [Donut chart + legend — existing, unchanged]

  [Key metrics list — 3–5 rows, DrawerKeyValueList shape]
    Each row: label (slate-400) + value (slate-200 tabular-nums)
    Color-coded value when the metric signals pressure (amber/rose)

  [Methodology box — DrawerHowCalculated shape, collapsed by default]
    Header "How this is calculated"
    Formula paragraph
    Inputs list (label → value, 3–5 rows)
    Note paragraph (the "interpretation hint")
```

All numbers come from live data; nothing is hardcoded. When the payload
is empty, the hero stat and metrics gracefully say "—" and the
methodology is still shown (explains the WHY even when there's no data
yet — that's the "premuroso" part).

### Section 3 — "Your next moves" (what to do)

**Narrative job:** primary action elevated, supporting actions clear.

```
[Accent-colored section card, border + gradient bg]
  [Accent icon + heading "YOUR NEXT MOVES"]

  [Primary action — DrawerNextAction shape]
    Headline (accent color, 11px uppercase): "START HERE"
    Label (16px white semibold): "Fix Silk Pillowcase"
    Description (13.5px slate-300, max-w-3xl):
      "This product is losing visitors at the product page itself.
       Check the photos, price, description, and stock availability."

  [Divider, subtle]

  [Supporting actions — 1–3, each a mini-card]
    Numbered badge (accent color)
    Label (14px slate-200 semibold)
    Description (12.5px slate-400, one line)
```

Replaces v3's flat numbered list. The primary action stands out;
supporting actions stay clean.

## PanelConfig v4 shape (TypeScript)

```ts
type PanelConfig = {
  title: string;
  getSubtitle: (ctx: PanelCtx) => string | null;

  // Section 1 — cosa è (replaces warmCopy)
  mechanics: string;                  // DrawerExplainer.body
  stakes: string;                     // DrawerExplainer.why

  // Section 2 — analisi (enriches analysisIntro + donut)
  getHeroStat: (ctx: PanelCtx) => {
    label: string;
    value: string;
    sublabel: string;
    color: string;
  } | null;
  getDonutSegments: (ctx: PanelCtx) => DonutSegment[] | null;  // unchanged
  getDonutHero: (ctx: PanelCtx) => { value: string; label: string };  // unchanged
  getKeyMetrics: (ctx: PanelCtx) => Array<{
    label: string;
    value: string;
    color?: string;
  }>;
  methodology: {
    formula: string;
    getInputs: (ctx: PanelCtx) => Array<{ label: string; value: string }>;
    note: string;
  };

  // Section 3 — what to do (replaces getWhatToDo flat array)
  getPrimaryAction: (ctx: PanelCtx) => {
    headline: string;   // short uppercase eyebrow ("START HERE" / "FIX FIRST")
    label: string;      // the action itself
    description: string; // 2 sentences of guidance
  } | null;
  getSupportingActions: (ctx: PanelCtx) => Array<{
    label: string;
    description: string;
  }>;
};
```

`warmCopy`, `analysisIntro`, `getWhatToDo` are REMOVED — fully superseded.

## Removing the deep-card embed (anti-redundancy decision)

Before v4, the expanded panel embedded the full deep card (e.g.,
`AbandonedIntentCard hideHeading`). That's no longer needed: the
inline hero stat + donut + metrics + methodology + actions
already render everything the merchant needs from that feature's data.
Keeping the deep card embed WOULD create the redundancy the founder
flagged.

So v4 removes `<ExpandedContent>` from the panel. Each feature's data
hook already returns the payload the PanelConfig needs; the inline
renderers own the full presentation.

**Exception:** Hot Products. The deep card's 3-product grid (visitor,
views, intent per product) is uniquely visual and not expressible in a
key-value list. For Hot Products, we keep the 3-card grid as the
"detailed items" block BETWEEN the key metrics and the methodology.

## Real-data contract (unchanged from v3)

Every hero stat, every metric row, every primary/supporting action —
sourced from the payloads the 4 data hooks already fetch
(`useRarsData`, `useAbandonedData`, `useLiveOppsData`,
`useVisitorIntentData`) + the existing `topProducts` + `effectiveBrief`.
No fabricated values. Empty states say "—" and the methodology copy
still explains the feature's logic.

## Per-feature definitions (v4 content)

### 1. Revenue at risk (amber)

- **mechanics**: "I add up every signal on your store that points to
  lost revenue this month — abandoned high-intent carts, refund trends,
  nudges underperforming peers, targets you're missing. One number, five
  sources, updated every minute."
- **stakes**: "This is the money HedgeSpark exists to earn back for
  you. Leaving it on the floor is the most expensive thing you can do
  this month — cheaper than acquiring new traffic to replace it."
- **heroStat**: the largest component's source + its loss_eur ("Biggest
  leak: abandoned high-intent carts · €680")
- **keyMetrics**:
  - Total at risk this month (amber when > 0)
  - Prevented so far this month (emerald)
  - Number of active leak sources
  - Top leak source's share %
- **methodology formula**: "Sum of five independent signal losses,
  reduced by already-prevented amounts, priced in your store's currency."
- **methodology inputs**: one row per component where `loss_eur > 0`
- **methodology note**: "Only components with material loss are
  included. The component list mirrors the breakdown you see on Pro."
- **primaryAction**: "FIX FIRST" + "Tackle [top component]" +
  description grounded in source type
- **supportingActions**: 2nd component fix + "keep prevention running"
  IF prevented > 0

### 2. Daily brief (violet)

- **mechanics**: "Every morning I scan the previous 24 hours of events
  on your store, rank every finding by economic impact, and surface the
  top story. If a signal is trending toward money lost, you hear about
  it here first."
- **stakes**: "Missing today's brief means missing the day's biggest
  opportunity. Merchants who act on the brief within 4 hours convert
  roughly 2× better on the flagged signal."
- **heroStat**: top_product_label + top_action summarized
- **keyMetrics**:
  - Findings today
  - Top signal type
  - Oldest actionable finding (hours)
  - New vs repeat signals
- **methodology formula**: "Events in the last 24h are grouped by
  signal type, each signal scored by recoverable revenue + urgency."
- **methodology inputs**: signals_count, top_product_label, etc
- **methodology note**: "Rankings refresh every 10 minutes during
  trading hours."
- **primaryAction**: top_action from the lead story
- **supportingActions**: "open N more findings below" + "check back
  this afternoon"

### 3. Abandoned intent (rose)

- **mechanics**: "These are your warmest leads that didn't close:
  visitors who scrolled your product pages, dwelled, sometimes added to
  cart — and still didn't buy. I compare buyer depth vs non-buyer depth
  and surface the products with the widest gap."
- **stakes**: "Traffic you already paid for is walking away. Fixing one
  bottleneck on this list is almost always cheaper than buying more
  ads to replace the lost intent."
- **heroStat**: worst leak this week (product name + abandon_rate_pct +
  views/carts/sales)
- **keyMetrics**:
  - Products leaking intent (total)
  - Browse-stage leaks
  - Cart-stage leaks
  - Buyer vs non-buyer depth (Pro) OR "upgrade for buyer depth" (Lite)
- **methodology formula**: "For each product: 1 − (purchases_7d /
  views_7d). Products with too few views are excluded — one-off clicks
  don't pollute the list."
- **methodology note**: "Browse-stage leaks usually mean the product
  page isn't convincing; cart-stage leaks usually mean shipping, price,
  or checkout friction."
- **primaryAction**: fix the top product, description keyed on leak_point
- **supportingActions**: 2nd product + "email the abandoning segment"

### 4. Live opportunities (amber-opp)

- **mechanics**: "These are the pages on your store leaking intent as I
  speak. Visitors are reading them, scrolling, clicking around — and
  not converting. Each row is one page + one reason + one concrete fix
  you can ship in minutes."
- **stakes**: "This is the fastest money on your store. High-intent
  pages with the right fix typically recover 10–30% of their lost
  revenue within a day."
- **heroStat**: top opportunity — its URL + priority_score + the
  recommended_action headline
- **keyMetrics**:
  - Pages leaking intent
  - High-intent pages
  - Engaged pages
  - Highest priority score
- **methodology formula**: "Pages are scored on engagement (scroll +
  dwell + click) minus conversion. Top scorers are live leaks."
- **methodology note**: "Rankings re-sort every 5 minutes as visitors
  flow through."
- **primaryAction**: top opportunity's recommended_action
- **supportingActions**: 2nd opportunity + "come back in a few hours
  for fresh rankings"

### 5. Visitor intent (rose)

- **mechanics**: "I classify every live visitor into Hot (engaged and
  clicked), Warm (engaged but no click), and Cold (pass-through). A
  Hot visitor is roughly 10× more likely to buy than a Cold one."
- **stakes**: "If your mix is mostly Cold, your traffic is wrong — fix
  the acquisition. If your mix is mostly Warm but low Hot, your product
  pages aren't earning the click — fix the conversion. Two very
  different costly mistakes."
- **heroStat**: dominant segment + count + hot→warm ratio
- **keyMetrics**:
  - Hot visitors
  - Warm visitors
  - Cold visitors
  - Total scored
- **methodology formula**: "conversion_score = weighted sum of dwell +
  scroll + click events per visitor, thresholded at HOT and WARM."
- **methodology note**: "Thresholds shown on the methodology footer of
  the card — we publish them so you can audit the classification."
- **primaryAction**: depends on dominant state (acquire vs convert)
- **supportingActions**: open Abandoned Intent OR Live Opportunities
  based on what the mix implies

### 6. Hot products (emerald)

- **mechanics**: "These are the products pulling the most attention
  right now — ranked by views, unique visitors, and the intent score I
  assign each product based on visitor depth. If you want to double
  down on what's working, start here."
- **stakes**: "Quiet products die quiet deaths. A hot product today
  that you don't push harder becomes a cold product next week."
- **heroStat**: #1 product — name + views + intent score
- **keyMetrics**:
  - #1 product views
  - #1 product visitors
  - Views-per-visitor ratio
  - Intent score
- **methodology formula**: "Products ranked by total_views × average
  intent_score over the last 7 days. Only products with at least N
  qualifying visitors are surfaced."
- **methodology note**: "Intent level (HOT/WARM/COLD) uses the same
  thresholds as Visitor Intent — so the signals are comparable."
- **additionalBlock**: the 3-product visual grid (kept from v3, it's
  uniquely visual and not reducible to a key-value list)
- **primaryAction**: "Double down on [top product]" + description
  keyed on views/visitors ratio and intent
- **supportingActions**: re-engagement OR quality tuning based on
  intent score

## Commit plan

1. **Commit A (this doc)**: Spec v4, no code. Atomic, reviewable.
2. **Commit B**: Extend `PanelConfig` type + inline rendering + port
   richness to all 6 features in a single coherent pass. Remove
   `<ExpandedContent>` embed (kept only for Hot Products' visual grid).
   Bundle budget may nudge up ~3–5 KB (spec update coming).

## Anti-flip-flop discipline

1. Read this spec before edit.
2. If changing a color/size/layout not in the spec, STOP, update the
   spec, ship the doc-only commit, then code.
3. No reactive typography reverts.
4. Keep storytelling in the copy — don't let the JSX ratios drift.
5. Real-data contract: never fabricate. "—" is honest; fake is not.
