# HedgeSpark — AI Agent Context

Product: HedgeSpark (formerly WishSpark)
Type: AI Commerce Intelligence SaaS for Shopify
Status: Production — live with real merchants

## Architecture

```
/opt/wishspark/
├── backend/          FastAPI API server (port 8000)
├── dashboard/        Next.js merchant dashboard (port 3000)
├── tracker/          Storefront JS scripts (spark-tracker.js, spark-pixel.js, spark-attribution.js)
├── migrations/       Alembic DB migrations
└── ecosystem.config.js   PM2 process manager config
```

Reverse proxy: Traefik (Docker) with Let's Encrypt TLS
- `api.hedgesparkhq.com` → backend:8000
- `app.hedgesparkhq.com` → dashboard:3000

## PM2 Processes (all singleton, fork mode)

| Process | Script | Cycle |
|---------|--------|-------|
| wishspark-backend | uvicorn app.main:app | Always |
| wishspark-dashboard | next start | Always |
| wishspark-worker | intelligence_worker.py | 10 min |
| wishspark-agent-worker | agent_worker.py | 15 min |
| wishspark-aggregation-worker | aggregation_worker.py | 5 min |
| wishspark-segment-monitor | segment_monitor_worker.py | 5 min |
| wishspark-nudge-optimizer | nudge_optimization_worker.py | 6 hours |
| wishspark-gdpr-worker | gdpr_worker.py | 5 min |

## Key Data Flows

**Storefront Tracking:**
spark-tracker.js → POST /track → events table → product_metrics (aggregation worker)

**Purchase Attribution:**
spark-pixel.js → POST /track (event_type=purchase) → shop_orders + visitor_purchase_sessions
Identity bridge: shopify_y cookie mapping (Redis hs:symap:{shop}:{id}) OR events table lookup

**Merchant Session:**
Shopify OAuth → /auth/callback → hs_session cookie (HttpOnly, Secure, SameSite=None)
Session bootstrap: GET /auth/session?shop=... → creates cookie → redirects to dashboard

**Webhook Lifecycle:**
OAuth install → ensure_orders_webhook (app/uninstalled only — orders/updated needs PCD approval)
Aggregation worker checks webhook health daily → auto-repair → webhook_monitor tracks status

## Key Infrastructure

**LLM Budget:** €5/month hard cap. Per-module daily limits. 429 exponential backoff on all providers.
Budget state: `app/core/llm_budget.py`. Operator view: GET /ops/llm-budget

**Sentry:** Enabled when SENTRY_DSN is set. Scope enriched with request_id, shop_domain, route, worker.

**Redis Keys:**
- `hs:symap:{shop}:{shopify_y}` — shopify_y → visitor_id mapping (90d TTL)
- `hs:wh_status:{shop}` — webhook health status (48h TTL)
- `hs:digest:sent:{date}` / `hs:digest:lock:{date}` — daily digest dedup
- `hs:mdigest:{shop}:{week}` — merchant weekly digest dedup
- `hs:repair_claim:{shop}:{area}` — distributed repair lock (5 min TTL)
- `llm:monthly_cost:{month}` — LLM spend tracking
- `llm:daily:{module}:{date}` — per-module call counts

## Safety Rules

See `EXECUTION_POLICY.md` for the full tiered execution model. Summary:

**TIER_2 — Never modify without explicit human approval:**
- `app/core/token_crypto.py` — merchant token encryption
- `app/core/merchant_session.py` — session JWT signing
- `app/api/shopify_oauth.py` — OAuth flow
- `app/api/billing.py` — billing logic
- `app/core/deps.py` — auth middleware
- `app/api/webhooks.py` — webhook handlers
- `app/services/order_ingestion.py` — revenue data pipeline
- `app/services/gdpr_processor.py` — GDPR compliance
- `migrations/` — database schema
- `ecosystem.config.js` — PM2 config
- `.env` — production secrets
- `deploy.sh` — deployment script

**TIER_1 — Propose only, human approves:**
- `tracker/*.js` — storefront scripts (runs in merchant browsers)
- `app/services/orchestrator*.py` — action execution logic
- `app/services/bugfix_pipeline.py`, `app/services/promotion_pipeline.py` — self-modification
- `app/services/reviewer_layer.py`, `app/services/project_brain.py` — governance logic
- `app/core/llm_budget.py`, `app/core/llm_router.py` — LLM infrastructure
- `app/models/*` — SQLAlchemy model definitions
- Multi-file refactors touching 6+ files

**TIER_0 — Safe to modify (with tests passing):**
- `app/services/*` — business logic services (except those listed above)
- `app/api/*` — API endpoints (except oauth, billing, webhooks)
- `app/workers/*` — background workers
- `dashboard/src/*` — frontend components
- `tests/*` — test files

## Verification After Changes

```bash
# Backend tests (must pass 631+)
./venv/bin/python -m pytest tests/ --ignore=tests/test_scaling_intelligence.py -q

# Dashboard build (must complete without errors)
cd /opt/wishspark/dashboard && npx next build

# Health check (after pm2 restart)
curl -s http://127.0.0.1:8000/system/health | python3 -m json.tool

# Attribution pipeline
curl -s http://127.0.0.1:8000/ops/attribution/health -H "X-API-Key: $KEY"
```

## Deploy

```bash
cd /opt/wishspark/dashboard && npx next build   # rebuild frontend
pm2 restart ecosystem.config.js                  # restart all processes
pm2 logs wishspark-backend --lines 20            # verify startup
```

## Blocklist

`legacy.myshopify.com` is a dead dev placeholder. Blocklisted in:
- `app/services/onboarding.py` (_ONBOARDING_BLOCKLIST)
- `app/services/webhook_health.py` (repair_missing_webhooks)
- `app/workers/aggregation_worker.py` (webhook health loop)

## Tracker Versioning

TRACKER_VERSION in `app/core/tracker_version.py`. Bump when `tracker/spark-tracker.js` changes.
Script tag URL: `{APP_URL}/tracker.js?v={VERSION}`. Stale tags auto-cleaned on next onboarding cycle.
