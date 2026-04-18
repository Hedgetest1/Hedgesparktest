# Reality map — every scheduled job, trigger, and time-gate

> **Ground-truth index** of every PM2 worker, internal sub-task, time-of-day
> gate, day-of-week gate, and Redis/DB dedup-claim in HedgeSpark.
>
> Companion to the founder-messaging reality map in session memory.
> Prevents "I'll just build X" when X already runs.
>
> **Guarded by `backend/scripts/audit_scheduled_jobs_map.py`** — the
> preflight hook verifies every `def _run_*` helper in `agent_worker.py`
> appears in this file, and every entry in the agent_worker table below
> maps to a real function. Drift → commit blocked.

**Last full verification:** 2026-04-18 (file born).
**Next mandatory re-verification:** whenever any `app/workers/**` or
`app/workers/tasks/**` file changes, OR monthly at minimum.

---

## 8 PM2 processes (ecosystem.config.js)

| # | Process | Cycle | Script | Memory | Last verified |
|---|---|---|---|---|---|
| 1 | wishspark-backend | always-on | uvicorn app.main:app | 512M | 2026-04-18 |
| 2 | wishspark-dashboard | always-on | next start | 300M | 2026-04-18 |
| 3 | wishspark-worker | 10 min | intelligence_worker.py | 200M | 2026-04-18 |
| 4 | wishspark-agent-worker | 15 min | agent_worker.py | 200M | 2026-04-18 |
| 5 | wishspark-aggregation-worker | 5 min | aggregation_worker.py | 200M | 2026-04-18 |
| 6 | wishspark-segment-monitor | 5 min | segment_monitor_worker.py | 200M | 2026-04-18 |
| 7 | wishspark-nudge-optimizer | 6 h | nudge_optimization_worker.py (env: NUDGE_OPTIMIZER_INTERVAL_HOURS) | 200M | 2026-04-18 |
| 8 | wishspark-gdpr-worker | 5 min | gdpr_worker.py | 200M | 2026-04-18 |

All singletons (fork mode, instances=1). Cross-process claim via
`worker_lock(name, ttl=cycle+60s)` Redis SETNX.

---

## Internal sub-tasks inside agent_worker.py (cycle = 15 min)

Agent worker calls 43 `_run_*` helpers on every cycle. Each helper
self-gates via one of: in-process monotonic cooldown, DB
`worker_state.last_*_date`, Redis key, or time-of-day condition.
Listed here in execution order with the actual gate.

