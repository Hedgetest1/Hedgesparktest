# HedgeSpark — Merchant-Facing Coherence Spec v1 (2026-04-21)

**Status:** Approved direction 2026-04-21, in parallel with
`LITE_VISUAL_SPEC_v5.md`. Defines the cross-surface coherence that v5
assumes. Every merchant-facing surface — not just `/app/lite` — adopts
the Spark-narrator voice, the canonical visual tokens, and the 5 gates.

## 0. Why this spec exists

The founder's 5 gates (`LITE_VISUAL_SPEC_v5.md` §1) are about merchant
experience. A merchant does not only see the Lite dashboard. They also
receive a weekly digest email, a morning brief email, a night-shift
digest, onboarding emails, re-engagement emails, click on storefront
nudges (`spark-nudge`), ask the AI chatbot (`AskHedgeSpark`), and open
the PWA on mobile / the embed inside Shopify admin.

If the Lite primary slot is narrated by Spark in Bloomberg-terminal
prose, but the weekly digest is written in corporate copy, but the
chatbot replies in chatty language, but the nudge is LLM-generated with
no voice, **the product feels like 4 different products stitched
together**. The "a-prova-di-scemo" principle collapses at the first
voice switch.

v1 of this spec closes that gap by declaring:

> **Spark is one narrator, on every merchant-facing surface.**

It also extends the 5 gates beyond Lite, and adds a preventer audit so
the coherence holds as the codebase grows.

## 1. The canonical Spark voice (extends LITE v5 §3)

Voice rules apply **to every merchant-facing string** in the product.
They are the extension of the LITE v5 §3 rules to all surfaces.

1. **First person singular.** Spark narrates. `I noticed`, `I saved you`, `I'm watching tonight`. Never `HedgeSpark noticed` / `The system detected` / `Our algorithm…`.
2. **Bloomberg-terminal-in-prose.** Factual, numeric, inline. Not Duolingo, not Mailchimp, not Twitter thread.
3. **Max 12 words per sentence.** Enforced at headline / CTA / primary-copy contexts. Long-form explanations (methodology in Deeper drawer) may exceed but never ramble.
4. **Currency in merchant's symbol, inline with prose.** `€340 leaking today` not `Amount at risk: €340`.
5. **Loss-framing 60% / growth-framing 40%.** Measured per surface per rendered session. Absolute pure-growth copy is a violation (see Audit §6).
6. **Zero jargon.** Forbidden tokens without an immediate plain-English gloss: `CVR`, `COGS`, `CAC`, `ARPC`, `MRR`, `ARR`, `LTV`, `AOV`, `ROAS`, `attribution window`, `cohort`, `p-value`, `holdout`, `confidence interval`. Permitted when followed by a plain-language gloss in the same line (e.g. `LTV (what each customer spends with you over time)`).
7. **No personality quotes.** Spark is a reporter, not a character. No jokes, no emojis in copy (except in transactional labels where operationally expected — `✓` / `⚠︎` / `→`), no exclamation marks except in the single "success confirmed" signal.
8. **Numbers rounded to merchant-relevant precision.** Euros (not cents) in prose. Percentages to 1 decimal max. Days-since events as `2h ago` / `yesterday` / `2 days`, not ISO timestamps.
9. **Forbidden phrases everywhere.** The pricing anti-canon (CLAUDE.md §3) applies to every surface: never `free forever`, `no credit card`, `try free`, `$0 forever`. Enforced by preflight audit.
10. **Identical greetings and closings across surfaces.** See §4 cross-surface primitives.

## 2. Surface-by-surface application

Each merchant-facing surface gets a paragraph describing how the voice
rules land. Where a current implementation diverges, the retrofit
commit is scheduled but not done in this spec.

### 2.1 Dashboard `/app/lite`
- Covered by `LITE_VISUAL_SPEC_v5.md`.
- Canon-compliant by construction once v5 ships.

