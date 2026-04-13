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

## What's NOT covered yet

- Shopify OAuth flow (requires real merchant session)
- Pro dashboard (requires authenticated session)
- Autonomous pipeline end-to-end

Those are the next rings. Keep this suite honest: every test here must
be a test you'd be afraid to break.