| Sub-task (fn) | Gate | Frequency reality |
|---|---|---|
| `_run_worker_watchdog` | no gate (Phase 0-pre0, runs FIRST in cycle) | every 15min cycle — resurrects stale PM2 workers |
| `_run_orchestrator` | no gate | every 15min cycle |
| `_run_onboarding` | no gate | every 15min cycle |
| `_run_onboarding_health` | no gate | every 15min cycle |
| `_run_bug_triage` | in-process cooldown | as fast as cooldown allows |
| `_run_bugfix_outcome_eval` | `worker_state` dedup | ~hourly effective |
| `_run_evolution_audit` | `should_run_audit()` monotonic cooldown | per cooldown constant |
| `_run_model_upgrade_scan` | `should_run_scan()` monotonic cooldown | per cooldown |
| `_run_meta_review` | `should_run_meta_review()` monotonic cooldown | per cooldown |
| `_run_evolution_conversion` | no gate | every cycle |
| `_run_evolution_gc` | `should_run_gc()` monotonic cooldown | per cooldown |
| `_run_monthly_evolution_audit` | `should_run_monthly_audit(db)` — 1st of month, OPUS call | monthly, Opus budget |
| `_run_scaling_intelligence` | no gate | every cycle |
| `_run_entitlement_health_scan` | no gate | every cycle |
| `_run_brain_refresh` | no gate | every cycle |
| `_run_daily_digest` | **Rome hour ≥ 8 AND `worker_state.last_digest_date != today_rome`** | **once per Rome day, first cycle after 08:00 Rome** (this IS the daily brief — see memory reality_founder_messaging.md system #1) |
| `_run_breach_classifier` | `worker_state` dedup | per cadence |
| `_run_audit_log_integrity_check` | `worker_state` dedup | per cadence |
| `_run_uninstall_erasure_watchdog` | in-process monotonic ~hourly | hourly |
| `_run_security_heartbeat` | self-rate-limited via `_should_run()` (hourly) | hourly |
| `_run_gdpr_sla_enforcement` | no gate | every cycle |
| `_run_data_retention` | `worker_state` dedup daily | once per day |
| `_run_pipeline_self_upgrade` | **Monday 04:00-05:00 UTC only** + `worker_state.last_self_upgrade_week` | weekly, kill switch `SELF_UPGRADE_PAUSED=1` |
| `_run_merchant_digest` | **Monday only, Europe/Rome** — sends weekly merchant emails | weekly |
| `_run_lifecycle_emails` | dedup per-email-kind | per cadence |
| `_run_followup_emails` | dedup per-email | per cadence |
| `_run_inbound_actions` | no gate | every cycle |
| `_run_silence_detection` | no gate | every cycle |
| `_run_action_agent` | no gate | every cycle |
| `_run_action_learning` | no gate | every cycle |
| `_run_revenue_triggers` | no gate | every cycle |
| `_run_email_orchestrator_flush` | no gate | every cycle |
| `_run_billing_sync` | no gate | every cycle |
| `_run_scoring_self_eval` | **Sunday only** (`weekday() == 6`) | weekly |
| `_run_sentry_triage` | in-process cooldown | ~5-15min effective |
| `_run_on_alert_responder` | **env `ON_ALERT_RESPONDER_ENABLED=0` default OFF** — framework only until founder approves LLM spend | polls unresolved critical ops_alerts last 24h; framework mode builds context packets without calling LLM |
| `_run_stale_alert_cleanup` | `worker_state` dedup | hourly |
| `_run_cto_health_check` | cooldown (5min transition / 4h critical-repeat) | on state-change |
| `_run_approval_expiry_sweep` | no gate | every cycle |
| `_run_approved_reminders` | `worker_state` dedup daily | once per day |
| `_run_regulatory_feed_monitor` | `worker_state` dedup daily | once per day |
| `_run_regulatory_watch` | `worker_state` dedup weekly | once per week |
| `_run_analytics_retention` | `worker_state` dedup daily | once per day |

**Note:** TIER_2 weekly review fires inside `_run_merchant_digest` via
the Monday gate (per CLAUDE.md §8.3 +
`telegram_agent.send_tier2_weekly_review`).

---

## Internal sub-tasks inside aggregation_worker.py (cycle = 5 min)

Main loop calls `run_product_metrics_task` + `run_store_metrics_task`
+ retention + watchdog + webhook health.

| Sub-task | Gate | Notes |
|---|---|---|
| `product_metrics_task.run` | watermark-based | every cycle; batches |
| `store_metrics_task.run` | no gate | every cycle |
| `retention_task.run_event_retention` | 24h interval (`_CIG_INTERVAL_S`) | once per day |
| `retention_task.run_nudge_event_retention` | same cadence | once per day (60d TTL) |
| `retention_task.run_worker_log_retention` | same cadence | once per day (30d TTL) |
| `watchdog_task.run` | per-hour cooldown | hourly |
| `webhook_health_task.run` | per-day cooldown | daily |
| `cleanup_task.run` (30d nudge_impression_daily) | DELETE WHERE inline | every cycle (idempotent) |
| `_check_cycle_time_regression` | per-hour cooldown (Redis) | 60s warn / 180s critical threshold; creates `aggregation_cycle_slow` ops_alert |

---

## Internal sub-tasks inside intelligence_worker.py (cycle = 10 min)

Scans products for opportunity scoring. No per-shop dim; runs globally.
Contains embedded opportunity-detection logic only.

---

## Internal sub-tasks inside segment_monitor_worker.py (cycle = 5 min)

- Scans Pro shops for hot audience segments
- Creates SCARCITY_NUDGE action tasks when revenue windows open
- Redis cursor `hs:segmon:cursor` for round-robin resume
- Time budget per cycle; batched so all shops processed over N cycles

---

## Internal sub-tasks inside nudge_optimization_worker.py (cycle = 6h)

- Evaluates all active A/B nudges for MDE threshold
- Promotes winners, queues AI challenger generation
- Kill switch via env

---

## Internal sub-tasks inside gdpr_worker.py (cycle = 5 min)

- Picks up pending GdprRequest rows
- Executes data deletion / redaction per Shopify GDPR webhooks

---

## Self-healing / observability schedules (internal to services, triggered from workers)

| Job | Trigger location | Schedule | Dedup |
|---|---|---|---|
| Lighthouse nightly (A3) | agent_worker _run_daily_digest path | nightly via `hs:lighthouse:last_run:{date}` | 30h TTL |
| p95 slow-trend flusher (A4) | backend uvicorn background | 10min interval + cross-worker `hs:p95:flush_lock` | 1min lock |
| LLM benchmark weekly (A5) | agent_worker nightly | weekly via `hs:llm_bench:last_run:{iso_week}` | 8d TTL |
| Sentry rate-spike (A6) | `_run_sentry_triage` | hourly via `hs:spike:sentry_rate:{hour}` | 1h cooldown |
| Sentry regression (A6) | `_run_sentry_triage` | per-fingerprint-hour via `hs:spike:sentry_regression:{fp}:{hour}` | 1h cooldown |
| Tracker error spike (A1) | backend tracker endpoint | per-shop-day via `hs:spike:tracker_runtime:{shop}:{day}` | 24h cooldown |
| Frontend error spike (A2) | backend frontend-error endpoint | per-hour via `hs:spike:frontend_error:{hour}` | 1h cooldown |
| UX frustration spike (A7) | backend ux endpoint | per-shop-day via `hs:spike:ux_frustration:{shop}:{day}` | 24h cooldown |
| Security heartbeat | `_run_security_heartbeat` | hourly (self-rate-limited) | creates critical ops_alert on failure |
| Pipeline heartbeat | `_run_cto_health_check` | per 15-min cycle (1h internal cooldown) | records `heartbeat_failed` ops_alert if probe fails |
| Audit log chain verification | `_run_audit_log_integrity_check` | per `worker_state` dedup | CRITICAL `audit_log_tampering` on mismatch |
| Compliance score auto-pause | `compliance_score.py` | on read (cached) | pauses pipeline if score < 70 |

---

## Key time-of-day / day-of-week gates (single-table summary)

| Gate | Where | What happens |
|---|---|---|
| Rome-hour ≥ 8 | `agent_worker._run_daily_digest:751` | digest is the DAILY BRIEF (B1 equivalent) |
| Rome-hour == any Monday | `agent_worker._run_merchant_digest` | weekly merchant digest + TIER_2 weekly review |
| UTC Monday 04:00-05:00 | `agent_worker._run_pipeline_self_upgrade:984` | weekly self-upgrade run, `SELF_UPGRADE_PAUSED` kill switch |
| UTC Sunday | `agent_worker._run_scoring_self_eval` | weekly scoring self-evaluation |
| 1st of month | `_run_monthly_evolution_audit` | Monthly Opus Audit (paid OPUS call) |

**⚠️ No job runs at a "true" wall-clock time — all fire on the FIRST
cycle that passes the gate after the boundary.** For 15-min
agent_worker, daily digest fires at ≈08:00-08:14 Rome. For 5-min
aggregation_worker, retention fires at ≈00:00-00:04 UTC.

---

## Worker-state DB columns (`worker_state` table)

- `last_run_at` — updated every cycle by each worker
- `last_digest_date` — daily digest send dedup (Rome date)
- `last_self_upgrade_week` — pipeline self-upgrade weekly dedup
- `last_tier2_review_week` — TIER_2 weekly batch dedup
- Plus per-task dedup columns (grow as new scheduled tasks land)

---

## Update protocol

- Any change to `ecosystem.config.js`, `app/workers/**`,
  `app/workers/tasks/**`, `should_run_*()` cooldown constants, or
  `worker_state` schema → update this file in the SAME commit.
- The preflight verifier (`scripts/audit_scheduled_jobs_map.py`)
  blocks commits when agent_worker `_run_*` helpers drift from the
  table above.
- Bump "Last full verification" when re-validating by: (1) `pm2 list`
  confirms 8 processes up, (2) spot-check 3 sub-task gates are firing
  at the expected cadence, (3) `worker_state` query shows sane
  `last_*` timestamps.
- Move deprecated jobs to a "Removed" section with the commit hash
  that killed them.

---

## The "I didn't grep first" checklist

Before writing ANY scheduled job or time-gated code:

1. ✅ Read this file top to bottom.
2. ✅ Grep for the proposed name (e.g., "daily brief" → `grep -ri "daily_brief\|daily_digest"`).
3. ✅ Check if a `_run_*` helper already covers the concern.
4. ✅ Check if a time-of-day gate already fires in the right window.
5. ✅ Only after 1-4 return clean: write the new job.

**If any step returns a match, the work is EDIT-EXISTING, not BUILD-NEW.**