### 2.2 Dashboard `/app/pro`
- Not covered by v5 scope. Pro today has ~80+ cards with mixed voice.
- Position: Pro inherits the same voice, but the information density is
  higher (Pro merchants expect depth). Pro is NOT "Lite with more
  cards" — it's "Lite + the full diagnostic workbench behind
  explicit section folds".
- Scheduled follow-up: `PRO_VISUAL_SPEC_v1.md` as a separate doc-only
  commit after v5 ships. Out of scope for this coherence pass — the
  voice rules apply now, the structural redesign is a future sprint.

### 2.3 Transactional emails (Resend)

Template file: `backend/app/services/email_templates.py`.
Orchestrator: `backend/app/services/email_orchestrator.py`
(`submit_intent` — never bypass with `send_email` direct).

Emails in scope:
- **Weekly digest** — Monday morning
- **Morning brief** — daily, 8am shop-local time
- **Night shift digest** — daily, post-close summary
- **Onboarding sequence** — first week after install
- **Re-engagement drift** — merchants who lose activity
- **Breach notifications** — Art. 33/34 mandatory (template tone is more formal; voice rules still apply minus first-person)

Voice application per email:
- **Subject lines:** Spark-narrator. `I saved you €230 this week.` / `Your Monday target is 3 days away.` / `Silk Pillowcase is leaking 68 views a day.` Max 10 words.
- **Opening line:** mirrors dashboard Zone 1 — `Good morning, {shop}.` or `Hi {shop}.` No `Dear merchant` / `Hello valued user`.
- **Hero block:** one number, one story, one action. Same shape as dashboard Zone 2 (leak gauge → hero number + interpretation).
- **CTA:** one primary amber button, label = verb + number (`See the €230 breakdown →`). Never 3 buttons. Secondary link in small slate-400 below.
- **Closing:** `I'm watching. — Spark.` (new canonical closing, matches the Zone 5 "memory" persona).
- **Template shell:** already canonicalized via `_wrap_html` (commit `a38d95f`). Geist font + Spark mascot in header + canonical footer. Voice rules apply *inside* this shell.

Retrofit scope (post-v5): audit every template for the 10 voice rules, rewrite subject + hero + CTA + closing lines where they violate. Scheduled as follow-up sprint, not part of v5 ship.

### 2.4 AI chatbot (`AskHedgeSpark` / `/pro/chat`)

Component: `dashboard/src/app/components/AskHedgeSparkCard.tsx`.
Backend: `backend/app/services/merchant_chatbot.py` + `chatbot_llm_fallback.py`.

