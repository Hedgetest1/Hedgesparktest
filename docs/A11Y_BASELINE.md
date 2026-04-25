# Dashboard a11y baseline — F6 closure 2026-04-25

This file is the empirical accessibility baseline for the HedgeSpark
dashboard. It captures (a) what axe-core flags as critical/serious
right now, (b) what the static pattern scanner flags as risk, and
(c) the policy that gets us to zero on both.

## Runtime baseline — axe-core (authoritative)

Captured against prod HTTPS via `npm run e2e:a11y` and the
companion `e2e/a11y_app.spec.ts` (authenticated routes).

**Public routes** (`e2e/a11y.spec.ts`): zero critical/serious.
Routes covered: `/`, `/pricing`, `/privacy`, `/terms`, `/cookies`,
`/status`. `/install` is intentionally excluded — its primary CTA
uses brand amber `#d4893a` on white at 2.82:1, which is a
coordinated palette decision (founder-domain per CLAUDE.md §1.1).

**Authenticated routes** (`e2e/a11y_app.spec.ts`): zero
critical/serious as of 2026-04-25 evening. Routes covered: `/app`,
`/app?as=lite`, `/app/pro`. Smoke shop:
`hedgespark-smoke.myshopify.com`. Requires
`MERCHANT_SESSION_SECRET` env var sourced from `backend/.env`.

**Routes NOT yet axe-covered** (R-blocker:sprint>1d) — added one
line each in `e2e/a11y_app.spec.ts` as their design ships:
- `/app/lite` (separate route from `/app?as=lite` preview)
- `/app/intelligence`
- `/app/operations`
- `/app/scale`
- `/app/marketplace`
- `/app/settings/*`
- `/app/groups`

Bar stays zero-critical/zero-serious for every newly added route.

## Static baseline — `audit_dashboard_a11y.py`

Run via preflight (informational, never blocking):
```
backend/venv/bin/python backend/scripts/audit_dashboard_a11y.py
```

**As of 2026-04-25 evening:** 415 findings.

| Pattern class | Count | Severity | axe-equivalent |
|---|---|---|---|
| Icon-only buttons missing aria-label / title | 0 | CRITICAL | button-name |
| Low-contrast small text (`text-slate-500/600` + `text-[≤13px]` / `text-xs`) | 415 | SERIOUS | color-contrast |

The 415 count is HIGH because `text-[10px] text-slate-500` is a
common label pattern across the dashboard. axe didn't flag all 415
because (a) some sites composite onto lighter backgrounds where
contrast actually passes, and (b) some sites are not yet rendered on
the routes the runtime suite covers. They remain a STRUCTURAL risk:
the next route added to axe coverage will surface a fresh batch.

## What was fixed in F6 (commit landing this baseline)

Five files, scoped to elements the runtime axe suite caught failing:

- `dashboard/src/app/components/Sidebar.tsx` — locked-tier nav text
  bumped `text-slate-600` → `text-slate-400` (floor selector + section
  nav + collapse toggle); "Pro" badge text bumped `text-[#d4893a]/60`
  → `text-[#e8a04e]` for 4.5:1+ contrast on the badge background.
- `dashboard/src/app/components/TopBar.tsx` — informational pills
  (date, Spark reputation, trial countdown) bumped
  `text-slate-500` → `text-slate-400`.
- `dashboard/src/app/components/SupportChat.tsx` — close + send
  buttons gained `aria-label`; subtle/header text bumped
  `text-slate-500/600` → `text-slate-400`.
- `dashboard/src/app/components/LiteTodaySection.tsx` — KpiTile
  label + "yesterday" delta bumped `text-slate-500` → `text-slate-400`.

## Policy going forward

1. **Runtime suite is the hard gate.** `npm run e2e:a11y` runs in CI
   nightly + on demand. Any critical/serious violation is a blocker.
2. **Static audit is the leading indicator.** `audit_dashboard_a11y.py`
   runs on every commit (info-only). Watch the count trend down.
   When it hits zero, flip to `--strict` and add to the preflight
   blocking gates.
3. **New routes added to `e2e/a11y_app.spec.ts` immediately** when
   their design ships. Don't let coverage drift behind feature work.
4. **Color tokens for small text:** prefer `text-slate-400` or lighter
   for any text under 14px on the default dark background. `slate-500`
   passes contrast only on lighter card backgrounds (≥`#1c1c2c`).
5. **Icon-only buttons** must always carry an `aria-label`. Decorative
   inner SVGs should be `aria-hidden="true"`.
6. **Tooltips:** prefer `aria-label` (concise) or `aria-describedby`
   (longer text via referenced element). The `title` attribute alone
   is a weak fallback — supported by axe today, but not always
   announced by screen readers.

## Routes intentionally not blocked

`/install` keeps its 2.82:1 amber CTA pending a brand palette
decision (CTA dark text on amber, or darker brand amber). Don't
re-add it to the enforced list until that decision lands.

## Related

- `e2e/a11y.spec.ts` — public-route axe suite
- `e2e/a11y_app.spec.ts` — authenticated dashboard axe suite (NEW 2026-04-25)
- `backend/scripts/audit_dashboard_a11y.py` — static pattern scanner (NEW 2026-04-25)
- `backend/app/core/wired_audits.py` — telemetry registry (audit added)
- CLAUDE.md §4 — visual language (slate palette tokens)
- `feedback_visual_standards.md` — color tokens by purpose
