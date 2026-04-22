# E2E smoke suite — HedgeSpark

The minimum viable foundation. Two specs, fast (<30s), deterministic.

## Why this exists

Unit tests pass all day while the shipped bundle white-screens new visitors.
This suite runs against a real backend + frontend and fails loudly the
moment the golden path breaks.

## Running locally

```bash
cd /opt/wishspark/dashboard
npm install                # installs @playwright/test
npm run e2e:install        # downloads the chromium runner
npm run e2e                # runs the suite
```

Environment variables:
- `E2E_BASE_URL` — frontend URL (default `http://127.0.0.1:3000`)
- `E2E_API_BASE` — backend URL (default `http://127.0.0.1:8000`)

## What's covered

- **landing.spec.ts** — hero, CTAs, **fake-floor guard** (the €125,000
  string is explicitly forbidden to appear on the landing page), 16
  capabilities section.
- **backend_contract.spec.ts** — public ROI counter shape contract (live/
  warming state invariants), `/system/health` shape.
- **session_durability.spec.ts** — 11 scenarios covering JWT recovery
  (tampered / expired / forced-logout), hint-based re-bootstrap,
  retry-backoff-absorbs-backend-blips, Reconnect-UI fallback,
  preview-downgrade, cross-reload persistence. Each scenario names the
  invariant it protects in the failure message so a broken test
  points at the exact regression.

## Session durability suite — runtime requirements

The `hs_session` cookie is hardcoded `secure=true` in the backend, so
browsers drop it on `http://localhost`. The session suite therefore
runs against HTTPS prod infrastructure using a dedicated smoke
merchant:

```bash
set -a && source /opt/wishspark/backend/.env && set +a
E2E_BASE_URL=https://app.hedgesparkhq.com \
E2E_API_BASE=https://api.hedgesparkhq.com \
npm run e2e:session
```

Required env:
- `MERCHANT_SESSION_SECRET` — for minting synthetic JWTs (tampered /
  expired / stale-sv tokens). Lives in `backend/.env`.
- `E2E_SMOKE_SHOP` — defaults to `hedgespark-smoke.myshopify.com`.
  Must be an active Pro merchant.

Total runtime: ~34s. Zero flake observed across 3 consecutive runs.

## Drift preventer

The preflight step `Session durability invariants` (in
`backend/scripts/preflight.sh`) runs
`audit_session_durability_invariants.py` on every commit. It greps the
session-handling source files (`/app/page.tsx`, `useSession.ts`,
`deps.py`, `merchant_session.py`, `shopify_oauth.py`) for the 11
invariants the E2E scenarios assert against. If someone removes the
retry backoff, the `session_version` check, or the Reconnect UI copy,
the commit is blocked BEFORE a broken build ships. Each audit line
names which E2E scenario would regress.

## What's NOT covered yet

- Shopify OAuth flow (requires real merchant session)
- Autonomous pipeline end-to-end
- Shopify embedded iframe context (complex auth interaction)

Keep this suite honest: every test here must be a test you'd be afraid
to break.