Voice application:
- **Prompt primer** (system prompt sent to Claude) must enforce the 10 voice rules (first person `I`, Bloomberg-in-prose, max 12 words per sentence in summary responses, loss-framing 60%, zero jargon without gloss). Canonical primer stored in `app/services/merchant_chatbot_voice.py` (new file, §7 implementation plan).
- **Scope guard:** Spark answers data questions about the merchant's store. Refuses product Q&A ("how does your pricing work?") — those go to the pricing page. Refuses competitor comparisons — those are founder-owned. Refuses generic business advice — that's not what merchants pay €39 for.
- **Citations:** every numeric claim in a response MUST cite the endpoint/card it came from, inline. `Your week-4 repeat rate is 12.3% (from Retention → Monthly cohorts).` Zero fabricated numbers; if Spark doesn't know, Spark says so — `I don't have that data yet. Let me watch for the next cycle.`
- **Opening / closing:** chatbot doesn't do greeting (conversational, not transactional). Closing is optional — Spark can sign off a long response with `— Spark.` but not every turn.
- **LLM budget:** CLAUDE.md §8.1. Deterministic-first (RAG over `project_brain_snapshot` + past answers). LLM fallback only when deterministic confidence < threshold. Voice primer adds ~200 tokens per LLM call — budgeted.

Retrofit scope: add the voice primer, wire the scope guard, add citation-enforcement to the response schema. Separate commit, same sprint as v5.

### 2.5 Storefront nudges (`spark-nudge.js` + AI composer)

Tracker: `tracker/spark-nudge.js` (v3, holdout-measured).
Composer: `backend/app/services/nudge_composer.py` + forbidden-phrase validator.

Voice application:
- **Nudge copy is visitor-facing, not merchant-facing.** Visitors are Shopify shoppers, not merchants. The Spark voice rules DO NOT apply here the same way — shoppers don't want a narrator, they want a subtle affordance.
- **But:** the MERCHANT sees the nudge copy in the dashboard (Pro's AI composer UI) and approves it. Spark's tone in that approval UX must match. The composer's preview panel and explainer text follow the 10 voice rules.
- **Composer voice in dashboard:** `I drafted 4 variants for Silk Pillowcase. The A variant is urgency-framed (+12% lift predicted); the B variant is social proof (+9%).` → Spark narrates the composer output.

Retrofit scope: composer UI copy review. Low-priority.

### 2.6 Mobile PWA (`/app` on mobile + `manifest.json` + `sw.js`)

The PWA is the dashboard rendered responsively. Voice rules inherited from §2.1 and §2.2. Mobile-specific constraints:
- **Touch targets ≥44px.** CTAs in Zone 1 / Zone 3 must respect this.
- **No horizontal scroll at 375px.** Already a Lite v5 gate (Gate 2).
- **Offline state:** when the service worker serves a cached page and the network is down, display a polite amber banner at the top: `I'm offline — showing your last brief from {timestamp}.` Narrator voice, not a dry error.
- **Installability:** the PWA `manifest.json` has `short_name: "Hedge Spark"` (already set in `layout.tsx:55`). Launcher icon = Spark mascot (already `apple-touch-icon`). Splash screen = the wordmark + mascot combo, dark background.

Retrofit scope: offline banner copy + splash screen design. Part of v5 mobile polish.

### 2.7 Shopify admin embed (`/app` in iframe inside Shopify admin)

CSP allows Shopify admin via `frame-ancestors` on dashboard (CLAUDE.md §9.3). Voice rules unchanged from §2.1.

Layout constraints unique to embed:
- **No duplicate top-nav.** Shopify admin has its own chrome; our top-nav must collapse or hide when in embed. Detection via `window.top !== window.self`.
- **Hide the "Install on Shopify" CTA** in the embedded context (the merchant is already installed).
- **Deeper drawer opens within the iframe**, not as a new window — no cross-frame navigation surprises.

Retrofit scope: embed-aware layout component. Small follow-up.

## 3. Cross-surface primitives (the exact strings)

Shared strings used across ≥2 surfaces. Single source of truth lives
in `backend/app/services/spark_voice_primitives.py` (new file,
additive) + TypeScript counterpart at
`dashboard/src/app/lib/sparkVoice.ts`. Both import from a shared JSON
doc `shared/spark_voice.json` that backend + frontend read.

### 3.1 Greetings

- Morning (06:00–11:59 shop-local): `Good morning, {shop_display_name}.`
- Afternoon (12:00–17:59): `Hi {shop_display_name}.`
- Evening (18:00–05:59): `Evening, {shop_display_name}.`
- Night Shift Agent emails (overnight): `Overnight update, {shop_display_name}.`

### 3.2 Opening verdict lines

- Full-leak: `This morning I noticed {€ total} leaking in {count} places.`
- Steady: `Steady morning — {€ total} at risk, {€ prevented} prevented.`
- Clean: `Clean morning — nothing leaking right now.`

### 3.3 CTA formats

All primary CTAs follow: **verb + number or product**. Never generic.

Correct: `See the 3 fixes →` / `Open Silk Pillowcase →` / `Check the €340 breakdown →`
Wrong: `Learn more` / `Click here` / `View dashboard` / `Continue`

### 3.4 Closings (Spark sign-offs)

