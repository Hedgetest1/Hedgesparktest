# HedgeSpark Redis Keys — Full Catalog

Exhaustive Redis-prefix inventory used by `audit_claude_md_redis_keys.py`
to validate every prefix in `app/` is documented somewhere.

## Two-tier structure

- **CLAUDE.md §13** — curated subset of load-bearing keys (auto-loaded
  every session; bounded ≤24KB so the operational manual stays
  scrollable).
- **This file** — exhaustive list of every prefix used in `app/`. The
  audit passes if a prefix appears in EITHER file (union semantics).

## Maintenance

- New prefix in code → audit fails → add a row HERE (or §13 if
  load-bearing).
- Prefix removed from code → audit reports stale entry → remove the row.
- Auto-generated 2026-05-04 (Item 7 closure) from a live audit run.
  The TBD entries should be enriched manually with explicit TTL and
  Purpose during the next round of catalog hardening.

## Prefix table

| Prefix | First-seen source | TTL | Purpose |
|---|---|---|---|
| `hs:abndntrnd:v1` | `app/api/lite_extras.py:475` | TBD | TBD |
| `hs:action_cooldown:v1` | `app/services/orchestrator.py:82` | TBD | TBD |
| `hs:adversarial_probes` | `app/services/bugfix_pipeline.py:572` | TBD | TBD |
| `hs:auth:msv:v1` | `app/core/deps.py:115` | 30s | merchant session-version + existence cache (auth fast-path; eliminates per-request DB query under load) |
| `hs:agency:v1` | `app/services/agency.py:22` | TBD | TBD |
| `hs:agg_cursor` | `app/workers/aggregation_worker.py:605` | TBD | TBD |
| `hs:ai_budget` | `app/services/nudge_composer.py:148` | TBD | TBD |
| `hs:ai_compose` | `app/core/redis_client.py:60` | TBD | TBD |
| `hs:ai_compose:*` | `app/api/health.py:167` | TBD | TBD |
| `hs:alert:agg_cycle_slow` | `app/workers/aggregation_worker.py:178` | TBD | TBD |
| `hs:alock` | `app/core/distributed_lock.py:123` | TBD | TBD |
| `hs:annotations:v1` | `app/services/annotations.py:28` | TBD | TBD |
| `hs:antigen` | `app/services/bugfix_pipeline.py:261` | TBD | TBD |
| `hs:approval_reminder` | `app/workers/agent_worker.py:1915` | TBD | TBD |
| `hs:approved_reminder` | `app/workers/agent_worker.py:1880` | TBD | TBD |
| `hs:audit_log:chain_head` | `app/services/audit.py:44` | TBD | TBD |
| `hs:audit_log:quarantined_row_ids` | `app/services/audit.py:384` | TBD | TBD |
| `hs:audit_log_check:day` | `app/workers/agent_worker.py:895` | TBD | TBD |
| `hs:audit_log_tampering:active` | `app/services/compliance_score.py:334` | TBD | TBD |
| `hs:auth:known_fp` | `app/core/auth_hardening.py:94` | TBD | TBD |
| `hs:auth:vel` | `app/core/auth_hardening.py:87` | TBD | TBD |
| `hs:auto_merge_cooldown` | `app/services/promotion_pipeline.py:491` | TBD | TBD |
| `hs:auto_push_cooldown:v1` | `app/services/promotion_pipeline.py:488` | TBD | TBD |
| `hs:auto_remediation:dashboard_drift:cooldown` | `app/services/dashboard_auto_remediation.py:90` | TBD | TBD |
| `hs:auto_remediation:dashboard_drift:count` | `app/services/dashboard_auto_remediation.py:86` | TBD | TBD |
| `hs:auto_resp` | `app/services/auto_responder.py:95` | TBD | TBD |
| `hs:autopsy:v1` | `app/services/revenue_autopsy.py:28` | TBD | TBD |
| `hs:bandit` | `app/services/contextual_bandit.py:84` | TBD | TBD |
| `hs:bench_v2:v1` | `app/services/benchmarks_vertical.py:70` | TBD | TBD |
| `hs:benchmarks:v1` | `app/services/benchmarks.py:71` | TBD | TBD |
| `hs:brief` | `app/workers/aggregation_worker.py:878` | TBD | TBD |
| `hs:chat` | `app/api/chat_support.py:41` | TBD | TBD |
| `hs:churn:lock:v1` | `app/api/lite_extras.py:1890` | TBD | TBD |
| `hs:churn:v1` | `app/api/lite_extras.py:1876` | TBD | TBD |
| `hs:cleanup_pending` | `app/services/telegram_agent.py:1677` | TBD | TBD |
| `hs:cogs_sync_done` | `app/services/pnl_engine.py:363` | TBD | TBD |
| `hs:compliance:auto_pause` | `app/services/compliance_score.py:63` | TBD | TBD |
| `hs:compliance:last_score` | `app/services/compliance_score.py:64` | TBD | TBD |
| `hs:consent` | `app/api/track.py:322` | TBD | TBD |
| `hs:cooldown:evolution_audit` | `app/services/evolution_engine.py:74` | TBD | TBD |
| `hs:cooldown:meta_review` | `app/services/meta_reviewer.py:42` | TBD | TBD |
| `hs:cooldown:monthly_audit` | `app/services/monthly_evolution_audit.py:44` | TBD | TBD |
| `hs:cto_signal_cooldown` | `app/services/system_health_synthesizer.py:294` | TBD | TBD |
| `hs:daily_revenue` | `app/api/webhooks.py:226` | TBD | TBD |
| `hs:dash` | `app/core/redis_client.py:62` | TBD | TBD |
| `hs:data_retention:day` | `app/workers/agent_worker.py:1039` | TBD | TBD |
| `hs:demo_lock` | `app/services/storefront_preview.py:34` | TBD | TBD |
| `hs:deploy:promotion` | `app/services/promotion_pipeline.py:962` | TBD | TBD |
| `hs:dev_brk:v1` | `app/api/lite_extras.py:334` | TBD | TBD |
| `hs:disc:v1` | `app/api/lite_extras.py:1227` | TBD | TBD |
| `hs:email_escalation` | `app/services/inbound_email_processor.py:242` | TBD | TBD |
| `hs:email_orch` | `app/services/email_orchestrator.py:100` | TBD | TBD |
| `hs:email_suppressed` | `app/api/resend_webhooks.py:358` | TBD | TBD |
| `hs:event_bus:cleanup_today` | `app/workers/agent_worker.py:2063` | TBD | TBD |
| `hs:event_bus:emit_fail_count` | `app/services/event_bus.py:203` | TBD | TBD |
| `hs:fe_errors` | `app/api/frontend_errors.py:150` | TBD | TBD |
| `hs:filelock` | `app/core/file_lock.py:42` | TBD | TBD |
| `hs:filelock:*` | `app/core/file_lock.py:315` | TBD | TBD |
| `hs:fix_template` | `app/services/bugfix_pipeline.py:425` | TBD | TBD |
| `hs:fix_template_hits` | `app/services/bugfix_pipeline.py:427` | TBD | TBD |
| `hs:flag` | `app/core/feature_flags.py:48` | TBD | TBD |
| `hs:followup_guard` | `app/services/followup_worker.py:38` | TBD | TBD |
| `hs:fusage` | `app/core/feature_usage.py:33` | TBD | TBD |
| `hs:fusion:v1` | `app/services/anomaly_fusion.py:378` | TBD | TBD |
| `hs:fvr:v1` | `app/api/lite_extras.py:587` | TBD | TBD |
| `hs:genome:v1` | `app/services/revenue_genome.py:43` | TBD | TBD |
| `hs:geo` | `app/core/geo.py:97` | TBD | TBD |
| `hs:geoip` | `app/core/geo.py:54` | TBD | TBD |
| `hs:goals:v1` | `app/services/goals.py:44` | TBD | TBD |
| `hs:google_oauth_state` | `app/api/google_oauth.py:89` | TBD | TBD |
| `hs:holdout:assignment` | `app/services/fix_holdout_measurement.py:58` | TBD | TBD |
| `hs:holdout:measurement` | `app/services/fix_holdout_measurement.py:59` | TBD | TBD |
| `hs:holdout:savings` | `app/services/fix_holdout_measurement.py:60` | TBD | TBD |
| `hs:instant_intel` | `app/services/instant_onboarding.py:40` | TBD | TBD |
| `hs:intent:v1` | `app/services/abandoned_intent.py:27` | TBD | TBD |
| `hs:inv_kpis:v1` | `app/api/inventory.py:38` | TBD | TBD |
| `hs:inv_snap:done` | `app/services/inventory_snapshot_runner.py:28` | TBD | TBD |
| `hs:inv_snap:lock` | `app/services/inventory_snapshot_runner.py:30` | TBD | TBD |
| `hs:kg:v1` | `app/services/knowledge_graph.py:48` | TBD | TBD |
| `hs:klaviyo_events:circuit` | `app/services/klaviyo_events.py:64` | TBD | TBD |
| `hs:klaviyo_events:rate` | `app/services/klaviyo_events.py:65` | TBD | TBD |
| `hs:known_shop` | `app/api/track.py:365` | TBD | TBD |
| `hs:kpush` | `app/services/klaviyo_export.py:608` | TBD | TBD |
| `hs:lite_digest` | `app/services/lite_morning_digest.py:39` | TBD | TBD |
| `hs:live_visitors` | `app/api/live_visitors.py:34` | TBD | TBD |
| `hs:liverts:snap:v1` | `app/api/realtime_stream.py:58` | TBD | TBD |
| `hs:llm:429` | `app/core/llm_budget.py:505` | TBD | TBD |
| `hs:llm:merchant` | `app/core/llm_budget.py:117` | TBD | TBD |
| `hs:llm_pii_guard:violations` | `app/core/llm_pii_guard.py:237` | TBD | TBD |
| `hs:ltv:predicted` | `app/services/ltv_engine.py:422` | TBD | TBD |
| `hs:ltv:products` | `app/services/ltv_engine.py:330` | TBD | TBD |
| `hs:margin_snapshot` | `app/services/margin_guard.py:43` | TBD | TBD |
| `hs:memail` | `app/services/merchant_email_service.py:47` | TBD | TBD |
| `hs:merchant_baseline:v1` | `app/services/data_integrity_probe.py:126` | TBD | TBD |
| `hs:metrics:worker` | `app/core/metrics.py:66` | TBD | TBD |
| `hs:metrics:worker:*` | `app/services/invariant_monitor.py:625` | TBD | TBD |
| `hs:mgroup:v1` | `app/services/merchant_groups.py:26` | TBD | TBD |
| `hs:model_cfg:v1` | `app/services/model_config.py:31` | TBD | TBD |
| `hs:mta` | `app/services/mta_engine.py:60` | TBD | TBD |
| `hs:night_shift` | `app/services/night_shift_agent.py:44` | TBD | TBD |
| `hs:night_shift_latest` | `app/services/night_shift_agent.py:47` | TBD | TBD |
| `hs:ns_cal:obs` | `app/services/night_shift_calibration.py:37` | TBD | TBD |
| `hs:ns_cal:truth` | `app/services/night_shift_calibration.py:38` | TBD | TBD |
| `hs:nudge_cap` | `app/services/action_agent.py:49` | TBD | TBD |
| `hs:nudge_dna:v1` | `app/services/nudge_dna.py:64` | TBD | TBD |
| `hs:oauth_nonce` | `app/api/shopify_oauth.py:106` | TBD | TBD |
| `hs:obc:v1` | `app/api/lite_extras.py:731` | TBD | TBD |
| `hs:order_geo` | `app/api/lite_extras.py:746` | TBD | TBD |
| `hs:orders_summary` | `app/api/webhooks.py:225` | TBD | TBD |
| `hs:p95:*` | `app/services/p95_snapshot.py:144` | TBD | TBD |
| `hs:patchfp:skeleton` | `app/services/bugfix_pipeline.py:246` | TBD | TBD |
| `hs:pixel_secret` | `app/api/track.py:431` | TBD | TBD |
| `hs:pmnt:v1` | `app/api/lite_extras.py:1536` | TBD | TBD |
| `hs:preflight:last_ok` | `app/api/public_transparency.py:224` | TBD | TBD |
| `hs:pricesens:v1` | `app/services/price_sensitivity.py:30` | TBD | TBD |
| `hs:proactive` | `app/services/proactive_chat.py:49` | TBD | TBD |
| `hs:proactive_ack` | `app/services/proactive_chat.py:53` | TBD | TBD |
| `hs:proactive_cache` | `app/services/proactive_chat.py:389` | TBD | TBD |
| `hs:product_conversions` | `app/api/webhooks.py:229` | TBD | TBD |
| `hs:pub_events:dedup` | `app/api/public_events.py:138` | TBD | TBD |
| `hs:pub_events:rate` | `app/api/public_events.py:117` | TBD | TBD |
| `hs:public_roi_counter:v1` | `app/api/public_roi_counter.py:41` | TBD | TBD |
| `hs:public_status:v1` | `app/api/public_status.py:25` | TBD | TBD |
| `hs:public_transparency:v1` | `app/api/public_transparency.py:39` | TBD | TBD |
| `hs:quarantine:cleared` | `app/services/bugfix_prompt_grounding.py:467` | TBD | TBD |
| `hs:rars:v1` | `app/services/revenue_at_risk.py:50` | TBD | TBD |
| `hs:rars_history:v1` | `app/api/roi_hero.py:250`, `app/services/chatbot_llm_fallback.py:317` | TBD | TBD |
| `hs:reengage:drift` | `app/services/onboarding_health.py:61` | TBD | TBD |
| `hs:refund_loss:v1` | `app/services/refund_loss.py:40` | TBD | TBD |
| `hs:refunds:v1` | `app/services/refund_ingest.py:36` | TBD | TBD |
| `hs:reg_feed:item` | `app/services/regulatory_feed_monitor.py:61` | TBD | TBD |
| `hs:reg_feed_monitor:last_run` | `app/services/regulatory_feed_monitor.py:59` | TBD | TBD |
| `hs:regulatory_watch:last_run` | `app/services/regulatory_watch.py:65` | TBD | TBD |
| `hs:repcad:v1` | `app/api/lite_extras.py:968` | TBD | TBD |
| `hs:report:run:v1` | `app/api/reports.py:720` | TBD | TBD |
| `hs:rfm:v1` | `app/services/rfm.py:38` | TBD | TBD |
| `hs:rhythm:v1` | `app/api/lite_extras.py:864` | TBD | TBD |
| `hs:rl` | `app/core/rate_limit.py:49` | TBD | TBD |
| `hs:rl:dash` | `app/main.py:399` | TBD | TBD |
| `hs:rl:track` | `app/api/track.py:346` | TBD | TBD |
| `hs:roi_hero` | `app/api/roi_hero.py:80` | TBD | TBD |
| `hs:rollout:ring_ts` | `app/core/staged_rollout.py:103` | TBD | TBD |
| `hs:rule_rate` | `app/services/rule_engine.py:50` | TBD | TBD |
| `hs:rum:last_run` | `app/services/rum_monitor.py:80` | TBD | TBD |
| `hs:rum:p75_hist` | `app/services/rum_monitor.py:77` | TBD | TBD |
| `hs:rum:samples` | `app/services/rum_monitor.py:74` | TBD | TBD |
| `hs:rum:samples:*` | `app/services/rum_monitor.py:175` | TBD | TBD |
| `hs:rum_rl` | `app/api/rum.py:99` | TBD | TBD |
| `hs:security_guard_blocks` | `app/services/bugfix_pipeline.py:635` | TBD | TBD |
| `hs:security_heartbeat:last_results` | `app/services/security_heartbeat.py:51` | TBD | TBD |
| `hs:security_heartbeat:last_run` | `app/services/security_heartbeat.py:50` | TBD | TBD |
| `hs:self_heal_standby` | `app/workers/agent_worker.py:1993` | TBD | TBD |
| `hs:sentry:quota:v1` | `app/services/sentry_quota.py:34` | TBD | TBD |
| `hs:sentry_poller:cooldown` | `app/services/sentry_poller.py:51` | TBD | TBD |
| `hs:shopify_rl` | `app/core/shopify_client.py:114` | TBD | TBD |
| `hs:signals` | `app/core/redis_client.py:55` | TBD | TBD |
| `hs:silence_detected` | `app/services/silence_detector.py:28` | TBD | TBD |
| `hs:silent_fallback` | `app/core/silent_fallback.py:74` | TBD | TBD |
| `hs:silent_fallback:*` | `app/core/silent_fallback.py:125` | TBD | TBD |
| `hs:silent_fallback:total` | `app/core/silent_fallback.py:76` | TBD | TBD |
| `hs:slo:err` | `app/core/slo.py:110` | TBD | TBD |
| `hs:slo:ok` | `app/core/slo.py:109` | TBD | TBD |
| `hs:slo:tm` | `app/core/slo.py:108` | TBD | TBD |
| `hs:spike:sentry_fp_storm` | `app/services/observability_spikes.py:742` | TBD | TBD |
| `hs:spike:sentry_triage_stuck` | `app/services/observability_spikes.py:671` | TBD | TBD |
| `hs:spike:slo` | `app/services/observability_spikes.py:833` | TBD | TBD |
| `hs:status:v1` | `app/api/lite_extras.py:1359` | TBD | TBD |
| `hs:system_health` | `app/workers/agent_worker.py:1788` | TBD | TBD |
| `hs:tax:v1` | `app/api/lite_extras.py:1446` | TBD | TBD |
| `hs:team_members:v1` | `app/services/team.py:21` | TBD | TBD |
| `hs:tg_confirm` | `app/core/telegram_safety.py:152` | TBD | TBD |
| `hs:tg_idem` | `app/core/telegram_safety.py:38` | TBD | TBD |
| `hs:tg_lock` | `app/core/telegram_safety.py:76` | TBD | TBD |
| `hs:tg_ratelimit:v1` | `app/core/telegram_safety.py:216` | TBD | TBD |
| `hs:tier2_weekly` | `app/workers/agent_worker.py:1189` | TBD | TBD |
| `hs:today_snapshot:v1` | `app/api/today_snapshot.py:294` | TBD | TBD |
| `hs:topltv:v1` | `app/api/lite_extras.py:412` | TBD | TBD |
| `hs:topprod:v1` | `app/api/lite_extras.py:1115` | TBD | TBD |
| `hs:topvar:v1` | `app/api/lite_extras.py:1643` | TBD | TBD |
| `hs:trkerr:tot:*` | `app/services/observability_spikes.py:105` | TBD | TBD |
| `hs:trust_quota` | `app/services/trust_contract.py:49` | TBD | TBD |
| `hs:vertical:v1` | `app/services/vertical_classifier.py:139` | TBD | TBD |
| `hs:watchdog:restart_cooldown` | `app/services/worker_watchdog.py:49` | TBD | TBD |
| `hs:webhook_circuit:fails` | `app/services/signal_webhooks.py:157` | TBD | TBD |
| `hs:webhook_circuit:open` | `app/services/signal_webhooks.py:161` | TBD | TBD |
| `hs:webhook_delivery:v1` | `app/services/signal_webhooks.py:64` | TBD | TBD |
| `hs:webhook_secret:v1` | `app/services/signal_webhooks.py:63` | TBD | TBD |
| `hs:webhooks:v1` | `app/services/signal_webhooks.py:62` | TBD | TBD |
| `hs:wlock` | `app/core/distributed_lock.py:66` | TBD | TBD |
| `llm:*` | `app/core/llm_budget.py:872` | TBD | TBD |
| `llm:daily:_global` | `app/core/llm_budget.py:668` | TBD | TBD |
| `llm:mode_override` | `app/core/llm_budget.py:405` | TBD | TBD |
