# HedgeSpark — AI Agent Operations Manual

## Entry Protocol

1. Read `CLAUDE.md` — system architecture, safety rules, verification commands
2. Read `EXECUTION_POLICY.md` — tier model, domain classification, escalation triggers
3. Check current health: `curl -s http://127.0.0.1:8000/system/health`
4. Check git state: `cd /opt/wishspark && git log --oneline -5 && git status --short`
5. Check test suite: `./venv/bin/python -m pytest tests/ --ignore=tests/test_scaling_intelligence.py -q`

## Module Ownership Map

| Domain | Primary Files | Responsibility |
|--------|--------------|----------------|
| **Event Ingestion** | `app/api/track.py` | POST /track, /track/batch — storefront events |
| **Purchase Bridge** | `app/api/track.py:_persist_visitor_bridge` | shopify_y mapping + VPS creation |
| **Attribution** | `app/services/utm_attribution.py`, `app/api/attribution.py` | First/last touch source attribution |
| **Behavioral Cohorts** | `app/services/behavioral_cohorts.py` | Pre-purchase behavior segmentation |
| **LTV Cohorts** | `app/services/ltv_engine.py`, `app/services/cohort_engine.py` | Monthly/weekly customer retention |
| **Revenue Forecast** | `app/services/revenue_forecast.py` | Trend + volatility projection |
| **Merchant Chatbot** | `app/services/merchant_chatbot.py` | Classification, diagnostics, repair |
| **Bugfix Pipeline** | `app/services/bugfix_pipeline.py` | Triage → propose → apply → promote |
| **Evolution Engine** | `app/services/evolution_engine.py` | Weekly code scanner, support patterns |
| **Meta Reviewer** | `app/services/meta_reviewer.py` | Strategic proposal prioritization |
| **Orchestrator** | `app/services/orchestrator.py` | Deterministic + LLM action execution |
| **Webhook Monitor** | `app/services/webhook_monitor.py` | Per-merchant drift tracking |
| **Telegram Agent** | `app/services/telegram_agent.py` | Operator commands, daily digest |
| **Merchant Digest** | `app/services/merchant_digest.py` | Weekly email via Resend |
| **LLM Budget** | `app/core/llm_budget.py` | Monthly cap, per-module limits, 429 backoff |
| **Repair Claims** | `app/core/repair_claim.py` | Distributed repair lock (Redis SET NX) |
| **Onboarding** | `app/services/onboarding.py` | Webhook + tracker registration |
| **Session Auth** | `app/core/merchant_session.py`, `app/core/deps.py` | JWT cookie, session version |

## Operator Endpoints

All require `X-API-Key: DASHBOARD_API_KEY` header.

| Endpoint | Returns |
|----------|---------|
| `GET /ops/llm-budget` | Monthly spend, cap, blocked count, 429 state |
| `GET /ops/incidents` | Active support incidents |
| `GET /ops/webhooks/status` | Fleet webhook health |
| `GET /ops/tracker/status` | Fleet tracker delivery |
| `GET /ops/attribution/health` | Attribution pipeline status |
| `GET /ops/digest/status` | Merchant email delivery |
| `GET /ops/meta-review` | Latest strategic review |
| `GET /ops/bugfixes` | Bugfix candidates |
| `GET /ops/evolution` | Evolution proposals |
| `GET /ops/alerts` | Unresolved operational alerts |
| `POST /ops/sentry-test` | Trigger test error for Sentry verification |
| `GET /system/health` | Subsystem health (no auth required) |

## Telegram Commands

`/status` `/costs` `/merchants` `/scaling` `/incidents` `/meta_review` `/digest` `/webhooks`
`/approvals` `/approve` `/reject` `/bugfixes` `/bugfix_approve` `/bugfix_apply`
`/promotions` `/merge` `/review` `/help`

## Database Tables (Key)

| Table | Purpose |
|-------|---------|
| merchants | Shop config, tokens, billing, onboarding state |
| events | Raw storefront events (partitioned by month) |
| shop_orders | Shopify orders (pixel + webhook) |
| visitor_purchase_sessions | Visitor → order attribution bridge |
| product_metrics | Pre-aggregated per-product behavioral metrics |
| opportunity_signals | Detected behavioral signals |
| bugfix_candidates | Auto-detected bug fix proposals |
| evolution_proposals | Self-improvement proposals |
| meta_reviews | Weekly strategic prioritization |
| ops_alerts | Operational alerts (deduped) |
| support_incidents | Merchant chatbot incidents |
| audit_log | Immutable action audit trail |
| reviewer_assessments | AI reviewer verdicts |

## What "Healthy" Looks Like

- `/system/health` → status: "ok", all subsystems ok
- `/ops/attribution/health` → pipeline_status: "healthy"
- `/ops/llm-budget` → monthly_cap_reached: false
- `/ops/webhooks/status` → broken: 0, unreachable: 0
- Worker logs show cycle completions without errors
- 631+ tests passing

## Danger Zones

| Area | Risk | Why |
|------|------|-----|
| `app/core/token_crypto.py` | **CRITICAL** | Merchant token encryption — wrong change = all tokens unreadable |
| `app/core/merchant_session.py` | **CRITICAL** | Session signing — wrong change = all merchants locked out |
| `app/api/shopify_oauth.py` | **HIGH** | OAuth flow — wrong change = installs break |
| `app/api/webhooks.py` | **HIGH** | Order ingestion + GDPR — wrong change = revenue data lost |
| `migrations/` | **HIGH** | Schema changes — wrong migration = data loss |
| `.env` | **CRITICAL** | Production secrets — never commit, never log |
| `ecosystem.config.js` | **HIGH** | PM2 config — wrong change = all processes down |

## When to Stop and Ask

See `EXECUTION_POLICY.md` §4 for the complete escalation trigger list. Summary:

- Before modifying any TIER_2 file (auth, billing, OAuth, webhooks, migrations, secrets)
- Before running database migrations or destructive git operations
- Before changing environment variables or deploy configuration
- When test count drops below 631
- When `/system/health` shows "critical"
- When unsure about tenant isolation (shop_domain scoping)
- When a change touches 3+ domains (auto-escalate to TIER_1)
- When modifying governance logic (orchestrator, reviewer, project_brain, execution policy)

## Execution Policy Reference

All code changes and operational actions are governed by the tiered execution model in `EXECUTION_POLICY.md`:

| Tier | Meaning | Examples |
|------|---------|----------|
| TIER_0 | Autonomous — apply with tests passing | Service logic, workers, frontend, tests |
| TIER_1 | Propose only — human must approve | Tracker JS, orchestrator, LLM infra, models, multi-domain refactors |
| TIER_2 | Human-only — agent must never modify | Auth, billing, OAuth, webhooks, migrations, secrets, deploy |

**Quick rule:** If unsure which tier, treat it as TIER_1 (propose, don't apply).