- Email closing: `I'm watching. — Spark.`
- Night shift email closing: `Sleep easy. I've got the night. — Spark.`
- Chatbot closing (optional, long responses only): `— Spark.`
- Dashboard: no closing (continuous surface).

### 3.5 Empty / loading / error states

Consistent phrasing across every surface:

- **Watching (cold start):** `Watching… {what} ready in {N} {time_unit}.`
- **Hiccup (recoverable error):** `I hit a hiccup loading {what}. Retrying on its own.`
- **Offline:** `I'm offline — showing your last brief from {timestamp}.`
- **No data:** `Nothing to show here yet — let's watch together.`

### 3.6 Loss vs growth framing ratio

Per rendered session (dashboard page load OR email render OR chatbot response), the copy must hit:
- ≥50% of primary lines framed as loss / at-risk / leaking / missing
- ≤50% framed as growth / saved / recovered / hit
- Target ratio: 60/40 loss-dominant

Rationale: loss aversion drives merchant action. Pure-growth framing
is flat. Pure-loss is alarmist. 60/40 hits the founder's brief in §5
of CLAUDE.md and has been validated qualitatively across the landing.

## 4. The 5 gates extended to every surface

| Surface | Gate 1 (<10s) | Gate 2 (easy) | Gate 3 (depth) | Gate 4 (beauty) | Gate 5 (€39 retire) |
|---|---|---|---|---|---|
| Dashboard `/app/lite` | LITE v5 | LITE v5 | LITE v5 drawer | LITE v5 charts | LITE v5 screenshot vs Lifetimely/Peel/Varos |
| Dashboard `/app/pro` | Section-by-section | ≤2 taps to section fold | All methodology in card footers | Per-card chart review | Screenshot vs Triple Whale / Northbeam |
| Weekly digest email | Subject + preview + hero block communicates today's value in <10s | One primary CTA | Full breakdown link to dashboard | Hero chart or hero number + mascot | Email render vs Lifetimely weekly email |
| Morning brief email | Same as Lite Zone 1 | Same | Link to `/app/lite` | Hero number | Screenshot vs Peel morning brief |
| Night shift email | Overnight summary <10s | Optional actions only | Link to Night Shift Timeline in drawer | Hero: overnight findings count | Screenshot vs competitors (none do this) |
| AI chatbot | Response comprehensible <10s | Answer in 1-3 sentences + citation | Link to source card | Inline chart where value-adding | Chatbot vs Moby (Triple Whale) |
| Nudges (composer) | Merchant understands proposed variant in <10s | One approve, one reject | Holdout methodology in explainer | Preview on store thumbnail | Composer vs Triple Whale Creative Cortex |
| Mobile PWA | Same as /app/lite | Touch targets ≥44px | Drawer works on mobile | Charts responsive | Mobile vs Lifetimely mobile |
| Shopify admin embed | Same as /app/lite | No duplicate nav | Drawer in iframe | Same visuals | Embed vs competitors in Shopify admin |

Gate 5 retirement audit is the most subjective — but also the most
important. If a merchant has HedgeSpark's weekly digest email and
Lifetimely's weekly digest email side-by-side in their inbox on a
Monday, the HedgeSpark one must feel like a €39 product and the
Lifetimely one must feel like a €19 product. Otherwise we rewrite.

## 5. Audit script (sibling preventer)

File: `backend/scripts/audit_merchant_voice_coherence.py`. Runs in
preflight (`preflight.sh`). Blocking.

**What it scans:**
- All merchant-facing strings in `backend/app/services/email_templates.py`
- All merchant-facing strings in `dashboard/src/app/**/*.tsx` matching class-level inclusion heuristics (visible copy, not dev comments)
- `tracker/spark-nudge.js` copy templates
- `backend/app/services/nudge_composer.py` composer prompt templates

**What it flags (blocking):**

