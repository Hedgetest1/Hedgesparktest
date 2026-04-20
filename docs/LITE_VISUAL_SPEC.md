# /app/lite — Visual Spec (2026-04-20)

Writer: Claude, acting as UI Designer.
Status: **DRAFT — awaiting founder approval before any TSX touched.**

---

## 1. Thesis (one sentence)

Every morning, HedgeSpark Lite tells a merchant in 60 seconds of
scroll: **what to do · what's slipping · what's alive · what Spark
did while you slept.**

If a merchant reads ONLY the first screen and scrolls to the bottom,
they must leave with those four signals in order.

## 2. Narrative beats (top → bottom)

| # | Beat | Section | Emotion |
|---|---|---|---|
| 0 | **Orientation** (first visit only) | LiteTourPrimer | "Ah, I get it." |
| 1 | **The action** | Today · 3 Moves | "OK, these 3 are today." |
| 2 | **The loss** | Revenue at Risk | "Wait, that much?" |
| 3 | **The story** | Daily Brief | "Here's the headline." |
| 4 | **The evidence** | Abandoned · Live Opps · Visitor Intent | "Here's the proof." |
| 5 | **The places** | Hot Products | "Where the value is." |
| 6 | **The pulse** | Live Radar + World Map | "It's alive right now." |
| 7 | **The signature** | Spark Status | "Spark was here." |

The scroll IS the narrative. No tabs, no drawers on the main path.

## 3. Type scale (3 levels only — enforced everywhere)

Single rule: **amber appears once per section, on the H2 only.**
Never on eyebrows, subtitles, meta, or secondary UI.

