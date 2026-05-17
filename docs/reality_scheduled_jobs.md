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

Agent worker calls 44 `_run_*` helpers on every cycle. Each helper
self-gates via one of: in-process monotonic cooldown, DB
`worker_state.last_*_date`, Redis key, or time-of-day condition.
Listed here in execution order with the actual gate.

| Sub-task (fn) | Gate | Frequency reality |
|---|---|---|
| `_run_dashboard_asset_probe` | in-process 5min `should_run()` + Redis SETNX alert dedup (1/hour) | Phase 0-pre-1 — catches stale Next.js in-memory manifest bugs by fetching `/`, `/app`, `/pricing` and HEAD-probing every `_next/static` chunk; alerts `dashboard_asset_drift` on any non-200 |
| `_run_dashboard_auto_remediation` | hourly rate-limit (max 3/hour via Redis) + back-to-back cooldown 120s + kill-switch env `DASHBOARD_AUTO_REMEDIATION_ENABLED=1` (default ON) | Phase 0-pre-1b — deterministic `pm2 restart wishspark-dashboard --update-env` for any unresolved `dashboard_asset_drift` alert; re-probes and resolves origin alert on success or escalates `dashboard_asset_drift_auto_remediation_failed` on failure. Shell-only, no LLM. |
| `_run_email_dns_status_check` | in-process 1h `should_run()` | Phase 0-pre-1c — refreshes Resend `hedgesparkhq.com` domain verification cache; flip-detects verified ↔ failed against `hs:email:last_verified:v1` and fires 🟢 / 🔴 Telegram alert on change. Cannot repair DNS (registrar-side) — only surface state + pair with `send_email()` runtime suppression gate. Companion: `docs/RESEND_DNS_RUNBOOK.md`. |
| `_run_worker_watchdog` | no gate (Phase 0-pre0, runs FIRST in cycle) | every 15min cycle — resurrects stale PM2 workers |
| `_run_orchestrator` | no gate | every 15min cycle |
| `_run_merchant_brain_tick` | env `MERCHANT_BRAIN_ENABLED=1` (default off; un-park ceremony flips on) | every 15min cycle — Brain Vero v0.1 per-merchant SENSE→SYNTHESIZE→DECIDE→COORDINATE→LEARN; bounded at 100 shops/cycle + 6h decision cooldown per shop |
| `_run_onboarding` | no gate | every 15min cycle |
| `_run_onboarding_health` | no gate | every 15min cycle |
| `_run_scaling_intelligence` | no gate | every cycle |
| `_run_entitlement_health_scan` | no gate | every cycle |
| `_run_daily_digest` | **Rome hour ≥ 8 AND `worker_state.last_digest_date != today_rome`** | **once per Rome day, first cycle after 08:00 Rome** (this IS the daily brief — see memory reality_founder_messaging.md system #1) |
| `_run_breach_classifier` | `worker_state` dedup | per cadence |
| `_run_audit_log_integrity_check` | `worker_state` dedup | per cadence |
| `_run_invariant_monitor` | no gate — cheap subprocess; alerting.write_alert dedup suppresses storms | every cycle (15 min); runs registered preflight audits on live source and emits `invariant_regression` ops_alert on failure |
| `_run_uninstall_erasure_watchdog` | in-process monotonic ~hourly | hourly |
| `_run_security_heartbeat` | self-rate-limited via `_should_run()` (hourly) | hourly |
| `_run_gdpr_sla_enforcement` | no gate | every cycle |
| `_run_data_retention` | `worker_state` dedup daily | once per day |
| `_run_merchant_digest` | **Monday only, Europe/Rome** — sends weekly merchant emails | weekly |
| `_run_lite_morning_digest` | **08:00–09:59 Europe/Rome** + Redis dedup `hs:lite_digest:{shop}:{YYYY-MM-DD}` (35h TTL) | **daily morning push for Lite merchants** — email_type `lite_morning_digest`, Gap A of €39-ready sprint (2026-04-20) |
| `_run_lifecycle_emails` | dedup per-email-kind | per cadence |
| `_run_followup_emails` | dedup per-email | per cadence |
| `_run_inbound_actions` | no gate | every cycle |
| `_run_silence_detection` | no gate | every cycle |
| `_run_action_agent` | no gate | every cycle |
| `_run_action_learning` | no gate | every cycle |
| `_run_email_orchestrator_flush` | no gate | every cycle |
| `_run_billing_sync` | no gate | every cycle |
| `_run_sentry_poller` | Redis cooldown `hs:sentry_poller:cooldown` (TTL 180s) | ≥3 min between polls; called inline before sentry_triage so the pipeline gets fresh issues without waiting on email forwarding |
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
| `data_integrity_task.run` | in-process 6h `should_run()` | semantic-drift probe; iterates N merchants so off-cycle from the 5-min main loop |
| `nudge_compose_task.run` | `ai_compose_pending=True` flag + per-cycle batch cap + `protection_state()` | AI variant upgrade for Pro nudges; self-protects against LLM budget degradation |
| `night_shift_task.run` | `night_shift_task.is_due()` → `should_run_nightly_now()` day-lock | wraps `night_shift_agent.run_nightly_for_all_pro` once per day; also triggers MA-6 email batch |
| `rollout_promotion_task.run` | in-process 5-min cooldown | walks flag registry; `promote_if_healthy` handles dwell + SLO gate per flag |
| `partition_maintenance_task.run` | `partition_maintenance_task.is_due()` → 24h interval | idempotent roll-forward of `events` monthly partitions (current + next 3 months via the in-DB `create_events_partition`); defuses the dated `events_default` cliff (born 2026-05-17, partitions had stopped at 2026-06) |
| `ingest_buffer.drain_events` (J3-part-2) | a daemon **thread** `ingest-drain` started in `aggregation_worker.main()` — NOT a `*_task` module + NOT the 5-min cycle (a 10k ingest buffer needs seconds-latency drain; the cycle is too slow). Singleton process (PM2 instances:1 + worker_lock) ⟹ exactly one drainer cluster-wide; atomic `LPOP count` ⟹ even an accidental concurrent drainer is safe. Idle sleep `INGEST_DRAIN_IDLE_SLEEP_S` (5s), loops fast under backlog | bulk `execute_values` INSERT of buffered non-purchase analytics events into `events` + batched visitor upsert; own SessionLocal (worker-style, not a request dep) |
| `dashboard_asset_probe_task.run` | invoked by agent_worker `_run_dashboard_asset_probe` (Phase 0-pre-1, see above) — listed here for task-module coverage | owned by agent_worker, not aggregation_worker |
| `email_dns_status_task.run` | invoked by agent_worker `_run_email_dns_status_check` (Phase 0-pre-1c, see above) — listed here for task-module coverage | owned by agent_worker, not aggregation_worker |
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