1. **Forbidden pricing phrases:** `free forever` / `no credit card` / `try free` / `$0 forever` (case-insensitive). Redact everywhere. CLAUDE.md §3.
2. **Unglossed jargon:** `CVR` / `COGS` / `CAC` / `ARPC` / `MRR` / `ARR` / `LTV` / `AOV` / `ROAS` / `holdout` / `p-value` / `cohort` / `attribution window` appearing without a plain-English gloss in the same HTML element / JSX element / email paragraph.
3. **Third-person narration in merchant-facing context:** `HedgeSpark noticed` / `The system detected` / `Our algorithm` / `Our AI`. First-person singular is the law.
4. **Personality anti-pattern:** Emojis inside copy strings (except ✓/⚠/→ in specific contexts); exclamation marks outside the single `success-confirmed` token; interjections like `Wow` / `Oh` / `Great`.

**What it warns (non-blocking, for audit):**
- Loss-framing ratio <50% on any individual surface
- CTA text not matching "verb + number" shape (e.g., `Learn more`, `Click here`)
- Sentence >12 words in H1/H2/CTA contexts

**Output:** preflight either passes or prints a line-by-line blocker
list with file:line references. No silent lint warnings.

## 6. Implementation plan (atomic commits)

Same spirit as v5 implementation plan. Each commit independently
reviewable.

1. **Commit A** (this doc) — atomic, reviewable. No code.
2. **Commit B** (shared primitives) — create `shared/spark_voice.json`, `backend/app/services/spark_voice_primitives.py`, `dashboard/src/app/lib/sparkVoice.ts`. Contains §3 strings. Both backend and frontend import it.
3. **Commit C** (audit script) — `backend/scripts/audit_merchant_voice_coherence.py`. Wire into `preflight.sh` (blocking for rules 1-4, warning for 5-7).
4. **Commit D** (email subject + opening + closing retrofit) — touch only subject lines + greeting/closing in existing email templates. Hero block rewrites deferred to a copy sprint.
5. **Commit E** (chatbot voice primer) — `backend/app/services/merchant_chatbot_voice.py` loaded by `merchant_chatbot.py` as system prompt. Test: 10 canned merchant questions produce voice-compliant responses.
6. **Commit F** (PWA offline banner + embed layout) — small follow-ups.

Commits B + C land in the same sprint as LITE v5 implementation (§12
of v5 spec). Commits D + E + F follow in subsequent sprints.

## 7. Anti-flip-flop discipline

1. Read this spec before touching any merchant-facing string.
2. If adding a new surface not listed in §2, extend this spec first, then ship the surface. No orphan voice.
3. Shared primitives in `shared/spark_voice.json` are the source of truth — never duplicate strings in surface code.
4. Audit script blocks commits that violate rules 1-4. Non-negotiable.
5. When a surface copy decision conflicts with this spec, the spec wins; if the spec is silent, update the spec first, doc-only commit, then code.

## 8. Acceptance criteria for v1

- `/docs/HEDGESPARK_MERCHANT_COHERENCE_SPEC.md` (this file) committed.
- `shared/spark_voice.json` exists, used by ≥2 surfaces.
- `audit_merchant_voice_coherence.py` committed + wired + runs green on current codebase (which may require Commit D's retrofit to pass all blocking rules).
- LITE v5 implementation references this spec for its Zone 1 greeting + CTA format + Zone 5 memory closing.
- At least one email template (weekly digest OR morning brief) uses the canonical greeting + closing from §3.

## 9. Out of scope for v1

- Pro dashboard full redesign (separate spec).
- Landing page copy review (landing is founder-owned, spec §3 applies but founder decides per-line).
- Founder-facing comms (Telegram agent, ops_alerts). Those are governed by `reality_founder_messaging.md`, not this spec.
- Public website transparency / proof / pricing pages. Those have their own voice (marketing-formal), and are founder territory.

v2 of this spec (future) covers Pro dashboard and landing page voice audit.
