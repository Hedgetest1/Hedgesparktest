# Sentry operations — HedgeSpark

Operator-facing runbook for the Sentry Team-plan integration shipped
2026-04-24 (C1..C6 sweep). Reference when setting up, debugging, or
tuning Sentry in HedgeSpark.

## What's wired today

| Surface | Initialized in | Component tag |
|---|---|---|
| Backend API (FastAPI) | `app/core/sentry_init.py` via `app/main.py` | `backend` |
| agent_worker | same module, called from worker entrypoint | `agent_worker` |
| intelligence_worker | " | `intelligence_worker` |
| aggregation_worker | " | `aggregation_worker` |
| segment_monitor_worker | " | `segment_monitor_worker` |
| nudge_optimization_worker | " | `nudge_optimization_worker` |
| gdpr_worker | " | `gdpr_worker` |
| Next.js dashboard (client) | `dashboard/sentry.client.config.ts` | `frontend` |
| Next.js dashboard (SSR) | `dashboard/sentry.server.config.ts` | `frontend-ssr` |
| Next.js dashboard (edge) | `dashboard/sentry.edge.config.ts` | `frontend-edge` |

All 10 surfaces share the same DSN (separate frontend project
recommended long-term for cleaner stack-trace symbolication).

## Capabilities actually used

- **Error tracking** — every unhandled exception in any of the 10
  surfaces surfaces in Sentry with release tag, component tag,
  shop_domain user, request_id.
- **Performance monitoring** — FastApiIntegration + HttpxIntegration +
  SqlalchemyIntegration auto-trace every request / outbound HTTP /
  SQL query. `traces_sample_rate=0.05` (Team plan), `/auth/`,
  `/billing/`, `/webhooks/` sampled at 4× via TracesSampler.
- **Profiling** — `profiles_sample_rate=0.10` of traced transactions
  get a flame graph (Team plan).
- **Session Replay** — 1% baseline + 100% on error, `maskAllText=true`
  so merchant GMV/AOV numbers never leak.
- **Cron monitoring** — opt-in per-worker via `SENTRY_CRON_MONITORING`
  env var. **DEFAULT OFF** because Team-plan base includes only 1
  monitor (6 workers × check-ins would saturate the quota within
  hours). See "Cron monitoring" section below.

## Env var setup

`backend/.env.example` documents every Sentry var. Minimum for prod:

```
SENTRY_DSN=https://<key>@<org>.ingest.de.sentry.io/<project>
SENTRY_ENVIRONMENT=production
SENTRY_TRACES_SAMPLE_RATE=0.05
SENTRY_PROFILES_SAMPLE_RATE=0.10
SENTRY_WEBHOOK_SECRET=<hmac key for /webhooks/sentry/inbound>
```

Optional for release tagging + source-map upload:

```
SENTRY_RELEASE=hedgespark@<git-sha-12>      # auto-resolved if unset
SENTRY_AUTH_TOKEN=<personal auth token>
SENTRY_ORG=hedgespark
SENTRY_PROJECT=backend
```

Dashboard (`dashboard/.env.local`) — **`NEXT_PUBLIC_*` variants** so the
values land in the client bundle:

```
NEXT_PUBLIC_SENTRY_DSN=<same as SENTRY_DSN>
NEXT_PUBLIC_SENTRY_ENVIRONMENT=production
NEXT_PUBLIC_SENTRY_RELEASE=hedgespark@<git-sha-12>
```

After editing either file: rebuild + restart the affected process
(`pm2 restart wishspark-backend` or `dashboard/scripts/deploy.sh`).

## Cron monitoring (quota-gated)

Team-plan base tier includes **1 cron monitor**. If you enable all 6
workers, Sentry silently caps at 1 and the rest never check in — worse,
overage charges apply if volume tiers are billed.

The `cron_monitor()` decorator is wired on all 6 workers but GATED by
the `SENTRY_CRON_MONITORING` env allowlist. Empty (default) = every
`@cron_monitor` is a no-op. To enable:

```
SENTRY_CRON_MONITORING=agent_worker_cycle
```

Available slugs (see `backend/app/workers/*.py` `@cron_monitor(slug=...)`):

- `agent_worker_cycle` (15min) — **recommended** (orchestrator + bugfix pipeline + LLM)
- `intelligence_worker_cycle` (10min)
- `aggregation_worker_cycle` (5min)
- `segment_monitor_worker_cycle` (5min)
- `nudge_optimization_worker_cycle` (6h)
- `gdpr_worker_cycle` (5min)

**Fallback observability**: `invariant_monitor` (runs every 15min in
agent_worker) already queries `WorkerState.last_run_at` + runs the
critical preflight audits against live source. Sentry cron is additive,
not load-bearing.

## Notification of new releases

On every prod deploy, invoke:

```
backend/scripts/sentry_notify_release.sh
```

Skips gracefully if `SENTRY_AUTH_TOKEN` / `SENTRY_ORG` / `SENTRY_PROJECT`
or `sentry-cli` are missing. Uses `git rev-parse HEAD` for the release
identifier when `SENTRY_RELEASE` env isn't explicitly set.

To install sentry-cli on the VPS:

```
curl -sL https://sentry.io/get-cli/ | bash
```

## PII posture

`send_default_pii=False` is enforced in every init call. On top of that:

- `before_send` callback in `app/core/sentry_init.py` pipes every
  event through `app/core/llm_pii_guard.sanitize()` to redact emails,
  API keys, bearer tokens, IBAN, phone numbers, Shopify access tokens
  from exception values, breadcrumbs, capture_message text, request
  bodies.
- Scrubbed events carry the `sentry.pii_scrubbed=true` tag so we
  have visibility on how often the filter fires.
- Frontend Session Replay masks ALL text + ALL media (`maskAllText=true`,
  `blockAllMedia=true`). Merchant GMV / AOV / customer counts never
  appear in replay recordings.
- CSP `connect-src` allows `https://*.ingest.de.sentry.io` (EU region)
  only — no US endpoint reachable from the browser.

## Preflight + runtime enforcement

- `backend/scripts/audit_sentry_invariants.py` blocks every commit
  that deletes `init_sentry`, drops `send_default_pii=False`, removes
  a worker's Sentry init, or removes the Sentry CSP allowlist entry.
- `app/services/invariant_monitor` runs the same audit every 15min
  against live source; drift emits an `invariant_regression` ops_alert.

## Debugging checklist

1. No events arriving → check startup log for `sentry_init: initialized
   for component=...`. If absent, DSN missing or env not loaded.
2. Events arriving but no stack-trace symbolication → `SENTRY_RELEASE`
   probably unset at build time for the dashboard; re-run build with
   env var set + `SENTRY_AUTH_TOKEN` for source-map upload.
3. Replay quota exhausted before month end → lower
   `replaysSessionSampleRate` in `dashboard/sentry.client.config.ts`
   (base 50/month on Team plan).
4. Cron monitor not firing → confirm slug is in
   `SENTRY_CRON_MONITORING` allowlist; confirm worker restarted AFTER
   env change.
5. "100% cron monitors consumed" email → same as above; allowlist too
   wide, restrict to 1 slug.