| Level | Usage | Size | Weight | Color |
|---|---|---|---|---|
| **Hero H1** | Page-level hero (Today·3 Moves #1 action headline) — max 1 per page | `2rem → 2.5rem sm+` | `extrabold` | `#e8a04e` |
| **Section H2** | Every section start | `1.5rem → 1.75rem sm+` | `extrabold` | `#e8a04e` |
| **Card H3** | Inside cards (product names, bullet rows) | `0.9rem → 1rem` | `bold` | `white` |
| **Body** | Paragraphs, subtitles | `0.9375rem` | `normal` | `slate-400` |
| **Meta** | Timestamps, chip labels, hints | `0.75rem` | `medium` | `slate-500` |
| **Tabular** | Numbers | same as context + `tabular-nums` | `extrabold` for heroes | context-colored |

**No more than 3 H1s on /app/lite total.** The Today headline is
the only true H1. The LiteTourPrimer and Spark Status use H2.

## 4. Spacing system (8-point grid)

| Token | Use |
|---|---|
| `4` | inline chip padding |
| `8` | card internal gutters |
| `12` | small stack gaps |
| `16` | standard content padding |
| `24` | heading to content |
| `32` | card to card inside a section |
| `40` | **section to section** (main vertical rhythm) |
| `48` | hero to first section |

The current `space-y-6` (24px between top-level children) is WRONG
for section-to-section — it reads as cramped. Move to `space-y-10`
(40px) on the Lite floor parent only (Pro keeps current for now).

## 5. Color palette (where, not just what)

| Color | Role | Where |
|---|---|---|
| amber `#e8a04e` | **Section identity only** | H1/H2, never twice in one section |
| white | Primary content text | Card titles, hero numbers when neutral |
| slate-300 | Secondary strong | Body emphasis |
| slate-400 | Secondary normal | Paragraphs, subtitles |
| slate-500 | Tertiary / meta | Timestamps, captions, chip labels |
| emerald `#10b981` | Positive delta | Revenue gained, repeat rate good |
| amber warning | Warning numbers | RARS amount, at-risk products |
| rose `#f43f5e` | Critical | Degraded state, leaks above threshold |
| violet `#a78bfa` | Intelligence accent | Pro/AI moments (Spark learning) |
| brand gradient | **Wordmark only** — §4 CLAUDE.md | NEVER on section titles |

## 6. Moodboard — 3 references

- **Linear (linear.app/method)** → How typography alone carries
  hierarchy. No amber-on-amber, no competing eyebrows. Just size +
  weight + color-once. *Takeaway: remove the small eyebrow tag I've
  been adding — it's noise.*
- **Stripe Dashboard** → Rigorous 40-point section rhythm, cards
  that EARN their presence (one concrete number = one card). Dense
  but not cramped because spacing is honest. *Takeaway: each section
  should answer one question, not three.*
- **Superhuman** → Conversational first-person voice at hero scale
  ("You have 3 emails left today"). Not cringe because voice is
  **paired with specificity** (a number, a product name). *Takeaway:
  Spark voice works when tied to a specific number; fails when
  abstract.*

## 7. Originality layer — 3 HedgeSpark-unique elements

These make Lite feel €39, not a generic dashboard template.

### 7a. **Signature SVG icon per section** — NOT stock Heroicons

Each of the 7 sections gets a custom 24px SVG header icon that
encodes its meaning visually:

- Today · 3 Moves → **arrow trail** (3 dots linked by a flowing path)
- Revenue at Risk → **coin dropping** (tilted coin with motion lines)
- Daily Brief → **folded paper** (letter from Spark)
- Abandoned Intent → **escape routes** (dotted paths exiting a circle)
- Live Opportunities → **pulsing crack** (line with a widening break)
- Visitor Intent → **3-band thermometer** (hot/warm/cold vertical bars)
- Hot Products → **podium** (3 blocks ascending)
- Live Radar → existing map (already original)
- Spark Status → Spark mascot (already original)

Each icon has the amber stroke color so it ties to the H2. Cat-5 if
founder wants custom work: 4h total (30 min per icon × 8).

### 7b. **Animated count-up on key numbers**

Only on first render per page visit:

- Revenue at Risk total (big €56px number) → counts up from 0 to
  the real number over 900ms ease-out
- Visitor Intent Hot / Warm / Cold counts → count up simultaneously
- Today's impact estimate chips (if present)

Subtle. Stops after first render. localStorage flag per session so
page refresh doesn't re-animate (respects repeat visitors).

### 7c. **Spark margin signature at scroll threshold**

At 70% scroll depth on /app/lite (approximately above the Spark
Status section), Spark mascot briefly fades in on the left margin
with a single contextual one-liner:

> "You're here for 2 minutes — saw 3 new visitors while you read."

Disappears after 4 seconds. Non-interactive. Once per page visit.
Pairs voice with specificity (Superhuman takeaway). Brand-building
without being cringe.

## 8. Visual devil's advocate (brutal self-critique of this spec)

- **8 custom SVG icons = 3–4h of design work.** If the founder wants
  to ship fast, Cat-5 the SVG work and use amber-tinted Heroicons as
  Phase 1. Originality drops from 8/10 → 6/10 but shippable same day.
  → **Decision needed: now or Phase 2?**
- **Animated count-up** can feel gimmicky if merchants refresh and
  see the same stat "grow" weekly. Mitigation: localStorage per
  session, NOT per-page-load. If the merchant closes the tab and
  reopens, new session → animate. Same-session refresh → no animate.
- **Spark margin one-liner** risks cringe. A Shopify small-business
  merchant may find it cute OR patronizing. High variance. **Best
  to A/B test** or ship behind a feature flag so we can measure
  engagement before committing. → **Decision: ship behind flag, off
  by default, enable for 1 week on founder's store?**
- **Moodboard is 3 tech-native apps** (Linear/Stripe/Superhuman).
  A Shopify merchant may not share this aesthetic sensibility. Risk
  of building for an audience we admire, not serve. Counter: the
  landing already converts in this aesthetic → consistent is
  better than diverged.
- **This spec doesn't cover color-blindness accessibility audit on
  amber-on-emerald combinations.** Deferred to Phase 2 a11y sweep.
- **Section-to-section 40px spacing** means the page becomes TALLER
  (more scroll). At ~8 sections × 40px = 320px extra scroll depth.
  Mitigation: the scroll is the narrative (thesis §1), so taller
  = more deliberate reading. Not a bug.
- **I flip-flopped twice on typography today.** This spec must be
  the FINAL word. Before any future typography edit, founder or I
  re-open THIS doc, update it, re-approve. Not edits-first.

## 9. Rubric target (honest, per `project_brutal_scoring_rubric.md`)

| Domain | Current | Target after this spec shipped |
|---|---|---|
| Visual hierarchy | 7.0 | 9.0 |
| Originality | 6.0 | 8.0 (if SVG ships) / 7.0 (Heroicons) |
| Warmth | 7.5 | 8.5 |
| Density / cramped | 6.5 | 8.5 (with 40px rhythm) |
| Cohesion with landing | 5.5 | 9.0 (same gradient, same type) |
| **Weighted Lite rubric** | 8.3 | **9.2** (target) |

9.5 remains the honest ceiling. 11/10 is an inspirational target; 9.2
is what this spec realistically unlocks without inventing new product
content.

## 10. Implementation plan — atomic commits after approval

### Commit A — type scale + spacing + color tokens (≈1–2h)
- Write all tokens into a small `LiteTokens.ts` shared file.
- Rewrite the 9 sections on /app/lite to consume tokens.
- Enforce: 1 amber H2 per section, 40px rhythm between sections.
- No icon / animation work yet.
- **Exit criteria**: /app/lite reads consistent, nothing bespoke.
  Founder validates the baseline.

### Commit B — originality layer (SVG icons + count-ups) (≈3–4h)
- Design + ship 8 custom SVG signatures.
- Wire count-up animation on RARS + Visitor Intent numbers.
- No Spark margin signature yet.
- **Exit criteria**: originality 8/10. Founder validates.

### Commit C (optional) — Spark margin signature (≈1h)
- Behind feature flag `NEXT_PUBLIC_SPARK_MARGIN=1`.
- Enable on founder's store only for 1 week; measure scroll depth
  + tab time before rolling out.
- **Exit criteria**: founder reads the one-liner, says "ship" or
  "kill".

---

## 11. What founder must decide before I touch TSX

Three binary decisions:

1. **SVG icons now or Phase 2?** → impacts Commit B scope.
2. **Spark margin signature — ship behind flag, or skip entirely?**
3. **Any sections to add / remove from the 7 narrative beats?** Or
   keep them exactly as listed in §2?

Answer those three, I execute Commit A first in one atomic pass.
